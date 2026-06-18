"""Endpoint coordination helpers."""

from __future__ import annotations

import warnings as _warnings

# AXI channel prefixes used by :func:`resolve_signal_name` to fall back
# from canonical concatenated naming (``slv_awvalid``) to underscore-
# separated naming (``slv_aw_valid``). Real-world Verilog (pulp,
# vendor-generated cores) commonly uses the underscore form.
_AXI_CHANNELS = ("aw", "ar", "w", "b", "r")
_AXIS_CHANNELS = ("t",)


def resolve_signal_name(sim, prefix: str, suffix: str) -> str | None:
    """Return the matching simulator signal name for ``prefix_suffix``.

    Tries the canonical form ``f"{prefix}_{suffix}"`` first. If that
    signal is not present in the simulator and ``suffix`` begins with a
    known AXI/AXIS channel prefix (``aw``/``ar``/``w``/``b``/``r``/
    ``t``), falls back to the underscore-separated form (e.g.
    ``slv_aw_valid`` for canonical ``slv_awvalid``). Returns ``None``
    if neither form exists.
    """
    canonical = f"{prefix}_{suffix}"
    try:
        sim.signal(canonical)
        return canonical
    except Exception:
        pass
    for chan in (*_AXI_CHANNELS, *_AXIS_CHANNELS):
        if suffix.startswith(chan) and len(suffix) > len(chan):
            field = suffix[len(chan) :]
            alt = f"{prefix}_{chan}_{field}"
            try:
                sim.signal(alt)
                return alt
            except Exception:
                continue
    return None


def _settle_current_time(sim, clock_name: str) -> None:
    sim.settle()


class _GuardedSimulator:
    """Phase-tracking sim facade used by ``EndpointCoordinator(strict=True)``.

    Wraps the real ``Simulator`` and enforces the endpoint contract:

    * ``drive()`` raises ``RuntimeError`` during ``sample_pre`` — sampling
      must be read-only; a drive at this point corrupts the combinational
      settle that just ran.
    * ``read()`` emits ``UserWarning`` during ``tick_post`` — NBA (non-blocking
      assign) results are committed but not yet propagated; reads here may
      observe stale combinational values.

    All other attribute access is forwarded transparently to the real sim.

    **Limitation**: this guard only intercepts calls routed through
    ``sim.drive()`` / ``sim.read()``.  Reads via a stored ``SignalHandle``
    (``handle.value``) bypass it because handles hold a direct scheduler
    reference captured at construction time.
    """

    def __init__(self, real_sim) -> None:
        self._real = real_sim
        self._phase: str = "idle"

    def __getattr__(self, name: str):
        return getattr(self._real, name)

    def drive(self, name: str, value) -> None:
        if self._phase == "sample_pre":
            raise RuntimeError(
                f"EndpointCoordinator(strict=True): drive({name!r}) called during "
                "sample_pre phase.  sample_pre must only read signals — a drive here "
                "corrupts the combinational-settle state."
            )
        self._real.drive(name, value)

    def read(self, name: str):
        if self._phase == "tick_post":
            _warnings.warn(
                f"EndpointCoordinator(strict=True): read({name!r}) called during "
                "tick_post phase.  NBA results are committed but combinational "
                "signals may not be settled yet — capture values in sample_pre instead.",
                UserWarning,
                stacklevel=3,
            )
        return self._real.read(name)

    def settle(self) -> None:
        self._real.settle()

    def signal(self, name: str):
        return self._real.signal(name)


