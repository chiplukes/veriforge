"""High-level :class:`Testbench` runtime built on top of :class:`TestbenchPlan`.

This module is the user-facing entry point for Phase 7 of the testbench
roadmap. It hides clock setup, reset sequencing, endpoint instantiation,
and multi-domain stepping behind a small DSL surface::

    from veriforge.sim.bench import Testbench

    bench = Testbench(parsed_module, engine="reference")
    with bench.run():
        bench.reset_all()
        bench.iface("m_axis").put([0x11, 0x22, 0x33])
        frame = bench.iface("s_axis").get(timeout=200)
        assert list(frame.data) == [0x11, 0x22, 0x33]

Construction performs one inference pass via
:func:`veriforge.sim.bench.build_plan` (subject to ``overrides`` and
``strict``) and then materializes one :class:`Domain` per planned
clock domain. Interface proxies are created on first ``iface(name)``
call and registered with their domain so multi-domain stepping picks up
every endpoint.

The runtime intentionally keeps its public surface tiny so that future
phases (engine-native lowering) can swap implementations without
breaking existing tests.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Iterator

from veriforge.model.design import Module as ModelModule
from veriforge.sim.endpoints import DomainCoordinator, MultiDomainRunner
from veriforge.sim.step_harness import step_drive
from veriforge.sim.testbench import Clock, Simulator
from veriforge.sim.trace import attach_vcd

from .interfaces import AXI4Proxy, AXILiteProxy, AXIStreamProxy, BenchTimeoutError, MemBusProxy, StreamProxy
from .plan import ClockDomain, TestbenchPlan
from .planner import PlannerOverrides, build_plan

if TYPE_CHECKING:
    from collections.abc import Mapping

    from veriforge.model.design import Design

    from .lowering import InterfaceLowering, LoweredDesign

# Default clock period (in simulator time units) when ClockSpec.period_hint
# is None. Chosen to match the existing Simulator examples.
_DEFAULT_PERIOD = 10
# Number of cycles of low/high reset assertion during reset_all().
_RESET_ASSERT_CYCLES = 4
_RESET_RELEASE_SETTLE_CYCLES = 2
# Maximum sim time horizon for clock event scheduling on construction.
_DEFAULT_MAX_TIME = 1_000_000


class Domain:  # cm:e3f1b5
    """A single clock domain with optional reset and registered endpoints.

    A :class:`Domain` is created internally by :class:`Testbench` from a
    :class:`ClockDomain` plan entry. It owns:

    * the clock period (from the plan or testbench default),
    * the reset polarity / style metadata,
    * a :class:`~veriforge.sim.endpoints.DomainCoordinator` that
      collects every endpoint bound to this domain so that the
      :class:`~veriforge.sim.endpoints.MultiDomainRunner` can drive
      them in lock-step,
    * a back-reference to the owning :class:`Testbench` so proxies can
      step the right shared runner.
    """

    _COMBINATIONAL_SENTINEL = "__combinational__"

    def __init__(self, bench: "Testbench", spec: ClockDomain, *, period: int, strict: bool = True):
        self.bench = bench
        self.spec = spec
        self.name = spec.name
        self.clock_name = spec.clock.name
        self.period = period
        self.reset_name = spec.reset.name if spec.reset is not None else None
        self.reset_active_low = spec.reset.active_low if spec.reset is not None else None
        self.reset_style = spec.reset.style if spec.reset is not None else None
        self._is_combinational = self.clock_name == self._COMBINATIONAL_SENTINEL
        self._coord = DomainCoordinator(
            bench.sim,
            endpoints=[],
            clock_name=self.clock_name if not self._is_combinational else "",
            name=self.name,
            reset_name=self.reset_name,
            strict=strict,
        )

    @property
    def sim(self):
        """Convenience access to the owning testbench's simulator."""
        return self.bench.sim

    @property
    def coordinator(self) -> DomainCoordinator:
        return self._coord

    def register(self, endpoint) -> None:
        """Register ``endpoint`` so the multi-domain runner ticks it."""
        self._coord.endpoints.append(endpoint)
        if self._coord._guard is not None and hasattr(endpoint, "sim"):
            endpoint.sim = self._coord._guard
        if self._is_combinational and self.bench._runner is not None:
            self.bench._runner.endpoints.append(endpoint)  # type: ignore[attr-defined]

    def generator(self, gen_fn):
        """Decorator: register a generator-based endpoint on this domain.

        The generator's ``yield`` marks the clock-edge boundary: everything
        before ``yield`` runs in ``tick_pre`` (drives), everything after
        runs in ``tick_post`` (state commit, only when this domain's clock
        rises).

        Usage::

            @d_a.generator
            def my_driver():
                d_a.drive("a", value)
                yield
                value = (value + 1) & 0xFF
        """
        from veriforge.sim.endpoints._generator import GeneratorEndpoint

        ep = GeneratorEndpoint(gen_fn)
        self.register(ep)
        return gen_fn

    def step(self, cycles: int = 1) -> bool:
        """Advance the shared runner until this domain has ``cycles`` rising edges.

        Returns ``True`` if all requested edges occurred; ``False`` if the
        simulator stalled (no more events scheduled).
        """
        for _ in range(cycles):
            if not self.bench._step_until_domain_edge(self):
                return False
        return True

    def assert_reset(self) -> None:
        """Drive the reset port to its asserted level (no stepping)."""
        if self.reset_name is None:
            return
        level = 0 if self.reset_active_low else 1
        step_drive(self.bench.sim, self.bench.sim._engine, self.reset_name, level)

    def release_reset(self) -> None:
        """Drive the reset port to its released level (no stepping)."""
        if self.reset_name is None:
            return
        level = 1 if self.reset_active_low else 0
        step_drive(self.bench.sim, self.bench.sim._engine, self.reset_name, level)


