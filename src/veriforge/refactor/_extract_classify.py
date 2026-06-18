"""AST analysis helpers, connection classification, and location utilities for extract."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from ..model.base import SourceLocation
from ..model.design import Module
from ..model.expressions import (
    AssignmentPattern,
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    Mintypmax,
    PartSelect,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from ..model.instances import Instance
from ..model.nets import Net
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.statements import BlockingAssign, NonblockingAssign, ParBlock, SeqBlock, TaskEnable
from ..model.variables import Variable

from .diagnostics import RefactorDiagnostic
from ._refactor_utils import _loc_range, _simple_identifier_name
from ._extract_models import ExtractPreview, ExtractSelection, _SelectedDeclarations


def _source_lines(file_path: str) -> list[str]:
    path = Path(file_path)
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)


def _module_net_declaration(module: Module, name: str) -> Net | None:
    net = module.get_net(name)
    if net is not None:
        return net
    return next((candidate for candidate in module.find(Net) if candidate.name == name), None)


def _module_variable_declaration(module: Module, name: str) -> Variable | None:
    variable = module.get_variable(name)
    if variable is not None:
        return variable
    return next((candidate for candidate in module.find(Variable) if candidate.name == name), None)


def _written_identifier_names(nodes: list[object]) -> set[str]:
    names: set[str] = set()
    for assignment in _procedural_assignments(*nodes):
        lhs = _simple_identifier_name(assignment.lhs)
        if lhs is not None:
            names.add(lhs)
    return names


def _procedural_assignments(*nodes: object) -> list[BlockingAssign | NonblockingAssign]:
    assignments: list[BlockingAssign | NonblockingAssign] = []
    for node in nodes:
        if not hasattr(node, "find"):
            continue
        assignments.extend(node.find(BlockingAssign))  # type: ignore[attr-defined]
        assignments.extend(node.find(NonblockingAssign))  # type: ignore[attr-defined]
    return assignments


def _subroutine_refs(*nodes: object) -> list[str]:
    """Return sorted unique non-system function/task names referenced inside nodes.

    System functions/tasks (e.g. ``$display``) are excluded — they remain valid
    inside the lifted child module. Non-system subroutine calls would silently
    become dangling references in the extracted module since user-defined
    functions/tasks live in the parent and are not lifted today.
    """
    names: set[str] = set()
    for node in nodes:
        if not hasattr(node, "find"):
            continue
        for call in node.find(FunctionCall):  # type: ignore[attr-defined]
            if not call.name:
                continue
            # Some parser paths leave is_system=False even for $-prefixed
            # builtins; treat any $-prefixed name as a system call too.
            if call.is_system or call.name.startswith("$"):
                continue
            names.add(call.name)
        for task in node.find(TaskEnable):  # type: ignore[attr-defined]
            if task.task_name:
                names.add(task.task_name)
    return sorted(names)


def _signal_has_dimensions(module: Module, name: str) -> bool:
    net = _module_net_declaration(module, name)
    if net is not None:
        return bool(net.dimensions)
    variable = _module_variable_declaration(module, name)
    if variable is not None:
        return bool(variable.dimensions)
    return False


def _identifier_names(node: Expression | object) -> set[str]:
    if not hasattr(node, "find"):
        return set()
    return {
        identifier.name
        for identifier in node.find(Identifier)  # type: ignore[attr-defined]
        if not identifier.hierarchy
    }


def _signal_order(module: Module) -> list[str]:
    ordered: list[str] = []
    for collection in (module.ports, module.find(Net), module.find(Variable)):
        for signal in collection:
            if signal.name not in ordered:
                ordered.append(signal.name)
    return ordered


def _ordered_names(names: set[str], order: list[str]) -> list[str]:
    ordered = [name for name in order if name in names]
    ordered.extend(sorted(names - set(ordered)))
    return ordered


def _module_parameter_names(module: Module) -> set[str]:
    return {param.name for param in module.parameters}


def _block_local_var_names(*nodes: object) -> set[str]:
    """Names declared as block-local variables inside any SeqBlock/ParBlock under nodes."""
    names: set[str] = set()
    for node in nodes:
        if not hasattr(node, "find"):
            continue
        for block in node.find(SeqBlock):  # type: ignore[attr-defined]
            for var in getattr(block, "local_vars", []) or []:
                names.add(var.name)
        for block in node.find(ParBlock):  # type: ignore[attr-defined]
            for var in getattr(block, "local_vars", []) or []:
                names.add(var.name)
    return names


def _collect_constant_refs(  # noqa: PLR0912
    module: Module,
    *,
    lifted_items: tuple[object, ...],
    boundary_input_names: set[str],
    boundary_output_names: set[str],
    boundary_internal_names: set[str],
) -> set[str]:
    """Collect identifier names from lifted logic AND from declarations/widths/bindings
    that the extracted child module will mention.

    This is the width-aware, scope-safe view of identifiers the child must
    resolve. Caller filters this against parent parameters/localparams.
    """
    names: set[str] = set()
    for item in lifted_items:
        names.update(_identifier_names(item))
        # Walk parameter bindings on instances explicitly (the model already
        # walks them via _child_nodes, but be explicit for clarity).
        if isinstance(item, Instance):
            for binding in item.parameter_bindings or []:
                names.update(_identifier_names(binding.value))

    # Add identifiers from the widths of declarations/ports the child will
    # emit (input/output ports built from parent signals, plus internal nets/
    # variables copied from the parent).
    for name in (*boundary_input_names, *boundary_output_names):
        port = module.get_port(name)
        if port is not None and port.width is not None:
            names.update(_identifier_names(port.width.msb))
            names.update(_identifier_names(port.width.lsb))
            continue
        net = _module_net_declaration(module, name)
        if net is not None and net.width is not None:
            names.update(_identifier_names(net.width.msb))
            names.update(_identifier_names(net.width.lsb))
            continue
        var = _module_variable_declaration(module, name)
        if var is not None and var.width is not None:
            names.update(_identifier_names(var.width.msb))
            names.update(_identifier_names(var.width.lsb))

    for name in boundary_internal_names:
        net = _module_net_declaration(module, name)
        if net is not None:
            if net.width is not None:
                names.update(_identifier_names(net.width.msb))
                names.update(_identifier_names(net.width.lsb))
            for dim in net.dimensions or []:
                names.update(_identifier_names(dim.msb))
                names.update(_identifier_names(dim.lsb))
            continue
        var = _module_variable_declaration(module, name)
        if var is not None:
            if var.width is not None:
                names.update(_identifier_names(var.width.msb))
                names.update(_identifier_names(var.width.lsb))
            for dim in var.dimensions or []:
                names.update(_identifier_names(dim.msb))
                names.update(_identifier_names(dim.lsb))

    # Drop block-local variable names so a procedural local 'W' isn't
    # misclassified as parent constant 'W'.
    names -= _block_local_var_names(*lifted_items)
    return names


def _residual_blocked_param_refs(
    module: Module,
    candidate_param_names: set[str],
    selected_declarations: _SelectedDeclarations,
) -> set[str]:
    """Filter `candidate_param_names` (already known to be parent params) down
    to only those that the auto-handling classifier cannot resolve.

    Used to relax instance-flow diagnostics so unselected parent params that
    can be auto-forwarded or auto-copied no longer block extraction.
    """
    if not candidate_param_names:
        return set()
    classification = _classify_parent_constants(module, candidate_param_names, selected_declarations)
    return set(candidate_param_names) - classification.handled_names


@dataclass(frozen=True)
class _ParentConstantClassification:
    forwarded_parameters: tuple[Parameter, ...]
    copied_localparams: tuple[Parameter, ...]
    blocked: tuple[tuple[str, str], ...]  # (name, reason)
    handled_names: frozenset[str]


def _classify_parent_constants(
    module: Module,
    referenced_names: set[str],
    selected_declarations: _SelectedDeclarations,
) -> _ParentConstantClassification:
    """Classify referenced identifiers against the parent's parameters/localparams.

    For each referenced name that resolves to a parent parameter or localparam
    that is NOT explicitly selected (which would route through the existing
    "moved" path), recursively close over its default-value identifier
    references. Localparams are auto-copied into the child; overrideable
    parameters are forwarded as child parameter ports. Names whose recursive
    closure references a non-constant identifier (signal/port) or an unknown
    identifier are returned as blocked with a reason.

    Emission order preserves the parent's source declaration order.
    """
    by_name: dict[str, Parameter] = {param.name: param for param in module.parameters}
    selected_param_names = selected_declarations.parameter_names
    parent_param_names = set(by_name.keys())
    signal_names = {port.name for port in module.ports}
    signal_names.update(net.name for net in module.nets)
    signal_names.update(var.name for var in module.variables)

    handled: set[str] = set()
    blocked: dict[str, str] = {}

    def visit(name: str, stack: tuple[str, ...]) -> None:  # noqa: PLR0911
        if name in handled or name in blocked:
            return
        if name in stack:
            blocked[name] = "cyclic-localparam-dependency"
            return
        if name in selected_param_names:
            # Selected parameters/localparams flow through the existing
            # _SelectedDeclarations pathway; do not double-handle.
            return
        param = by_name.get(name)
        if param is None:
            # Unknown identifier (could be a signal we already know about, or
            # something else entirely). Caller decides — this helper only
            # cares about parent parameters/localparams.
            return
        # Reject auto-handling if the param's default/width references a
        # user-defined function/task — those live in the parent and would
        # become dangling references in the lifted child.
        sub_refs = _subroutine_refs(param)
        if sub_refs:
            blocked[name] = f"depends-on-subroutine:{sub_refs[0]}"
            return
        # Walk RHS dependencies first to ensure transitive close.
        rhs_names = _identifier_names(param.default_value) if param.default_value is not None else set()
        if param.width is not None:
            rhs_names |= _identifier_names(param.width.msb)
            rhs_names |= _identifier_names(param.width.lsb)
        for ref in rhs_names:
            if ref in parent_param_names:
                visit(ref, (*stack, name))
                if ref in blocked:
                    blocked[name] = f"depends-on-blocked:{ref}"
                    return
            elif ref in signal_names:
                blocked[name] = f"depends-on-signal:{ref}"
                return
            # Otherwise: unknown identifier (e.g., $clog2 builtins or system
            # tasks should not appear here; macros are pre-expanded). Treat as
            # benign — emitter will reproduce verbatim.
        handled.add(name)

    for name in referenced_names:
        if name in by_name and name not in selected_param_names:
            visit(name, ())

    # Emit in parent declaration source order.
    forwarded: list[Parameter] = []
    copied: list[Parameter] = []
    for param in module.parameters:
        if param.name not in handled:
            continue
        if param.is_local:
            copied.append(param)
        else:
            forwarded.append(param)

    return _ParentConstantClassification(
        forwarded_parameters=tuple(forwarded),
        copied_localparams=tuple(copied),
        blocked=tuple(sorted(blocked.items())),
        handled_names=frozenset(handled),
    )


def _allocate_synthetic_port_name(
    instance_name: str | None,
    port_name: str,
    used_names: set[str],
) -> str:
    base_inst = instance_name or "u"
    base = f"{base_inst}_{port_name}"
    candidate = base
    counter = 2
    while candidate in used_names:
        candidate = f"{base}_{counter}"
        counter += 1
    used_names.add(candidate)
    return candidate


def _port_width_is_self_contained(port: Port) -> bool:
    """True if the port's declared width has no identifier references.

    The synthetic output port we emit on the extracted child module copies
    the inner module's port width verbatim. If that width references a
    parameter that is not in the child module's scope (e.g., the inner
    module's local parameter), the emitted Verilog will not compile; reject
    those cases for now and defer support to a follow-up slice.
    """
    if port.width is None:
        return True
    for endpoint in (port.width.msb, port.width.lsb):
        if endpoint is None:
            continue
        if any(True for _ in endpoint.find(Identifier)):
            return False
    return True


@dataclass(frozen=True)
class _InputConnectionClassification:
    """Result of classifying a port-connection expression for an input port.

    The classifier walks the expression and records which identifiers are
    parent signals vs parent parameters. Unsupported node kinds (function
    calls, mintypmax, assignment patterns) cause ``supported`` to be False
    and ``reason`` to name the offending node kind. Hierarchical identifier
    references are surfaced separately so callers can emit the existing
    ``unsupported-hierarchical-reference`` diagnostic.
    """

    supported: bool
    reason: str | None
    signal_reads: frozenset[str]
    param_refs: frozenset[str]
    has_hierarchical: bool


_INPUT_CONNECTION_SAFE_NODES: tuple[type, ...] = (
    Identifier,
    Literal,
    StringLiteral,
    UnaryOp,
    BinaryOp,
    TernaryOp,
    Concatenation,
    Replication,
    BitSelect,
    RangeSelect,
    PartSelect,
)

_INPUT_CONNECTION_UNSUPPORTED_NODES: tuple[tuple[type, str], ...] = (
    (FunctionCall, "function-call"),
    (AssignmentPattern, "assignment-pattern"),
    (Mintypmax, "mintypmax"),
)


def _classify_input_connection(
    expr: Expression | None,
    parent_param_names: set[str],
) -> _InputConnectionClassification:
    """Classify an input-port connection expression.

    Returns a structured result describing whether the expression is a safe
    input-side connection, which signals it reads, and which parent
    parameters it references. Unconnected ports (``expr is None``) are
    treated as safe with no contributions, matching the existing engine's
    tolerance for ``.port()``.
    """
    signal_reads: set[str] = set()
    param_refs: set[str] = set()
    has_hierarchical = False
    unsupported_reason: str | None = None

    if expr is None:
        return _InputConnectionClassification(
            supported=True,
            reason=None,
            signal_reads=frozenset(),
            param_refs=frozenset(),
            has_hierarchical=False,
        )

    for node in expr.walk():
        for unsupported_type, reason in _INPUT_CONNECTION_UNSUPPORTED_NODES:
            if isinstance(node, unsupported_type):
                unsupported_reason = reason
                break
        if unsupported_reason is not None:
            break
        if isinstance(node, Identifier):
            if node.hierarchy:
                has_hierarchical = True
                continue
            if node.name in parent_param_names:
                param_refs.add(node.name)
            else:
                signal_reads.add(node.name)

    return _InputConnectionClassification(
        supported=unsupported_reason is None,
        reason=unsupported_reason,
        signal_reads=frozenset(signal_reads),
        param_refs=frozenset(param_refs),
        has_hierarchical=has_hierarchical,
    )


@dataclass(frozen=True)
class _OutputConnectionClassification:
    """Classification result for an output-port connection expression.

    Output ports require their connection to be "writable" (a concat of
    nets, bit-selects, range-selects or part-selects). Pure expressions
    like ``~y`` or ``a + b`` are not legal as instance output connections
    and are rejected. Hierarchical references and unselected parent
    parameter references are also rejected.
    """

    supported: bool
    reason: str | None
    parent_signal_writes: frozenset[str]
    param_refs: frozenset[str]
    has_hierarchical: bool


def _is_writable_output_element(node: Expression) -> bool:
    """A single element of a writable concatenation.

    Hierarchical identifiers are accepted as the right *shape* here so that the
    deeper walk in ``_classify_output_connection`` can surface the more specific
    ``unsupported-hierarchical-reference`` diagnostic instead of being short-
    circuited as a generic ``non-writable-expression``.
    """
    if isinstance(node, Identifier):
        return True
    if isinstance(node, (BitSelect, RangeSelect, PartSelect)):
        return isinstance(node.target, Identifier)
    return False


def _classify_output_connection(  # noqa: PLR0912
    expr: Expression | None,
    parent_param_names: set[str],
) -> _OutputConnectionClassification:
    if expr is None:
        return _OutputConnectionClassification(
            supported=True,
            reason=None,
            parent_signal_writes=frozenset(),
            param_refs=frozenset(),
            has_hierarchical=False,
        )

    # Top-level shape check: must be a writable element or a concatenation thereof.
    if isinstance(expr, Concatenation):
        elements = list(expr.parts)
    else:
        elements = [expr]
    for element in elements:
        if not _is_writable_output_element(element):
            return _OutputConnectionClassification(
                supported=False,
                reason="non-writable-expression",
                parent_signal_writes=frozenset(),
                param_refs=frozenset(),
                has_hierarchical=False,
            )

    # Walk the entire expression to collect identifier references and reject
    # unsupported sub-nodes inside index expressions (e.g., function calls).
    parent_signal_writes: set[str] = set()
    param_refs: set[str] = set()
    has_hierarchical = False
    unsupported_reason: str | None = None

    for node in expr.walk():
        for unsupported_type, reason in _INPUT_CONNECTION_UNSUPPORTED_NODES:
            if isinstance(node, unsupported_type):
                unsupported_reason = reason
                break
        if unsupported_reason is not None:
            break
        if isinstance(node, Identifier):
            if node.hierarchy:
                has_hierarchical = True
                continue
            if node.name in parent_param_names:
                param_refs.add(node.name)
            else:
                parent_signal_writes.add(node.name)

    return _OutputConnectionClassification(
        supported=unsupported_reason is None,
        reason=unsupported_reason,
        parent_signal_writes=frozenset(parent_signal_writes),
        param_refs=frozenset(param_refs),
        has_hierarchical=has_hierarchical,
    )


def _loc_in_selection(loc: SourceLocation | None, selection: ExtractSelection) -> bool:
    if loc is None or not loc.file:
        return False
    if os.path.normcase(os.path.abspath(loc.file)) != os.path.normcase(os.path.abspath(selection.file)):
        return False
    start = loc.line or 0
    end = loc.end_line or start
    return start >= selection.start_line and end <= selection.end_line


def _loc_overlaps_selection(loc: SourceLocation | None, selection: ExtractSelection) -> bool:
    if loc is None or not loc.file:
        return False
    if os.path.normcase(os.path.abspath(loc.file)) != os.path.normcase(os.path.abspath(selection.file)):
        return False
    start = loc.line or 0
    end = loc.end_line or start
    return start <= selection.end_line and end >= selection.start_line


def _range_start_line(range_payload: dict[str, object]) -> int:
    start = range_payload.get("start", {})
    return int(start.get("line", 0)) if isinstance(start, dict) else 0


def _range_start_col(range_payload: dict[str, object]) -> int:
    start = range_payload.get("start", {})
    return int(start.get("character", 0)) if isinstance(start, dict) else 0


def _source_line_range(
    loc: SourceLocation | None,
    *,
    lines: list[str] | None = None,
    start_line_override: int | None = None,
    end_line_override: int | None = None,
) -> dict[str, object]:
    if loc is None:
        return {}
    if lines is None:
        file_path = loc.file or ""
        lines = _source_lines(file_path) if file_path else []
    start_line = start_line_override if start_line_override is not None else max(0, (loc.line or 1) - 1)
    end_line_1based = end_line_override if end_line_override is not None else (loc.end_line or loc.line or 1)
    end_line = max(start_line, min(max(0, end_line_1based), len(lines)))
    return {
        "start": {"line": start_line, "character": 0},
        "end": {"line": end_line, "character": 0},
    }


def _source_text_for_loc(loc: SourceLocation | None) -> str:
    if loc is None or not loc.file:
        return ""
    path = Path(loc.file)
    if not path.is_file():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    start = max(0, (loc.line or 1) - 1)
    end = loc.end_line if loc.end_line else loc.line
    return "".join(lines[start:end])


def _blocked(  # noqa: PLR0913
    module_name: str,
    extracted_module_name: str,
    instance_name: str,
    selection: ExtractSelection,
    code: str,
    message: str,
) -> ExtractPreview:
    return ExtractPreview(
        module_name=module_name,
        extracted_module_name=extracted_module_name,
        instance_name=instance_name,
        selection=selection,
        confidence="blocked",
        diagnostics=(RefactorDiagnostic(code, message, severity="error"),),
    )
