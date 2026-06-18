"""Clock and reset signal extraction from Verilog always blocks.

Detects clock and reset signals by structural analysis of always blocks:
- **Clocks**: Edge-triggered signals in sensitivity lists that drive sequential logic.
- **Async resets**: Edge-triggered signals in sensitivity lists whose condition guards
  the reset path (first ``if`` in the always body).
- **Sync resets**: Condition-only resets inside a single-edge sequential always block
  (the reset signal does NOT appear in the sensitivity list).

Usage::

    from veriforge.analysis import extract_clocks_resets

    info = extract_clocks_resets(module)
    for clk in info.clocks:
        print(f"Clock: {clk.name} ({clk.edge})")
    for rst in info.resets:
        print(f"Reset: {rst.name} (active_low={rst.active_low}, style={rst.style})")
"""

from __future__ import annotations

from dataclasses import dataclass, field

from veriforge.model.behavioral import AlwaysBlock, SensitivityType
from veriforge.model.design import Design, Module
from veriforge.model.expressions import BinaryOp, Identifier, UnaryOp
from veriforge.model.statements import IfStatement, SeqBlock


# ---------------------------------------------------------------------------
# Public data classes
# ---------------------------------------------------------------------------


@dataclass
class ClockSignal:  # cm:1a5b6e
    """A detected clock signal."""

    name: str
    """Port or net name of the clock."""

    edge: str
    """Edge type: ``"posedge"`` or ``"negedge"``."""

    always_blocks: list[AlwaysBlock] = field(default_factory=list, repr=False)
    """Always blocks driven by this clock."""


@dataclass
class ResetSignal:  # cm:d8c7a3
    """A detected reset signal."""

    name: str
    """Port or net name of the reset."""

    style: str
    """``"async"`` (in sensitivity list) or ``"sync"`` (condition-only)."""

    active_low: bool
    """True when the reset is active-low (e.g. ``!rst_n``)."""

    edge: str | None = None
    """Sensitivity edge for async resets (``"posedge"``/``"negedge"``), None for sync."""

    clock: str | None = None
    """Name of the associated clock signal, if determinable."""

    always_blocks: list[AlwaysBlock] = field(default_factory=list, repr=False)
    """Always blocks using this reset."""


@dataclass
class ClockResetInfo:
    """Aggregated clock/reset analysis results for a module."""

    clocks: list[ClockSignal] = field(default_factory=list)
    resets: list[ResetSignal] = field(default_factory=list)

    # Convenience helpers ------------------------------------------------

    def clock_names(self) -> list[str]:
        """Return sorted unique clock signal names."""
        return sorted({c.name for c in self.clocks})

    def reset_names(self) -> list[str]:
        """Return sorted unique reset signal names."""
        return sorted({r.name for r in self.resets})

    def domain_map(self) -> dict[str, list[AlwaysBlock]]:
        """Map each clock name to the always blocks it drives."""
        result: dict[str, list[AlwaysBlock]] = {}
        for clk in self.clocks:
            result.setdefault(clk.name, []).extend(clk.always_blocks)
        return result


# ---------------------------------------------------------------------------
# Extraction helpers (private)
# ---------------------------------------------------------------------------


def _get_first_if(block: AlwaysBlock) -> IfStatement | None:
    """Return the first ``IfStatement`` in the always body, unwrapping SeqBlock."""
    body = block.body
    if isinstance(body, SeqBlock) and body.statements:
        body = body.statements[0]
    if isinstance(body, IfStatement):
        return body
    return None


def _extract_condition_signal(expr) -> str | None:
    """Extract the signal name from a reset condition expression.

    Handles:
    - ``rst``               → "rst"
    - ``!rst`` / ``~rst``   → "rst"
    - ``rst == 0``          → "rst"
    - ``rst == 1'b0``       → "rst"
    """
    if isinstance(expr, Identifier):
        return expr.name
    if isinstance(expr, UnaryOp) and expr.op in ("!", "~"):
        if isinstance(expr.operand, Identifier):
            return expr.operand.name
    if isinstance(expr, BinaryOp) and expr.op in ("==", "!="):
        if isinstance(expr.left, Identifier):
            return expr.left.name
        if isinstance(expr.right, Identifier):
            return expr.right.name
    return None


def _is_active_low_condition(expr) -> bool:
    """Return True if the condition represents an active-low check.

    Active-low patterns:
    - ``!rst_n``
    - ``~rst_n``
    - ``rst_n == 0``
    - ``rst_n == 1'b0``

    Active-high patterns:
    - ``rst``  (bare identifier)
    - ``rst == 1``
    - ``rst != 0``
    """
    if isinstance(expr, Identifier):
        return False  # bare identifier → active high
    if isinstance(expr, UnaryOp) and expr.op in ("!", "~"):
        return True
    if isinstance(expr, BinaryOp):
        if expr.op == "==" and _is_zero_literal(expr.right):
            return True
        if expr.op == "!=" and not _is_zero_literal(expr.right):
            return True
    return False