class Testbench:  # cm:8a7c9d
    """High-level multi-domain testbench harness driven by a :class:`TestbenchPlan`.

    Args:
        module: Parsed model module representing the DUT. Used both for
            inference (when ``plan`` is ``None``) and for instantiating
            the underlying :class:`~veriforge.sim.testbench.Simulator`.
        plan: Optional pre-built :class:`TestbenchPlan`. When supplied,
            inference is skipped and ``overrides`` / ``strict`` are
            ignored.
        engine: Simulator engine identifier (``"reference"``, ``"vm"``,
            or ``"compiled"``).
        overrides: Optional :class:`PlannerOverrides` (or mapping)
            applied during plan inference. Ignored if ``plan`` is given.
        strict: Strict-mode toggle for plan inference.
        default_clock_period: Period (in simulator time units) used for
            any domain whose plan does not specify a ``period_hint``.

    The constructor:

    1. Builds (or accepts) the plan.
    2. Instantiates the :class:`Simulator`.
    3. Creates a :class:`Domain` per planned clock domain and schedules
       its clock toggles.
    4. Initializes any reset signal to its *released* level.

    Endpoint proxies are created lazily on the first :meth:`iface` call
    so tests pay nothing for unused interfaces.
    """

    # Tell pytest not to collect this class as a test even though its name
    # starts with "Test".
    __test__ = False

    def __init__(  # noqa: PLR0913
        self,
        module: ModelModule,
        *,
        plan: TestbenchPlan | None = None,
        engine: str = "reference",
        overrides: PlannerOverrides | dict | None = None,
        strict: bool = True,
        default_clock_period: int = _DEFAULT_PERIOD,
        max_sim_time: int = _DEFAULT_MAX_TIME,
        design: "Design | None" = None,
    ):
        # Plan on the un-flattened (boundary) module so clock/reset names
        # match the DUT's external port names — even when the module
        # delegates sequential logic to child cells (e.g. ``stream_fifo``
        # -> ``fifo_v3``). The naming fallback in :func:`build_plan`
        # handles modules with no ``always`` blocks of their own.
        # ``design`` is forwarded to ``Simulator`` for actual elaboration.
        self.module = module
        self._strict = strict
        coerced_overrides = PlannerOverrides.coerce(overrides)
        self._iface_layouts: dict[str, dict[str, object]] = {
            k: dict(v) for k, v in coerced_overrides.iface_layouts.items()
        }
        self.plan = (
            plan if plan is not None else build_plan(module, overrides=coerced_overrides, strict=strict, design=design)
        )
        self.engine = engine
        self.default_clock_period = default_clock_period

        self.sim = Simulator(module, engine=engine, design=design)
        self.sim.run(max_time=0)

        # Drive every DUT input to a known 0 value so combinational outputs
        # don't propagate X into endpoint samplers before the user has had
        # a chance to release reset and queue stimulus.
        for port in module.input_ports():
            try:
                step_drive(self.sim, self.sim._engine, port.name, 0)
            except (KeyError, AttributeError, ValueError):
                # Best-effort: skip ports the engine can't drive directly
                # (e.g., already-bound clocks, unknown signal handles).
                pass
        self.sim.run(max_time=0)

        self._domains: dict[str, Domain] = {}
        for spec in self.plan.domains:
            period = spec.clock.period_hint or default_clock_period
            domain = Domain(self, spec, period=period, strict=self._strict)
            self._domains[domain.name] = domain
            if domain._is_combinational:
                continue
            # Drive reset to released state so combinational logic settles
            # cleanly before the runner starts ticking endpoints.
            domain.release_reset()
            # Schedule clock toggles for the full simulation horizon.
            self.sim._schedule_clock_events(
                Clock(self.sim.signal(domain.clock_name), period=period),
                max_sim_time,
            )
        self.sim.run(max_time=0)

        # Build the multi-domain runner up front; endpoints will be
        # appended to each domain's coordinator as proxies are created.
        coords = [d.coordinator for d in self._domains.values() if not d._is_combinational]
        if coords:
            from veriforge.sim.endpoints import MultiDomainRunner as _MDR

            self._runner = _MDR(self.sim, coords)  # type: ignore[assignment]
        else:
            # Combinational / clockless DUT: no clock domains, so use
            # CombinationalCoordinator as a degenerate single-step runner.
            from veriforge.sim.endpoints import CombinationalCoordinator as _CC

            self._runner = _CC(self.sim, [], strict=self._strict)  # type: ignore[assignment]

        self._proxies: dict[str, AXIStreamProxy | AXILiteProxy | AXI4Proxy | StreamProxy | MemBusProxy] = {}

    # ------------------------------------------------------------------ lookup

    def domain(self, name: str) -> Domain:
        """Return the :class:`Domain` named ``name``."""
        try:
            return self._domains[name]
        except KeyError as exc:
            raise KeyError(f"no domain named {name!r}; known: {sorted(self._domains)}") from exc

    def iface(self, prefix: str) -> AXIStreamProxy | AXILiteProxy | AXI4Proxy | StreamProxy | MemBusProxy:
        """Return (creating on first call) the proxy for interface ``prefix``."""
        if prefix in self._proxies:
            return self._proxies[prefix]
        binding = self.plan.interface(prefix)
        domain = self.domain(binding.domain_name)
        proxy: AXIStreamProxy | AXILiteProxy | AXI4Proxy | StreamProxy | MemBusProxy
        if binding.protocol == "axi_stream":
            layout = self._iface_layouts.get(prefix, {})
            proxy = AXIStreamProxy(
                domain,
                prefix,
                role=binding.role,
                elements_per_beat=layout.get("elements_per_beat"),  # type: ignore[arg-type]
                element_size_bits=layout.get("element_size_bits"),  # type: ignore[arg-type]
                endian=layout.get("endian", "little"),  # type: ignore[arg-type]
            )
        elif binding.protocol == "axi_lite":
            proxy = AXILiteProxy(domain, prefix, role=binding.role)
        elif binding.protocol == "axi4":
            proxy = AXI4Proxy(domain, prefix, role=binding.role)
        elif binding.protocol == "stream":
            proxy = StreamProxy(
                domain,
                prefix,
                role=binding.role,
                signals=dict(binding.signals),
            )
        elif binding.protocol == "membus":
            proxy = MemBusProxy(
                domain,
                prefix,
                role=binding.role,
                signals=dict(binding.signals),
            )
        else:
            raise NotImplementedError(f"protocol {binding.protocol!r} is not supported by Testbench")
        self._proxies[prefix] = proxy
        return proxy

    # ------------------------------------------------------------------ native lowering

    def compile_native(
        self,
        *,
        lowerings: "Mapping[str, InterfaceLowering]",
        name: str = "bench_native_top",
    ) -> "LoweredDesign":
        """Lower this bench to an engine-native wrapper module.

        Thin wrapper around :func:`veriforge.sim.bench.compile_native`
        — see that function for the subset rules and return shape.
        """
        from .lowering import compile_native as _compile_native  # noqa: PLC0415

        return _compile_native(self, lowerings=lowerings, name=name)

    # ------------------------------------------------------------------ control

    def reset_all(self) -> None:
        """Drive every domain's reset asserted, run a few cycles, then release.

        Domains without a reset are skipped. After release, the testbench
        runs a small settle window so that registered endpoints observe a
        clean post-reset state.
        """
        had_reset = False
        for domain in self._domains.values():
            if domain.reset_name is not None:
                domain.assert_reset()
                had_reset = True
        self.sim.run(max_time=0)
        if had_reset:
            self.step(_RESET_ASSERT_CYCLES)
        for domain in self._domains.values():
            if domain.reset_name is not None:
                domain.release_reset()
        self.sim.run(max_time=0)
        if had_reset:
            self.step(_RESET_RELEASE_SETTLE_CYCLES)

    def step(self, cycles: int = 1, *, domain: str | None = None) -> bool:
        """Advance the simulator.

        With ``domain=None`` (default), each requested cycle advances the
        shared :class:`MultiDomainRunner` until *any* clock has a rising
        edge. With ``domain=<name>``, each cycle advances until that
        specific domain's clock has a rising edge.

        For combinational / clockless DUTs, each cycle runs a single
        drive → settle → sample step through the combinational coordinator.

        Returns ``True`` on success, ``False`` if the simulator stalled.
        """
        if self._runner is None:
            raise RuntimeError("Testbench has no runner")
        from veriforge.sim.endpoints import CombinationalCoordinator

        if domain is None:
            if isinstance(self._runner, CombinationalCoordinator):
                for _ in range(cycles):
                    if not self._runner.step():
                        return False
                return True
            # MultiDomainRunner path
            for _ in range(cycles):
                if not self._runner.step():
                    return False
            return True
        target = self.domain(domain)
        if isinstance(self._runner, CombinationalCoordinator):
            raise RuntimeError(
                f"Testbench has no clock domains — cannot step domain {domain!r}. "
                "Use bench.step() (without domain=) for combinational DUTs."
            )
        for _ in range(cycles):
            if not self._step_until_domain_edge(target):
                return False
        return True

    def _step_until_domain_edge(self, target: Domain, *, max_inner_steps: int = 10_000) -> bool:
        """Run the multi-domain runner until ``target`` sees a rising edge.

        Each call to ``MultiDomainRunner.step()`` returns at the next rising
        edge of *some* clock; we loop until the runner reports that
        ``target`` was among the domains that rose.

        Combines clockless DUT support: returns False.
        """
        from veriforge.sim.endpoints import CombinationalCoordinator

        if self._runner is None or isinstance(self._runner, CombinationalCoordinator):
            return False
        target_coord = target.coordinator
        for _ in range(max_inner_steps):
            if not self._runner.step():
                return False
            if target_coord in self._runner.last_risen:
                return True
        return False

    # ---------------------------------------------------------------- lifecycle

    @contextmanager
    def run(
        self,
        *,
        vcd: str | Path | None = None,
        vcd_timescale: str = "1ns",
        vcd_signals: Iterable[str] | None = None,
    ) -> Iterator["Testbench"]:
        """Context manager around the test body.

        Args:
            vcd: If provided, open a VCD trace at this path for the
                duration of the ``with`` block. The file is finalized
                automatically on exit (even if the body raises).
            vcd_timescale: VCD ``$timescale`` directive
                (default ``"1ns"``).
            vcd_signals: Iterable of signal names to record. ``None``
                records every top-level signal known to the simulator.
        """
        trace_session = None
        if vcd is not None:
            trace_session = attach_vcd(
                self.sim,
                vcd,
                timescale=vcd_timescale,
                signal_names=vcd_signals,
            )
        try:
            yield self
        finally:
            if trace_session is not None:
                trace_session.close()


def make_bench(module: ModelModule, **kwargs) -> Testbench:  # cm:6d5f2c
    """Convenience factory: ``Testbench(module, **kwargs)``."""
    return Testbench(module, **kwargs)


__all__ = [
    "BenchTimeoutError",
    "Domain",
    "Testbench",
    "make_bench",
]
