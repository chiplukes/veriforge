"""Preview-only extraction of selected logic into a child module."""

from __future__ import annotations

import copy
import os
from pathlib import Path

from ..analysis.lint import _check_latch_inferred
from ..codegen import emit_module
from ..codegen.verilog_emitter import _emit_instance, _emit_net, _emit_port, _emit_variable, emit_expression
from ..model.assignments import ContinuousAssign
from ..model.base import SourceLocation
from ..model.behavioral import AlwaysBlock, InitialBlock, SensitivityType
from ..model.design import Design, Module
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
from ..model.instances import Instance, ParameterBinding, PortConnection
from ..model.nets import Net
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.statements import BlockingAssign, NonblockingAssign, ParBlock, SeqBlock, TaskEnable
from ..model.variables import Variable
from .diagnostics import RefactorDiagnostic
from ._refactor_utils import (
    TextEditPlan,
    _apply_edit_plans,
    _apply_text_edits,
    _group_edits_by_file,
    _loc_range,
    _simple_identifier_name,
    _unified_diff,
)
from ._extract_models import (
    ExtractApplyResult,
    ExtractPreview,
    ExtractSelection,
    ExtractSelectionItem,
    ExtractSelectionSuggestion,
    NormalizedExtractSelection,
    _Boundary,
    _ComplexOutput,
    _SelectedDeclarations,
    _movable_selected_declarations,
)
from ._extract_classify import (
    _InputConnectionClassification,
    _OutputConnectionClassification,
    _ParentConstantClassification,
    _allocate_synthetic_port_name,
    _blocked,
    _block_local_var_names,
    _classify_input_connection,
    _classify_output_connection,
    _classify_parent_constants,
    _collect_constant_refs,
    _identifier_names,
    _is_writable_output_element,
    _loc_in_selection,
    _loc_overlaps_selection,
    _module_net_declaration,
    _module_variable_declaration,
    _module_parameter_names,
    _ordered_names,
    _port_width_is_self_contained,
    _procedural_assignments,
    _range_start_col,
    _range_start_line,
    _residual_blocked_param_refs,
    _signal_has_dimensions,
    _signal_order,
    _source_line_range,
    _source_text_for_loc,
    _subroutine_refs,
    _written_identifier_names,
)


def preview_extract_submodule(  # noqa: PLR0911, PLR0912  # cm:2a9b7e
    design: Design,
    *,
    module_name: str,
    selection: ExtractSelection,
    extracted_module_name: str,
    instance_name: str | None = None,
) -> ExtractPreview:
    """Preview extracting selected logic into a child module."""

    instance_name = instance_name or f"u_{extracted_module_name}"
    parent = design.get_module(module_name)
    if parent is None:
        return _blocked(module_name, extracted_module_name, instance_name, selection, "module-not-found", module_name)

    if selection.signal is not None:
        if selection.signal_module and selection.signal_module != module_name:
            return _blocked(
                module_name,
                extracted_module_name,
                instance_name,
                selection,
                "trace-module-mismatch",
                (
                    f"Trace-neighborhood selection module {selection.signal_module!r} "
                    f"does not match extract module {module_name!r}."
                ),
            )
        # Ensure resolver sees the parent module name even if caller only set outer moduleName.
        if not selection.signal_module:
            selection = ExtractSelection(
                file=selection.file,
                start_line=selection.start_line,
                end_line=selection.end_line,
                signal=selection.signal,
                signal_module=module_name,
                node_id_allowlist=selection.node_id_allowlist,
            )
        normalized = _resolve_signal_selection(design, selection)
        if any(diag.severity == "error" for diag in normalized.diagnostics):
            return ExtractPreview(
                module_name=module_name,
                extracted_module_name=extracted_module_name,
                instance_name=instance_name,
                selection=selection,
                confidence="blocked",
                diagnostics=normalized.diagnostics,
                metadata={"selectionNormalization": normalized.to_dict()},
            )
        # Adopt the resolved selection (carries node_id_allowlist + module file path).
        selection = normalized.selection
    else:
        normalized = normalize_extract_selection(parent, selection)
        if normalized.diagnostics:
            return ExtractPreview(
                module_name=module_name,
                extracted_module_name=extracted_module_name,
                instance_name=instance_name,
                selection=selection,
                confidence="blocked",
                diagnostics=normalized.diagnostics,
                metadata={"selectionNormalization": normalized.to_dict()},
            )

    selected_assigns = _selected_continuous_assigns(parent, selection)
    selected_always = _selected_always_blocks(parent, selection)
    selected_initial = _selected_initial_blocks(parent, selection)
    selected_instances = _selected_instances(parent, selection)
    selected_declarations = _selected_declarations(parent, selection)
    if (selected_always and selected_initial) or (
        (selected_always or selected_initial) and (selected_assigns or selected_instances)
    ):
        return ExtractPreview(
            module_name=module_name,
            extracted_module_name=extracted_module_name,
            instance_name=instance_name,
            selection=selection,
            confidence="blocked",
            diagnostics=(
                RefactorDiagnostic(
                    "mixed-selection-unsupported",
                    "Extract preview supports structural selections (continuous assignments and safe instance groups) or one procedural category at a time.",
                    severity="error",
                ),
            ),
            metadata={"selectionNormalization": normalized.to_dict()},
        )
    if not selected_assigns and not selected_always and not selected_initial and not selected_instances:
        diagnostics = normalized.diagnostics
        if not diagnostics and normalized.items:
            diagnostics = RefactorDiagnostic(
                "unsupported-selected-nodes",
                "Selection maps to complete semantic nodes, but none are supported by the current extract rewrite.",
                severity="error",
            )
        if diagnostics:
            return ExtractPreview(
                module_name=module_name,
                extracted_module_name=extracted_module_name,
                instance_name=instance_name,
                selection=selection,
                confidence="blocked",
                diagnostics=diagnostics,
                metadata={"selectionNormalization": normalized.to_dict()},
            )
        return _blocked(
            module_name,
            extracted_module_name,
            instance_name,
            selection,
            "no-extractable-selection",
            "Selection does not contain any complete continuous assignments, always blocks, initial blocks, or instance groups.",
        )
    if any(inst.instance_name == instance_name for inst in parent.instances):
        return _blocked(
            module_name,
            extracted_module_name,
            instance_name,
            selection,
            "instance-name-collision",
            f"Parent module already contains an instance named {instance_name}.",
        )

    if selected_always:
        return _preview_extract_procedural_blocks(
            parent,
            selected_always,
            selection=selection,
            extracted_module_name=extracted_module_name,
            instance_name=instance_name,
            normalized=normalized,
            selected_declarations=selected_declarations,
            block_kind="always",
        )

    if selected_initial:
        return _preview_extract_procedural_blocks(
            parent,
            selected_initial,
            selection=selection,
            extracted_module_name=extracted_module_name,
            instance_name=instance_name,
            normalized=normalized,
            selected_declarations=selected_declarations,
            block_kind="initial",
        )

    if selected_assigns and selected_instances:
        return _preview_extract_mixed_structural(
            design,
            parent,
            selected_assigns,
            selected_instances,
            selection=selection,
            extracted_module_name=extracted_module_name,
            instance_name=instance_name,
            normalized=normalized,
            selected_declarations=selected_declarations,
        )

    if selected_instances:
        return _preview_extract_instance_group(
            design,
            parent,
            selected_instances,
            selection=selection,
            extracted_module_name=extracted_module_name,
            instance_name=instance_name,
            normalized=normalized,
            selected_declarations=selected_declarations,
        )

    boundary = _compute_boundary(parent, selected_assigns, selected_declarations)
    movable_selected_declarations = _movable_selected_declarations(selected_declarations, boundary)
    diagnostics = [
        *_selection_diagnostics(parent, selected_assigns),
        *_selected_declaration_diagnostics(
            parent,
            selected_declarations,
            boundary,
            selected_logic_ids={id(assign) for assign in selected_assigns},
        ),
    ]
    if any(diag.severity == "error" for diag in diagnostics):
        return ExtractPreview(
            module_name=module_name,
            extracted_module_name=extracted_module_name,
            instance_name=instance_name,
            selection=selection,
            confidence="blocked",
            diagnostics=tuple(diagnostics),
            metadata={"selectionNormalization": normalized.to_dict()},
        )

    extracted = _build_extracted_module(
        parent,
        selected_assigns,
        extracted_module_name,
        boundary,
        movable_selected_declarations,
    )
    transformed_parent = _build_parent_with_extracted_instance(
        parent,
        selected_assigns,
        extracted,
        instance_name,
        boundary,
        movable_selected_declarations,
    )

    extracted_text = emit_module(extracted, emit_comments=True).rstrip()
    edits = _build_extract_edit_plans(
        parent=parent,
        transformed_parent=transformed_parent,
        selected_locs=[assign.loc for assign in selected_assigns],
        selected_declarations=movable_selected_declarations,
        boundary=boundary,
        generated_module_text=extracted_text + "\n",
        generated_module_name=extracted_module_name,
    )
    if not edits:
        return _blocked(
            module_name,
            extracted_module_name,
            instance_name,
            selection,
            "extract-edit-plan-unavailable",
            "Could not build extract edit plan for the selected logic.",
        )
    return ExtractPreview(
        module_name=module_name,
        extracted_module_name=extracted_module_name,
        instance_name=instance_name,
        selection=selection,
        confidence="preview",
        diagnostics=tuple(diagnostics),
        edits=edits,
        diff=_diff_for_edit_plans(edits),
        boundary={
            "inputs": tuple(boundary.inputs),
            "outputs": tuple(boundary.outputs),
            "internals": tuple(boundary.internals),
        },
        generated_module=extracted_text + "\n",
        metadata={
            "selectedAssignments": len(selected_assigns),
            "selectedDeclarations": movable_selected_declarations.to_dict(),
            "selectionNormalization": normalized.to_dict(),
            "generatedModuleFile": _generated_module_path(selection.file, extracted_module_name),
        },
    )


def apply_extract_preview(preview: ExtractPreview) -> ExtractApplyResult:
    """Apply an extract preview after validating the source text still matches."""

    if not preview.ok:
        return ExtractApplyResult(
            applied=False,
            diagnostics=(
                RefactorDiagnostic(
                    "preview-not-applicable",
                    "Extract preview has blocking diagnostics and cannot be applied.",
                    severity="error",
                ),
                *preview.diagnostics,
            ),
        )

    written: list[str] = []
    for file_path, file_edits in _group_edits_by_file(preview.edits).items():
        diagnostic = _apply_text_edits(file_path, file_edits)
        if diagnostic is not None:
            return ExtractApplyResult(applied=False, diagnostics=(diagnostic,), written_files=tuple(written))
        written.append(file_path)
    return ExtractApplyResult(applied=True, written_files=tuple(written))