class EndpointCoordinator:
    """Coordinate multiple endpoints around the simulator step boundary.

    Parameters
    ----------
    sim:
        The ``Simulator`` instance shared by all endpoints.
    endpoints:
        Sequence of endpoint objects implementing ``tick_pre`` / ``tick_post``
        (and optionally ``sample_pre``).
    clock_name:
        Name of the clock signal to watch for rising edges.
    strict:
        When ``True``, wrap the simulator in a :class:`_GuardedSimulator` and
        patch each endpoint's ``.sim`` attribute to use it.  The guard raises
        ``RuntimeError`` on ``drive()`` calls during ``sample_pre`` and emits
        ``UserWarning`` on ``read()`` calls during ``tick_post``, surfacing
        contract violations at the call site rather than as silent data
        corruption.

        Only calls routed through ``sim.drive()`` / ``sim.read()`` are
        intercepted; reads via a stored ``SignalHandle`` bypass the guard
        because handles capture a direct scheduler reference at construction.
    """

    def __init__(self, sim, endpoints, *, clock_name: str = "clk", strict: bool = False):
        self.sim = sim
        self.endpoints = list(endpoints)
        self.clock = sim.signal(clock_name)
        if strict:
            guard = _GuardedSimulator(sim)
            self._guard: _GuardedSimulator | None = guard
            for ep in self.endpoints:
                if hasattr(ep, "sim"):
                    ep.sim = guard
        else:
            self._guard = None

    def step(self) -> bool:
        guard = self._guard
        if guard is not None:
            guard._phase = "tick_pre"
        for endpoint in self.endpoints:
            endpoint.tick_pre()
        _settle_current_time(self.sim, self.clock.name)
        if guard is not None:
            guard._phase = "sample_pre"
        for endpoint in self.endpoints:
            sample_pre = getattr(endpoint, "sample_pre", None)
            if sample_pre is not None:
                sample_pre()
        if guard is not None:
            guard._phase = "idle"
        while True:
            previous_clock = int(self.clock.value)
            stepped = self.sim.run_step()
            if not stepped:
                return False
            current_clock = int(self.clock.value)
            if previous_clock == 0 and current_clock == 1:
                if guard is not None:
                    guard._phase = "tick_post"
                for endpoint in self.endpoints:
                    endpoint.tick_post()
                if guard is not None:
                    guard._phase = "idle"
                return True

    def run_until(self, predicate, *, max_steps: int, message: str) -> None:
        for _ in range(max_steps):
            if predicate():
                return
            if not self.step():
                raise RuntimeError(f"simulation stopped before {message}")
        raise TimeoutError(message)


class CombinationalCoordinator:
    """Drive → settle → sample coordinator for clockless / combinational DUTs.

    Replaces the clock-edge wait of :class:`EndpointCoordinator` with an
    immediate ``settle()``.  Each :meth:`step` call is:

    1. ``tick_pre`` on all endpoints  — drives stimulus inputs.
    2. ``sim.settle()``               — propagates drives through
       combinational logic at the current time.
    3. ``sample_pre`` / ``tick_post`` — captures combinational outputs.

    This lets purely combinational DUTs (no always-blocks, no clock)
    participate in the endpoint framework without needing a clock signal.

    Example::

        coordinator = CombinationalCoordinator(sim, [driver, checker])
        driver.set_inputs(a=1, b=0)
        coordinator.step()        # drive → settle → sample in one call
        assert checker.output == 1

    Parameters
    ----------
    sim:
        The ``Simulator`` instance.
    endpoints:
        Endpoint objects implementing ``tick_pre`` / ``tick_post``
        (and optionally ``sample_pre``).
    strict:
        When ``True``, wrap the simulator in a :class:`_GuardedSimulator` and
        patch each endpoint's ``.sim`` to use it.  The guard raises
        ``RuntimeError`` on ``drive()`` during ``sample_pre`` and emits
        ``UserWarning`` on ``read()`` during ``tick_post``.
    """

    def __init__(self, sim, endpoints, *, strict: bool = False) -> None:
        self._strict = strict
        self._guard: _GuardedSimulator | None = None
        self.sim = sim
        if strict:
            self._guard = _GuardedSimulator(sim)
            for ep in endpoints:
                if hasattr(ep, "sim"):
                    ep.sim = self._guard
        self.endpoints = list(endpoints)

    def step(self) -> bool:
        """Drive inputs, settle combinational logic, sample outputs.

        Always returns ``True`` (combinational evaluation cannot stall).
        """
        guard = self._guard
        if guard is not None:
            guard._phase = "tick_pre"
        for ep in self.endpoints:
            ep.tick_pre()
        self.sim.settle()
        if guard is not None:
            guard._phase = "sample_pre"
        for ep in self.endpoints:
            sampler = getattr(ep, "sample_pre", None)
            if sampler is not None:
                sampler()
        if guard is not None:
            guard._phase = "tick_post"
        for ep in self.endpoints:
            ep.tick_post()
        if guard is not None:
            guard._phase = "idle"
        return True

    def run_until(self, predicate, *, max_steps: int, message: str) -> None:
        """Repeat :meth:`step` until *predicate* returns ``True``.

        Raises :class:`TimeoutError` if the predicate is not satisfied
        within *max_steps* steps.
        """
        for _ in range(max_steps):
            if predicate():
                return
            self.step()
        raise TimeoutError(message)


