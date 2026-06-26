"""Lint-style checks for Verilog designs.

Detects common coding issues by analysing the semantic model after
``analyze_design()`` has populated cross-references and driver/load info.

Checks implemented
------------------
- **UNDRIVEN**: Net or variable with no drivers (floating signal).
- **UNUSED**: Net or variable with no loads (dead signal).
- **MULTI_DRIVEN**: Net or variable driven from multiple sources.
- **LATCH_INFERRED**: Combinational always block with incomplete ``if``
  or ``case`` (not all paths assign every output).
- **WIDTH_MISMATCH**: Port-connection or continuous-assign width differs
  between LHS and RHS.
- **MIXED_BLOCKING**: Sequential always block containing blocking
  assignments (``=`` instead of ``<=``).
- **MIXED_NONBLOCKING**: Combinational always block containing
  non-blocking assignments (``<=`` instead of ``=``).
- **UNCONNECTED_PORT**: Instance port connection left open (empty
  expression).

Usage::

    from veriforge.analysis import analyze_design, infer_widths, lint_module, lint_design

    analyze_design(design)
    infer_widths(design)          # needed for WIDTH_MISMATCH

    warnings = lint_module(module)
    for w in warnings:
        print(f"[{w.code}] {w.message}  (signal={w.signal})")

    # Or lint the whole design
    all_warnings = lint_design(design)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto

from veriforge.analysis.const_fold import const_range_width
from veriforge.model.behavioral import AlwaysBlock, SensitivityType
from veriforge.model.design import Design, Module
from veriforge.model.expressions import Identifier
from veriforge.model.ports import Port, PortDirection
from veriforge.model.statements import (
    BlockingAssign,
    CaseStatement,
    ForLoop,
    ForeverLoop,
    IfStatement,
    NonblockingAssign,
    RepeatLoop,
    SeqBlock,
    WhileLoop,
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class LintCode(Enum):
    """Lint warning codes."""

    UNDRIVEN = auto()
    UNUSED = auto()
    MULTI_DRIVEN = auto()
    LATCH_INFERRED = auto()
    WIDTH_MISMATCH = auto()
    MIXED_BLOCKING = auto()
    MIXED_NONBLOCKING = auto()
    UNCONNECTED_PORT = auto()
    INPUT_INIT = auto()


@dataclass
class LintWarning:
    """A single lint diagnostic."""

    code: LintCode
    """Warning category."""

    message: str
    """Human-readable description."""

    module: str
    """Name of the module where the issue was found."""

    signal: str | None = None
    """Signal or port name involved (if applicable)."""

    instance: str | None = None
    """Instance name (for UNCONNECTED_PORT)."""


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _walk_statements(stmt, visitor):
    """Recursively walk a statement tree, calling *visitor* on each node."""
    if stmt is None:
        return
    visitor(stmt)
    if isinstance(stmt, SeqBlock):
        for s in stmt.statements:
            _walk_statements(s, visitor)
    elif isinstance(stmt, IfStatement):
        _walk_statements(stmt.then_body, visitor)
        _walk_statements(stmt.else_body, visitor)
    elif isinstance(stmt, CaseStatement):
        for item in stmt.items:
            _walk_statements(item.body, visitor)
    elif isinstance(stmt, ForLoop):
        _walk_statements(stmt.body, visitor)
    elif isinstance(stmt, (WhileLoop, RepeatLoop, ForeverLoop)):
        _walk_statements(stmt.body, visitor)


def _has_blocking(block: AlwaysBlock) -> bool:
    """Return True if the always block body contains any blocking assignment."""
    found = [False]

    def _check(stmt):
        if isinstance(stmt, BlockingAssign):
            found[0] = True

    _walk_statements(block.body, _check)
    return found[0]


def _has_nonblocking(block: AlwaysBlock) -> bool:
    """Return True if the always block body contains any non-blocking assignment."""
    found = [False]

    def _check(stmt):
        if isinstance(stmt, NonblockingAssign):
            found[0] = True

    _walk_statements(block.body, _check)
    return found[0]


def _collect_assigned_targets(stmt) -> set[str]:
    """Collect all signal names assigned (LHS) under a statement subtree."""
    targets: set[str] = set()

    def _visitor(s):
        if isinstance(s, (BlockingAssign, NonblockingAssign)):
            lhs = s.lhs
            if isinstance(lhs, Identifier):
                targets.add(lhs.name)

    _walk_statements(stmt, _visitor)
    return targets


def _check_latch_inferred(block: AlwaysBlock, mod_name: str) -> list[LintWarning]:
    """Detect latch inference in combinational always blocks.

    A combinational always block infers a latch when an ``if`` has no
    ``else``, or a ``case`` doesn't cover all values (no ``default``).
    We check that every branch assigns the same set of targets.
    """
    warnings: list[LintWarning] = []

    def _check(stmt):
        if isinstance(stmt, IfStatement):
            if stmt.else_body is None:
                # No else → some signals may not be assigned on all paths
                then_targets = _collect_assigned_targets(stmt.then_body)
                for sig in sorted(then_targets):
                    warnings.append(
                        LintWarning(
                            code=LintCode.LATCH_INFERRED,
                            message=f"Incomplete if (no else) — '{sig}' may infer a latch",
                            module=mod_name,
                            signal=sig,
                        )
                    )
            else:
                # Has else — check that both branches assign the same set
                then_targets = _collect_assigned_targets(stmt.then_body)
                else_targets = _collect_assigned_targets(stmt.else_body)
                only_then = then_targets - else_targets
                only_else = else_targets - then_targets
                for sig in sorted(only_then | only_else):
                    warnings.append(
                        LintWarning(
                            code=LintCode.LATCH_INFERRED,
                            message=f"Not all paths assign '{sig}' — may infer a latch",
                            module=mod_name,
                            signal=sig,
                        )
                    )
        elif isinstance(stmt, CaseStatement):
            has_default = any(item.is_default for item in stmt.items)
            if not has_default:
                all_targets: set[str] = set()
                for item in stmt.items:
                    all_targets |= _collect_assigned_targets(item.body)
                for sig in sorted(all_targets):
                    warnings.append(
                        LintWarning(
                            code=LintCode.LATCH_INFERRED,
                            message=f"Case without default — '{sig}' may infer a latch",
                            module=mod_name,
                            signal=sig,
                        )
                    )

    _walk_statements(block.body, _check)
    return warnings


def _check_assignment_style(block: AlwaysBlock, mod_name: str) -> list[LintWarning]:
    """Check for blocking in sequential / non-blocking in combinational."""
    warnings: list[LintWarning] = []

    if block.sensitivity_type == SensitivityType.SEQUENTIAL:
        if _has_blocking(block):
            warnings.append(
                LintWarning(
                    code=LintCode.MIXED_BLOCKING,
                    message="Blocking assignment (=) in sequential always block — use non-blocking (<=)",
                    module=mod_name,
                )
            )
    elif block.sensitivity_type == SensitivityType.COMBINATIONAL:
        if _has_nonblocking(block):
            warnings.append(
                LintWarning(
                    code=LintCode.MIXED_NONBLOCKING,
                    message="Non-blocking assignment (<=) in combinational always block — use blocking (=)",
                    module=mod_name,
                )
            )

    return warnings


def _port_names(module: Module) -> set[str]:
    """Return the set of port names for the module."""
    return {p.name for p in module.ports}


def _is_output_driven(port: Port, module: Module) -> bool:
    """Check whether an output port is driven by any source."""
    for net in module.nets:
        if net.name == port.name and net.drivers:
            return True
    for var in module.variables:
        if var.name == port.name and var.drivers:
            return True
    for ca in module.continuous_assigns:
        if isinstance(ca.lhs, Identifier) and ca.lhs.name == port.name:
            return True
    for blk in module.always_blocks:
        if port.name in _collect_assigned_targets(blk.body):
            return True
    return False


def _check_drivers_loads(module: Module) -> list[LintWarning]:
    """Check for undriven, unused, and multi-driven signals."""
    warnings: list[LintWarning] = []
    port_names = _port_names(module)

    # Check nets
    for net in module.nets:
        if net.name in port_names:
            continue  # Port connectivity is handled differently
        if not net.drivers:
            warnings.append(
                LintWarning(
                    code=LintCode.UNDRIVEN,
                    message=f"Net '{net.name}' has no drivers",
                    module=module.name,
                    signal=net.name,
                )
            )
        if not net.loads:
            warnings.append(
                LintWarning(
                    code=LintCode.UNUSED,
                    message=f"Net '{net.name}' is never read",
                    module=module.name,
                    signal=net.name,
                )
            )
        if len(net.drivers) > 1 and net.kind.name == "WIRE":
            warnings.append(
                LintWarning(
                    code=LintCode.MULTI_DRIVEN,
                    message=f"Net '{net.name}' has {len(net.drivers)} drivers",
                    module=module.name,
                    signal=net.name,
                )
            )

    # Check variables (regs)
    for var in module.variables:
        if var.name in port_names:
            continue
        if not var.drivers:
            warnings.append(
                LintWarning(
                    code=LintCode.UNDRIVEN,
                    message=f"Variable '{var.name}' has no drivers",
                    module=module.name,
                    signal=var.name,
                )
            )
        if not var.loads:
            warnings.append(
                LintWarning(
                    code=LintCode.UNUSED,
                    message=f"Variable '{var.name}' is never read",
                    module=module.name,
                    signal=var.name,
                )
            )
        if len(var.drivers) > 1:
            warnings.append(
                LintWarning(
                    code=LintCode.MULTI_DRIVEN,
                    message=f"Variable '{var.name}' has {len(var.drivers)} drivers",
                    module=module.name,
                    signal=var.name,
                )
            )

    # Check output ports that have no driver
    for port in module.ports:
        if port.direction == PortDirection.OUTPUT and not _is_output_driven(port, module):
            warnings.append(
                LintWarning(
                    code=LintCode.UNDRIVEN,
                    message=f"Output port '{port.name}' is not driven",
                    module=module.name,
                    signal=port.name,
                )
            )

    return warnings


def _check_width_mismatch(module: Module) -> list[LintWarning]:
    """Check for width mismatches in continuous assigns and port connections."""
    warnings: list[LintWarning] = []

    # Continuous assigns: LHS vs RHS width
    for ca in module.continuous_assigns:
        lw = getattr(ca.lhs, "inferred_width", None)
        rw = getattr(ca.rhs, "inferred_width", None)
        if lw is not None and rw is not None and lw != rw:
            lhs_name = ca.lhs.name if isinstance(ca.lhs, Identifier) else "<expr>"
            warnings.append(
                LintWarning(
                    code=LintCode.WIDTH_MISMATCH,
                    message=f"Width mismatch in assign: LHS '{lhs_name}' is {lw}-bit, RHS is {rw}-bit",
                    module=module.name,
                    signal=lhs_name,
                )
            )

    # Instance port connections
    for inst in module.instances:
        for pc in inst.port_connections:
            if pc.expression is None:
                continue
            port = getattr(pc, "resolved_port", None)
            if port is None:
                continue
            conn_width = getattr(pc.expression, "inferred_width", None)
            port_width = _port_decl_width(port)
            if conn_width is not None and port_width is not None and conn_width != port_width:
                warnings.append(
                    LintWarning(
                        code=LintCode.WIDTH_MISMATCH,
                        message=(
                            f"Port '{pc.port_name}' on instance '{inst.instance_name}' expects "
                            f"{port_width}-bit, connected to {conn_width}-bit expression"
                        ),
                        module=module.name,
                        signal=pc.port_name,
                        instance=inst.instance_name,
                    )
                )

    return warnings


def _port_decl_width(port: Port) -> int | None:
    """Get the declared width of a port (1 if scalar, msb-lsb+1 if ranged)."""
    if port.width is None:
        return 1
    return const_range_width(port.width)


def _check_unconnected_ports(module: Module) -> list[LintWarning]:
    """Check for unconnected instance ports."""
    warnings: list[LintWarning] = []

    for inst in module.instances:
        for pc in inst.port_connections:
            if pc.expression is None:
                warnings.append(
                    LintWarning(
                        code=LintCode.UNCONNECTED_PORT,
                        message=f"Port '{pc.port_name}' on instance '{inst.instance_name}' is unconnected",
                        module=module.name,
                        signal=pc.port_name,
                        instance=inst.instance_name,
                    )
                )

    return warnings


def _check_input_port_init(module: Module) -> list[LintWarning]:
    """Check for input ports with initial/default values (synthesis error)."""
    warnings: list[LintWarning] = []
    for port in module.ports:
        if port.direction == PortDirection.INPUT and port.default_value is not None:
            warnings.append(
                LintWarning(
                    code=LintCode.INPUT_INIT,
                    message=(
                        f"Input port '{port.name}' has an initial value — not synthesizable; ignored by simulator"
                    ),
                    module=module.name,
                    signal=port.name,
                )
            )
    return warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lint_module(
    module: Module,
    *,
    skip: set[LintCode] | None = None,
) -> list[LintWarning]:
    """Run all lint checks on a module.

    Parameters
    ----------
    module : Module
        The module to lint. Should have been processed by
        ``analyze_design()`` and ``infer_widths()`` first.
    skip : set[LintCode] | None
        Optional set of check codes to skip.

    Returns
    -------
    list[LintWarning]
        All warnings found, sorted by code then signal name.
    """
    skip = skip or set()
    warnings: list[LintWarning] = []

    # Signal driver/load checks
    if not (skip & {LintCode.UNDRIVEN, LintCode.UNUSED, LintCode.MULTI_DRIVEN}):
        for w in _check_drivers_loads(module):
            if w.code not in skip:
                warnings.append(w)

    # Always block checks
    for block in module.always_blocks:
        if LintCode.LATCH_INFERRED not in skip:
            if block.sensitivity_type == SensitivityType.COMBINATIONAL:
                warnings.extend(_check_latch_inferred(block, module.name))

        if LintCode.MIXED_BLOCKING not in skip and LintCode.MIXED_NONBLOCKING not in skip:
            warnings.extend(_check_assignment_style(block, module.name))

    # Width mismatch
    if LintCode.WIDTH_MISMATCH not in skip:
        warnings.extend(_check_width_mismatch(module))

    # Unconnected ports
    if LintCode.UNCONNECTED_PORT not in skip:
        warnings.extend(_check_unconnected_ports(module))

    # Input port initializations
    if LintCode.INPUT_INIT not in skip:
        warnings.extend(_check_input_port_init(module))

    # Stable sort: code name, then signal
    warnings.sort(key=lambda w: (w.code.name, w.signal or "", w.instance or ""))
    return warnings


def lint_design(
    design: Design,
    *,
    skip: set[LintCode] | None = None,
) -> list[LintWarning]:
    """Run lint checks on all modules in a design.

    Parameters
    ----------
    design : Design
        The design to lint.
    skip : set[LintCode] | None
        Optional set of check codes to skip.

    Returns
    -------
    list[LintWarning]
        All warnings from all modules.
    """
    warnings: list[LintWarning] = []
    for mod in design.modules:
        warnings.extend(lint_module(mod, skip=skip))
    return warnings