def normalize_extract_selection(
    module: Module, selection: ExtractSelection, *, allow_generate_nested: bool = False
) -> NormalizedExtractSelection:
    """Normalize a source range to complete top-level semantic nodes in a module."""

    items: list[ExtractSelectionItem] = []
    diagnostics: list[RefactorDiagnostic] = []
    suggestions: list[ExtractSelectionSuggestion] = []
    contained_lines: list[tuple[int, int]] = []
    overlap_lines: list[tuple[int, int]] = []
    seen_suggestion_ranges: set[tuple[int, int]] = set()

    for node, kind, name, supported, support in _candidate_extract_nodes(
        module, allow_generate_nested=allow_generate_nested
    ):
        loc = getattr(node, "loc", None)
        if _loc_in_selection(loc, selection):
            items.append(
                ExtractSelectionItem(
                    kind=kind,
                    name=name,
                    range=_loc_range(loc),
                    supported=supported,
                    support=support,
                )
            )
            node_start = loc.line or 0
            node_end = loc.end_line or node_start
            if node_start and node_end:
                contained_lines.append((node_start, node_end))
            if not supported:
                diagnostics.append(
                    RefactorDiagnostic(
                        "unsupported-selected-node",
                        f"Selected {kind} {name!r} is recognized but not extractable yet: {support}.",
                        severity="error",
                    )
                )
        elif _loc_overlaps_selection(loc, selection):
            node_start = loc.line or 0
            node_end = loc.end_line or node_start
            if node_start and node_end:
                overlap_lines.append((node_start, node_end))
            range_key = (node_start, node_end)
            if range_key not in seen_suggestion_ranges:
                seen_suggestion_ranges.add(range_key)
                suggestions.append(
                    ExtractSelectionSuggestion(
                        kind="expand-to-node",
                        label=f"Expand selection to cover {kind} {name!r} (lines {node_start}-{node_end})",
                        start_line=node_start,
                        end_line=node_end,
                        range=_loc_range(loc),
                        node_kind=kind,
                        node_name=name,
                    )
                )
            hint = (
                f" (suggested: select lines {node_start}-{node_end} to cover the full {kind})"
                if node_start and node_end
                else ""
            )
            diagnostics.append(
                RefactorDiagnostic(
                    "partial-selection",
                    f"Selection partially overlaps {kind} {name!r}; select the complete node or narrow the range.{hint}",
                    severity="error",
                )
            )

    if overlap_lines:
        union_start = min(s for s, _ in overlap_lines + contained_lines)
        union_end = max(e for _, e in overlap_lines + contained_lines)
        union_key = (union_start, union_end)
        if union_key not in seen_suggestion_ranges:
            seen_suggestion_ranges.add(union_key)
            suggestions.append(
                ExtractSelectionSuggestion(
                    kind="expand-to-cover-selection",
                    label=f"Expand selection to cover all touched nodes (lines {union_start}-{union_end})",
                    start_line=union_start,
                    end_line=union_end,
                    range={
                        "start": {"line": max(0, union_start - 1), "character": 0},
                        "end": {"line": max(0, union_end - 1), "character": 0},
                    },
                )
            )

    items.sort(key=lambda item: (_range_start_line(item.range), _range_start_col(item.range), item.kind, item.name))
    return NormalizedExtractSelection(
        selection=selection,
        items=tuple(items),
        diagnostics=tuple(diagnostics),
        suggestions=tuple(suggestions),
    )


_TRACE_EXTRACTABLE_KINDS = frozenset({"continuous_assign", "always_block", "initial_block", "instance"})
_MIN_RACE_WRITERS = 2
_MIN_DISTINCT_CLOCKS_FOR_DOMAIN_WARNING = 2


def resolve_extract_selection(design: Design, selection: ExtractSelection) -> NormalizedExtractSelection | None:
    """Resolve a trace-neighborhood selection to a concrete set of model nodes.

    Returns ``None`` for range-mode selections so callers fall back to
    :func:`normalize_extract_selection`. For signal-mode selections the result
    contains items, diagnostics, and a derived ``ExtractSelection`` whose
    ``node_id_allowlist`` carries the resolved node identities for the rest of
    the extract pipeline.
    """

    if selection.signal is None:
        return None
    return _resolve_signal_selection(design, selection)


def _resolve_signal_selection(design: Design, selection: ExtractSelection) -> NormalizedExtractSelection:  # noqa: PLR0915
    signal_name = selection.signal or ""
    module_name = selection.signal_module or ""
    module = design.get_module(module_name) if module_name else None
    if module is None:
        return NormalizedExtractSelection(
            selection=selection,
            diagnostics=(
                RefactorDiagnostic(
                    "unknown-trace-module",
                    f"Trace-neighborhood selection references unknown module {module_name!r}.",
                    severity="error",
                ),
            ),
        )

    target = module.get_net(signal_name) or module.get_variable(signal_name) or module.get_port(signal_name)
    if target is None:
        return NormalizedExtractSelection(
            selection=selection,
            diagnostics=(
                RefactorDiagnostic(
                    "unknown-trace-signal",
                    f"Signal {signal_name!r} not found in module {module_name!r}.",
                    severity="error",
                ),
            ),
        )

    candidates_by_id: dict[int, tuple[object, str, str, bool, str]] = {
        id(entry[0]): entry for entry in _candidate_extract_nodes(module) if entry[1] in _TRACE_EXTRACTABLE_KINDS
    }

    selected_entries: list[tuple[object, str, str, bool, str]] = []
    seen_ids: set[int] = set()
    diagnostics: list[RefactorDiagnostic] = []
    seen_initializer_names: set[str] = set()
    seen_unsupported_ids: set[int] = set()

    def _classify(node: object, role: str) -> None:
        if node is None:
            return
        entry = candidates_by_id.get(id(node))
        if entry is not None:
            if id(node) not in seen_ids:
                seen_ids.add(id(node))
                selected_entries.append(entry)
            return
        if isinstance(node, (Net, Variable, Port)):
            decl_name = getattr(node, "name", "") or "<unnamed>"
            if decl_name not in seen_initializer_names:
                seen_initializer_names.add(decl_name)
                diagnostics.append(
                    RefactorDiagnostic(
                        "trace-neighborhood-initializer-source",
                        (
                            f"Signal {signal_name!r} {role} comes from declaration {decl_name!r} "
                            "(initializer/port); not extractable as a top-level node."
                        ),
                        severity="warning",
                    )
                )
            return
        if id(node) not in seen_unsupported_ids:
            seen_unsupported_ids.add(id(node))
            diagnostics.append(
                RefactorDiagnostic(
                    "trace-neighborhood-unsupported-source",
                    (
                        f"Signal {signal_name!r} {role} maps to a node "
                        f"({type(node).__name__}) that is not a top-level extractable item."
                    ),
                    severity="warning",
                )
            )

    for driver in getattr(target, "drivers", []) or []:
        source = getattr(driver, "source", None) or driver
        _classify(source, "driver")
    for load in getattr(target, "loads", []) or []:
        consumer = getattr(load, "consumer", None) or load
        _classify(consumer, "load")

    if not selected_entries:
        diagnostics.insert(
            0,
            RefactorDiagnostic(
                "empty-trace-neighborhood",
                f"Signal {signal_name!r} in module {module_name!r} has no extractable drivers or loads.",
                severity="error",
            ),
        )
        return NormalizedExtractSelection(selection=selection, diagnostics=tuple(diagnostics))

    items: list[ExtractSelectionItem] = []
    file_path = ""
    line_lo: int | None = None
    line_hi: int | None = None
    for node, kind, name, supported, support in selected_entries:
        loc = getattr(node, "loc", None)
        if loc is None:
            continue
        if not file_path and loc.file:
            file_path = loc.file
        node_start = loc.line or 0
        node_end = loc.end_line or node_start
        if node_start:
            line_lo = node_start if line_lo is None else min(line_lo, node_start)
        if node_end:
            line_hi = node_end if line_hi is None else max(line_hi, node_end)
        items.append(
            ExtractSelectionItem(
                kind=kind,
                name=name,
                range=_loc_range(loc),
                supported=supported,
                support=support,
            )
        )
        if not supported:
            diagnostics.append(
                RefactorDiagnostic(
                    "unsupported-selected-node",
                    f"Selected {kind} {name!r} is recognized but not extractable yet: {support}.",
                    severity="error",
                )
            )

    if not file_path:
        module_loc = getattr(module, "loc", None)
        file_path = (module_loc.file if module_loc and module_loc.file else "") or selection.file

    resolved = ExtractSelection(
        file=file_path,
        start_line=line_lo or 0,
        end_line=line_hi or 0,
        signal=signal_name,
        signal_module=module_name,
        node_id_allowlist=frozenset(seen_ids),
    )

    items.sort(key=lambda item: (_range_start_line(item.range), _range_start_col(item.range), item.kind, item.name))
    return NormalizedExtractSelection(
        selection=resolved,
        items=tuple(items),
        diagnostics=tuple(diagnostics),
    )


def _candidate_extract_nodes(
    module: Module, *, allow_generate_nested: bool = False
) -> list[tuple[object, str, str, bool, str]]:
    candidates: list[tuple[object, str, str, bool, str]] = []
    candidates.extend(
        (assign, "continuous_assign", _assign_name(assign), True, "supported") for assign in module.continuous_assigns
    )
    candidates.extend((block, "always_block", _always_name(block), True, "supported") for block in module.always_blocks)
    candidates.extend((block, "initial_block", "initial", True, "supported") for block in module.initial_blocks)
    candidates.extend((inst, "instance", inst.instance_name, True, "supported") for inst in module.instances)
    candidates.extend(
        (param, "parameter", param.name, True, "supported with selected logic") for param in module.parameters
    )
    candidates.extend((net, "net", net.name, True, "supported with selected logic") for net in module.nets)
    candidates.extend(
        (variable, "variable", variable.name, True, "supported with selected logic") for variable in module.variables
    )
    if allow_generate_nested:
        candidates.extend(_generate_extract_nodes(module))
    return [candidate for candidate in candidates if getattr(candidate[0], "loc", None) is not None]


def _generate_extract_nodes(module: Module) -> list[tuple[object, str, str, bool, str]]:
    candidates: list[tuple[object, str, str, bool, str]] = []
    for generate in module.generate_blocks:
        candidates.extend(
            (assign, "continuous_assign", _assign_name(assign), True, "supported")
            for assign in generate.find(ContinuousAssign)
        )
        candidates.extend(
            (block, "always_block", _always_name(block), True, "supported") for block in generate.find(AlwaysBlock)
        )
        candidates.extend(
            (block, "initial_block", "initial", True, "supported") for block in generate.find(InitialBlock)
        )
        candidates.extend((inst, "instance", inst.instance_name, True, "supported") for inst in generate.find(Instance))
        candidates.extend(
            (param, "parameter", param.name, True, "supported with selected logic")
            for param in generate.find(Parameter)
        )
        candidates.extend((net, "net", net.name, True, "supported with selected logic") for net in generate.find(Net))
        candidates.extend(
            (variable, "variable", variable.name, True, "supported with selected logic")
            for variable in generate.find(Variable)
        )
    return candidates


def _assign_name(assign: ContinuousAssign) -> str:
    return _simple_identifier_name(assign.lhs) or "<assign>"


def _always_name(block: AlwaysBlock) -> str:
    return block.sensitivity_type.name.lower()


def _selected_continuous_assigns(
    module: Module, selection: ExtractSelection, *, allow_generate_nested: bool = False
) -> list[ContinuousAssign]:
    if allow_generate_nested:
        nodes = list(module.find(ContinuousAssign))
        if selection.node_id_allowlist is not None:
            return [assign for assign in nodes if id(assign) in selection.node_id_allowlist]
        return [assign for assign in nodes if _loc_in_selection(assign.loc, selection)]
    if selection.node_id_allowlist is not None:
        return [assign for assign in module.continuous_assigns if id(assign) in selection.node_id_allowlist]
    return [assign for assign in module.continuous_assigns if _loc_in_selection(assign.loc, selection)]


