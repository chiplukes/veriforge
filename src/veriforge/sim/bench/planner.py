"""Inference pipeline that builds a :class:`TestbenchPlan` from a parsed module.

Phase 2 entry point. The planner combines:

* structural clock/reset analysis from
  :func:`veriforge.analysis.clock_reset.extract_clocks_resets`,
* flat-port AXI / AXI-Stream detection from
  :func:`veriforge.sim.endpoints.detect.detect_interfaces`,
* AXI naming heuristics for clock/reset associations
  (``aclk``/``aresetn``, ``pclk``/``presetn``, ``<prefix>_clk``,
  ``<prefix>_rst[_n]``, ...),
* and explicit user overrides,

into a single immutable :class:`TestbenchPlan`. Strict mode (the
default) refuses to silently pick when more than one domain plausibly
owns an interface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from veriforge.analysis.clock_reset import (
    ClockResetInfo,
    ClockSignal,
    ResetSignal,
    extract_clocks_resets,
    extract_clocks_resets_hier,
)
from veriforge.model.behavioral import AlwaysBlock
from veriforge.model.design import Design, Module, PortDirection
from veriforge.model.expressions import Identifier
from veriforge.sim.endpoints.detect import (
    DetectedInterface,
    detect_interfaces,
    detect_near_misses,
    detect_relaxed_interfaces,
)

from .plan import (
    ClockDomain,
    ClockSpec,
    InterfaceBinding,
    PlanValidationError,
    ResetSpec,
    TestbenchPlan,
)


class AmbiguousDomainError(ValueError):
    """Raised in strict mode when an interface has >1 plausible domain."""


class NoDomainError(ValueError):
    """Raised when an interface cannot be bound to any domain at all."""


# ---------------------------------------------------------------------------
# Override surface (Phase 2 minimal; expanded in Phase 6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlannerOverrides:  # cm:1f6e5b
    """User-supplied corrections applied during plan construction.

    Attributes:
        iface_domains: Map ``interface prefix -> domain name``. Forces the
            named interface to the named domain regardless of inference.
        clock_periods: Map ``clock signal name -> period`` in simulator
            time units. Filled into :attr:`ClockSpec.period_hint`.
        domain_aliases: Map ``clock signal name -> domain name``. By
            default the domain name is the clock name; this lets the user
            rename, e.g. ``{"aclk": "axi"}``.
        reset_polarities: Map ``reset signal name -> "active_high"|
            "active_low"``. Forces :attr:`ResetSpec.active_low` for the
            named reset, regardless of inferred polarity.
        iface_layouts: Map ``interface prefix -> layout dict`` with any of
            the keys ``elements_per_beat`` (int, > 0),
            ``element_size_bits`` (int, > 0), ``endian``
            (``"little"`` | ``"big"``). Any keys present override the
            value auto-inferred by the proxy from the DUT's TDATA / TKEEP
            widths. Unspecified keys keep the inferred value. Useful when
            the DUT lacks TKEEP (so the proxy can't derive an element
            width on its own) or when the element width does not divide
            the TDATA width evenly (e.g., 12-bit pixels in 48-bit beats).
    """

    iface_domains: Mapping[str, str] = field(default_factory=dict)
    clock_periods: Mapping[str, int] = field(default_factory=dict)
    domain_aliases: Mapping[str, str] = field(default_factory=dict)
    reset_polarities: Mapping[str, str] = field(default_factory=dict)
    iface_layouts: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    relaxed_iface_signals: Mapping[str, list[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, pol in self.reset_polarities.items():
            if pol not in {"active_high", "active_low"}:
                raise ValueError(f"reset_polarities[{name!r}] must be 'active_high' or 'active_low', got {pol!r}")
        for prefix, layout in self.iface_layouts.items():
            if not isinstance(layout, Mapping):
                raise TypeError(f"iface_layouts[{prefix!r}] must be a mapping, got {type(layout).__name__}")
            unknown = set(layout) - {"elements_per_beat", "element_size_bits", "endian"}
            if unknown:
                raise ValueError(
                    f"iface_layouts[{prefix!r}] has unknown keys {sorted(unknown)}; "
                    "allowed: elements_per_beat, element_size_bits, endian"
                )
            for key in ("elements_per_beat", "element_size_bits"):
                if key in layout:
                    val = layout[key]
                    if not isinstance(val, int) or val <= 0:
                        raise ValueError(f"iface_layouts[{prefix!r}][{key!r}] must be a positive int, got {val!r}")
            if "endian" in layout and layout["endian"] not in {"little", "big"}:
                raise ValueError(
                    f"iface_layouts[{prefix!r}]['endian'] must be 'little' or 'big', got {layout['endian']!r}"
                )

    @classmethod
    def coerce(cls, value: "PlannerOverrides | Mapping | None") -> "PlannerOverrides":
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if isinstance(value, Mapping):
            return cls(
                iface_domains=dict(value.get("iface_domains", {})),
                clock_periods=dict(value.get("clock_periods", {})),
                domain_aliases=dict(value.get("domain_aliases", {})),
                reset_polarities=dict(value.get("reset_polarities", {})),
                iface_layouts={k: dict(v) for k, v in value.get("iface_layouts", {}).items()},
                relaxed_iface_signals={k: list(v) for k, v in value.get("relaxed_iface_signals", {}).items()},
            )
        raise TypeError(f"unsupported overrides type: {type(value).__name__}")


# ---------------------------------------------------------------------------
# Naming heuristics
# ---------------------------------------------------------------------------


# Conventional AXI/AMBA pairs: prefix on the bus -> (clock_substr, reset_substr)
# These are matched as substrings in the bus prefix; e.g. a bus "s_axi" links
# to clocks containing "aclk" and resets containing "aresetn"/"areset".
_BUS_CLOCK_HINTS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    # AXI / AXI-Lite / AXI-Stream
    ("axi", ("aclk",), ("aresetn", "areset", "arst_n", "arst")),
    # APB
    ("apb", ("pclk",), ("presetn", "preset")),
    # AHB
    ("ahb", ("hclk",), ("hresetn", "hreset")),
)


def _bus_clock_candidates(prefix: str, clock_names: list[str]) -> list[str]:
    """Return clocks whose names match a known AXI/APB/AHB convention for the bus."""
    matches: list[str] = []
    p = prefix.lower()
    for bus_token, clk_tokens, _ in _BUS_CLOCK_HINTS:
        if bus_token in p:
            for clk in clock_names:
                lc = clk.lower()
                if any(tok in lc for tok in clk_tokens):
                    matches.append(clk)
    return matches


def _prefix_clock_candidates(prefix: str, clock_names: list[str]) -> list[str]:
    """Return clocks that share a name stem with the bus prefix.

    Examples:
        prefix="rx" matches clock "rx_clk".
        prefix="m_axis_video" matches clock "video_clk".
    """
    matches: list[str] = []
    p_parts = {tok for tok in prefix.lower().split("_") if tok}
    for clk in clock_names:
        c_parts = {tok for tok in clk.lower().split("_") if tok and tok not in {"clk", "clock"}}
        if c_parts & p_parts:
            matches.append(clk)
    return matches


# ---------------------------------------------------------------------------
# Structural clock association
# ---------------------------------------------------------------------------


def _signal_names_in_block(block: AlwaysBlock) -> set[str]:
    return {ident.name for ident in block.find(Identifier)}


def _structural_clock_candidates(
    iface: DetectedInterface,
    clocks: list[ClockSignal],
) -> list[str]:
    """Return clock names whose always blocks reference any signal in the bundle.

    The DUT-side port name (e.g. ``m_axis_tvalid``) is what we look for. If
    the bundle's signals are read or written in always blocks driven by
    clock C, then C is structurally implicated as the bundle's domain.
    """
    bundle_ports = {port.name for port in iface.signals.values()}
    matches: list[str] = []
    for clk in clocks:
        for block in clk.always_blocks:
            if bundle_ports & _signal_names_in_block(block):
                matches.append(clk.name)
                break
    return matches


# ---------------------------------------------------------------------------
# Port-name fallback for modules with no sequential always blocks
# ---------------------------------------------------------------------------

# Canonical clock port names. Matching is case-insensitive. Edge defaults
# to "posedge" — Verilog modules that clock a negedge are always_block-
# driven and so won't reach this path.
_CLOCK_PORT_NAMES: tuple[str, ...] = (
    "clk_i",
    "aclk",
    "pclk",
    "hclk",
    "clk",
    "clock",
)

# Canonical reset port names: ``(name, active_low, style)``.
_RESET_PORT_NAMES: tuple[tuple[str, bool, str], ...] = (
    ("rst_ni", True, "async"),
    ("aresetn", True, "async"),
    ("presetn", True, "async"),
    ("hresetn", True, "async"),
    ("rst_n", True, "async"),
    ("resetn", True, "async"),
    ("rstn", True, "async"),
    ("rst", False, "async"),
    ("reset", False, "async"),
)


def _naming_fallback_clocks_resets(module: Module) -> ClockResetInfo:
    """Synthesize ``ClockResetInfo`` from canonical clock/reset port names.

    Last-resort fallback when neither :func:`extract_clocks_resets` nor
    the hierarchical structural extractor surface anything (e.g. the
    module is a stub, blackbox, or its instances couldn't be linked).
    Only canonical names (``clk``, ``clk_i``, ``rst_n``, ...) are
    recognized — for prefixed names like ``src_clk_i``, prefer
    :func:`extract_clocks_resets_hier` which discovers them via
    instance port maps.
    """
    input_ports = {p.name for p in module.ports if p.direction == PortDirection.INPUT}
    lower_to_actual = {name.lower(): name for name in input_ports}

    clocks: list[ClockSignal] = []
    seen: set[str] = set()
    for canonical in _CLOCK_PORT_NAMES:
        actual = lower_to_actual.get(canonical)
        if actual is not None and actual not in seen:
            clocks.append(ClockSignal(name=actual, edge="posedge"))
            seen.add(actual)

    resets: list[ResetSignal] = []
    seen_r: set[str] = set()
    sole_clock = clocks[0].name if len(clocks) == 1 else None
    for canonical, active_low, style in _RESET_PORT_NAMES:
        actual = lower_to_actual.get(canonical)
        if actual is not None and actual not in seen_r:
            resets.append(
                ResetSignal(
                    name=actual,
                    style=style,
                    active_low=active_low,
                    edge="negedge" if active_low else "posedge",
                    clock=sole_clock,
                )
            )
            seen_r.add(actual)

    return ClockResetInfo(clocks=clocks, resets=resets)


# ---------------------------------------------------------------------------
# Domain construction
# ---------------------------------------------------------------------------


def _build_domains(
    info: ClockResetInfo,
    overrides: PlannerOverrides,
) -> tuple[tuple[ClockDomain, ...], dict[str, str], list[str]]:
    """Build :class:`ClockDomain` objects from clock/reset analysis.

    Returns ``(domains, clock_to_domain_name, applied_overrides)``.
    """
    applied: list[str] = []
    clock_to_domain: dict[str, str] = {}

    # Group resets by their associated clock (from structural analysis).
    resets_by_clock: dict[str, ResetSignal] = {}
    unassigned_resets: list[ResetSignal] = []
    for r in info.resets:
        if r.clock is not None and r.clock not in resets_by_clock:
            resets_by_clock[r.clock] = r
        elif r.clock is None:
            unassigned_resets.append(r)

    # Sole-clock fallback for a reset that did not get a clock association.
    if len(info.clocks) == 1 and unassigned_resets and info.clocks[0].name not in resets_by_clock:
        resets_by_clock[info.clocks[0].name] = unassigned_resets[0]

    domains: list[ClockDomain] = []
    for clk in info.clocks:
        clk_name = clk.name
        domain_name = overrides.domain_aliases.get(clk_name, clk_name)
        if domain_name != clk_name:
            applied.append(f"domain_aliases[{clk_name!r}]={domain_name!r}")

        period = overrides.clock_periods.get(clk_name)
        if period is not None:
            applied.append(f"clock_periods[{clk_name!r}]={period}")

        clock_spec = ClockSpec(name=clk_name, edge=clk.edge, period_hint=period)

        rst_signal = resets_by_clock.get(clk_name)
        reset_spec: ResetSpec | None = None
        if rst_signal is not None:
            active_low = rst_signal.active_low
            pol_override = overrides.reset_polarities.get(rst_signal.name)
            if pol_override is not None:
                forced_low = pol_override == "active_low"
                if forced_low != active_low:
                    applied.append(f"reset_polarities[{rst_signal.name!r}]={pol_override!r}")
                active_low = forced_low
            reset_spec = ResetSpec(
                name=rst_signal.name,
                active_low=active_low,
                style=rst_signal.style,
                edge=rst_signal.edge if rst_signal.style == "async" else None,
            )

        domains.append(ClockDomain(name=domain_name, clock=clock_spec, reset=reset_spec))
        clock_to_domain[clk_name] = domain_name

    return tuple(domains), clock_to_domain, applied


# ---------------------------------------------------------------------------
# Interface binding
# ---------------------------------------------------------------------------


def _bind_interfaces(  # noqa: PLR0912
    interfaces: list[DetectedInterface],
    info: ClockResetInfo,
    clock_to_domain: dict[str, str],
    overrides: PlannerOverrides,
    *,
    strict: bool,
) -> tuple[tuple[InterfaceBinding, ...], list[str], list[str]]:
    """Bind each detected interface to a domain.

    Returns ``(bindings, warnings, applied_overrides)``.
    """
    warnings: list[str] = []
    applied: list[str] = []
    bindings: list[InterfaceBinding] = []

    clock_names = [clk.name for clk in info.clocks]
    domain_names = list(clock_to_domain.values())

    for iface in interfaces:
        # Build suffix -> port-name mapping for the binding payload.
        signals = {suffix: port.name for suffix, port in iface.signals.items()}

        # 1) Explicit user override always wins.
        override_dom = overrides.iface_domains.get(iface.prefix)
        if override_dom is not None:
            if override_dom not in domain_names:
                raise PlanValidationError(
                    f"override iface_domains[{iface.prefix!r}]={override_dom!r} "
                    f"refers to unknown domain (known: {sorted(domain_names)})"
                )
            applied.append(f"iface_domains[{iface.prefix!r}]={override_dom!r}")
            bindings.append(
                InterfaceBinding(
                    prefix=iface.prefix,
                    protocol=iface.protocol,
                    role=iface.role,
                    domain_name=override_dom,
                    signals=signals,
                    confidence="override",
                )
            )
            continue

        # 2) Structural: which clocks' always blocks reference these ports?
        structural = _structural_clock_candidates(iface, info.clocks)
        if len(structural) == 1:
            bindings.append(_binding(iface, signals, clock_to_domain[structural[0]], "structural"))
            continue
        # If structural narrowed but did not converge, hand the candidate
        # set to later stages so we never silently pick a clock that has
        # no structural justification.
        candidates = list(structural)

        # 3) Naming convention (AXI/APB/AHB and shared stems).
        if not candidates:
            naming = _bus_clock_candidates(iface.prefix, clock_names)
            if not naming:
                naming = _prefix_clock_candidates(iface.prefix, clock_names)
            naming = sorted(set(naming))
            if len(naming) == 1:
                bindings.append(_binding(iface, signals, clock_to_domain[naming[0]], "naming"))
                continue
            candidates = naming

        # 4) Sole-domain fallback.
        if not candidates and len(clock_names) == 1:
            sole = clock_names[0]
            bindings.append(_binding(iface, signals, clock_to_domain[sole], "sole-domain"))
            continue

        # 5) Multiple clocks but no narrowing => every clock is a candidate.
        if not candidates and len(clock_names) > 1:
            candidates = list(clock_names)

        # 6) Ambiguous or empty.
        if not candidates:
            msg = (
                f"interface {iface.prefix!r}: no clock domain could be inferred "
                f"(known clocks: {clock_names or '<none>'})"
            )
            if strict:
                raise NoDomainError(msg)
            warnings.append(msg)
            if not clock_names:
                # Truly nothing to pick; skip this interface.
                continue
            chosen = clock_names[0]
        else:
            msg = f"interface {iface.prefix!r}: ambiguous clock domain (candidates: {sorted(set(candidates))})"
            if strict:
                raise AmbiguousDomainError(msg)
            warnings.append(msg)
            chosen = sorted(set(candidates))[0]

        bindings.append(_binding(iface, signals, clock_to_domain[chosen], "naming"))

    return tuple(bindings), warnings, applied


def _resets_are_non_canonical(resets: "list[ResetSignal]") -> bool:
    """Return True if all detected resets have non-canonical names.

    Sync resets found only via the first-if-condition heuristic can be
    false positives when the first ``if`` guards functional logic rather
    than reset logic (e.g. ``if (s_mb_wen) mem[...] <= ...``).  When
    every discovered reset is synchronous (not in the sensitivity list)
    and has a non-canonical name, it is almost certainly a false positive
    — fall back to canonical port-name detection.
    """
    canonical_names = frozenset(name for name, _, _ in _RESET_PORT_NAMES)
    return bool(resets) and all(r.style == "sync" and r.name.lower() not in canonical_names for r in resets)


def _binding(
    iface: DetectedInterface,
    signals: dict[str, str],
    domain_name: str,
    confidence: str,
) -> InterfaceBinding:
    return InterfaceBinding(
        prefix=iface.prefix,
        protocol=iface.protocol,
        role=iface.role,
        domain_name=domain_name,
        signals=signals,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_plan(  # cm:4b3d2a
    module: Module,
    *,
    overrides: PlannerOverrides | Mapping | None = None,
    strict: bool = True,
    design: Design | None = None,
) -> TestbenchPlan:
    """Infer a :class:`TestbenchPlan` for ``module``.

    Args:
        module: Parsed model module to plan a testbench for.
        overrides: Optional user-supplied corrections. Either a
            :class:`PlannerOverrides` instance or a mapping with keys
            ``iface_domains``, ``clock_periods``, ``domain_aliases``.
        strict: When ``True`` (default), an interface that cannot be
            unambiguously bound to a single domain raises
            :class:`AmbiguousDomainError` (or :class:`NoDomainError` when
            no candidate domain exists at all). When ``False``, a
            warning is recorded on the plan and the lowest-named
            candidate is picked deterministically.
        design: Optional owning :class:`Design`. When supplied, the
            planner uses :func:`extract_clocks_resets_hier` to discover
            clocks/resets that flow into the module via instance port
            maps (i.e., the module is a thin wrapper instantiating
            sub-cells whose ``always_ff`` blocks define the actual
            sequential logic). This is preferred over name-based
            fallbacks.

    Returns:
        Immutable :class:`TestbenchPlan`. The plan is fully self-
        consistent (no orphan domain references); callers can rely on
        ``plan.domain(...)`` and ``plan.interface(...)`` lookups.
    """
    overrides = PlannerOverrides.coerce(overrides)

    info = extract_clocks_resets(module)
    if not info.clocks and design is not None:
        # Structural promotion: trace clocks/resets through instance
        # port maps to whichever top-level signals (typically input
        # ports) drive submodule clock/reset ports.
        info = extract_clocks_resets_hier(module, design)
    if not info.clocks:
        # Last-resort: canonical clock/reset port-name detection. Used
        # when neither the module's own ``always`` blocks nor structural
        # elaboration (e.g. unresolved instances, blackbox cells)
        # surface any clocks.
        info = _naming_fallback_clocks_resets(module)
    elif not info.resets or _resets_are_non_canonical(info.resets):
        # Clocks were found structurally but no resets (or all detected resets
        # are non-canonical sync signals that are likely false positives from the
        # first-if-condition heuristic, e.g. functional enables like s_mb_wen).
        # Fall back to canonical port-name detection for resets only.
        naming_info = _naming_fallback_clocks_resets(module)
        if naming_info.resets:
            info = ClockResetInfo(clocks=info.clocks, resets=naming_info.resets)
    interfaces = detect_interfaces(module)
    near_misses = detect_near_misses(module)

    # Relaxed detection: promote near-misses to full interfaces when the user
    # has opted into relaxing specific required signals (e.g. tlast-less AXIS).
    relaxed_signals = dict(overrides.relaxed_iface_signals)
    relaxed_prefixes: set[str] = set()
    if relaxed_signals:
        relaxed = detect_relaxed_interfaces(module, relaxed_signals=relaxed_signals)
        interfaces.extend(relaxed)
        relaxed_prefixes = {ri.prefix for ri in relaxed}

    domains, clock_to_domain, applied_clk = _build_domains(info, overrides)

    # Combinational / clockless DUT: when no clock domains exist, synthesize
    # a degenerate combinational domain so the Testbench can step via
    # CombinationalCoordinator (and interfaces can bind if any are detected).
    if not domains:
        from .plan import ClockSpec

        comb_name = "__combinational__"
        comb_spec = ClockSpec(name=comb_name, period_hint=1)
        comb_domain = ClockDomain(name=comb_name, clock=comb_spec)
        domains = (comb_domain,)
        clock_to_domain = {comb_name: comb_name}
        # Inject the sentinel into info so _bind_interfaces can find the domain.
        info = ClockResetInfo(clocks=[ClockSignal(name=comb_name, edge="posedge")], resets=info.resets)

    bindings, warnings, applied_iface = _bind_interfaces(interfaces, info, clock_to_domain, overrides, strict=strict)

    if domains and domains[0].clock.name == "__combinational__":
        warnings.append(
            "No clock domains detected — using combinational domain '__combinational__'. "
            "Use bench.step() to drive→settle→sample."
        )

    for ri_prefix in relaxed_prefixes:
        warnings.append(f"relaxed detection at prefix '{ri_prefix}': allowed missing signals: {relaxed_signals}")

    # Append near-miss explanations so callers (CLI, testbench generator) can
    # surface them without re-running detection.
    for nm in near_misses:
        warnings.append(f"near-miss: {nm.explain()}")

    return TestbenchPlan(
        top=module.name,
        domains=domains,
        interfaces=bindings,
        warnings=tuple(warnings),
        overrides_applied=tuple(applied_clk + applied_iface),
    )