class DomainCoordinator:  # cm:9a8f2c
    """Single-clock-domain wrapper for use inside :class:`MultiDomainRunner`.

    Owns the endpoints belonging to one clock domain and exposes the
    ``tick_pre``/``tick_post`` lifecycle without driving the simulator
    itself. Simulation advancement is delegated to :class:`MultiDomainRunner`.

    Attributes:
        sim: The shared simulator instance.
        endpoints: Endpoints bound to this domain.
        name: Domain name (typically the clock signal name).
        clock: The clock signal handle (``sim.signal(clock_name)``).
        reset_name: Optional reset port name for diagnostics; the
            coordinator does not drive reset itself.
    """

    def __init__(
        self,
        sim,
        endpoints,
        *,
        clock_name: str,
        name: str | None = None,
        reset_name: str | None = None,
        strict: bool = False,
    ):
        self.sim = sim
        self.endpoints = list(endpoints)
        self.name = name or clock_name
        self.clock = sim.signal(clock_name) if clock_name else None
        self.reset_name = reset_name
        self._strict = strict
        self._guard: _GuardedSimulator | None = None
        if strict:
            self._guard = _GuardedSimulator(sim)
            for ep in self.endpoints:
                if hasattr(ep, "sim"):
                    ep.sim = self._guard

    def _set_phase(self, phase: str) -> None:
        if self._guard is not None:
            self._guard._phase = phase

    def tick_pre(self) -> None:
        self._set_phase("tick_pre")
        for endpoint in self.endpoints:
            endpoint.tick_pre()

    def sample_pre(self) -> None:
        self._set_phase("sample_pre")
        for endpoint in self.endpoints:
            sampler = getattr(endpoint, "sample_pre", None)
            if sampler is not None:
                sampler()

    def tick_post(self) -> None:
        self._set_phase("tick_post")
        for endpoint in self.endpoints:
            endpoint.tick_post()
        self._set_phase("idle")


class MultiDomainRunner:  # cm:4d2b6e
    """Step a simulator that has multiple independent clock domains.

    Each registered :class:`DomainCoordinator` owns the endpoints for one
    clock. ``step()`` advances the simulator until **any** registered
    clock has a rising edge, then dispatches ``tick_post()`` only on the
    domain(s) that observed the edge. Simultaneous edges (rare but
    possible when periods divide evenly) are handled by dispatching to
    every domain whose clock just rose.

    Endpoints' ``tick_pre`` and ``sample_pre`` hooks are invoked across
    all domains before stepping begins, mirroring the contract of
    :class:`EndpointCoordinator` for the single-domain case.
    """

    def __init__(self, sim, domains):
        self.sim = sim
        self.domains: list[DomainCoordinator] = list(domains)
        if not self.domains:
            raise ValueError("MultiDomainRunner requires at least one DomainCoordinator")
        seen: set[str] = set()
        for d in self.domains:
            if d.name in seen:
                raise ValueError(f"duplicate domain name: {d.name!r}")
            seen.add(d.name)
        # Domains that observed a rising edge during the most recent
        # successful :meth:`step` call. Empty before the first step.
        self.last_risen: list[DomainCoordinator] = []

    def step(self) -> bool:
        for d in self.domains:
            d.tick_pre()
        # Any clock signal will do — we just need to wake the scheduler at
        # the current time so combinational settling completes.
        _settle_current_time(self.sim, self.domains[0].clock.name)  # type: ignore[union-attr]
        for d in self.domains:
            d.sample_pre()

        while True:
            previous = [int(d.clock.value) for d in self.domains]  # type: ignore[union-attr]
            stepped = self.sim.run_step()
            if not stepped:
                self.last_risen = []
                return False
            current = [int(d.clock.value) for d in self.domains]  # type: ignore[union-attr]
            risen = [d for d, p, c in zip(self.domains, previous, current, strict=True) if p == 0 and c == 1]
            if risen:
                for d in risen:
                    d.tick_post()
                self.last_risen = risen
                return True

    def run_until(self, predicate, *, max_steps: int, message: str) -> None:
        for _ in range(max_steps):
            if predicate():
                return
            if not self.step():
                raise RuntimeError(f"simulation stopped before {message}")
        raise TimeoutError(message)

    def domain(self, name: str) -> DomainCoordinator:
        for d in self.domains:
            if d.name == name:
                return d
        raise KeyError(f"no domain named {name!r}")