def _selected_always_blocks(
    module: Module, selection: ExtractSelection, *, allow_generate_nested: bool = False
) -> list[AlwaysBlock]:
    if allow_generate_nested:
        nodes = list(module.find(AlwaysBlock))
        if selection.node_id_allowlist is not None:
            return [block for block in nodes if id(block) in selection.node_id_allowlist]
        return [block for block in nodes if _loc_in_selection(block.loc, selection)]
    if selection.node_id_allowlist is not None:
        return [block for block in module.always_blocks if id(block) in selection.node_id_allowlist]
    return [block for block in module.always_blocks if _loc_in_selection(block.loc, selection)]


def _selected_initial_blocks(
    module: Module, selection: ExtractSelection, *, allow_generate_nested: bool = False
) -> list[InitialBlock]:
    if allow_generate_nested:
        nodes = list(module.find(InitialBlock))
        if selection.node_id_allowlist is not None:
            return [block for block in nodes if id(block) in selection.node_id_allowlist]
        return [block for block in nodes if _loc_in_selection(block.loc, selection)]
    if selection.node_id_allowlist is not None:
        return [block for block in module.initial_blocks if id(block) in selection.node_id_allowlist]
    return [block for block in module.initial_blocks if _loc_in_selection(block.loc, selection)]


def _selected_instances(
    module: Module, selection: ExtractSelection, *, allow_generate_nested: bool = False
) -> list[Instance]:
    if allow_generate_nested:
        nodes = list(module.find(Instance))
        if selection.node_id_allowlist is not None:
            return [inst for inst in nodes if id(inst) in selection.node_id_allowlist]
        return [inst for inst in nodes if _loc_in_selection(inst.loc, selection)]
    if selection.node_id_allowlist is not None:
        return [inst for inst in module.instances if id(inst) in selection.node_id_allowlist]
    return [inst for inst in module.instances if _loc_in_selection(inst.loc, selection)]


def _selected_declarations(
    module: Module, selection: ExtractSelection, *, allow_generate_nested: bool = False
) -> _SelectedDeclarations:
    if selection.node_id_allowlist is not None:
        # Trace-neighborhood selections never carry declarations; declarations are
        # surfaced via dedicated diagnostics during resolution if a driver/load is one.
        return _SelectedDeclarations()
    if allow_generate_nested:
        parameters = {id(param): param for param in module.find(Parameter) if _loc_in_selection(param.loc, selection)}
        nets = {id(net): net for net in module.find(Net) if _loc_in_selection(net.loc, selection)}
        variables = {
            id(variable): variable for variable in module.find(Variable) if _loc_in_selection(variable.loc, selection)
        }
        return _SelectedDeclarations(
            parameters=tuple(parameters.values()),
            nets=tuple(nets.values()),
            variables=tuple(variables.values()),
        )
    return _SelectedDeclarations(
        parameters=tuple(param for param in module.parameters if _loc_in_selection(param.loc, selection)),
        nets=tuple(net for net in module.nets if _loc_in_selection(net.loc, selection)),
        variables=tuple(variable for variable in module.variables if _loc_in_selection(variable.loc, selection)),
    )


def _selection_diagnostics(_module: Module, selected: list[ContinuousAssign]) -> list[RefactorDiagnostic]:
    diagnostics: list[RefactorDiagnostic] = []
    for assign in selected:
        lhs = _simple_identifier_name(assign.lhs)
        if lhs is None:
            diagnostics.append(
                RefactorDiagnostic(
                    "unsupported-extract-lhs",
                    "Initial extract preview only supports continuous assignments with a simple identifier LHS.",
                    severity="error",
                )
            )
        for identifier in assign.find(Identifier):
            if identifier.hierarchy:
                diagnostics.append(
                    RefactorDiagnostic(
                        "unsupported-hierarchical-reference",
                        "Initial extract preview does not support hierarchical references inside the selected logic.",
                        severity="error",
                    )
                )
                break
    return diagnostics


def _preview_extract_instance_group(  # noqa: PLR0913
    design: Design,
    parent: Module,
    selected: list[Instance],
    *,
    selection: ExtractSelection,
    extracted_module_name: str,
    instance_name: str,
    normalized: NormalizedExtractSelection,
    selected_declarations: _SelectedDeclarations,
) -> ExtractPreview:
    boundary = _compute_instance_boundary(design, parent, selected, selected_declarations)
    movable_selected_declarations = _movable_selected_declarations(selected_declarations, boundary)
    diagnostics = [
        *_instance_selection_diagnostics(design, parent, selected, selected_declarations),
        *_selected_declaration_diagnostics(
            parent,
            selected_declarations,
            boundary,
            selected_logic_ids={id(inst) for inst in selected},
        ),
    ]
    if any(diag.severity == "error" for diag in diagnostics):
        return ExtractPreview(
            module_name=parent.name,
            extracted_module_name=extracted_module_name,
            instance_name=instance_name,
            selection=selection,
            confidence="blocked",
            diagnostics=tuple(diagnostics),
            metadata={"selectionNormalization": normalized.to_dict()},
        )

    extracted = _build_extracted_instance_group_module(
        parent,
        selected,
        extracted_module_name,
        boundary,
        movable_selected_declarations,
    )
    transformed_parent = _build_parent_with_extracted_instance_group(
        parent,
        selected,
        extracted,
        instance_name,
        movable_selected_declarations,
        boundary,
    )

    extracted_text = emit_module(extracted, emit_comments=True).rstrip()
    edits = _build_extract_edit_plans(
        parent=parent,
        transformed_parent=transformed_parent,
        selected_locs=[inst.loc for inst in selected],
        selected_declarations=movable_selected_declarations,
        boundary=boundary,
        generated_module_text=extracted_text + "\n",
        generated_module_name=extracted_module_name,
    )
    if not edits:
        return _blocked(
            parent.name,
            extracted_module_name,
            instance_name,
            selection,
            "extract-edit-plan-unavailable",
            "Could not build extract edit plan for the selected instance group.",
        )
    return ExtractPreview(
        module_name=parent.name,
        extracted_module_name=extracted_module_name,
        instance_name=instance_name,
        selection=selection,
        confidence="preview",
        diagnostics=tuple(diagnostics),
        edits=edits,
        diff=_diff_for_edit_plans(edits),
        boundary={
            "inputs": tuple(boundary.inputs),
            "outputs": tuple(boundary.outputs),
            "internals": tuple(boundary.internals),
        },
        generated_module=extracted_text + "\n",
        metadata={
            "selectedInstances": len(selected),
            "selectedDeclarations": movable_selected_declarations.to_dict(),
            "selectionNormalization": normalized.to_dict(),
            "generatedModuleFile": _generated_module_path(selection.file, extracted_module_name),
        },
    )


def _preview_extract_mixed_structural(  # noqa: PLR0913
    design: Design,
    parent: Module,
    selected_assigns: list[ContinuousAssign],
    selected_instances: list[Instance],
    *,
    selection: ExtractSelection,
    extracted_module_name: str,
    instance_name: str,
    normalized: NormalizedExtractSelection,
    selected_declarations: _SelectedDeclarations,
) -> ExtractPreview:
    selected_logic_ids = {id(assign) for assign in selected_assigns} | {id(inst) for inst in selected_instances}
    boundary = _compute_mixed_structural_boundary(
        design, parent, selected_assigns, selected_instances, selected_declarations
    )
    movable_selected_declarations = _movable_selected_declarations(selected_declarations, boundary)
    diagnostics = [
        *_selection_diagnostics(parent, selected_assigns),
        *_instance_selection_diagnostics(
            design,
            parent,
            selected_instances,
            selected_declarations,
            selected_logic_ids=selected_logic_ids,
            selection_label="selected structural logic",
        ),
        *_mixed_structural_driver_diagnostics(design, selected_assigns, selected_instances),
        *_selected_declaration_diagnostics(
            parent,
            selected_declarations,
            boundary,
            selected_logic_ids=selected_logic_ids,
        ),
    ]
    if any(diag.severity == "error" for diag in diagnostics):
        return ExtractPreview(
            module_name=parent.name,
            extracted_module_name=extracted_module_name,
            instance_name=instance_name,
            selection=selection,
            confidence="blocked",
            diagnostics=tuple(diagnostics),
            metadata={"selectionNormalization": normalized.to_dict()},
        )

    extracted = _build_extracted_mixed_structural_module(
        parent,
        selected_assigns,
        selected_instances,
        extracted_module_name,
        boundary,
        movable_selected_declarations,
    )
    transformed_parent = _build_parent_with_extracted_mixed_structural_instance(
        parent,
        selected_assigns,
        selected_instances,
        extracted,
        instance_name,
        movable_selected_declarations,
        boundary,
    )

    extracted_text = emit_module(extracted, emit_comments=True).rstrip()
    edits = _build_extract_edit_plans(
        parent=parent,
        transformed_parent=transformed_parent,
        selected_locs=[*(assign.loc for assign in selected_assigns), *(inst.loc for inst in selected_instances)],
        selected_declarations=movable_selected_declarations,
        boundary=boundary,
        generated_module_text=extracted_text + "\n",
        generated_module_name=extracted_module_name,
    )
    if not edits:
        return _blocked(
            parent.name,
            extracted_module_name,
            instance_name,
            selection,
            "extract-edit-plan-unavailable",
            "Could not build extract edit plan for the selected structural logic.",
        )
    return ExtractPreview(
        module_name=parent.name,
        extracted_module_name=extracted_module_name,
        instance_name=instance_name,
        selection=selection,
        confidence="preview",
        diagnostics=tuple(diagnostics),
        edits=edits,
        diff=_diff_for_edit_plans(edits),
        boundary={
            "inputs": tuple(boundary.inputs),
            "outputs": tuple(boundary.outputs),
            "internals": tuple(boundary.internals),
        },
        generated_module=extracted_text + "\n",
        metadata={
            "selectedAssignments": len(selected_assigns),
            "selectedInstances": len(selected_instances),
            "selectedDeclarations": movable_selected_declarations.to_dict(),
            "selectionNormalization": normalized.to_dict(),
            "generatedModuleFile": _generated_module_path(selection.file, extracted_module_name),
        },
    )