def _is_zero_literal(expr) -> bool:
    """Check if an expression is a zero literal (0, 1'b0, etc.)."""
    from veriforge.model.expressions import Literal

    if isinstance(expr, Literal):
        return expr.value == 0
    return False


def _analyze_sequential_block(block: AlwaysBlock, clocks: dict, resets: dict) -> None:
    """Analyze a sequential always block for clock and reset signals."""
    edges = block.sensitivity_list
    if not edges:
        return

    first_if = _get_first_if(block)
    reset_signal_name: str | None = None

    if first_if is not None:
        reset_signal_name = _extract_condition_signal(first_if.condition)

    if len(edges) == 1:
        # Single edge → clock only, possibly with sync reset
        edge = edges[0]
        sig_name = edge.signal.name if isinstance(edge.signal, Identifier) else None
        if sig_name:
            _record_clock(clocks, sig_name, edge.edge, block)
            # Check for sync reset
            if reset_signal_name and reset_signal_name != sig_name and first_if is not None:
                active_low = _is_active_low_condition(first_if.condition)
                _record_reset(
                    resets,
                    reset_signal_name,
                    style="sync",
                    active_low=active_low,
                    edge=None,
                    clock=sig_name,
                    block=block,
                )

    elif len(edges) >= 2:
        # Multiple edges → one is clock, rest are async resets
        # Determine which edge-triggered signal is the reset vs. clock
        # by matching the if-condition signal name to a sensitivity edge
        edge_names = {}
        for se in edges:
            if isinstance(se.signal, Identifier):
                edge_names[se.signal.name] = se

        if reset_signal_name and reset_signal_name in edge_names and first_if is not None:
            # The if-condition signal is the async reset
            reset_edge = edge_names[reset_signal_name]
            active_low = _is_active_low_condition(first_if.condition)
            # Remaining edges are clocks
            clock_name: str | None = None
            for se in edges:
                se_name = se.signal.name if isinstance(se.signal, Identifier) else None
                if se_name and se_name != reset_signal_name:
                    _record_clock(clocks, se_name, se.edge, block)
                    if clock_name is None:
                        clock_name = se_name
            _record_reset(
                resets,
                reset_signal_name,
                style="async",
                active_low=active_low,
                edge=reset_edge.edge,
                clock=clock_name,
                block=block,
            )
        else:
            # Cannot determine reset from condition — treat first edge as clock
            first_edge = edges[0]
            first_name = first_edge.signal.name if isinstance(first_edge.signal, Identifier) else None
            if first_name:
                _record_clock(clocks, first_name, first_edge.edge, block)
            # Remaining edges recorded as async resets without polarity info
            for se in edges[1:]:
                se_name = se.signal.name if isinstance(se.signal, Identifier) else None
                if se_name:
                    _record_reset(
                        resets,
                        se_name,
                        style="async",
                        active_low=False,
                        edge=se.edge,
                        clock=first_name,
                        block=block,
                    )


def _record_clock(clocks: dict, name: str, edge: str, block: AlwaysBlock) -> None:
    """Record a clock signal, merging into existing entry if present."""
    if name not in clocks:
        clocks[name] = ClockSignal(name=name, edge=edge)
    clocks[name].always_blocks.append(block)


def _record_reset(
    resets: dict,
    name: str,
    *,
    style: str,
    active_low: bool,
    edge: str | None,
    clock: str | None,
    block: AlwaysBlock,
) -> None:
    """Record a reset signal, merging into existing entry if present."""
    if name not in resets:
        resets[name] = ResetSignal(name=name, style=style, active_low=active_low, edge=edge, clock=clock)
    resets[name].always_blocks.append(block)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_clocks_resets(module: Module) -> ClockResetInfo:  # cm:f2b8e7
    """Extract clock and reset signals from a module's always blocks.

    Analyzes the sensitivity lists and body structure of sequential always
    blocks to identify clock and reset signals without relying on naming
    conventions.

    Parameters
    ----------
    module : Module
        The module to analyze.

    Returns
    -------
    ClockResetInfo
        Detected clocks and resets with edge/polarity metadata.
    """
    clocks: dict[str, ClockSignal] = {}
    resets: dict[str, ResetSignal] = {}

    for block in module.always_blocks:
        if block.sensitivity_type != SensitivityType.SEQUENTIAL:
            continue
        _analyze_sequential_block(block, clocks, resets)

    return ClockResetInfo(
        clocks=sorted(clocks.values(), key=lambda c: c.name),
        resets=sorted(resets.values(), key=lambda r: r.name),
    )