def _instance_selection_diagnostics(  # noqa: PLR0912, PLR0913, PLR0915
    design: Design,
    module: Module,
    selected: list[Instance],
    selected_declarations: _SelectedDeclarations,
    *,
    selected_logic_ids: set[int] | None = None,
    selection_label: str = "selected instance group",
) -> list[RefactorDiagnostic]:
    diagnostics: list[RefactorDiagnostic] = []
    selected_ids = {id(inst) for inst in selected}
    parent_param_names = _module_parameter_names(module)
    outside_written = _outside_written_identifier_names(
        module, selected_logic_ids or selected_ids
    ) | _outside_instance_output_names(design, module, selected_ids)
    seen_messages: set[tuple[str, str]] = set()

    for inst in selected:
        if inst.instance_array is not None:
            diagnostics.append(
                RefactorDiagnostic(
                    "unsupported-instance-array",
                    f"Selected instance {inst.instance_name!r} is an instance array; extraction of instance arrays is not supported yet.",
                    severity="error",
                )
            )

        resolved, port_connections, resolution_diagnostics = _resolved_instance_connections(design, inst)
        diagnostics.extend(resolution_diagnostics)
        if resolved is None:
            continue

        for port, connection in port_connections:
            expr = connection.expression

            if port.direction == PortDirection.INOUT:
                diagnostics.append(
                    RefactorDiagnostic(
                        "unsupported-instance-inout",
                        f"Selected instance {inst.instance_name!r} uses inout port {port.name!r}; instance-group extraction only supports input/output connectivity.",
                        severity="error",
                    )
                )
                continue

            if port.direction == PortDirection.INPUT:
                classification = _classify_input_connection(expr, parent_param_names)
                if classification.has_hierarchical:
                    diagnostics.append(
                        RefactorDiagnostic(
                            "unsupported-hierarchical-reference",
                            "Instance-group extraction does not support hierarchical references inside selected instance connections.",
                            severity="error",
                        )
                    )
                if not classification.supported:
                    diagnostics.append(
                        RefactorDiagnostic(
                            "unsupported-instance-connection",
                            (
                                f"Selected instance {inst.instance_name!r} port {port.name!r} connection uses an "
                                f"unsupported expression form ({classification.reason})."
                            ),
                            severity="error",
                        )
                    )
                    continue
                external_param_refs = classification.param_refs - selected_declarations.parameter_names
                external_param_refs = _residual_blocked_param_refs(module, external_param_refs, selected_declarations)
                if external_param_refs:
                    refs = ", ".join(sorted(external_param_refs))
                    diagnostics.append(
                        RefactorDiagnostic(
                            "unsupported-instance-parameter-dependencies",
                            (
                                f"Selected instance {inst.instance_name!r} port {port.name!r} connection depends "
                                f"on unselected parameter(s): {refs}."
                            ),
                            severity="error",
                        )
                    )
                continue

            # OUTPUT port path
            signal_name = _simple_identifier_name(expr)
            if signal_name is None and expr is not None:
                output_classification = _classify_output_connection(expr, parent_param_names)
                if output_classification.has_hierarchical:
                    diagnostics.append(
                        RefactorDiagnostic(
                            "unsupported-hierarchical-reference",
                            "Instance-group extraction does not support hierarchical references inside selected instance connections.",
                            severity="error",
                        )
                    )
                if not output_classification.supported:
                    diagnostics.append(
                        RefactorDiagnostic(
                            "unsupported-output-instance-connection",
                            (
                                f"Selected instance {inst.instance_name!r} output port {port.name!r} connection uses "
                                f"an unsupported expression form ({output_classification.reason})."
                            ),
                            severity="error",
                        )
                    )
                    continue
                external_param_refs = output_classification.param_refs - selected_declarations.parameter_names
                external_param_refs = _residual_blocked_param_refs(module, external_param_refs, selected_declarations)
                if external_param_refs:
                    refs = ", ".join(sorted(external_param_refs))
                    diagnostics.append(
                        RefactorDiagnostic(
                            "unsupported-instance-parameter-dependencies",
                            (
                                f"Selected instance {inst.instance_name!r} output port {port.name!r} connection "
                                f"depends on unselected parameter(s): {refs}."
                            ),
                            severity="error",
                        )
                    )
                    continue
                inner_port = resolved.get_port(port.name)
                if inner_port is None or not _port_width_is_self_contained(inner_port):
                    diagnostics.append(
                        RefactorDiagnostic(
                            "unsupported-output-instance-connection",
                            (
                                f"Selected instance {inst.instance_name!r} output port {port.name!r} has a "
                                "parameter-dependent width; complex connections on parameterized output ports are "
                                "not supported yet."
                            ),
                            severity="error",
                        )
                    )
                    continue
                # Conservative multi-driver check: if any underlying parent signal in the
                # complex output expression is also driven outside the selection, reject.
                for written_name in sorted(output_classification.parent_signal_writes):
                    message_key = ("multiple-instance-drivers", written_name)
                    if written_name in outside_written and message_key not in seen_messages:
                        diagnostics.append(
                            RefactorDiagnostic(
                                "multiple-instance-drivers",
                                f"Signal {written_name!r} is driven both inside and outside the {selection_label}.",
                                severity="error",
                            )
                        )
                        seen_messages.add(message_key)
                continue

            if signal_name is not None:
                message_key = ("multiple-instance-drivers", signal_name)
                if signal_name in outside_written and message_key not in seen_messages:
                    diagnostics.append(
                        RefactorDiagnostic(
                            "multiple-instance-drivers",
                            f"Signal {signal_name!r} is driven both inside and outside the {selection_label}.",
                            severity="error",
                        )
                    )
                    seen_messages.add(message_key)
                if _signal_has_dimensions(module, signal_name):
                    diagnostics.append(
                        RefactorDiagnostic(
                            "unsupported-output-memory",
                            f"Signal {signal_name!r} is an unpacked array or memory; extraction of memory outputs is not supported yet.",
                            severity="error",
                        )
                    )

        for binding in inst.parameter_bindings:
            external_refs = _identifier_names(binding) - selected_declarations.parameter_names
            external_refs = external_refs - _module_parameter_names(module) | _residual_blocked_param_refs(
                module,
                external_refs & _module_parameter_names(module),
                selected_declarations,
            )
            if external_refs:
                refs = ", ".join(sorted(external_refs))
                diagnostics.append(
                    RefactorDiagnostic(
                        "unsupported-instance-parameter-dependencies",
                        f"Selected instance {inst.instance_name!r} has parameter binding(s) that depend on unselected name(s): {refs}.",
                        severity="error",
                    )
                )
                break

    return diagnostics


def _preview_extract_procedural_blocks(  # noqa: PLR0913
    parent: Module,
    selected: list[AlwaysBlock | InitialBlock],
    *,
    selection: ExtractSelection,
    extracted_module_name: str,
    instance_name: str,
    normalized: NormalizedExtractSelection,
    selected_declarations: _SelectedDeclarations,
    block_kind: str,
) -> ExtractPreview:
    block_label = f"{block_kind}-block"
    boundary = _compute_procedural_boundary(parent, selected, selected_declarations)
    movable_selected_declarations = _movable_selected_declarations(selected_declarations, boundary)
    diagnostics = [
        *_procedural_selection_diagnostics(parent, selected, block_label=block_label),
        *_selected_declaration_diagnostics(
            parent,
            selected_declarations,
            boundary,
            selected_logic_ids={id(block) for block in selected},
        ),
    ]
    if any(diag.severity == "error" for diag in diagnostics):
        return ExtractPreview(
            module_name=parent.name,
            extracted_module_name=extracted_module_name,
            instance_name=instance_name,
            selection=selection,
            confidence="blocked",
            diagnostics=tuple(diagnostics),
            metadata={"selectionNormalization": normalized.to_dict()},
        )

    extracted = _build_extracted_procedural_module(
        parent,
        selected,
        extracted_module_name,
        boundary,
        movable_selected_declarations,
        block_kind=block_kind,
    )
    transformed_parent = _build_parent_with_extracted_procedural_instance(
        parent,
        selected,
        extracted,
        instance_name,
        boundary,
        movable_selected_declarations,
        block_kind=block_kind,
    )

    extracted_text = emit_module(extracted, emit_comments=True).rstrip()
    edits = _build_extract_edit_plans(
        parent=parent,
        transformed_parent=transformed_parent,
        selected_locs=[block.loc for block in selected],
        selected_declarations=movable_selected_declarations,
        boundary=boundary,
        generated_module_text=extracted_text + "\n",
        generated_module_name=extracted_module_name,
    )
    if not edits:
        return _blocked(
            parent.name,
            extracted_module_name,
            instance_name,
            selection,
            "extract-edit-plan-unavailable",
            "Could not build extract edit plan for the selected always block.",
        )
    return ExtractPreview(
        module_name=parent.name,
        extracted_module_name=extracted_module_name,
        instance_name=instance_name,
        selection=selection,
        confidence="preview",
        diagnostics=tuple(diagnostics),
        edits=edits,
        diff=_diff_for_edit_plans(edits),
        boundary={
            "inputs": tuple(boundary.inputs),
            "outputs": tuple(boundary.outputs),
            "internals": tuple(boundary.internals),
        },
        generated_module=extracted_text + "\n",
        metadata={
            "selectedAlwaysBlocks": len(selected) if block_kind == "always" else 0,
            "selectedInitialBlocks": len(selected) if block_kind == "initial" else 0,
            "selectedDeclarations": movable_selected_declarations.to_dict(),
            "selectionNormalization": normalized.to_dict(),
            "generatedModuleFile": _generated_module_path(selection.file, extracted_module_name),
        },
    )


def _procedural_selection_diagnostics(  # noqa: PLR0912, PLR0915
    module: Module,
    selected: list[AlwaysBlock | InitialBlock],
    *,
    block_label: str,
    allow_local_functions: bool = False,
) -> list[RefactorDiagnostic]:
    diagnostics: list[RefactorDiagnostic] = []
    selected_ids = {id(block) for block in selected}
    outside_written = _outside_written_identifier_names(module, selected_ids)
    seen_messages: set[tuple[str, str]] = set()
    writers_by_name: dict[str, int] = {}
    clock_signal_keys: set[str] = set()
    child_function_names = {fn.name for fn in module.functions}

    for block in selected:
        assignments = _procedural_assignments(block)
        subroutine_names = _subroutine_refs(block)
        for sub_name in subroutine_names:
            if allow_local_functions and sub_name in child_function_names:
                continue
            message_key = ("unsupported-procedural-subroutine-reference", sub_name)
            if message_key in seen_messages:
                continue
            diagnostics.append(
                RefactorDiagnostic(
                    "unsupported-procedural-subroutine-reference",
                    (
                        f"{block_label.capitalize()} extraction does not lift user-defined "
                        f"functions/tasks; selected logic calls {sub_name!r}, which would "
                        "leave a dangling reference in the extracted module."
                    ),
                    severity="error",
                )
            )
            seen_messages.add(message_key)
        if not assignments and not subroutine_names:
            diagnostics.append(
                RefactorDiagnostic(
                    "unsupported-empty-always-extract",
                    f"{block_label.capitalize()} extraction requires at least one blocking or nonblocking assignment.",
                    severity="error",
                )
            )
        for identifier in block.find(Identifier):
            if identifier.hierarchy:
                diagnostics.append(
                    RefactorDiagnostic(
                        "unsupported-hierarchical-reference",
                        f"{block_label.capitalize()} extraction does not support hierarchical references inside selected logic.",
                        severity="error",
                    )
                )
                break
        written = set[str]()
        for assignment in assignments:
            lhs = _simple_identifier_name(assignment.lhs)
            if lhs is None:
                diagnostics.append(
                    RefactorDiagnostic(
                        "unsupported-procedural-lhs",
                        f"{block_label.capitalize()} extraction currently supports only simple identifier procedural LHS targets.",
                        severity="error",
                    )
                )
            else:
                written.add(lhs)

        for name in sorted(written & outside_written):
            message_key = ("multiple-procedural-drivers", name)
            if message_key not in seen_messages:
                diagnostics.append(
                    RefactorDiagnostic(
                        "multiple-procedural-drivers",
                        f"Signal {name!r} is assigned both inside and outside the selected always block.",
                        severity="error",
                    )
                )
                seen_messages.add(message_key)
        for name in sorted(written):
            if _signal_has_dimensions(module, name):
                diagnostics.append(
                    RefactorDiagnostic(
                        "unsupported-output-memory",
                        f"Signal {name!r} is an unpacked array or memory; extraction of memory outputs is not supported yet.",
                        severity="error",
                    )
                )
        for name in written:
            writers_by_name[name] = writers_by_name.get(name, 0) + 1

        if isinstance(block, AlwaysBlock) and block.sensitivity_type == SensitivityType.COMBINATIONAL:
            for warn in _check_latch_inferred(block, module.name):
                signal = warn.signal or ""
                message_key = ("procedural-inferred-latch", signal or warn.message)
                if message_key in seen_messages:
                    continue
                diagnostics.append(
                    RefactorDiagnostic(
                        "procedural-inferred-latch",
                        (
                            f"Combinational {block_label} extraction may infer a latch in the "
                            f"extracted module: {warn.message}."
                        ),
                        severity="warning",
                    )
                )
                seen_messages.add(message_key)

        if isinstance(block, AlwaysBlock):
            edge_signals_seen: set[str] = set()
            for index, edge in enumerate(block.sensitivity_list):
                if edge.edge not in {"posedge", "negedge"}:
                    continue
                try:
                    signal_key = emit_expression(edge.signal)
                except Exception:  # pragma: no cover - defensive
                    signal_key = repr(edge.signal)
                clock_signal_keys.add(signal_key)
                if signal_key in edge_signals_seen:
                    continue
                edge_signals_seen.add(signal_key)
                if index == 0:
                    continue
                message_key = ("procedural-additional-edge-sensitive-input", signal_key)
                if message_key in seen_messages:
                    continue
                diagnostics.append(
                    RefactorDiagnostic(
                        "procedural-additional-edge-sensitive-input",
                        (
                            f"Sequential {block_label} extraction will expose additional "
                            f"edge-sensitive input {signal_key!r} as a child input port."
                        ),
                        severity="info",
                    )
                )
                seen_messages.add(message_key)

    for name in sorted(writers_by_name):
        if writers_by_name[name] < _MIN_RACE_WRITERS:
            continue
        message_key = ("multiple-selected-procedural-drivers", name)
        if message_key in seen_messages:
            continue
        diagnostics.append(
            RefactorDiagnostic(
                "multiple-selected-procedural-drivers",
                (
                    f"Signal {name!r} is assigned by more than one selected {block_label}; "
                    "lifting them together would produce a Verilog race instead of preserving "
                    "execution order."
                ),
                severity="error",
            )
        )
        seen_messages.add(message_key)

    if len(clock_signal_keys) >= _MIN_DISTINCT_CLOCKS_FOR_DOMAIN_WARNING:
        sorted_clocks = sorted(clock_signal_keys)
        diagnostics.append(
            RefactorDiagnostic(
                "procedural-multi-clock-domain",
                (
                    f"Selected {block_label} blocks span multiple clock domains "
                    f"({', '.join(repr(k) for k in sorted_clocks)}); the extracted "
                    "module will receive all clocks as inputs without a domain-crossing review."
                ),
                severity="warning",
            )
        )
    return diagnostics


def _selected_declaration_diagnostics(  # noqa: PLR0912
    module: Module,
    selected: _SelectedDeclarations,
    boundary: _Boundary,
    *,
    selected_logic_ids: set[int],
    allow_local_functions: bool = False,
) -> list[RefactorDiagnostic]:
    diagnostics: list[RefactorDiagnostic] = []
    child_function_names = {fn.name for fn in module.functions}
    for name, reason in boundary.blocked_constants:
        if reason.startswith("depends-on-signal:"):
            signal = reason.split(":", 1)[1]
            diagnostics.append(
                RefactorDiagnostic(
                    "unsupported-localparam-depends-on-signal",
                    (
                        f"Auto-copied parent localparam {name!r} transitively depends on "
                        f"signal/port {signal!r}; cannot lift into extracted child."
                    ),
                    severity="error",
                )
            )
        elif reason == "cyclic-localparam-dependency":
            diagnostics.append(
                RefactorDiagnostic(
                    "unsupported-localparam-cycle",
                    f"Auto-copied parent constant {name!r} forms a dependency cycle.",
                    severity="error",
                )
            )
        elif reason.startswith("depends-on-blocked:"):
            other = reason.split(":", 1)[1]
            diagnostics.append(
                RefactorDiagnostic(
                    "unsupported-localparam-dependency-blocked",
                    (f"Auto-copied parent constant {name!r} depends on blocked constant {other!r}."),
                    severity="error",
                )
            )
        elif reason.startswith("depends-on-subroutine:"):
            sub = reason.split(":", 1)[1]
            diagnostics.append(
                RefactorDiagnostic(
                    "unsupported-parent-constant-depends-on-subroutine",
                    (
                        f"Auto-handled parent constant {name!r} depends on user-defined "
                        f"subroutine {sub!r}; cannot lift into extracted child."
                    ),
                    severity="error",
                )
            )
        else:
            diagnostics.append(
                RefactorDiagnostic(
                    "unsupported-parent-constant",
                    f"Auto-copied parent constant {name!r} is unsupported: {reason}.",
                    severity="error",
                )
            )
    selected_parameter_names = selected.parameter_names
    outside_refs = _outside_identifier_names(module, selected_logic_ids)

    for param in selected.parameters:
        sub_refs = _subroutine_refs(param)
        unsupported_sub_refs = [
            name for name in sub_refs if not (allow_local_functions and name in child_function_names)
        ]
        if unsupported_sub_refs:
            diagnostics.append(
                RefactorDiagnostic(
                    "unsupported-parent-constant-depends-on-subroutine",
                    (
                        f"Selected {'localparam' if param.is_local else 'parameter'} "
                        f"{param.name!r} depends on user-defined subroutine "
                        f"{unsupported_sub_refs[0]!r}; cannot lift into extracted child."
                    ),
                    severity="error",
                )
            )
        if param.is_local and param.name in outside_refs:
            diagnostics.append(
                RefactorDiagnostic(
                    "selected-declaration-used-outside",
                    f"Selected localparam {param.name!r} is still referenced outside the selected logic.",
                    severity="error",
                )
            )
        external_refs = _identifier_names(param) - selected_parameter_names
        if external_refs:
            refs = ", ".join(sorted(external_refs))
            if param.is_local:
                diagnostics.append(
                    RefactorDiagnostic(
                        "unsupported-localparam-dependencies",
                        f"Selected localparam {param.name!r} depends on unselected name(s): {refs}.",
                        severity="error",
                    )
                )
            else:
                diagnostics.append(
                    RefactorDiagnostic(
                        "unsupported-parameter-dependencies",
                        f"Selected parameter {param.name!r} depends on unselected name(s): {refs}.",
                        severity="error",
                    )
                )

    movable_internals = set(boundary.internals)
    boundary_names = set(boundary.inputs) | set(boundary.outputs)
    for kind, declaration in [
        *((("net", net) for net in selected.nets)),
        *((("variable", variable) for variable in selected.variables)),
    ]:
        if declaration.name in movable_internals:
            continue
        if declaration.name in boundary_names:
            diagnostics.append(
                RefactorDiagnostic(
                    "selected-boundary-declaration",
                    (
                        f"Selected {kind} {declaration.name!r} stays declared in the parent "
                        "because it is a boundary signal for the extracted child."
                    ),
                    severity="info",
                )
            )
        else:
            diagnostics.append(
                RefactorDiagnostic(
                    "selected-declaration-not-in-child",
                    (
                        f"Selected {kind} {declaration.name!r} stays declared in the parent "
                        "because it is not part of the extracted logic."
                    ),
                    severity="info",
                )
            )
    if boundary.copied_localparams:
        remaining_names = _remaining_parent_usage_names(module, selected_logic_ids, selected)
        for param in boundary.copied_localparams:
            if param.name in remaining_names:
                diagnostics.append(
                    RefactorDiagnostic(
                        "extracted-localparam-copy-also-used-in-parent",
                        (
                            f"Parent localparam {param.name!r} is auto-copied into the extracted "
                            f"child but also remains referenced by parent logic; future edits to "
                            f"either copy will not propagate to the other."
                        ),
                        severity="info",
                    )
                )
    return diagnostics


def _compute_procedural_boundary(
    module: Module,
    selected: list[AlwaysBlock | InitialBlock],
    selected_declarations: _SelectedDeclarations,
) -> _Boundary:
    selected_ids = {id(block) for block in selected}
    assigned = _written_identifier_names(selected)
    read = set[str]()
    for block in selected:
        read.update(_identifier_names(block))

    outside_refs = _outside_identifier_names(module, selected_ids)
    port_names = {port.name for port in module.ports}
    outputs = {name for name in assigned if name in outside_refs or name in port_names}
    internals = set(assigned) - outputs

    constant_refs = _collect_constant_refs(
        module,
        lifted_items=tuple(selected),
        boundary_input_names=read - assigned,
        boundary_output_names=outputs,
        boundary_internal_names=internals,
    )
    classification = _classify_parent_constants(module, constant_refs, selected_declarations)
    inputs = (
        read
        - assigned
        - selected_declarations.parameter_names
        - classification.handled_names
        - {name for name, _ in classification.blocked}
    )

    ordered = _signal_order(module)
    return _Boundary(
        inputs=tuple(_ordered_names(inputs, ordered)),
        outputs=tuple(_ordered_names(outputs, ordered)),
        internals=tuple(_ordered_names(internals, ordered)),
        forwarded_parameters=classification.forwarded_parameters,
        copied_localparams=classification.copied_localparams,
        blocked_constants=classification.blocked,
    )


def _build_extracted_procedural_module(  # noqa: PLR0913
    parent: Module,
    selected: list[AlwaysBlock | InitialBlock],
    extracted_module_name: str,
    boundary: _Boundary,
    selected_declarations: _SelectedDeclarations,
    *,
    block_kind: str,
) -> Module:
    ports = [
        *[_port_for_signal(parent, name, PortDirection.INPUT) for name in boundary.inputs],
        *[_port_for_signal(parent, name, PortDirection.OUTPUT) for name in boundary.outputs],
    ]
    internals = _internal_declarations(parent, boundary, selected_declarations)
    nets = [decl for decl in internals if isinstance(decl, Net)]
    variables = [decl for decl in internals if isinstance(decl, Variable)]
    extracted = Module(
        extracted_module_name,
        parameters=_merged_child_parameters(selected_declarations, boundary),
        ports=ports,
        nets=nets,
        variables=variables,
    )
    if block_kind == "always":
        extracted.always_blocks = [copy.deepcopy(block) for block in selected]
    else:
        extracted.initial_blocks = [copy.deepcopy(block) for block in selected]
    return extracted


def _build_parent_with_extracted_procedural_instance(  # noqa: PLR0913
    parent: Module,
    selected: list[AlwaysBlock | InitialBlock],
    extracted: Module,
    instance_name: str,
    boundary: _Boundary,
    selected_declarations: _SelectedDeclarations,
    *,
    block_kind: str,
) -> Module:
    selected_ids = {id(block) for block in selected}
    transformed = copy.deepcopy(parent)
    if block_kind == "always":
        transformed.always_blocks = [
            copy.deepcopy(block) for block in parent.always_blocks if id(block) not in selected_ids
        ]
    else:
        transformed.initial_blocks = [
            copy.deepcopy(block) for block in parent.initial_blocks if id(block) not in selected_ids
        ]
    _remove_selected_declarations(transformed, selected_declarations)
    _convert_child_driven_outputs_to_parent_nets(transformed, set(boundary.outputs))
    port_names = [port.name for port in extracted.ports]
    transformed.instances = [copy.deepcopy(inst) for inst in parent.instances]
    transformed.instances.append(
        Instance(
            extracted.name,
            instance_name,
            has_parameter_override=_has_parameter_override(selected_declarations, boundary),
            parameter_bindings=_instance_parameter_bindings(selected_declarations, boundary),
            port_connections=[
                PortConnection(port_name=name, expression=Identifier(name), is_named=True) for name in port_names
            ],
        )
    )
    return transformed