def extract_clocks_resets_from_design(design: Design) -> dict[str, ClockResetInfo]:
    """Extract clock/reset info for every module in a design.

    Parameters
    ----------
    design : Design
        The full design.

    Returns
    -------
    dict[str, ClockResetInfo]
        Mapping of module name → ClockResetInfo.
    """
    return {mod.name: extract_clocks_resets(mod) for mod in design.modules}


def extract_clocks_resets_hier(  # noqa: PLR0912  # cm:3c9d4f
    module: Module,
    design: Design | None,
    *,
    _cache: dict[str, ClockResetInfo] | None = None,
) -> ClockResetInfo:
    """Extract clocks/resets including those used inside instantiated submodules.

    For modules whose own ``always`` blocks don't reference any
    sequential sensitivity (e.g. a top-level testbench that just wraps a
    DUT instance), :func:`extract_clocks_resets` returns nothing. This
    hierarchical variant additionally walks ``module.instances`` and
    promotes any submodule clock/reset *port* up to the parent's signal
    that drives it via the instance port map.

    The returned :class:`ClockResetInfo` lists only signals that are (a)
    detected locally via ``always`` blocks, or (b) connected to a
    submodule's clock/reset port and resolve to a simple identifier on
    the parent — typically the parent's own input port. Reset metadata
    (style/polarity) is inherited from the submodule's reset; the
    paired clock is the parent's signal connected to the same
    submodule's clock-of-reset.

    Parameters
    ----------
    module : Module
        Module to analyze. Must have ``instances`` populated; for the
        promotion to resolve targets, the design must have been linked
        (``link_instances``/``resolve_port_connections``).
    design : Design | None
        Owning design, used for ``inst.resolved_module`` lookups when
        instance modules need to be recursively analyzed. May be
        ``None``, in which case behaviour matches
        :func:`extract_clocks_resets`.
    """
    # Local always-block analysis always wins; if it produced anything we
    # trust those signals and just return.
    info = extract_clocks_resets(module)
    if info.clocks or design is None:
        return info

    if _cache is None:
        _cache = {}
    cache_key = module.name
    if cache_key in _cache:
        return _cache[cache_key]
    # Sentinel to break recursion on circular instantiation (illegal but
    # cheap to guard against).
    _cache[cache_key] = ClockResetInfo(clocks=[], resets=[])

    promoted_clocks: dict[str, ClockSignal] = {}
    promoted_resets: dict[str, ResetSignal] = {}

    for inst in module.instances:
        target = getattr(inst, "resolved_module", None)
        if target is None:
            continue
        sub_info = extract_clocks_resets_hier(target, design, _cache=_cache)
        if not sub_info.clocks and not sub_info.resets:
            continue
        sub_clk_ports = {c.name for c in sub_info.clocks}
        sub_rst_by_port = {r.name: r for r in sub_info.resets}

        # Map sub-port name -> parent simple identifier (only simple
        # identifier connections are usable as clock/reset promotion).
        port_to_signal: dict[str, str] = {}
        for conn in inst.port_connections:
            if conn.expression is None:
                continue
            sn = _simple_identifier_name(conn.expression)
            if sn is None:
                continue
            # Prefer resolved_port (covers positional connections); fall
            # back to the textual port_name from the source instantiation.
            port_obj = getattr(conn, "resolved_port", None)
            sub_port_name = port_obj.name if port_obj is not None else getattr(conn, "port_name", None)
            if sub_port_name is None:
                continue
            port_to_signal[sub_port_name] = sn

        for sub_clk_port in sub_clk_ports:
            sn = port_to_signal.get(sub_clk_port)
            if sn is not None and sn not in promoted_clocks:
                promoted_clocks[sn] = ClockSignal(name=sn, edge="posedge")

        for sub_rst_port, sub_rst in sub_rst_by_port.items():
            sn = port_to_signal.get(sub_rst_port)
            if sn is None or sn in promoted_resets:
                continue
            paired_sub_clk = sub_rst.clock
            paired_clock = port_to_signal.get(paired_sub_clk) if paired_sub_clk else None
            promoted_resets[sn] = ResetSignal(
                name=sn,
                style=sub_rst.style,
                active_low=sub_rst.active_low,
                edge=sub_rst.edge,
                clock=paired_clock,
            )

    result = ClockResetInfo(
        clocks=sorted(promoted_clocks.values(), key=lambda c: c.name),
        resets=sorted(promoted_resets.values(), key=lambda r: r.name),
    )
    _cache[cache_key] = result
    return result


def _simple_identifier_name(expr) -> str | None:
    """Return the bare identifier name for ``expr`` (no hierarchy/index)."""
    if isinstance(expr, Identifier) and not getattr(expr, "hierarchy", None):
        return expr.name
    return None