def _convert_child_driven_outputs_to_parent_nets(module: Module, output_names: set[str]) -> None:
    if not output_names:
        return

    port_names = {port.name for port in module.ports}
    converted_nets: list[Net] = []
    for port in module.ports:
        if port.name in output_names and port.direction == PortDirection.OUTPUT:
            port.data_type = None

    for variable in module.variables:
        if variable.name not in output_names:
            continue
        if variable.name in port_names:
            continue
        converted_nets.append(
            Net(
                variable.name,
                width=copy.deepcopy(variable.width),
                signed=variable.signed,
                dimensions=copy.deepcopy(variable.dimensions),
                initial_value=None,
                loc=copy.deepcopy(variable.loc),
            )
        )

    module.variables = [variable for variable in module.variables if variable.name not in output_names]
    existing_nets = {net.name for net in module.nets}
    module.nets.extend(net for net in converted_nets if net.name not in existing_nets)


def _internal_declarations(
    parent: Module,
    boundary: _Boundary,
    selected_declarations: _SelectedDeclarations,
) -> list[Net | Variable]:
    declarations: list[Net | Variable] = []
    selected_by_name: dict[str, Net | Variable] = {
        **{net.name: net for net in selected_declarations.nets},
        **{variable.name: variable for variable in selected_declarations.variables},
    }
    for name in boundary.internals:
        source = selected_by_name.get(name)
        declarations.append(copy.deepcopy(source) if source is not None else _declaration_for_internal(parent, name))
    return declarations


def _remove_selected_declarations(module: Module, selected_declarations: _SelectedDeclarations) -> None:
    parameter_names = selected_declarations.moved_parameter_names
    net_names = {net.name for net in selected_declarations.nets}
    variable_names = {variable.name for variable in selected_declarations.variables}
    module.parameters = [param for param in module.parameters if param.name not in parameter_names]
    module.nets = [net for net in module.nets if net.name not in net_names]
    module.variables = [variable for variable in module.variables if variable.name not in variable_names]


def _build_extract_edit_plans(  # noqa: PLR0913
    *,
    parent: Module,
    transformed_parent: Module,
    selected_locs: list[SourceLocation | None],
    selected_declarations: _SelectedDeclarations,
    boundary: _Boundary,
    generated_module_text: str,
    generated_module_name: str,
) -> tuple[TextEditPlan, ...]:
    parent_file = parent.loc.file if parent.loc and parent.loc.file else ""
    if not parent_file:
        return ()
    lines = _source_lines(parent_file)
    if not lines:
        return ()

    edits: list[TextEditPlan] = []
    logic_edit = _build_selected_logic_replacement_edit(
        parent_file,
        selected_locs,
        transformed_parent.instances[-1] if transformed_parent.instances else None,
        lines=lines,
    )
    if logic_edit is None:
        return ()
    edits.append(logic_edit)

    edits.extend(_selected_declaration_removal_edits(parent_file, selected_declarations, lines=lines))
    edits.extend(_output_port_rewrite_edits(parent, transformed_parent, boundary, lines=lines))
    edits.extend(_output_declaration_rewrite_edits(parent, transformed_parent, boundary, lines=lines))

    generated_path = _generated_module_path(parent_file, generated_module_name)
    generated_original = ""
    generated_file = Path(generated_path)
    if generated_file.exists():
        generated_original = generated_file.read_text(encoding="utf-8", errors="replace")
    edits.append(
        TextEditPlan(
            file=generated_path,
            range={"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 0}},
            original=generated_original,
            replacement=generated_module_text,
        )
    )
    return tuple(edits)


def _build_selected_logic_replacement_edit(
    file_path: str,
    selected_locs: list[SourceLocation | None],
    instance: Instance | None,
    *,
    lines: list[str],
) -> TextEditPlan | None:
    if instance is None:
        return None
    concrete_locs = [loc for loc in selected_locs if loc is not None]
    if not concrete_locs:
        return None
    start_line = min(max(0, (loc.line or 1) - 1) for loc in concrete_locs)
    end_line = max(loc.end_line or loc.line or 1 for loc in concrete_locs)
    range_payload = _source_line_range(
        concrete_locs[0],
        lines=lines,
        start_line_override=start_line,
        end_line_override=end_line,
    )
    indent = " " * min(max(0, (loc.column or 1) - 1) for loc in concrete_locs)
    replacement = _emit_instance(instance, indent) + "\n"
    return TextEditPlan(
        file=file_path,
        range=range_payload,
        original=_source_text_for_range(file_path, range_payload, lines=lines),
        replacement=replacement,
    )


def _selected_declaration_removal_edits(
    file_path: str,
    selected_declarations: _SelectedDeclarations,
    *,
    lines: list[str],
) -> list[TextEditPlan]:
    edits: list[TextEditPlan] = []
    for declaration in (
        *selected_declarations.parameters,
        *selected_declarations.nets,
        *selected_declarations.variables,
    ):
        if isinstance(declaration, Parameter) and not declaration.is_local:
            continue
        range_payload = _source_line_range(declaration.loc, lines=lines)
        if not range_payload:
            continue
        edits.append(
            TextEditPlan(
                file=file_path,
                range=range_payload,
                original=_source_text_for_range(file_path, range_payload, lines=lines),
                replacement="",
            )
        )
    return edits


def _output_declaration_rewrite_edits(
    parent: Module,
    transformed_parent: Module,
    boundary: _Boundary,
    *,
    lines: list[str],
) -> list[TextEditPlan]:
    file_path = parent.loc.file if parent.loc and parent.loc.file else ""
    if not file_path:
        return []
    port_names = {port.name for port in parent.ports}
    edits: list[TextEditPlan] = []
    for name in boundary.outputs:
        if name in port_names:
            continue
        original_var = parent.get_variable(name)
        transformed_var = transformed_parent.get_variable(name)
        transformed_net = transformed_parent.get_net(name)
        if original_var is None or (transformed_var is None and transformed_net is None):
            continue
        range_payload = _source_line_range(original_var.loc, lines=lines)
        if not range_payload:
            continue
        indent = " " * max(0, (original_var.loc.column or 1) - 1)
        if transformed_net is not None:
            replacement = _emit_net(transformed_net, indent) + "\n"
        else:
            replacement = _emit_variable(transformed_var, indent) + "\n"
        edits.append(
            TextEditPlan(
                file=file_path,
                range=range_payload,
                original=_source_text_for_range(file_path, range_payload, lines=lines),
                replacement=replacement,
            )
        )
    return edits


def _output_port_rewrite_edits(
    parent: Module,
    transformed_parent: Module,
    boundary: _Boundary,
    *,
    lines: list[str],
) -> list[TextEditPlan]:
    file_path = parent.loc.file if parent.loc and parent.loc.file else ""
    if not file_path:
        return []
    edits: list[TextEditPlan] = []
    for name in boundary.outputs:
        original_port = parent.get_port(name)
        transformed_port = transformed_parent.get_port(name)
        if original_port is None or transformed_port is None or original_port.loc is None:
            continue
        original_text = _emit_port(original_port)
        replacement_text = _emit_port(transformed_port)
        if original_text == replacement_text:
            continue
        range_payload = _source_line_range(original_port.loc, lines=lines)
        if not range_payload:
            continue
        indent = " " * max(0, (original_port.loc.column or 1) - 1)
        edits.append(
            TextEditPlan(
                file=file_path,
                range=range_payload,
                original=_source_text_for_range(file_path, range_payload, lines=lines),
                replacement=f"{indent}{replacement_text}\n",
            )
        )
    return edits


def _generated_module_path(parent_file: str, generated_module_name: str) -> str:
    path = Path(parent_file)
    suffix = path.suffix or ".v"
    return str(path.with_name(f"{generated_module_name}{suffix}"))


def _source_lines(file_path: str) -> list[str]:
    path = Path(file_path)
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)


def _source_text_for_range(file_path: str, range_payload: dict[str, object], *, lines: list[str] | None = None) -> str:
    if lines is None:
        lines = _source_lines(file_path)
    if not lines:
        return ""
    start = range_payload.get("start", {})
    end = range_payload.get("end", {})
    if not isinstance(start, dict) or not isinstance(end, dict):
        return ""
    start_offset = _line_char_to_offset(lines, int(start.get("line", 0)), int(start.get("character", 0)))
    end_offset = _line_char_to_offset(lines, int(end.get("line", 0)), int(end.get("character", 0)))
    text = "".join(lines)
    return text[start_offset:end_offset]


def _line_char_to_offset(lines: list[str], line: int, character: int) -> int:
    clamped_line = max(0, min(line, len(lines)))
    offset = sum(len(entry) for entry in lines[:clamped_line])
    if clamped_line >= len(lines):
        return offset
    return offset + max(0, min(character, len(lines[clamped_line])))


def _diff_for_edit_plans(edits: tuple[TextEditPlan, ...]) -> str:
    grouped = _group_edits_by_file(edits)
    diffs: list[str] = []
    for file_path, file_edits in grouped.items():
        lines = _source_lines(file_path)
        before = "".join(lines)
        if not lines and Path(file_path).exists():
            before = Path(file_path).read_text(encoding="utf-8", errors="replace")
            lines = before.splitlines(keepends=True)
        after = _apply_edit_plans(before, file_edits)
        diffs.append(_unified_diff(file_path, before, after))
    return "\n".join(diff for diff in diffs if diff)


def _compute_boundary(
    module: Module,
    selected: list[ContinuousAssign],
    selected_declarations: _SelectedDeclarations,
) -> _Boundary:
    selected_ids = {id(assign) for assign in selected}
    assigned = {_simple_identifier_name(assign.lhs) for assign in selected}
    assigned.discard(None)
    read = set[str]()
    for assign in selected:
        read.update(_identifier_names(assign.rhs))

    outside_refs = _outside_identifier_names(module, selected_ids)
    port_names = {port.name for port in module.ports}
    outputs = {name for name in assigned if name in outside_refs or name in port_names}
    internals = set(assigned) - outputs

    constant_refs = _collect_constant_refs(
        module,
        lifted_items=tuple(selected),
        boundary_input_names=read - internals,
        boundary_output_names=outputs,
        boundary_internal_names=internals,
    )
    classification = _classify_parent_constants(module, constant_refs, selected_declarations)
    inputs = (
        read
        - internals
        - selected_declarations.parameter_names
        - classification.handled_names
        - {name for name, _ in classification.blocked}
    )

    ordered = _signal_order(module)
    return _Boundary(
        inputs=tuple(_ordered_names(inputs, ordered)),
        outputs=tuple(_ordered_names(outputs, ordered)),
        internals=tuple(_ordered_names(internals, ordered)),
        forwarded_parameters=classification.forwarded_parameters,
        copied_localparams=classification.copied_localparams,
        blocked_constants=classification.blocked,
    )


def _compute_instance_boundary(  # noqa: PLR0912
    design: Design,
    module: Module,
    selected: list[Instance],
    selected_declarations: _SelectedDeclarations,
) -> _Boundary:
    selected_ids = {id(inst) for inst in selected}
    parent_param_names = _module_parameter_names(module)
    read = set[str]()
    written = set[str]()
    complex_outputs: list[_ComplexOutput] = []
    used_synth_names: set[str] = {port.name for port in module.ports}
    used_synth_names.update(net.name for net in module.nets)
    used_synth_names.update(var.name for var in module.variables)

    for inst_index, inst in enumerate(selected):
        resolved, port_connections, _diagnostics = _resolved_instance_connections(design, inst)
        for port, connection in port_connections:
            if port.direction == PortDirection.INPUT:
                classification = _classify_input_connection(connection.expression, parent_param_names)
                if not classification.supported or classification.has_hierarchical:
                    continue
                if classification.param_refs - selected_declarations.parameter_names:
                    continue
                read.update(classification.signal_reads)
            elif port.direction == PortDirection.OUTPUT:
                signal_name = _simple_identifier_name(connection.expression)
                if signal_name is not None:
                    written.add(signal_name)
                    continue
                if connection.expression is None:
                    continue
                output_classification = _classify_output_connection(connection.expression, parent_param_names)
                if not output_classification.supported or output_classification.has_hierarchical:
                    continue
                if output_classification.param_refs - selected_declarations.parameter_names:
                    continue
                inner_port = resolved.get_port(port.name) if resolved is not None else None
                if inner_port is None or not _port_width_is_self_contained(inner_port):
                    continue
                synthetic_name = _allocate_synthetic_port_name(inst.instance_name, port.name, used_synth_names)
                try:
                    connection_index = inst.port_connections.index(connection)
                except ValueError:
                    continue
                complex_outputs.append(
                    _ComplexOutput(
                        synthetic_port_name=synthetic_name,
                        parent_expression=connection.expression,
                        inner_port=inner_port,
                        selected_instance_index=inst_index,
                        connection_index=connection_index,
                    )
                )

    outside_refs = _outside_identifier_names(module, selected_ids)
    port_names = {port.name for port in module.ports}
    outputs = {name for name in written if name in outside_refs or name in port_names}
    internals = set(written) - outputs

    # Collect parent-parameter references from the instances (input
    # connections, parameter bindings, and complex output expressions) so the
    # auto-handling machinery can also resolve unselected parent constants for
    # the instance/mixed flows.
    instance_const_refs: set[str] = set()
    for inst in selected:
        for binding in inst.parameter_bindings or []:
            instance_const_refs.update(_identifier_names(binding.value))
        for connection in inst.port_connections:
            instance_const_refs.update(_identifier_names(connection.expression))
    instance_const_refs &= parent_param_names

    constant_refs = (
        _collect_constant_refs(
            module,
            lifted_items=tuple(selected),
            boundary_input_names=read - internals,
            boundary_output_names=outputs,
            boundary_internal_names=internals,
        )
        | instance_const_refs
    )
    classification = _classify_parent_constants(module, constant_refs, selected_declarations)
    inputs = (
        read
        - written
        - selected_declarations.parameter_names
        - classification.handled_names
        - {name for name, _ in classification.blocked}
    )

    ordered = _signal_order(module)
    return _Boundary(
        inputs=tuple(_ordered_names(inputs, ordered)),
        outputs=tuple(_ordered_names(outputs, ordered)),
        internals=tuple(_ordered_names(internals, ordered)),
        complex_outputs=tuple(complex_outputs),
        forwarded_parameters=classification.forwarded_parameters,
        copied_localparams=classification.copied_localparams,
        blocked_constants=classification.blocked,
    )


def _compute_mixed_structural_boundary(  # noqa: PLR0912, PLR0915
    design: Design,
    module: Module,
    selected_assigns: list[ContinuousAssign],
    selected_instances: list[Instance],
    selected_declarations: _SelectedDeclarations,
) -> _Boundary:
    selected_ids = {id(assign) for assign in selected_assigns} | {id(inst) for inst in selected_instances}
    parent_param_names = _module_parameter_names(module)
    written = {_simple_identifier_name(assign.lhs) for assign in selected_assigns}
    written.discard(None)
    read = set[str]()
    for assign in selected_assigns:
        read.update(_identifier_names(assign.rhs))

    complex_outputs: list[_ComplexOutput] = []
    used_synth_names: set[str] = {port.name for port in module.ports}
    used_synth_names.update(net.name for net in module.nets)
    used_synth_names.update(var.name for var in module.variables)

    for inst_index, inst in enumerate(selected_instances):
        resolved, port_connections, _diagnostics = _resolved_instance_connections(design, inst)
        for port, connection in port_connections:
            if port.direction == PortDirection.INPUT:
                classification = _classify_input_connection(connection.expression, parent_param_names)
                if not classification.supported or classification.has_hierarchical:
                    continue
                if classification.param_refs - selected_declarations.parameter_names:
                    continue
                read.update(classification.signal_reads)
            elif port.direction == PortDirection.OUTPUT:
                signal_name = _simple_identifier_name(connection.expression)
                if signal_name is not None:
                    written.add(signal_name)
                    continue
                if connection.expression is None:
                    continue
                output_classification = _classify_output_connection(connection.expression, parent_param_names)
                if not output_classification.supported or output_classification.has_hierarchical:
                    continue
                if output_classification.param_refs - selected_declarations.parameter_names:
                    continue
                inner_port = resolved.get_port(port.name) if resolved is not None else None
                if inner_port is None or not _port_width_is_self_contained(inner_port):
                    continue
                synthetic_name = _allocate_synthetic_port_name(inst.instance_name, port.name, used_synth_names)
                try:
                    connection_index = inst.port_connections.index(connection)
                except ValueError:
                    continue
                complex_outputs.append(
                    _ComplexOutput(
                        synthetic_port_name=synthetic_name,
                        parent_expression=connection.expression,
                        inner_port=inner_port,
                        selected_instance_index=inst_index,
                        connection_index=connection_index,
                    )
                )

    outside_refs = _outside_identifier_names(module, selected_ids)
    port_names = {port.name for port in module.ports}
    outputs = {name for name in written if name in outside_refs or name in port_names}
    internals = set(written) - outputs

    instance_const_refs: set[str] = set()
    for inst in selected_instances:
        for binding in inst.parameter_bindings or []:
            instance_const_refs.update(_identifier_names(binding.value))
        for connection in inst.port_connections:
            instance_const_refs.update(_identifier_names(connection.expression))
    instance_const_refs &= parent_param_names

    constant_refs = (
        _collect_constant_refs(
            module,
            lifted_items=(*selected_assigns, *selected_instances),
            boundary_input_names=read - internals,
            boundary_output_names=outputs,
            boundary_internal_names=internals,
        )
        | instance_const_refs
    )
    classification = _classify_parent_constants(module, constant_refs, selected_declarations)
    inputs = (
        read
        - written
        - selected_declarations.parameter_names
        - classification.handled_names
        - {name for name, _ in classification.blocked}
    )

    ordered = _signal_order(module)
    return _Boundary(
        inputs=tuple(_ordered_names(inputs, ordered)),
        outputs=tuple(_ordered_names(outputs, ordered)),
        internals=tuple(_ordered_names(internals, ordered)),
        complex_outputs=tuple(complex_outputs),
        forwarded_parameters=classification.forwarded_parameters,
        copied_localparams=classification.copied_localparams,
        blocked_constants=classification.blocked,
    )


def _build_extracted_instance_group_module(
    parent: Module,
    selected: list[Instance],
    extracted_module_name: str,
    boundary: _Boundary,
    selected_declarations: _SelectedDeclarations,
) -> Module:
    ports = [
        *[_port_for_signal(parent, name, PortDirection.INPUT) for name in boundary.inputs],
        *[_port_for_signal(parent, name, PortDirection.OUTPUT) for name in boundary.outputs],
        *[_synthetic_output_port(entry) for entry in boundary.complex_outputs],
    ]
    internals = _internal_declarations(parent, boundary, selected_declarations)
    nets = [decl for decl in internals if isinstance(decl, Net)]
    variables = [decl for decl in internals if isinstance(decl, Variable)]
    new_instances = [copy.deepcopy(inst) for inst in selected]
    _rewrite_complex_output_connections(new_instances, boundary.complex_outputs)
    return Module(
        extracted_module_name,
        parameters=_merged_child_parameters(selected_declarations, boundary),
        ports=ports,
        nets=nets,
        variables=variables,
        instances=new_instances,
    )


def _build_extracted_mixed_structural_module(  # noqa: PLR0913
    parent: Module,
    selected_assigns: list[ContinuousAssign],
    selected_instances: list[Instance],
    extracted_module_name: str,
    boundary: _Boundary,
    selected_declarations: _SelectedDeclarations,
) -> Module:
    ports = [
        *[_port_for_signal(parent, name, PortDirection.INPUT) for name in boundary.inputs],
        *[_port_for_signal(parent, name, PortDirection.OUTPUT) for name in boundary.outputs],
        *[_synthetic_output_port(entry) for entry in boundary.complex_outputs],
    ]
    internals = _internal_declarations(parent, boundary, selected_declarations)
    nets = [decl for decl in internals if isinstance(decl, Net)]
    variables = [decl for decl in internals if isinstance(decl, Variable)]
    new_instances = [copy.deepcopy(inst) for inst in selected_instances]
    _rewrite_complex_output_connections(new_instances, boundary.complex_outputs)
    return Module(
        extracted_module_name,
        parameters=_merged_child_parameters(selected_declarations, boundary),
        ports=ports,
        nets=nets,
        variables=variables,
        instances=new_instances,
        continuous_assigns=[copy.deepcopy(assign) for assign in selected_assigns],
    )


def _merged_child_parameters(
    selected_declarations: _SelectedDeclarations,
    boundary: _Boundary,
) -> list[Parameter]:
    """Combine explicitly-selected parameters with auto-handled forwarded
    parameters and copied localparams, deduping by name (selected wins)."""
    merged: list[Parameter] = []
    seen: set[str] = set()
    for param in selected_declarations.parameters:
        if param.name in seen:
            continue
        merged.append(copy.deepcopy(param))
        seen.add(param.name)
    for param in (*boundary.forwarded_parameters, *boundary.copied_localparams):
        if param.name in seen:
            continue
        merged.append(copy.deepcopy(param))
        seen.add(param.name)
    return merged


def _auto_parameter_bindings(boundary: _Boundary) -> list[ParameterBinding]:
    """Parameter bindings for auto-forwarded parent parameters (preserve override)."""
    return [ParameterBinding(name=param.name, value=Identifier(param.name)) for param in boundary.forwarded_parameters]


def _build_extracted_module(
    parent: Module,
    selected: list[ContinuousAssign],
    extracted_module_name: str,
    boundary: _Boundary,
    selected_declarations: _SelectedDeclarations,
) -> Module:
    ports = [
        *[_port_for_signal(parent, name, PortDirection.INPUT) for name in boundary.inputs],
        *[_port_for_signal(parent, name, PortDirection.OUTPUT) for name in boundary.outputs],
    ]
    internals = _internal_declarations(parent, boundary, selected_declarations)
    nets = [decl for decl in internals if isinstance(decl, Net)]
    variables = [decl for decl in internals if isinstance(decl, Variable)]
    return Module(
        extracted_module_name,
        parameters=_merged_child_parameters(selected_declarations, boundary),
        ports=ports,
        nets=nets,
        variables=variables,
        continuous_assigns=[copy.deepcopy(assign) for assign in selected],
    )


def _build_parent_with_extracted_mixed_structural_instance(  # noqa: PLR0913
    parent: Module,
    selected_assigns: list[ContinuousAssign],
    selected_instances: list[Instance],
    extracted: Module,
    instance_name: str,
    selected_declarations: _SelectedDeclarations,
    boundary: _Boundary,
) -> Module:
    selected_assign_ids = {id(assign) for assign in selected_assigns}
    selected_instance_ids = {id(inst) for inst in selected_instances}
    transformed = copy.deepcopy(parent)
    transformed.continuous_assigns = [
        copy.deepcopy(assign) for assign in parent.continuous_assigns if id(assign) not in selected_assign_ids
    ]
    transformed.instances = [copy.deepcopy(inst) for inst in parent.instances if id(inst) not in selected_instance_ids]
    _remove_selected_declarations(transformed, selected_declarations)
    transformed.instances.append(
        Instance(
            extracted.name,
            instance_name,
            has_parameter_override=_has_parameter_override(selected_declarations, boundary),
            parameter_bindings=_instance_parameter_bindings(selected_declarations, boundary),
            port_connections=_extracted_instance_port_connections(extracted, boundary),
        )
    )
    return transformed


def _build_parent_with_extracted_instance_group(  # noqa: PLR0913
    parent: Module,
    selected: list[Instance],
    extracted: Module,
    instance_name: str,
    selected_declarations: _SelectedDeclarations,
    boundary: _Boundary,
) -> Module:
    selected_ids = {id(inst) for inst in selected}
    transformed = copy.deepcopy(parent)
    transformed.instances = [copy.deepcopy(inst) for inst in parent.instances if id(inst) not in selected_ids]
    _remove_selected_declarations(transformed, selected_declarations)
    transformed.instances.append(
        Instance(
            extracted.name,
            instance_name,
            has_parameter_override=_has_parameter_override(selected_declarations, boundary),
            parameter_bindings=_instance_parameter_bindings(selected_declarations, boundary),
            port_connections=_extracted_instance_port_connections(extracted, boundary),
        )
    )
    return transformed


def _extracted_instance_port_connections(extracted: Module, boundary: _Boundary) -> list[PortConnection]:
    """Build the parent-side port connections for the new outer extracted instance.

    Plain inputs/outputs are connected by name (``.<name>(<name>)``); synthetic
    output ports created for complex instance connections (slices/concat) are
    bound to the original parent expression.
    """
    synthetic_exprs = {entry.synthetic_port_name: entry.parent_expression for entry in boundary.complex_outputs}
    connections: list[PortConnection] = []
    for port in extracted.ports:
        if port.name in synthetic_exprs:
            expression = copy.deepcopy(synthetic_exprs[port.name])
        else:
            expression = Identifier(port.name)
        connections.append(PortConnection(port_name=port.name, expression=expression, is_named=True))
    return connections


def _build_parent_with_extracted_instance(  # noqa: PLR0913
    parent: Module,
    selected: list[ContinuousAssign],
    extracted: Module,
    instance_name: str,
    boundary: _Boundary,
    selected_declarations: _SelectedDeclarations,
) -> Module:
    selected_ids = {id(assign) for assign in selected}
    transformed = copy.deepcopy(parent)
    transformed.continuous_assigns = [
        copy.deepcopy(assign) for assign in parent.continuous_assigns if id(assign) not in selected_ids
    ]
    _remove_selected_declarations(transformed, selected_declarations)
    transformed.instances = [copy.deepcopy(inst) for inst in parent.instances]
    port_names = [port.name for port in extracted.ports]
    transformed.instances.append(
        Instance(
            extracted.name,
            instance_name,
            has_parameter_override=_has_parameter_override(selected_declarations, boundary),
            parameter_bindings=_instance_parameter_bindings(selected_declarations, boundary),
            port_connections=[
                PortConnection(port_name=name, expression=Identifier(name), is_named=True) for name in port_names
            ],
        )
    )
    return transformed


def _instance_parameter_bindings(
    selected_declarations: _SelectedDeclarations,
    boundary: _Boundary | None = None,
) -> list[ParameterBinding]:
    bindings = [
        ParameterBinding(name=param.name, value=Identifier(param.name))
        for param in selected_declarations.parameters
        if not param.is_local
    ]
    seen = {b.name for b in bindings}
    if boundary is not None:
        for param in boundary.forwarded_parameters:
            if param.name in seen:
                continue
            bindings.append(ParameterBinding(name=param.name, value=Identifier(param.name)))
            seen.add(param.name)
    return bindings


def _has_parameter_override(
    selected_declarations: _SelectedDeclarations,
    boundary: _Boundary,
) -> bool:
    return bool(selected_declarations.inherited_parameter_names) or bool(boundary.forwarded_parameters)


def _synthetic_output_port(entry: _ComplexOutput) -> Port:
    """Build the synthetic output port for the extracted child module from an inner-port template."""
    inner = entry.inner_port
    return Port(
        entry.synthetic_port_name,
        PortDirection.OUTPUT,
        net_type=inner.net_type,
        data_type=inner.data_type,
        width=copy.deepcopy(inner.width),
        signed=inner.signed,
    )


def _rewrite_complex_output_connections(
    new_instances: list[Instance],
    complex_outputs: tuple[_ComplexOutput, ...],
) -> None:
    """Rewrite the deep-copied instances so complex output connections drive the synthetic port wire."""
    for entry in complex_outputs:
        inst = new_instances[entry.selected_instance_index]
        connection = inst.port_connections[entry.connection_index]
        connection.expression = Identifier(entry.synthetic_port_name)


def _port_for_signal(module: Module, name: str, direction: PortDirection) -> Port:
    source = module.get_port(name)
    if source is not None:
        port = copy.deepcopy(source)
        port.direction = direction
        if direction == PortDirection.INPUT:
            port.data_type = None
        return port

    net = _module_net_declaration(module, name)
    if net is not None:
        return Port(name, direction, net_type=net.kind.value, width=copy.deepcopy(net.width), signed=net.signed)

    variable = _module_variable_declaration(module, name)
    if variable is not None:
        return Port(
            name, direction, data_type=variable.kind.value, width=copy.deepcopy(variable.width), signed=variable.signed
        )

    return Port(name, direction)


def _declaration_for_internal(module: Module, name: str) -> Net | Variable:
    net = _module_net_declaration(module, name)
    if net is not None:
        return copy.deepcopy(net)
    variable = _module_variable_declaration(module, name)
    if variable is not None:
        return copy.deepcopy(variable)
    return Net(name)


def _outside_identifier_names(module: Module, selected_ids: set[int]) -> set[str]:
    names: set[str] = set()
    for assign in module.find(ContinuousAssign):
        if id(assign) in selected_ids:
            continue
        names.update(_identifier_names(assign.lhs))
        names.update(_identifier_names(assign.rhs))
    for inst in module.find(Instance):
        if id(inst) in selected_ids:
            continue
        names.update(_identifier_names(inst))
    for block in [*module.find(AlwaysBlock), *module.find(InitialBlock)]:
        if id(block) in selected_ids:
            continue
        names.update(_identifier_names(block))
    return names


def _remaining_parent_usage_names(
    module: Module,
    selected_logic_ids: set[int],
    selected_declarations: _SelectedDeclarations,
) -> set[str]:
    """Identifier names referenced by parent items that will REMAIN after the
    extraction — unselected logic plus unselected parameter/localparam
    default+width expressions. Used to detect copied-localparam value drift.
    """
    names = _outside_identifier_names(module, selected_logic_ids)
    selected_param_ids = {id(p) for p in selected_declarations.parameters}
    for param in module.parameters:
        if id(param) in selected_param_ids:
            continue
        if param.default_value is not None:
            names.update(_identifier_names(param.default_value))
        if param.width is not None:
            names.update(_identifier_names(param.width.msb))
            names.update(_identifier_names(param.width.lsb))
    return names


def _outside_written_identifier_names(module: Module, selected_ids: set[int]) -> set[str]:
    names: set[str] = set()
    for assign in module.find(ContinuousAssign):
        if id(assign) in selected_ids:
            continue
        lhs = _simple_identifier_name(assign.lhs)
        if lhs is not None:
            names.add(lhs)
    for block in [*module.find(AlwaysBlock), *module.find(InitialBlock)]:
        if id(block) in selected_ids:
            continue
        names.update(_written_identifier_names([block]))
    return names


def _outside_instance_output_names(design: Design, module: Module, selected_ids: set[int]) -> set[str]:
    names: set[str] = set()
    for inst in module.find(Instance):
        if id(inst) in selected_ids:
            continue
        _resolved, port_connections, _diagnostics = _resolved_instance_connections(design, inst)
        for port, connection in port_connections:
            if port.direction != PortDirection.OUTPUT:
                continue
            signal_name = _simple_identifier_name(connection.expression)
            if signal_name is not None:
                names.add(signal_name)
    return names


def _mixed_structural_driver_diagnostics(
    design: Design,
    selected_assigns: list[ContinuousAssign],
    selected_instances: list[Instance],
) -> list[RefactorDiagnostic]:
    assign_written = {_simple_identifier_name(assign.lhs) for assign in selected_assigns}
    assign_written.discard(None)
    instance_written = set[str]()
    for inst in selected_instances:
        _resolved, port_connections, _diagnostics = _resolved_instance_connections(design, inst)
        for port, connection in port_connections:
            if port.direction != PortDirection.OUTPUT:
                continue
            signal_name = _simple_identifier_name(connection.expression)
            if signal_name is not None:
                instance_written.add(signal_name)
    return [
        RefactorDiagnostic(
            "multiple-selected-drivers",
            f"Signal {name!r} is driven by both selected continuous assignments and selected instances.",
            severity="error",
        )
        for name in sorted(assign_written & instance_written)
    ]


def _resolved_instance_connections(
    design: Design, inst: Instance
) -> tuple[Module | None, list[tuple[Port, PortConnection]], list[RefactorDiagnostic]]:
    diagnostics: list[RefactorDiagnostic] = []
    resolved = design.get_module(inst.module_name)
    if resolved is None:
        diagnostics.append(
            RefactorDiagnostic(
                "unresolved-selected-instance",
                f"Selected instance {inst.instance_name!r} references unknown module {inst.module_name!r}.",
                severity="error",
            )
        )
        return None, [], diagnostics

    port_connections: list[tuple[Port, PortConnection]] = []
    for index, connection in enumerate(inst.port_connections):
        if connection.is_named:
            if connection.port_name is None:
                diagnostics.append(
                    RefactorDiagnostic(
                        "unresolved-instance-port",
                        f"Selected instance {inst.instance_name!r} has a named connection without a port name.",
                        severity="error",
                    )
                )
                continue
            port = resolved.get_port(connection.port_name)
        else:
            port = resolved.ports[index] if index < len(resolved.ports) else None
        if port is None:
            port_label = connection.port_name if connection.port_name is not None else f"#{index}"
            diagnostics.append(
                RefactorDiagnostic(
                    "unresolved-instance-port",
                    f"Selected instance {inst.instance_name!r} references unknown port {port_label!r} on module {inst.module_name!r}.",
                    severity="error",
                )
            )
            continue
        port_connections.append((port, connection))
    return resolved, port_connections, diagnostics
