"""Pull-up hierarchy boundary move engine."""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path

from ..codegen import emit_module
from ..model.assignments import ContinuousAssign
from ..model.behavioral import AlwaysBlock, InitialBlock
from ..model.base import SourceLocation, VerilogNode
from ..model.design import Design, Module
from ..model.expressions import Expression, FunctionCall, Identifier, Range
from ..model.functions import FunctionDecl
from ..model.generate import GenerateBlock, GenerateCase, GenerateFor, GenerateIf, GenvarDecl
from ..model.instances import Instance, ParameterBinding, PortConnection
from ..model.nets import Net
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.variables import Variable, VariableKind

from .diagnostics import RefactorDiagnostic
from ._refactor_utils import (
    TextEditPlan,
    _apply_text_edits,
    _group_edits_by_file,
    _line_offsets,
    _loc_range,
    _simple_identifier_name,
)
from ._extract_models import ExtractSelection, _Boundary
from ._extract_classify import (
    _identifier_names,
    _module_parameter_names,
    _ordered_names,
    _signal_order,
    _subroutine_refs,
)
from .hierarchy_extract import (
    _compute_boundary,
    _compute_mixed_structural_boundary,
    _compute_procedural_boundary,
    _convert_child_driven_outputs_to_parent_nets,
    _declaration_for_internal,
    _instance_selection_diagnostics,
    _mixed_structural_driver_diagnostics,
    _outside_identifier_names,
    _port_for_signal,
    _procedural_selection_diagnostics,
    _selected_always_blocks,
    _selected_continuous_assigns,
    _selected_declaration_diagnostics,
    _selected_declarations,
    _selected_initial_blocks,
    _selected_instances,
    _selection_diagnostics,
    normalize_extract_selection,
)
from ._boundary_models import (
    BoundaryEndpoint,
    BoundaryMovePreview,
    BoundaryMoveRequest,
    BoundaryMoveSelection,
    _MIN_INSTANCE_PATH_PARTS,
)
from ._boundary_selection import (
    _instance_endpoint,
    _instance_source_and_range,
    _line_char_offset,
    _loc_contains_selection,
    _loc_span,
    _module_endpoint,
    _module_item_summary,
    _node_source_and_range,
    _offset_to_position,
    _resolve_pull_up_range_selection,
    _source_line_range,
    _source_text_for_range_payload,
)


def _preview_pull_up_range(design: Design, request: BoundaryMoveRequest) -> BoundaryMovePreview:
    resolved = _resolve_pull_up_range_selection(design, request.selection)
    if isinstance(resolved, RefactorDiagnostic):
        if resolved.code not in {"mixed-selection-unsupported", "no-pull-up-instance-selection"}:
            return BoundaryMovePreview(request=request, confidence="blocked", diagnostics=(resolved,))
        return _preview_pull_up_child_range(design, request)

    parent_module, selected_instance, selected_module = resolved
    if _count_module_instance_sites(design, parent_module.name) > 0:
        return _preview_pull_up_child_range(design, request)
    source_path = f"{parent_module.name}/{selected_instance.instance_name}"
    source = _instance_endpoint(source_path, selected_instance)
    parent = _module_endpoint(parent_module, parent_module.name)
    rewrite = _build_local_pull_up_edit(parent_module, selected_instance, selected_module)
    return _pull_up_preview_from_rewrite(request, source, parent, selected_module, rewrite)


def _preview_pull_up(
    design: Design,
    request: BoundaryMoveRequest,
    source: BoundaryEndpoint,
    parent: BoundaryEndpoint | None,
    selected_module: Module,
) -> BoundaryMovePreview:
    if parent is None:
        return BoundaryMovePreview(
            request=request,
            confidence="blocked",
            source=source,
            diagnostics=(
                RefactorDiagnostic(
                    "parent-context-required",
                    "Pull-up boundary moves require an instance or subtree selection with a resolved parent.",
                    severity="error",
                ),
            ),
        )

    if request.target_parent_path:
        target_check = _validate_target_parent_on_path(request.target_parent_path, request.selection.instance_path)
        if target_check is not None:
            return BoundaryMovePreview(
                request=request,
                confidence="blocked",
                source=source,
                parent=parent,
                diagnostics=(target_check,),
            )

    rewrite = _build_pull_up_edit(design, request, source, parent, selected_module)
    return _pull_up_preview_from_rewrite(request, source, parent, selected_module, rewrite)


def _pull_up_preview_from_rewrite(
    request: BoundaryMoveRequest,
    source: BoundaryEndpoint,
    parent: BoundaryEndpoint,
    selected_module: Module,
    rewrite: tuple[TextEditPlan, ...] | RefactorDiagnostic,
) -> BoundaryMovePreview:
    edits: tuple[TextEditPlan, ...] = ()
    diagnostics: tuple[RefactorDiagnostic, ...] = ()
    metadata: dict[str, object] = {"rewriteStatus": "not-implemented", "applyBlockedReason": "preview-contract-only"}
    if isinstance(rewrite, tuple):
        edits = rewrite
        metadata = {"rewriteStatus": "apply-ready"}
    elif isinstance(rewrite, RefactorDiagnostic):
        diagnostics = (rewrite,)
        metadata = {"rewriteStatus": "not-implemented", "applyBlockedReason": rewrite.code}

    return BoundaryMovePreview(
        request=request,
        confidence="safe" if edits else "planning",
        diagnostics=diagnostics,
        source=source,
        parent=parent,
        before_hierarchy={
            "selectedPath": source.instance_path,
            "parentPath": parent.instance_path,
            "selectedModule": source.module_name,
        },
        after_hierarchy={
            "removedInstancePath": source.instance_path,
            "mergedIntoPath": parent.instance_path,
            "movedModule": source.module_name,
        },
        moved_items=_module_item_summary(selected_module),
        edits=edits,
        metadata=metadata,
    )


def _build_pull_up_edit(
    design: Design,
    request: BoundaryMoveRequest,
    source: BoundaryEndpoint,
    parent_endpoint: BoundaryEndpoint,
    selected_module: Module,
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic:
    chain = _resolve_pull_up_chain(design, request.selection.instance_path, request.target_parent_path)
    if isinstance(chain, RefactorDiagnostic):
        return chain
    if not chain:
        return RefactorDiagnostic(
            "instance-not-found", f"Instance path not found: {source.instance_path}.", severity="error"
        )
    return _build_pull_up_edit_from_chain(chain, selected_module, parent_endpoint.module_name)


def _build_local_pull_up_edit(
    parent_module: Module,
    selected_instance: Instance,
    selected_module: Module,
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic:
    return _build_pull_up_edit_from_chain(
        [(parent_module, selected_instance, selected_module)],
        selected_module,
        parent_module.name,
    )


def _blocked_preview(
    request: BoundaryMoveRequest,
    diagnostics: tuple[RefactorDiagnostic, ...],
    *,
    source: BoundaryEndpoint | None = None,
    metadata: dict[str, object] | None = None,
) -> BoundaryMovePreview:
    """Return a blocked BoundaryMovePreview with the given diagnostics."""
    kw: dict[str, object] = {"request": request, "confidence": "blocked", "diagnostics": diagnostics}
    if source is not None:
        kw["source"] = source
    if metadata is not None:
        kw["metadata"] = metadata
    return BoundaryMovePreview(**kw)  # type: ignore[arg-type]


def _maybe_blocked_on_errors(
    diagnostics: list[RefactorDiagnostic],
    request: BoundaryMoveRequest,
    source: BoundaryEndpoint,
    metadata: dict[str, object],
) -> BoundaryMovePreview | None:
    """Return a blocked preview if any diagnostic has severity='error', else None."""
    if any(diag.severity == "error" for diag in diagnostics):
        return _blocked_preview(request, tuple(diagnostics), source=source, metadata=metadata)
    return None


def _store_boundary_in_metadata(metadata: dict[str, object], boundary: _Boundary) -> None:
    """Store boundary inputs/outputs/internals in the metadata dict."""
    metadata["boundary"] = {
        "inputs": list(boundary.inputs),
        "outputs": list(boundary.outputs),
        "internals": list(boundary.internals),
    }


def _preview_pull_up_child_range(  # noqa: PLR0911
    design: Design, request: BoundaryMoveRequest
) -> BoundaryMovePreview:
    selection = request.selection
    child_candidates = [
        module for module in design.modules if _loc_contains_selection(getattr(module, "loc", None), selection)
    ]
    if not child_candidates:
        return _blocked_preview(
            request,
            (
                RefactorDiagnostic(
                    "selection-module-not-found",
                    "Could not resolve the selected source range to a containing module.",
                    severity="error",
                ),
            ),
        )
    child_module = min(child_candidates, key=lambda module: _loc_span(getattr(module, "loc", None)))
    extract_selection = ExtractSelection(
        file=selection.file,
        start_line=selection.start_line,
        end_line=selection.end_line,
    )
    normalized = normalize_extract_selection(child_module, extract_selection, allow_generate_nested=True)
    source = BoundaryEndpoint(
        module_name=child_module.name,
        file=selection.file,
        range={"startLine": selection.start_line, "endLine": selection.end_line},
    )
    if normalized.diagnostics:
        return _blocked_preview(
            request,
            tuple(normalized.diagnostics),
            source=source,
            metadata={"selectionNormalization": normalized.to_dict(), "scope": "design-wide"},
        )

    selected_instances = _selected_instances(child_module, extract_selection, allow_generate_nested=True)
    selected_assigns = _selected_continuous_assigns(child_module, extract_selection, allow_generate_nested=True)
    selected_always = _selected_always_blocks(child_module, extract_selection, allow_generate_nested=True)
    selected_initial = _selected_initial_blocks(child_module, extract_selection, allow_generate_nested=True)
    if (selected_always and selected_initial) or (
        (selected_always or selected_initial) and (selected_assigns or selected_instances)
    ):
        return _blocked_preview(
            request,
            (
                RefactorDiagnostic(
                    "mixed-selection-unsupported",
                    (
                        "Design-wide hierarchy-up supports structural selections "
                        "(continuous assigns and/or instances) or one procedural category at a time."
                    ),
                    severity="error",
                ),
            ),
            source=source,
            metadata={"selectionNormalization": normalized.to_dict(), "scope": "design-wide"},
        )
    selected_blocks = selected_always or selected_initial
    if not selected_blocks and not selected_assigns and not selected_instances:
        diagnostics: tuple[RefactorDiagnostic, ...]
        if normalized.items:
            diagnostics = (
                RefactorDiagnostic(
                    "unsupported-selected-nodes",
                    (
                        "Selection maps to complete semantic nodes, but design-wide hierarchy-up currently only "
                        "supports structural selections (continuous assigns / instances) and always/initial blocks."
                    ),
                    severity="error",
                ),
            )
        else:
            diagnostics = (
                RefactorDiagnostic(
                    "no-pull-up-child-selection",
                    (
                        "Selection does not contain any complete continuous assignments, instances, or "
                        "always/initial blocks to move upward."
                    ),
                    severity="error",
                ),
            )
        return _blocked_preview(
            request,
            diagnostics,
            source=source,
            metadata={"selectionNormalization": normalized.to_dict(), "scope": "design-wide"},
        )

    selected_declarations = _selected_declarations(child_module, extract_selection, allow_generate_nested=True)

    sites = _collect_all_module_instance_sites(design, child_module.name)
    if not sites:
        return _blocked_preview(
            request,
            (
                RefactorDiagnostic(
                    "parent-instance-sites-not-found",
                    f"Module {child_module.name!r} is not instantiated anywhere in the design.",
                    severity="error",
                ),
            ),
            source=source,
            metadata={"selectionNormalization": normalized.to_dict(), "scope": "design-wide"},
        )

    metadata = {
        "scope": "design-wide",
        "selectionNormalization": normalized.to_dict(),
        "selectedDeclarations": selected_declarations.to_dict(),
        "selectedAssignments": len(selected_assigns),
        "selectedInstances": len(selected_instances),
        "selectedAlwaysBlocks": len(selected_always),
        "selectedInitialBlocks": len(selected_initial),
        "siteCount": len(sites),
        "parentModules": sorted({parent.name for parent, _inst in sites}),
        "sitePaths": [f"{parent.name}/{inst.instance_name}" for parent, inst in sites],
    }
    if selected_instances:
        selected_logic_ids = {id(assign) for assign in selected_assigns} | {id(inst) for inst in selected_instances}
        boundary = _compute_mixed_structural_boundary(
            design,
            child_module,
            selected_assigns,
            selected_instances,
            selected_declarations,
        )
        boundary = _augment_structural_boundary_for_complex_outputs(child_module, selected_logic_ids, boundary)
        _store_boundary_in_metadata(metadata, boundary)
        diagnostics = [
            *_selection_diagnostics(child_module, selected_assigns),
            *_instance_selection_diagnostics(
                design,
                child_module,
                selected_instances,
                selected_declarations,
                selected_logic_ids=selected_logic_ids,
                selection_label="selected child structural logic",
            ),
            *_mixed_structural_driver_diagnostics(design, selected_assigns, selected_instances),
            *_selected_declaration_diagnostics(
                child_module,
                selected_declarations,
                boundary,
                selected_logic_ids=selected_logic_ids,
                allow_local_functions=True,
            ),
            *_child_range_pull_up_diagnostics(child_module, [*selected_assigns, *selected_instances], boundary, sites),
        ]
        blocked = _maybe_blocked_on_errors(diagnostics, request, source, metadata)
        if blocked is not None:
            return blocked
        rewrite = _build_design_wide_pull_up_from_child_structural(
            design,
            child_module,
            selected_assigns,
            selected_instances,
            selected_declarations,
            boundary,
            sites=sites,
        )
        return _pull_up_child_range_preview_from_rewrite(
            request,
            source,
            child_module,
            rewrite,
            metadata,
        )
    if selected_assigns:
        boundary = _compute_boundary(child_module, selected_assigns, selected_declarations)
        _store_boundary_in_metadata(metadata, boundary)
        diagnostics = [
            *_selection_diagnostics(child_module, selected_assigns),
            *_selected_declaration_diagnostics(
                child_module,
                selected_declarations,
                boundary,
                selected_logic_ids={id(assign) for assign in selected_assigns},
                allow_local_functions=True,
            ),
            *_child_range_pull_up_diagnostics(child_module, selected_assigns, boundary, sites),
        ]
        blocked = _maybe_blocked_on_errors(diagnostics, request, source, metadata)
        if blocked is not None:
            return blocked
        rewrite = _build_design_wide_pull_up_from_child_assigns(
            design,
            child_module,
            selected_assigns,
            selected_declarations,
            boundary,
            sites=sites,
        )
        return _pull_up_child_range_preview_from_rewrite(
            request,
            source,
            child_module,
            rewrite,
            metadata,
        )

    block_kind = "always" if selected_always else "initial"
    boundary = _compute_procedural_boundary(child_module, selected_blocks, selected_declarations)
    _store_boundary_in_metadata(metadata, boundary)
    diagnostics = [
        *_procedural_selection_diagnostics(
            child_module,
            selected_blocks,
            block_label=f"{block_kind}-block",
            allow_local_functions=True,
        ),
        *_selected_declaration_diagnostics(
            child_module,
            selected_declarations,
            boundary,
            selected_logic_ids={id(block) for block in selected_blocks},
            allow_local_functions=True,
        ),
        *_child_range_pull_up_diagnostics(child_module, selected_blocks, boundary, sites),
    ]
    blocked = _maybe_blocked_on_errors(diagnostics, request, source, metadata)
    if blocked is not None:
        return blocked

    rewrite = _build_design_wide_pull_up_from_child_procedural(
        design,
        child_module,
        selected_blocks,
        selected_declarations,
        boundary,
        block_kind=block_kind,
        sites=sites,
    )
    return _pull_up_child_range_preview_from_rewrite(
        request,
        source,
        child_module,
        rewrite,
        metadata,
    )


def _pull_up_child_range_preview_from_rewrite(
    request: BoundaryMoveRequest,
    source: BoundaryEndpoint,
    child_module: Module,
    rewrite: tuple[TextEditPlan, ...] | RefactorDiagnostic,
    metadata: dict[str, object],
) -> BoundaryMovePreview:
    edits: tuple[TextEditPlan, ...] = ()
    diagnostics: tuple[RefactorDiagnostic, ...] = ()
    rewrite_metadata: dict[str, object] = {
        "rewriteStatus": "not-implemented",
        "applyBlockedReason": "preview-contract-only",
    }
    if isinstance(rewrite, tuple):
        edits = rewrite
        rewrite_metadata = {"rewriteStatus": "apply-ready"}
    else:
        diagnostics = (rewrite,)
        rewrite_metadata = {"rewriteStatus": "not-implemented", "applyBlockedReason": rewrite.code}

    merged_metadata = dict(metadata)
    merged_metadata.update(rewrite_metadata)
    confidence = "safe" if edits else "planning"
    if diagnostics and any(diag.severity == "error" for diag in diagnostics):
        confidence = "blocked"
    return BoundaryMovePreview(
        request=request,
        confidence=confidence,
        diagnostics=diagnostics,
        source=source,
        before_hierarchy={
            "selectedModule": child_module.name,
            "selectionKind": "range",
        },
        after_hierarchy={
            "movedModule": child_module.name,
            "rewrittenParentModules": merged_metadata.get("parentModules", []),
            "siteCount": merged_metadata.get("siteCount", 0),
        },
        moved_items={
            "continuousAssignments": merged_metadata.get("selectedAssignments", 0),
            "instances": merged_metadata.get("selectedInstances", 0),
            "alwaysBlocks": merged_metadata.get("selectedAlwaysBlocks", 0),
            "initialBlocks": merged_metadata.get("selectedInitialBlocks", 0),
            "signals": list(
                {
                    *merged_metadata.get("boundary", {}).get("inputs", []),
                    *merged_metadata.get("boundary", {}).get("outputs", []),
                    *merged_metadata.get("boundary", {}).get("internals", []),
                }
            )
            if isinstance(merged_metadata.get("boundary"), dict)
            else [],
        },
        edits=edits,
        metadata=merged_metadata,
    )


def _child_range_pull_up_diagnostics(
    child_module: Module,
    selected_nodes: list,
    boundary,
    sites: list[tuple[Module, Instance]],
) -> list[RefactorDiagnostic]:
    diagnostics: list[RefactorDiagnostic] = []
    child_ports = {port.name: port for port in child_module.ports}
    selected_input_ports = [name for name in boundary.inputs if name in child_ports]
    selected_output_ports = [name for name in boundary.outputs if name in child_ports]
    for name in selected_output_ports:
        port = child_ports[name]
        if port.direction != PortDirection.OUTPUT:
            diagnostics.append(
                RefactorDiagnostic(
                    "non-output-child-port-write-unsupported",
                    f"Selected child port {name!r} is not an output port and cannot be lifted into the parent.",
                    severity="error",
                )
            )
    for _parent, inst in sites:
        raw_port_map = _port_expression_map(child_module, inst, require_all_ports=False)
        if isinstance(raw_port_map, RefactorDiagnostic):
            diagnostics.append(raw_port_map)
            continue
        for name in selected_input_ports:
            if raw_port_map.get(name) is not None:
                continue
            diagnostics.append(
                RefactorDiagnostic(
                    "required-boundary-port-unconnected",
                    (
                        f"Selected child input port {name!r} on instance {inst.instance_name!r} must connect to a "
                        "parent expression for design-wide hierarchy-up."
                    ),
                    severity="error",
                )
            )
        for name in selected_output_ports:
            if _simple_identifier_name(raw_port_map.get(name)) is not None:
                continue
            diagnostics.append(
                RefactorDiagnostic(
                    "output-port-connection-unsupported",
                    (
                        f"Selected child output port {name!r} on instance {inst.instance_name!r} must connect to a "
                        "simple parent signal for design-wide hierarchy-up."
                    ),
                    severity="error",
                )
            )
    if any(inst.instance_array is not None for _parent, inst in sites):
        diagnostics.append(
            RefactorDiagnostic(
                "instance-array-unsupported",
                "Design-wide hierarchy-up does not yet support child modules instantiated as arrays.",
                severity="error",
            )
        )
    for name in (*boundary.inputs, *boundary.outputs):
        port = child_ports.get(name)
        if port is not None and port.direction == PortDirection.INOUT:
            diagnostics.append(
                RefactorDiagnostic(
                    "inout-port-unsupported",
                    f"Design-wide hierarchy-up does not yet support inout boundary port {name!r}.",
                    severity="error",
                )
            )
    if not selected_nodes:
        diagnostics.append(
            RefactorDiagnostic(
                "no-pull-up-child-selection",
                (
                    "Selection does not contain any complete continuous assignments, instances, or "
                    "always/initial blocks to move upward."
                ),
                severity="error",
            )
        )
    return diagnostics


def _augment_structural_boundary_for_complex_outputs(module: Module, selected_logic_ids: set[int], boundary):
    if not getattr(boundary, "complex_outputs", ()):
        return boundary

    child_param_names = _module_parameter_names(module)
    complex_written: set[str] = set()
    for entry in boundary.complex_outputs:
        for identifier in entry.parent_expression.find(Identifier):
            if identifier.hierarchy or identifier.name in child_param_names:
                continue
            complex_written.add(identifier.name)

    outside_refs = _outside_identifier_names(module, selected_logic_ids)
    port_names = {port.name for port in module.ports}
    outputs = set(boundary.outputs)
    outputs.update(name for name in complex_written if name in outside_refs or name in port_names)
    internals = set(boundary.internals)
    internals.update(complex_written - outputs)
    internals -= outputs
    inputs = set(boundary.inputs) - outputs - internals
    ordered = _signal_order(module)
    return _Boundary(
        inputs=tuple(_ordered_names(inputs, ordered)),
        outputs=tuple(_ordered_names(outputs, ordered)),
        internals=tuple(_ordered_names(internals, ordered)),
        complex_outputs=boundary.complex_outputs,
        forwarded_parameters=boundary.forwarded_parameters,
        copied_localparams=boundary.copied_localparams,
        blocked_constants=boundary.blocked_constants,
    )


def _collect_module_instance_sites(design: Design, module_name: str) -> list[tuple[Module, Instance]]:
    sites: list[tuple[Module, Instance]] = []
    for parent in design.modules:
        for inst in parent.instances:
            if not _instance_targets_module(inst, module_name):
                continue
            sites.append((parent, inst))
    sites.sort(key=lambda entry: (entry[0].name, entry[1].instance_name))
    return sites


def _collect_all_module_instance_sites(design: Design, module_name: str) -> list[tuple[Module, Instance]]:
    sites = [
        *_collect_module_instance_sites(design, module_name),
        *_collect_generate_module_instance_sites(design, module_name),
    ]
    sites.sort(key=lambda entry: (entry[0].name, entry[1].instance_name))
    return sites


def _collect_generate_module_instance_sites(design: Design, module_name: str) -> list[tuple[Module, Instance]]:
    sites: list[tuple[Module, Instance]] = []
    for parent in design.modules:
        for generate in parent.generate_blocks:
            for inst in generate.find(Instance):
                if not _instance_targets_module(inst, module_name):
                    continue
                sites.append((parent, inst))
    sites.sort(key=lambda entry: (entry[0].name, entry[1].instance_name))
    return sites


def _build_design_wide_pull_up_from_child_procedural(  # noqa: PLR0913
    design: Design,
    child_module: Module,
    selected_blocks: list,
    selected_declarations,
    boundary,
    *,
    block_kind: str,
    sites: list[tuple[Module, Instance]],
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic:
    transformed_child = _build_child_module_for_pulled_up_procedural(
        child_module,
        selected_blocks,
        selected_declarations,
        boundary,
        block_kind=block_kind,
    )
    child_edits = _design_wide_child_module_edits(
        child_module,
        transformed_child,
        selected_declarations,
        selected_blocks,
    )
    if isinstance(child_edits, RefactorDiagnostic):
        return child_edits
    if child_edits is None:
        child_edit = _module_replacement_edit(child_module, transformed_child)
        if isinstance(child_edit, RefactorDiagnostic):
            return child_edit
        child_edits = (child_edit,)

    grouped_sites: dict[str, tuple[Module, list[Instance]]] = {}
    for parent, inst in sites:
        grouped = grouped_sites.setdefault(parent.name, (parent, []))
        grouped[1].append(inst)

    edits: list[TextEditPlan] = [*child_edits]
    for parent_name in sorted(grouped_sites):
        parent_module, parent_instances = grouped_sites[parent_name]
        transformed_parent = _build_parent_module_for_pulled_up_child_logic(
            parent_module,
            child_module,
            transformed_child,
            parent_instances,
            selected_blocks,
            selected_declarations,
            boundary,
            block_kind=block_kind,
        )
        if isinstance(transformed_parent, RefactorDiagnostic):
            return transformed_parent
        parent_edits = _design_wide_parent_procedural_edits(
            parent_module,
            transformed_parent,
            child_module,
            transformed_child,
            parent_instances,
            selected_blocks,
            selected_declarations,
            boundary,
            block_kind=block_kind,
        )
        if isinstance(parent_edits, RefactorDiagnostic):
            return parent_edits
        if parent_edits is None:
            parent_edit = _module_replacement_edit(parent_module, transformed_parent)
            if isinstance(parent_edit, RefactorDiagnostic):
                return parent_edit
            parent_edits = (parent_edit,)
        edits.extend(parent_edits)
    return tuple(edits)


def _build_design_wide_pull_up_from_child_assigns(  # noqa: PLR0913
    design: Design,
    child_module: Module,
    selected_assigns: list,
    selected_declarations,
    boundary,
    *,
    sites: list[tuple[Module, Instance]],
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic:
    transformed_child = _build_child_module_for_pulled_up_assigns(
        child_module,
        selected_assigns,
        selected_declarations,
        boundary,
    )
    child_edits = _design_wide_child_module_edits(
        child_module,
        transformed_child,
        selected_declarations,
        selected_assigns,
    )
    if isinstance(child_edits, RefactorDiagnostic):
        return child_edits
    if child_edits is None:
        child_edit = _module_replacement_edit(child_module, transformed_child)
        if isinstance(child_edit, RefactorDiagnostic):
            return child_edit
        child_edits = (child_edit,)

    grouped_sites: dict[str, tuple[Module, list[Instance]]] = {}
    for parent, inst in sites:
        grouped = grouped_sites.setdefault(parent.name, (parent, []))
        grouped[1].append(inst)

    edits: list[TextEditPlan] = [*child_edits]
    for parent_name in sorted(grouped_sites):
        parent_module, parent_instances = grouped_sites[parent_name]
        transformed_parent = _build_parent_module_for_pulled_up_child_assigns(
            parent_module,
            child_module,
            transformed_child,
            parent_instances,
            selected_assigns,
            selected_declarations,
            boundary,
        )
        if isinstance(transformed_parent, RefactorDiagnostic):
            return transformed_parent
        parent_edits = _design_wide_parent_assign_edits(
            parent_module,
            transformed_parent,
            child_module,
            transformed_child,
            parent_instances,
            selected_assigns,
            selected_declarations,
            boundary,
        )
        if isinstance(parent_edits, RefactorDiagnostic):
            return parent_edits
        if parent_edits is None:
            parent_edit = _module_replacement_edit(parent_module, transformed_parent)
            if isinstance(parent_edit, RefactorDiagnostic):
                return parent_edit
            parent_edits = (parent_edit,)
        edits.extend(parent_edits)
    return tuple(edits)


def _build_design_wide_pull_up_from_child_structural(  # noqa: PLR0913
    design: Design,
    child_module: Module,
    selected_assigns: list,
    selected_instances: list[Instance],
    selected_declarations,
    boundary,
    *,
    sites: list[tuple[Module, Instance]],
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic:
    transformed_child = _build_child_module_for_pulled_up_structural(
        child_module,
        selected_assigns,
        selected_instances,
        selected_declarations,
        boundary,
    )
    child_edits = _design_wide_child_module_edits(
        child_module,
        transformed_child,
        selected_declarations,
        selected_assigns,
        selected_instances,
    )
    if isinstance(child_edits, RefactorDiagnostic):
        return child_edits
    if child_edits is None:
        child_edit = _module_replacement_edit(child_module, transformed_child)
        if isinstance(child_edit, RefactorDiagnostic):
            return child_edit
        child_edits = (child_edit,)

    grouped_sites: dict[str, tuple[Module, list[Instance]]] = {}
    for parent, inst in sites:
        grouped = grouped_sites.setdefault(parent.name, (parent, []))
        grouped[1].append(inst)

    edits: list[TextEditPlan] = [*child_edits]
    for parent_name in sorted(grouped_sites):
        parent_module, parent_instances = grouped_sites[parent_name]
        transformed_parent = _build_parent_module_for_pulled_up_child_structural(
            parent_module,
            child_module,
            transformed_child,
            parent_instances,
            selected_assigns,
            selected_instances,
            selected_declarations,
            boundary,
        )
        if isinstance(transformed_parent, RefactorDiagnostic):
            return transformed_parent
        parent_edits = _design_wide_parent_structural_edits(
            parent_module,
            transformed_parent,
            child_module,
            transformed_child,
            parent_instances,
            selected_assigns,
            selected_instances,
            selected_declarations,
            boundary,
        )
        if isinstance(parent_edits, RefactorDiagnostic):
            return parent_edits
        if parent_edits is None:
            parent_edit = _module_replacement_edit(parent_module, transformed_parent)
            if isinstance(parent_edit, RefactorDiagnostic):
                return parent_edit
            parent_edits = (parent_edit,)
        edits.extend(parent_edits)
    return tuple(edits)


def _build_child_module_for_pulled_up_procedural(
    child_module: Module,
    selected_blocks: list,
    selected_declarations,
    boundary,
    *,
    block_kind: str,
) -> Module:
    selected_ids = {id(block) for block in selected_blocks}
    selected_generate_ids = _selected_generate_item_ids(selected_declarations, selected_blocks)
    transformed = copy.deepcopy(child_module)
    _remove_selected_child_localparams(transformed, selected_declarations)
    transformed.generate_blocks = _copied_remaining_generate_constructs(
        child_module.generate_blocks, selected_generate_ids
    )
    if block_kind == "always":
        transformed.always_blocks = [
            copy.deepcopy(block) for block in child_module.always_blocks if id(block) not in selected_ids
        ]
    else:
        transformed.initial_blocks = [
            copy.deepcopy(block) for block in child_module.initial_blocks if id(block) not in selected_ids
        ]

    existing_port_names = {port.name for port in child_module.ports}
    existing_output_port_names = [name for name in boundary.outputs if name in existing_port_names]
    new_output_ports = [name for name in boundary.inputs if name not in existing_port_names]
    new_input_ports = [name for name in boundary.outputs if name not in existing_port_names]
    port_promoted_names = set(new_output_ports) | set(new_input_ports) | set(existing_output_port_names)
    removable_signal_names = set(boundary.internals) | port_promoted_names
    transformed.nets = [net for net in transformed.nets if net.name not in removable_signal_names]
    transformed.variables = [
        variable for variable in transformed.variables if variable.name not in removable_signal_names
    ]

    for port in transformed.ports:
        if port.name in boundary.outputs:
            port.direction = PortDirection.INPUT
            port.data_type = None
            port.default_value = None
    for name in new_output_ports:
        transformed.ports.append(_port_for_child_signal(child_module, name, PortDirection.OUTPUT))
    for name in new_input_ports:
        transformed.ports.append(_port_for_child_signal(child_module, name, PortDirection.INPUT))
    return transformed


def _build_child_module_for_pulled_up_assigns(
    child_module: Module,
    selected_assigns: list,
    selected_declarations,
    boundary,
) -> Module:
    selected_ids = {id(assign) for assign in selected_assigns}
    selected_generate_ids = _selected_generate_item_ids(selected_declarations, selected_assigns)
    transformed = copy.deepcopy(child_module)
    _remove_selected_child_localparams(transformed, selected_declarations)
    transformed.generate_blocks = _copied_remaining_generate_constructs(
        child_module.generate_blocks, selected_generate_ids
    )
    transformed.continuous_assigns = [
        copy.deepcopy(assign) for assign in child_module.continuous_assigns if id(assign) not in selected_ids
    ]

    existing_port_names = {port.name for port in child_module.ports}
    existing_output_port_names = [name for name in boundary.outputs if name in existing_port_names]
    new_output_ports = [name for name in boundary.inputs if name not in existing_port_names]
    new_input_ports = [name for name in boundary.outputs if name not in existing_port_names]
    port_promoted_names = set(new_output_ports) | set(new_input_ports) | set(existing_output_port_names)
    removable_signal_names = set(boundary.internals) | port_promoted_names
    transformed.nets = [net for net in transformed.nets if net.name not in removable_signal_names]
    transformed.variables = [
        variable for variable in transformed.variables if variable.name not in removable_signal_names
    ]

    for port in transformed.ports:
        if port.name in boundary.outputs:
            port.direction = PortDirection.INPUT
            port.data_type = None
            port.default_value = None
    for name in new_output_ports:
        transformed.ports.append(_port_for_child_signal(child_module, name, PortDirection.OUTPUT))
    for name in new_input_ports:
        transformed.ports.append(_port_for_child_signal(child_module, name, PortDirection.INPUT))
    return transformed


def _build_child_module_for_pulled_up_structural(
    child_module: Module,
    selected_assigns: list,
    selected_instances: list[Instance],
    selected_declarations,
    boundary,
) -> Module:
    selected_assign_ids = {id(assign) for assign in selected_assigns}
    selected_instance_ids = {id(inst) for inst in selected_instances}
    selected_generate_ids = _selected_generate_item_ids(selected_declarations, selected_assigns, selected_instances)
    transformed = copy.deepcopy(child_module)
    _remove_selected_child_localparams(transformed, selected_declarations)
    transformed.generate_blocks = _copied_remaining_generate_constructs(
        child_module.generate_blocks, selected_generate_ids
    )
    transformed.continuous_assigns = [
        copy.deepcopy(assign) for assign in child_module.continuous_assigns if id(assign) not in selected_assign_ids
    ]
    transformed.instances = [
        copy.deepcopy(inst) for inst in child_module.instances if id(inst) not in selected_instance_ids
    ]

    existing_port_names = {port.name for port in child_module.ports}
    existing_output_port_names = [name for name in boundary.outputs if name in existing_port_names]
    new_output_ports = [name for name in boundary.inputs if name not in existing_port_names]
    new_input_ports = [name for name in boundary.outputs if name not in existing_port_names]
    port_promoted_names = set(new_output_ports) | set(new_input_ports) | set(existing_output_port_names)
    removable_signal_names = set(boundary.internals) | port_promoted_names
    transformed.nets = [net for net in transformed.nets if net.name not in removable_signal_names]
    transformed.variables = [
        variable for variable in transformed.variables if variable.name not in removable_signal_names
    ]

    for port in transformed.ports:
        if port.name in boundary.outputs:
            port.direction = PortDirection.INPUT
            port.data_type = None
            port.default_value = None
    for name in new_output_ports:
        transformed.ports.append(_port_for_child_signal(child_module, name, PortDirection.OUTPUT))
    for name in new_input_ports:
        transformed.ports.append(_port_for_child_signal(child_module, name, PortDirection.INPUT))
    return transformed


def _build_parent_module_for_pulled_up_child_logic(  # noqa: PLR0911, PLR0913
    parent_module: Module,
    child_module: Module,
    transformed_child: Module,
    parent_instances: list[Instance],
    selected_blocks: list,
    selected_declarations,
    boundary,
    *,
    block_kind: str,
) -> Module | RefactorDiagnostic:
    transformed_parent = copy.deepcopy(parent_module)
    existing_names = _module_declared_names(transformed_parent)
    child_port_names = {port.name for port in child_module.ports}
    readback_names = [name for name in boundary.inputs if name not in child_port_names]
    child_input_names = [name for name in boundary.outputs if name not in child_port_names]
    existing_child_output_names = [name for name in boundary.outputs if name in child_port_names]
    lifted_internal_names = list(boundary.internals)
    lifted_signal_names = [*readback_names, *child_input_names, *lifted_internal_names]

    for parent_instance in parent_instances:
        site = _find_transformed_instance_site(transformed_parent, parent_module, parent_instance)
        if isinstance(site, RefactorDiagnostic):
            return site
        transformed_instance, transformed_site_block, transformed_site_index = site
        raw_param_map = _parameter_expression_map(child_module, parent_instance)
        if isinstance(raw_param_map, RefactorDiagnostic):
            return raw_param_map
        raw_port_map = _port_expression_map(child_module, parent_instance, require_all_ports=False)
        if isinstance(raw_port_map, RefactorDiagnostic):
            return raw_port_map
        constant_env = _child_constant_expression_map(child_module, raw_param_map)
        if isinstance(constant_env, RefactorDiagnostic):
            return constant_env

        rename_map = {
            name: _unique_name(f"{parent_instance.instance_name}__{name}", existing_names)
            for name in lifted_signal_names
        }
        expr_map: dict[str, Expression] = {
            **{name: copy.deepcopy(expr) for name, expr in constant_env.items()},
            **{name: copy.deepcopy(expr) for name, expr in raw_port_map.items()},
            **{name: Identifier(new_name) for name, new_name in rename_map.items()},
        }

        promotion = _promote_selected_child_output_ports(
            transformed_parent,
            child_module,
            existing_child_output_names,
            raw_port_map,
            expr_map,
        )
        if promotion is not None:
            return promotion

        for name in readback_names:
            decl = _parent_net_for_child_output(child_module, name, rename_map[name], expr_map)
            transformed_parent.nets.append(decl)
        for name in [*child_input_names, *lifted_internal_names]:
            decl = _parent_variable_for_lifted_signal(child_module, name, rename_map[name], expr_map)
            transformed_parent.variables.append(decl)

        copied_functions, function_name_map = _copy_referenced_child_functions(
            child_module,
            [*selected_declarations.parameters, *selected_blocks, *constant_env.values()],
            existing_names,
            name_prefix=parent_instance.instance_name,
            expr_map=expr_map,
        )
        if isinstance(copied_functions, RefactorDiagnostic):
            return copied_functions
        transformed_parent.functions.extend(copied_functions)

        top_level_selected_ids = {id(block) for block in getattr(child_module, f"{block_kind}_blocks")}
        copied_blocks = _copied_nodes(
            [block for block in selected_blocks if id(block) in top_level_selected_ids], expr_map
        )
        copied_generate_constructs = _copied_selected_generate_constructs(
            child_module.generate_blocks,
            _selected_generate_item_ids(selected_declarations, selected_blocks),
            expr_map,
            function_name_map=function_name_map,
        )
        _rewrite_function_call_names(copied_blocks, function_name_map)
        insertion_nodes = _nodes_by_source_order([*copied_generate_constructs, *copied_blocks])
        if transformed_site_block is not None and transformed_site_index is not None:
            transformed_site_block.items[transformed_site_index:transformed_site_index] = insertion_nodes
        elif block_kind == "always":
            transformed_parent.always_blocks.extend(copied_blocks)
            transformed_parent.generate_blocks.extend(copied_generate_constructs)
        else:
            transformed_parent.initial_blocks.extend(copied_blocks)
            transformed_parent.generate_blocks.extend(copied_generate_constructs)

        transformed_instance.port_connections = _named_child_site_connections(
            transformed_child,
            raw_port_map,
            rename_map,
        )
        transformed_instance.has_parameter_override = bool(parent_instance.parameter_bindings)
        transformed_instance.parameter_bindings = copy.deepcopy(parent_instance.parameter_bindings)

    return transformed_parent


def _build_parent_module_for_pulled_up_child_assigns(  # noqa: PLR0913
    parent_module: Module,
    child_module: Module,
    transformed_child: Module,
    parent_instances: list[Instance],
    selected_assigns: list,
    selected_declarations,
    boundary,
) -> Module | RefactorDiagnostic:
    transformed_parent = copy.deepcopy(parent_module)
    existing_names = _module_declared_names(transformed_parent)
    child_port_names = {port.name for port in child_module.ports}
    readback_names = [name for name in boundary.inputs if name not in child_port_names]
    child_input_names = [name for name in boundary.outputs if name not in child_port_names]
    existing_child_output_names = [name for name in boundary.outputs if name in child_port_names]
    lifted_internal_names = list(boundary.internals)
    lifted_signal_names = [*readback_names, *child_input_names, *lifted_internal_names]

    for parent_instance in parent_instances:
        site = _find_transformed_instance_site(transformed_parent, parent_module, parent_instance)
        if isinstance(site, RefactorDiagnostic):
            return site
        transformed_instance, transformed_site_block, transformed_site_index = site
        raw_param_map = _parameter_expression_map(child_module, parent_instance)
        if isinstance(raw_param_map, RefactorDiagnostic):
            return raw_param_map
        raw_port_map = _port_expression_map(child_module, parent_instance, require_all_ports=False)
        if isinstance(raw_port_map, RefactorDiagnostic):
            return raw_port_map
        constant_env = _child_constant_expression_map(child_module, raw_param_map)
        if isinstance(constant_env, RefactorDiagnostic):
            return constant_env

        rename_map = {
            name: _unique_name(f"{parent_instance.instance_name}__{name}", existing_names)
            for name in lifted_signal_names
        }
        expr_map: dict[str, Expression] = {
            **{name: copy.deepcopy(expr) for name, expr in constant_env.items()},
            **{name: copy.deepcopy(expr) for name, expr in raw_port_map.items()},
            **{name: Identifier(new_name) for name, new_name in rename_map.items()},
        }

        connected_output_names = {
            signal_name
            for signal_name in (_simple_identifier_name(raw_port_map.get(name)) for name in existing_child_output_names)
            if signal_name is not None
        }
        _convert_child_driven_outputs_to_parent_nets(transformed_parent, connected_output_names)

        for name in [*readback_names, *child_input_names, *lifted_internal_names]:
            decl = _parent_net_for_child_output(child_module, name, rename_map[name], expr_map)
            transformed_parent.nets.append(decl)

        copied_functions, function_name_map = _copy_referenced_child_functions(
            child_module,
            [*selected_declarations.parameters, *selected_assigns, *constant_env.values()],
            existing_names,
            name_prefix=parent_instance.instance_name,
            expr_map=expr_map,
        )
        if isinstance(copied_functions, RefactorDiagnostic):
            return copied_functions
        transformed_parent.functions.extend(copied_functions)

        top_level_assign_ids = {id(assign) for assign in child_module.continuous_assigns}
        copied_assigns = _copied_nodes(
            [assign for assign in selected_assigns if id(assign) in top_level_assign_ids], expr_map
        )
        copied_generate_constructs = _copied_selected_generate_constructs(
            child_module.generate_blocks,
            _selected_generate_item_ids(selected_declarations, selected_assigns),
            expr_map,
            function_name_map=function_name_map,
        )
        _rewrite_function_call_names(copied_assigns, function_name_map)
        insertion_nodes = _nodes_by_source_order([*copied_generate_constructs, *copied_assigns])
        if transformed_site_block is not None and transformed_site_index is not None:
            transformed_site_block.items[transformed_site_index:transformed_site_index] = insertion_nodes
        else:
            transformed_parent.continuous_assigns.extend(copied_assigns)
            transformed_parent.generate_blocks.extend(copied_generate_constructs)

        transformed_instance.port_connections = _named_child_site_connections(
            transformed_child,
            raw_port_map,
            rename_map,
        )
        transformed_instance.has_parameter_override = bool(parent_instance.parameter_bindings)
        transformed_instance.parameter_bindings = copy.deepcopy(parent_instance.parameter_bindings)

    return transformed_parent


def _build_parent_module_for_pulled_up_child_structural(  # noqa: PLR0913
    parent_module: Module,
    child_module: Module,
    transformed_child: Module,
    parent_instances: list[Instance],
    selected_assigns: list,
    selected_instances: list[Instance],
    selected_declarations,
    boundary,
) -> Module | RefactorDiagnostic:
    transformed_parent = copy.deepcopy(parent_module)
    existing_names = _module_declared_names(transformed_parent)
    child_port_names = {port.name for port in child_module.ports}
    readback_names = [name for name in boundary.inputs if name not in child_port_names]
    child_input_names = [name for name in boundary.outputs if name not in child_port_names]
    existing_child_output_names = [name for name in boundary.outputs if name in child_port_names]
    lifted_internal_names = list(boundary.internals)
    lifted_signal_names = [*readback_names, *child_input_names, *lifted_internal_names]

    for parent_instance in parent_instances:
        site = _find_transformed_instance_site(transformed_parent, parent_module, parent_instance)
        if isinstance(site, RefactorDiagnostic):
            return site
        transformed_instance, transformed_site_block, transformed_site_index = site
        raw_param_map = _parameter_expression_map(child_module, parent_instance)
        if isinstance(raw_param_map, RefactorDiagnostic):
            return raw_param_map
        raw_port_map = _port_expression_map(child_module, parent_instance, require_all_ports=False)
        if isinstance(raw_port_map, RefactorDiagnostic):
            return raw_port_map
        constant_env = _child_constant_expression_map(child_module, raw_param_map)
        if isinstance(constant_env, RefactorDiagnostic):
            return constant_env

        rename_map = {
            name: _unique_name(f"{parent_instance.instance_name}__{name}", existing_names)
            for name in lifted_signal_names
        }
        selected_instance_name_map = {
            inst.instance_name: _unique_name(f"{parent_instance.instance_name}__{inst.instance_name}", existing_names)
            for inst in selected_instances
        }
        expr_map: dict[str, Expression] = {
            **{name: copy.deepcopy(expr) for name, expr in constant_env.items()},
            **{name: copy.deepcopy(expr) for name, expr in raw_port_map.items()},
            **{name: Identifier(new_name) for name, new_name in rename_map.items()},
        }

        connected_output_names = {
            signal_name
            for signal_name in (_simple_identifier_name(raw_port_map.get(name)) for name in existing_child_output_names)
            if signal_name is not None
        }
        _convert_child_driven_outputs_to_parent_nets(transformed_parent, connected_output_names)

        for name in [*readback_names, *child_input_names, *lifted_internal_names]:
            decl = _parent_net_for_child_output(child_module, name, rename_map[name], expr_map)
            transformed_parent.nets.append(decl)

        copied_functions, function_name_map = _copy_referenced_child_functions(
            child_module,
            [*selected_declarations.parameters, *selected_assigns, *selected_instances, *constant_env.values()],
            existing_names,
            name_prefix=parent_instance.instance_name,
            expr_map=expr_map,
        )
        if isinstance(copied_functions, RefactorDiagnostic):
            return copied_functions
        transformed_parent.functions.extend(copied_functions)

        top_level_assign_ids = {id(assign) for assign in child_module.continuous_assigns}
        top_level_instance_ids = {id(inst) for inst in child_module.instances}
        copied_assigns = _copied_nodes(
            [assign for assign in selected_assigns if id(assign) in top_level_assign_ids], expr_map
        )
        copied_instances = _copied_selected_instances(
            [inst for inst in selected_instances if id(inst) in top_level_instance_ids],
            selected_instance_name_map,
            expr_map,
        )
        copied_generate_constructs = _copied_selected_generate_constructs(
            child_module.generate_blocks,
            _selected_generate_item_ids(selected_declarations, selected_assigns, selected_instances),
            expr_map,
            function_name_map=function_name_map,
            selected_instance_name_map=selected_instance_name_map,
        )
        _rewrite_function_call_names(copied_assigns, function_name_map)
        _rewrite_function_call_names(copied_instances, function_name_map)
        insertion_nodes = _nodes_by_source_order([*copied_generate_constructs, *copied_assigns, *copied_instances])
        if transformed_site_block is not None and transformed_site_index is not None:
            transformed_site_block.items[transformed_site_index:transformed_site_index] = insertion_nodes
        else:
            transformed_parent.continuous_assigns.extend(copied_assigns)
            transformed_parent.instances.extend(copied_instances)
            transformed_parent.generate_blocks.extend(copied_generate_constructs)

        transformed_instance.port_connections = _named_child_site_connections(
            transformed_child,
            raw_port_map,
            rename_map,
        )
        transformed_instance.has_parameter_override = bool(parent_instance.parameter_bindings)
        transformed_instance.parameter_bindings = copy.deepcopy(parent_instance.parameter_bindings)

    return transformed_parent


def _design_wide_parent_procedural_edits(  # noqa: PLR0911, PLR0913
    parent_module: Module,
    transformed_parent: Module,
    child_module: Module,
    transformed_child: Module,
    parent_instances: list[Instance],
    selected_blocks: list,
    selected_declarations,
    boundary,
    *,
    block_kind: str,
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic | None:
    top_level_selected_ids = {id(block) for block in getattr(child_module, f"{block_kind}_blocks")}
    if any(id(block) not in top_level_selected_ids for block in selected_blocks):
        return None

    header_and_decl_edits = _design_wide_parent_header_and_declaration_edits(parent_module, transformed_parent)
    if isinstance(header_and_decl_edits, RefactorDiagnostic):
        return header_and_decl_edits

    existing_names = _module_declared_names(parent_module)
    child_port_names = {port.name for port in child_module.ports}
    readback_names = [name for name in boundary.inputs if name not in child_port_names]
    child_input_names = [name for name in boundary.outputs if name not in child_port_names]
    lifted_internal_names = list(boundary.internals)
    lifted_signal_names = [*readback_names, *child_input_names, *lifted_internal_names]

    site_edits: list[TextEditPlan] = []
    for parent_instance in parent_instances:
        instance_source, instance_range = _top_level_parent_instance_source_and_range(parent_module, parent_instance)
        if instance_source is None and instance_range is None:
            return None
        if isinstance(instance_source, RefactorDiagnostic):
            return instance_source

        raw_param_map = _parameter_expression_map(child_module, parent_instance)
        if isinstance(raw_param_map, RefactorDiagnostic):
            return raw_param_map
        raw_port_map = _port_expression_map(child_module, parent_instance, require_all_ports=False)
        if isinstance(raw_port_map, RefactorDiagnostic):
            return raw_port_map
        constant_env = _child_constant_expression_map(child_module, raw_param_map)
        if isinstance(constant_env, RefactorDiagnostic):
            return constant_env

        rename_map = {
            name: _unique_name(f"{parent_instance.instance_name}__{name}", existing_names)
            for name in lifted_signal_names
        }
        expr_map: dict[str, Expression] = {
            **{name: copy.deepcopy(expr) for name, expr in constant_env.items()},
            **{name: copy.deepcopy(expr) for name, expr in raw_port_map.items()},
            **{name: Identifier(new_name) for name, new_name in rename_map.items()},
        }

        declarations = [
            *(_parent_net_for_child_output(child_module, name, rename_map[name], expr_map) for name in readback_names),
            *(
                _parent_variable_for_lifted_signal(child_module, name, rename_map[name], expr_map)
                for name in [*child_input_names, *lifted_internal_names]
            ),
        ]
        copied_functions, function_name_map = _copy_referenced_child_functions(
            child_module,
            [*selected_declarations.parameters, *selected_blocks, *constant_env.values()],
            existing_names,
            name_prefix=parent_instance.instance_name,
            expr_map=expr_map,
        )
        if isinstance(copied_functions, RefactorDiagnostic):
            return copied_functions
        copied_blocks = _copied_nodes(
            [block for block in selected_blocks if id(block) in top_level_selected_ids], expr_map
        )
        _rewrite_function_call_names(copied_blocks, function_name_map)

        rewritten_instance = copy.deepcopy(parent_instance)
        rewritten_instance.port_connections = _named_child_site_connections(
            transformed_child,
            raw_port_map,
            rename_map,
        )
        rewritten_instance.has_parameter_override = bool(parent_instance.parameter_bindings)
        rewritten_instance.parameter_bindings = copy.deepcopy(parent_instance.parameter_bindings)

        replacement = _emit_module_items_text(
            _nodes_by_source_order([*declarations, *copied_functions, *copied_blocks])
        )
        replacement += _emit_module_item_text(rewritten_instance)
        site_edits.append(
            TextEditPlan(
                file=parent_instance.loc.file if parent_instance.loc and parent_instance.loc.file else "",
                range=instance_range,
                original=instance_source,
                replacement=replacement,
            )
        )
    return (*header_and_decl_edits, *site_edits)


def _design_wide_parent_assign_edits(  # noqa: PLR0911, PLR0913
    parent_module: Module,
    transformed_parent: Module,
    child_module: Module,
    transformed_child: Module,
    parent_instances: list[Instance],
    selected_assigns: list,
    selected_declarations,
    boundary,
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic | None:
    top_level_assign_ids = {id(assign) for assign in child_module.continuous_assigns}
    if any(id(assign) not in top_level_assign_ids for assign in selected_assigns):
        return None

    header_and_decl_edits = _design_wide_parent_header_and_declaration_edits(parent_module, transformed_parent)
    if isinstance(header_and_decl_edits, RefactorDiagnostic):
        return header_and_decl_edits

    existing_names = _module_declared_names(parent_module)
    child_port_names = {port.name for port in child_module.ports}
    readback_names = [name for name in boundary.inputs if name not in child_port_names]
    child_input_names = [name for name in boundary.outputs if name not in child_port_names]
    lifted_internal_names = list(boundary.internals)
    lifted_signal_names = [*readback_names, *child_input_names, *lifted_internal_names]

    site_edits: list[TextEditPlan] = []
    for parent_instance in parent_instances:
        instance_source, instance_range = _top_level_parent_instance_source_and_range(parent_module, parent_instance)
        if instance_source is None and instance_range is None:
            return None
        if isinstance(instance_source, RefactorDiagnostic):
            return instance_source

        raw_param_map = _parameter_expression_map(child_module, parent_instance)
        if isinstance(raw_param_map, RefactorDiagnostic):
            return raw_param_map
        raw_port_map = _port_expression_map(child_module, parent_instance, require_all_ports=False)
        if isinstance(raw_port_map, RefactorDiagnostic):
            return raw_port_map
        constant_env = _child_constant_expression_map(child_module, raw_param_map)
        if isinstance(constant_env, RefactorDiagnostic):
            return constant_env

        rename_map = {
            name: _unique_name(f"{parent_instance.instance_name}__{name}", existing_names)
            for name in lifted_signal_names
        }
        expr_map: dict[str, Expression] = {
            **{name: copy.deepcopy(expr) for name, expr in constant_env.items()},
            **{name: copy.deepcopy(expr) for name, expr in raw_port_map.items()},
            **{name: Identifier(new_name) for name, new_name in rename_map.items()},
        }

        declarations = [
            *(
                _parent_net_for_child_output(child_module, name, rename_map[name], expr_map)
                for name in [*readback_names, *child_input_names, *lifted_internal_names]
            )
        ]
        copied_functions, function_name_map = _copy_referenced_child_functions(
            child_module,
            [*selected_declarations.parameters, *selected_assigns, *constant_env.values()],
            existing_names,
            name_prefix=parent_instance.instance_name,
            expr_map=expr_map,
        )
        if isinstance(copied_functions, RefactorDiagnostic):
            return copied_functions
        copied_assigns = _copied_nodes(
            [assign for assign in selected_assigns if id(assign) in top_level_assign_ids], expr_map
        )
        _rewrite_function_call_names(copied_assigns, function_name_map)

        rewritten_instance = copy.deepcopy(parent_instance)
        rewritten_instance.port_connections = _named_child_site_connections(
            transformed_child,
            raw_port_map,
            rename_map,
        )
        rewritten_instance.has_parameter_override = bool(parent_instance.parameter_bindings)
        rewritten_instance.parameter_bindings = copy.deepcopy(parent_instance.parameter_bindings)

        replacement = _emit_module_items_text(
            _nodes_by_source_order([*declarations, *copied_functions, *copied_assigns])
        )
        replacement += _emit_module_item_text(rewritten_instance)
        site_edits.append(
            TextEditPlan(
                file=parent_instance.loc.file if parent_instance.loc and parent_instance.loc.file else "",
                range=instance_range,
                original=instance_source,
                replacement=replacement,
            )
        )
    return (*header_and_decl_edits, *site_edits)


def _design_wide_parent_structural_edits(  # noqa: PLR0911, PLR0913
    parent_module: Module,
    transformed_parent: Module,
    child_module: Module,
    transformed_child: Module,
    parent_instances: list[Instance],
    selected_assigns: list,
    selected_instances: list[Instance],
    selected_declarations,
    boundary,
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic | None:
    top_level_assign_ids = {id(assign) for assign in child_module.continuous_assigns}
    top_level_instance_ids = {id(instance) for instance in child_module.instances}
    if any(id(assign) not in top_level_assign_ids for assign in selected_assigns):
        return None
    if any(id(instance) not in top_level_instance_ids for instance in selected_instances):
        return None

    header_and_decl_edits = _design_wide_parent_header_and_declaration_edits(parent_module, transformed_parent)
    if isinstance(header_and_decl_edits, RefactorDiagnostic):
        return header_and_decl_edits

    existing_names = _module_declared_names(parent_module)
    child_port_names = {port.name for port in child_module.ports}
    readback_names = [name for name in boundary.inputs if name not in child_port_names]
    child_input_names = [name for name in boundary.outputs if name not in child_port_names]
    lifted_internal_names = list(boundary.internals)
    lifted_signal_names = [*readback_names, *child_input_names, *lifted_internal_names]

    site_edits: list[TextEditPlan] = []
    for parent_instance in parent_instances:
        instance_source, instance_range = _top_level_parent_instance_source_and_range(parent_module, parent_instance)
        if instance_source is None and instance_range is None:
            return None
        if isinstance(instance_source, RefactorDiagnostic):
            return instance_source

        raw_param_map = _parameter_expression_map(child_module, parent_instance)
        if isinstance(raw_param_map, RefactorDiagnostic):
            return raw_param_map
        raw_port_map = _port_expression_map(child_module, parent_instance, require_all_ports=False)
        if isinstance(raw_port_map, RefactorDiagnostic):
            return raw_port_map
        constant_env = _child_constant_expression_map(child_module, raw_param_map)
        if isinstance(constant_env, RefactorDiagnostic):
            return constant_env

        rename_map = {
            name: _unique_name(f"{parent_instance.instance_name}__{name}", existing_names)
            for name in lifted_signal_names
        }
        selected_instance_name_map = {
            inst.instance_name: _unique_name(f"{parent_instance.instance_name}__{inst.instance_name}", existing_names)
            for inst in selected_instances
        }
        expr_map: dict[str, Expression] = {
            **{name: copy.deepcopy(expr) for name, expr in constant_env.items()},
            **{name: copy.deepcopy(expr) for name, expr in raw_port_map.items()},
            **{name: Identifier(new_name) for name, new_name in rename_map.items()},
        }

        declarations = [
            *(
                _parent_net_for_child_output(child_module, name, rename_map[name], expr_map)
                for name in [*readback_names, *child_input_names, *lifted_internal_names]
            )
        ]
        copied_functions, function_name_map = _copy_referenced_child_functions(
            child_module,
            [*selected_declarations.parameters, *selected_assigns, *selected_instances, *constant_env.values()],
            existing_names,
            name_prefix=parent_instance.instance_name,
            expr_map=expr_map,
        )
        if isinstance(copied_functions, RefactorDiagnostic):
            return copied_functions
        copied_assigns = _copied_nodes(
            [assign for assign in selected_assigns if id(assign) in top_level_assign_ids], expr_map
        )
        copied_instances = _copied_selected_instances(
            [instance for instance in selected_instances if id(instance) in top_level_instance_ids],
            selected_instance_name_map,
            expr_map,
        )
        _rewrite_function_call_names(copied_assigns, function_name_map)
        _rewrite_function_call_names(copied_instances, function_name_map)

        rewritten_instance = copy.deepcopy(parent_instance)
        rewritten_instance.port_connections = _named_child_site_connections(
            transformed_child,
            raw_port_map,
            rename_map,
        )
        rewritten_instance.has_parameter_override = bool(parent_instance.parameter_bindings)
        rewritten_instance.parameter_bindings = copy.deepcopy(parent_instance.parameter_bindings)

        replacement = _emit_module_items_text(
            _nodes_by_source_order([*declarations, *copied_functions, *copied_assigns, *copied_instances])
        )
        replacement += _emit_module_item_text(rewritten_instance)
        site_edits.append(
            TextEditPlan(
                file=parent_instance.loc.file if parent_instance.loc and parent_instance.loc.file else "",
                range=instance_range,
                original=instance_source,
                replacement=replacement,
            )
        )
    return (*header_and_decl_edits, *site_edits)


def _port_for_child_signal(child_module: Module, name: str, direction: PortDirection) -> Port:
    port = copy.deepcopy(_port_for_signal(child_module, name, direction))
    if direction == PortDirection.INPUT:
        port.data_type = None
        port.default_value = None
    return port


def _remove_selected_child_localparams(module: Module, selected_declarations) -> None:
    selected_localparam_names = {param.name for param in selected_declarations.parameters if param.is_local}
    if not selected_localparam_names:
        return
    module.parameters = [
        param for param in module.parameters if not param.is_local or param.name not in selected_localparam_names
    ]


def _selected_generate_item_ids(selected_declarations, *selected_groups: list[object]) -> set[int]:
    selected_ids = {id(item) for group in selected_groups for item in group}
    selected_ids.update(id(param) for param in selected_declarations.parameters)
    selected_ids.update(id(net) for net in selected_declarations.nets)
    selected_ids.update(id(variable) for variable in selected_declarations.variables)
    return selected_ids


def _copied_remaining_generate_constructs(
    generate_blocks: list[GenerateFor | GenerateIf | GenerateCase | GenvarDecl], selected_ids: set[int]
) -> list[GenerateFor | GenerateIf | GenerateCase | GenvarDecl]:
    kept: list[GenerateFor | GenerateIf | GenerateCase | GenvarDecl] = []
    for construct in generate_blocks:
        filtered = _copy_generate_construct_without_selected(construct, selected_ids)
        if filtered is not None:
            kept.append(filtered)
    return kept


def _copied_selected_generate_constructs(
    generate_blocks: list[GenerateFor | GenerateIf | GenerateCase | GenvarDecl],
    selected_ids: set[int],
    expr_map: dict[str, Expression],
    *,
    function_name_map: dict[str, str] | None = None,
    selected_instance_name_map: dict[str, str] | None = None,
) -> list[GenerateFor | GenerateIf | GenerateCase]:
    copied: list[GenerateFor | GenerateIf | GenerateCase] = []
    for construct in generate_blocks:
        selected = _copy_generate_construct_with_selected(
            construct,
            selected_ids,
            expr_map,
            function_name_map=function_name_map,
            selected_instance_name_map=selected_instance_name_map,
        )
        if selected is not None:
            copied.append(selected)
    return copied


def _copy_generate_construct_without_selected(  # noqa: PLR0911
    construct: GenerateFor | GenerateIf | GenerateCase | GenvarDecl, selected_ids: set[int]
) -> GenerateFor | GenerateIf | GenerateCase | GenvarDecl | None:
    if isinstance(construct, GenvarDecl):
        return copy.deepcopy(construct)
    if isinstance(construct, GenerateFor):
        body = _copy_generate_block_without_selected(construct.body, selected_ids)
        if body is None:
            return None
        clone = copy.deepcopy(construct)
        clone.body = body
        return clone
    if isinstance(construct, GenerateIf):
        then_body = (
            _copy_generate_block_without_selected(construct.then_body, selected_ids) if construct.then_body else None
        )
        else_body = (
            _copy_generate_block_without_selected(construct.else_body, selected_ids) if construct.else_body else None
        )
        if then_body is None and else_body is None:
            return None
        clone = copy.deepcopy(construct)
        clone.then_body = then_body
        clone.else_body = else_body
        return clone
    items = []
    for item in construct.items:
        body = _copy_generate_block_without_selected(item.body, selected_ids) if item.body else None
        if body is None:
            continue
        clone_item = copy.deepcopy(item)
        clone_item.body = body
        items.append(clone_item)
    if not items:
        return None
    clone = copy.deepcopy(construct)
    clone.items = items
    return clone


def _copy_generate_block_without_selected(block: GenerateBlock | None, selected_ids: set[int]) -> GenerateBlock | None:
    if block is None:
        return None
    items: list[object] = []
    for item in block.items:
        if isinstance(item, (GenerateFor, GenerateIf, GenerateCase, GenvarDecl)):
            filtered = _copy_generate_construct_without_selected(item, selected_ids)
            if filtered is not None:
                items.append(filtered)
            continue
        if id(item) in selected_ids:
            continue
        items.append(copy.deepcopy(item))
    if not items:
        return None
    clone = copy.deepcopy(block)
    clone.items = items
    return clone


def _copy_generate_construct_with_selected(  # noqa: PLR0911
    construct: GenerateFor | GenerateIf | GenerateCase | GenvarDecl,
    selected_ids: set[int],
    expr_map: dict[str, Expression],
    *,
    function_name_map: dict[str, str] | None = None,
    selected_instance_name_map: dict[str, str] | None = None,
) -> GenerateFor | GenerateIf | GenerateCase | None:
    if isinstance(construct, GenvarDecl):
        return None
    if isinstance(construct, GenerateFor):
        body = _copy_generate_block_with_selected(
            construct.body,
            selected_ids,
            expr_map,
            function_name_map=function_name_map,
            selected_instance_name_map=selected_instance_name_map,
        )
        if body is None:
            return None
        clone = copy.deepcopy(construct)
        clone.body = body
        _rewrite_node_expressions(clone, expr_map)
        if function_name_map:
            _rewrite_function_call_names(clone, function_name_map)
        return clone
    if isinstance(construct, GenerateIf):
        then_body = (
            _copy_generate_block_with_selected(
                construct.then_body,
                selected_ids,
                expr_map,
                function_name_map=function_name_map,
                selected_instance_name_map=selected_instance_name_map,
            )
            if construct.then_body
            else None
        )
        else_body = (
            _copy_generate_block_with_selected(
                construct.else_body,
                selected_ids,
                expr_map,
                function_name_map=function_name_map,
                selected_instance_name_map=selected_instance_name_map,
            )
            if construct.else_body
            else None
        )
        if then_body is None and else_body is None:
            return None
        clone = copy.deepcopy(construct)
        clone.then_body = then_body
        clone.else_body = else_body
        _rewrite_node_expressions(clone, expr_map)
        if function_name_map:
            _rewrite_function_call_names(clone, function_name_map)
        return clone
    items = []
    for item in construct.items:
        body = (
            _copy_generate_block_with_selected(
                item.body,
                selected_ids,
                expr_map,
                function_name_map=function_name_map,
                selected_instance_name_map=selected_instance_name_map,
            )
            if item.body
            else None
        )
        if body is None:
            continue
        clone_item = copy.deepcopy(item)
        clone_item.body = body
        _rewrite_node_expressions(clone_item, expr_map)
        if function_name_map:
            _rewrite_function_call_names(clone_item, function_name_map)
        items.append(clone_item)
    if not items:
        return None
    clone = copy.deepcopy(construct)
    clone.items = items
    _rewrite_node_expressions(clone, expr_map)
    if function_name_map:
        _rewrite_function_call_names(clone, function_name_map)
    return clone


def _copy_generate_block_with_selected(
    block: GenerateBlock | None,
    selected_ids: set[int],
    expr_map: dict[str, Expression],
    *,
    function_name_map: dict[str, str] | None = None,
    selected_instance_name_map: dict[str, str] | None = None,
) -> GenerateBlock | None:
    if block is None:
        return None
    items: list[object] = []
    for item in block.items:
        if isinstance(item, (GenerateFor, GenerateIf, GenerateCase, GenvarDecl)):
            selected = _copy_generate_construct_with_selected(
                item,
                selected_ids,
                expr_map,
                function_name_map=function_name_map,
                selected_instance_name_map=selected_instance_name_map,
            )
            if selected is not None:
                items.append(selected)
            continue
        if id(item) not in selected_ids:
            continue
        clone = copy.deepcopy(item)
        if isinstance(clone, Instance) and selected_instance_name_map is not None:
            clone.instance_name = selected_instance_name_map[clone.instance_name]
        _rewrite_node_expressions(clone, expr_map)
        if function_name_map:
            _rewrite_function_call_names(clone, function_name_map)
        items.append(clone)
    if not items:
        return None
    clone = copy.deepcopy(block)
    clone.items = items
    return clone


def _nodes_by_source_order(nodes: list[object]) -> list[object]:
    return sorted(
        nodes,
        key=lambda node: (
            getattr(getattr(node, "loc", None), "line", 0) or 0,
            getattr(getattr(node, "loc", None), "column", 0) or 0,
        ),
    )


def _promote_selected_child_output_ports(
    parent: Module,
    child_module: Module,
    output_port_names: list[str],
    raw_port_map: dict[str, Expression],
    expr_map: dict[str, Expression],
) -> RefactorDiagnostic | None:
    for name in output_port_names:
        child_port = child_module.get_port(name)
        if child_port is None:
            return RefactorDiagnostic(
                "selected-output-port-not-found",
                f"Selected child output port {name!r} was not found during hierarchy-up rewrite.",
                severity="error",
            )
        parent_name = _simple_identifier_name(raw_port_map.get(name))
        if parent_name is None:
            return RefactorDiagnostic(
                "output-port-connection-unsupported",
                f"Selected child output port {name!r} must connect to a simple parent signal for hierarchy-up.",
                severity="error",
            )
        promotion_port = copy.deepcopy(child_port)
        if promotion_port.data_type is None:
            promotion_port.data_type = "reg"
            promotion_port.net_type = None
        _promote_parent_signal_to_variable(parent, parent_name, promotion_port, expr_map)
    return None


def _child_constant_expression_map(
    child_module: Module,
    param_env: dict[str, Expression],
) -> dict[str, Expression] | RefactorDiagnostic:
    resolved: dict[str, Expression] = {name: copy.deepcopy(expr) for name, expr in param_env.items()}
    localparams = {param.name: param for param in child_module.parameters if param.is_local}
    visiting: set[str] = set()

    def resolve(name: str) -> RefactorDiagnostic | Expression | None:
        if name in resolved:
            return copy.deepcopy(resolved[name])
        param = localparams.get(name)
        if param is None:
            return None
        if name in visiting:
            return RefactorDiagnostic(
                "unsupported-localparam-cycle",
                f"Localparam {name!r} forms a dependency cycle and cannot be specialized for hierarchy-up.",
                severity="error",
            )
        if param.default_value is None:
            return RefactorDiagnostic(
                "localparam-default-required",
                f"Localparam {name!r} requires a value before hierarchy-up rewrite.",
                severity="error",
            )
        visiting.add(name)
        for dependency in _identifier_names(param.default_value):
            resolved_dep = resolve(dependency)
            if isinstance(resolved_dep, RefactorDiagnostic):
                visiting.remove(name)
                return resolved_dep
        rewritten = copy.deepcopy(param.default_value)
        substituted = _rewrite_expression(rewritten, resolved)
        resolved[name] = substituted if substituted is not None else rewritten
        visiting.remove(name)
        return copy.deepcopy(resolved[name])

    for param in child_module.parameters:
        if not param.is_local:
            continue
        resolved_expr = resolve(param.name)
        if isinstance(resolved_expr, RefactorDiagnostic):
            return resolved_expr
    return resolved


def _parent_net_for_child_output(
    child_module: Module,
    name: str,
    renamed_name: str,
    expr_map: dict[str, Expression],
) -> Net:
    source = _declaration_for_internal(child_module, name)
    width = copy.deepcopy(getattr(source, "width", None))
    if width is not None:
        _rewrite_node_expressions(width, expr_map)
    dimensions = copy.deepcopy(getattr(source, "dimensions", []))
    if dimensions:
        _rewrite_node_expressions(dimensions, expr_map)
    signed = bool(getattr(source, "signed", False))
    return Net(
        renamed_name,
        width=width,
        signed=signed,
        dimensions=dimensions,
    )


def _parent_variable_for_lifted_signal(
    child_module: Module,
    name: str,
    renamed_name: str,
    expr_map: dict[str, Expression],
) -> Variable:
    source = _declaration_for_internal(child_module, name)
    if isinstance(source, Variable):
        variable = copy.deepcopy(source)
    else:
        variable = Variable(
            renamed_name,
            VariableKind.REG,
            width=copy.deepcopy(getattr(source, "width", None)),
            signed=bool(getattr(source, "signed", False)),
            dimensions=copy.deepcopy(getattr(source, "dimensions", [])),
            initial_value=copy.deepcopy(getattr(source, "initial_value", None)),
        )
    variable.name = renamed_name
    _rewrite_node_expressions(variable, expr_map)
    return variable


def _named_child_site_connections(
    transformed_child: Module,
    raw_port_map: dict[str, Expression],
    rename_map: dict[str, str],
) -> list[PortConnection]:
    connections: list[PortConnection] = []
    for port in transformed_child.ports:
        if port.name in raw_port_map:
            expr = copy.deepcopy(raw_port_map[port.name])
        elif port.name in rename_map:
            expr = Identifier(rename_map[port.name])
        else:
            expr = None
        connections.append(PortConnection(port_name=port.name, expression=expr, is_named=True))
    return connections


def _module_replacement_edit(module: Module, transformed: Module) -> TextEditPlan | RefactorDiagnostic:
    if module.loc is None or not module.loc.file:
        return RefactorDiagnostic(
            "module-location-required",
            f"Cannot rewrite module {module.name!r} because its source location is missing.",
            severity="error",
        )
    edit_range = _source_line_range(module.loc)
    original = _source_text_for_range_payload(module.loc.file, edit_range)
    replacement = emit_module(transformed, emit_comments=True).rstrip() + "\n"
    return TextEditPlan(
        file=module.loc.file,
        range=edit_range,
        original=original,
        replacement=replacement,
    )


def _module_header_edit(module: Module, transformed: Module) -> TextEditPlan | RefactorDiagnostic | None:
    if not _module_header_semantics_changed(module, transformed):
        return None
    if module.loc is None or not module.loc.file:
        return RefactorDiagnostic(
            "module-location-required",
            f"Cannot rewrite module {module.name!r} because its source location is missing.",
            severity="error",
        )
    path = Path(module.loc.file)
    if not path.is_file():
        return RefactorDiagnostic(
            "module-location-required",
            f"Cannot read source for module {module.name!r}: {module.loc.file}",
            severity="error",
        )
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    start_line = max(0, (module.loc.line or 1) - 1)
    start_char = max(0, (module.loc.column or 1) - 1)
    start_offset = _line_char_offset(text, start_line, start_char)
    end_offset = _module_header_end_offset(text, start_offset)
    if end_offset is None:
        return None
    line_offsets = _line_offsets(text)
    end_position = _offset_to_position(line_offsets, end_offset)
    original = text[start_offset:end_offset]
    replacement = _module_header_text(transformed)
    if original == replacement:
        return None
    return TextEditPlan(
        file=module.loc.file,
        range={
            "start": {"line": start_line, "character": start_char},
            "end": end_position,
        },
        original=original,
        replacement=replacement,
    )


def _module_header_end_offset(text: str, start_offset: int) -> int | None:  # noqa: PLR0912, PLR0915
    paren_depth = 0
    in_line_comment = False
    in_block_comment = False
    in_string = False
    escaping = False
    index = start_offset
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            index += 1
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                index += 2
            else:
                index += 1
            continue
        if in_string:
            if escaping:
                escaping = False
            elif char == "\\":
                escaping = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            index += 2
            continue
        if char == '"':
            in_string = True
            index += 1
            continue
        if char == "(":
            paren_depth += 1
        elif char == ")" and paren_depth:
            paren_depth -= 1
        elif char == ";" and paren_depth == 0:
            end_offset = index + 1
            if end_offset < len(text) and text[end_offset] == "\n":
                end_offset += 1
            return end_offset
        index += 1
    return None


def _module_header_text(module: Module) -> str:
    header_only = copy.deepcopy(module)
    header_only.comments = []
    header_only.parameters = [param for param in header_only.parameters if not param.is_local]
    header_only.nets = []
    header_only.variables = []
    header_only.instances = []
    header_only.continuous_assigns = []
    header_only.always_blocks = []
    header_only.initial_blocks = []
    header_only.functions = []
    header_only.tasks = []
    header_only.generate_blocks = []
    header_only.specify_blocks = []
    header_only.typedefs = []
    header_only.imports = []
    header_only.interface_instances = []
    emitted = emit_module(header_only, emit_comments=True)
    return emitted.removesuffix("\nendmodule") + "\n"


def _module_header_semantics_changed(module: Module, transformed: Module) -> bool:
    return _semantic_signature(
        {
            "name": module.name,
            "parameters": [param for param in module.parameters if not param.is_local],
            "ports": list(module.ports),
        }
    ) != _semantic_signature(
        {
            "name": transformed.name,
            "parameters": [param for param in transformed.parameters if not param.is_local],
            "ports": list(transformed.ports),
        }
    )


def _design_wide_parent_header_and_declaration_edits(
    parent_module: Module,
    transformed_parent: Module,
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic:
    edits: list[TextEditPlan] = []
    header_edit = _module_header_edit(parent_module, transformed_parent)
    if isinstance(header_edit, RefactorDiagnostic):
        return header_edit
    if header_edit is not None:
        edits.append(header_edit)
    declaration_edits = _top_level_declaration_replacement_edits(parent_module, transformed_parent)
    if isinstance(declaration_edits, RefactorDiagnostic):
        return declaration_edits
    edits.extend(declaration_edits)
    return tuple(edits)


def _top_level_declaration_replacement_edits(
    parent_module: Module,
    transformed_parent: Module,
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic:
    edits: list[TextEditPlan] = []
    transformed_by_name: dict[str, Net | Variable] = {
        **{net.name: net for net in transformed_parent.nets},
        **{variable.name: variable for variable in transformed_parent.variables},
    }
    for original in [*parent_module.nets, *parent_module.variables]:
        transformed = transformed_by_name.get(original.name)
        if transformed is None:
            continue
        if _semantic_signature(original) == _semantic_signature(transformed):
            continue
        original_source, original_range = _node_source_and_range(
            original,
            error_code="parent-declaration-location-required",
            description=f"parent declaration {original.name!r}",
            whole_lines=True,
            include_leading_attribute_lines=True,
        )
        if isinstance(original_source, RefactorDiagnostic):
            return original_source
        replacement = _emit_module_item_text(transformed)
        if original_source == replacement:
            continue
        edits.append(
            TextEditPlan(
                file=original.loc.file if original.loc and original.loc.file else "",
                range=original_range,
                original=original_source,
                replacement=replacement,
            )
        )
    return tuple(edits)


def _top_level_parent_instance_source_and_range(
    parent_module: Module,
    parent_instance: Instance,
) -> tuple[str | RefactorDiagnostic | None, dict[str, object] | None]:
    if not any(_same_instance_site(instance, parent_instance) for instance in parent_module.instances):
        return None, None
    instance_source, instance_range = _instance_source_and_range(parent_instance)
    if isinstance(instance_source, RefactorDiagnostic):
        return instance_source, None
    if _has_top_level_instance_separator(instance_source):
        return None, None
    return instance_source, instance_range


def _emit_module_items_text(items: list[object]) -> str:
    return "".join(_emit_module_item_text(item) for item in items)


def _semantic_signature(value: object) -> object:
    if isinstance(value, VerilogNode):
        return _semantic_signature(value.to_dict())
    if isinstance(value, dict):
        return {key: _semantic_signature(nested) for key, nested in value.items() if key not in {"loc", "comments"}}
    if isinstance(value, list):
        return [_semantic_signature(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_semantic_signature(item) for item in value)
    return value


def _emit_module_item_text(item: object) -> str:  # noqa: PLR0911
    if isinstance(item, Parameter):
        return _module_body_text(parameters=[item])
    if isinstance(item, Net):
        return _module_body_text(nets=[item])
    if isinstance(item, Variable):
        return _module_body_text(variables=[item])
    if isinstance(item, ContinuousAssign):
        return _module_body_text(continuous_assigns=[item])
    if isinstance(item, Instance):
        return _module_body_text(instances=[item])
    if isinstance(item, AlwaysBlock):
        return _module_body_text(always_blocks=[item])
    if isinstance(item, InitialBlock):
        return _module_body_text(initial_blocks=[item])
    if isinstance(item, FunctionDecl):
        return _module_body_text(functions=[item])
    raise TypeError(f"Unsupported module item type for localized hierarchy-up edit: {type(item)!r}")


def _build_pull_up_edit_from_chain(  # noqa: PLR0911
    chain: list[tuple[Module, Instance, Module]],
    selected_module: Module,
    parent_module_name: str,
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic:
    target_parent_module, outermost_instance, _outermost_child = chain[0]
    deepest_parent_module, selected_instance, deepest_child = chain[-1]
    if deepest_child.name != selected_module.name:
        return RefactorDiagnostic(
            "selected-module-mismatch",
            "Resolved chain endpoint does not match the selection's resolved module.",
            severity="error",
        )
    if deepest_parent_module.name != parent_module_name:
        return RefactorDiagnostic(
            "parent-mismatch",
            "Resolved parent does not match the hierarchy boundary preview parent.",
            severity="error",
        )
    if selected_instance.instance_array is not None:
        return RefactorDiagnostic(
            "instance-array-unsupported",
            "Pull-up rewrite does not yet support instance arrays.",
        )
    unsupported = _unsupported_pull_up_features(selected_module)
    if unsupported is not None:
        return unsupported

    for index, (_intermediate_parent, _intermediate_inst, intermediate_child) in enumerate(chain[:-1]):
        next_inst_name = chain[index + 1][1].instance_name
        diag = _intermediate_erasable_diagnostic(intermediate_child, next_inst_name)
        if diag is not None:
            return diag

    composed_env: dict[str, Expression] = {}
    for _parent_mod, instance, child_module in chain:
        raw_param_map = _parameter_expression_map(child_module, instance)
        if isinstance(raw_param_map, RefactorDiagnostic):
            return raw_param_map
        raw_port_map = _port_expression_map(child_module, instance)
        if isinstance(raw_port_map, RefactorDiagnostic):
            return raw_port_map
        next_env: dict[str, Expression] = {}
        for name, expr in {**raw_param_map, **raw_port_map}.items():
            cloned = copy.deepcopy(expr)
            substituted = _rewrite_expression(cloned, composed_env)
            next_env[name] = substituted if substituted is not None else cloned
        composed_env = next_env

    composed_port_map = {p.name: composed_env[p.name] for p in selected_module.ports if p.name in composed_env}
    rename_prefix = "__".join(link[1].instance_name for link in chain)
    return _emit_pull_up_inline_edit(
        target_parent=target_parent_module,
        outermost_instance=outermost_instance,
        selected_module=selected_module,
        composed_env=composed_env,
        composed_port_map=composed_port_map,
        rename_prefix=rename_prefix,
    )


def _emit_pull_up_inline_edit(  # noqa: PLR0911, PLR0913
    target_parent: Module,
    outermost_instance: Instance,
    selected_module: Module,
    composed_env: dict[str, Expression],
    composed_port_map: dict[str, Expression],
    rename_prefix: str,
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic:
    if outermost_instance.loc is None or not outermost_instance.loc.file:
        return RefactorDiagnostic(
            "instance-location-required",
            "Cannot minimally rewrite pull-up because the selected instance has no source location.",
            severity="error",
        )
    instance_source, instance_range = _instance_source_and_range(outermost_instance)
    if isinstance(instance_source, RefactorDiagnostic):
        return instance_source
    if _has_top_level_instance_separator(instance_source):
        return RefactorDiagnostic(
            "multi-instance-statement-unsupported",
            "Minimal pull-up edits do not yet support replacing one instance inside a multi-instance statement.",
            severity="error",
        )

    transformed = copy.deepcopy(target_parent)
    selected_copy = next(
        (
            inst
            for inst in transformed.instances
            if inst.instance_name == outermost_instance.instance_name
            and inst.module_name == outermost_instance.module_name
        ),
        None,
    )
    if selected_copy is None:
        return RefactorDiagnostic(
            "selected-instance-not-found",
            f"Selected instance {outermost_instance.instance_name!r} was not found in the copied parent module.",
            severity="error",
        )

    existing_names = _module_declared_names(transformed)
    internal_names = _child_internal_names(selected_module)
    rename_map = {name: _unique_name(f"{rename_prefix}__{name}", existing_names) for name in sorted(internal_names)}
    expr_map: dict[str, Expression] = {
        **{name: copy.deepcopy(expr) for name, expr in composed_env.items()},
        **{name: Identifier(new_name) for name, new_name in rename_map.items()},
    }

    promoted_parent_names = _connected_output_reg_parent_names(selected_module, composed_port_map)
    port_promotions = promoted_parent_names & {port.name for port in target_parent.ports}
    if port_promotions:
        return RefactorDiagnostic(
            "port-promotion-minimal-edit-unsupported",
            "Minimal pull-up edits do not yet support promoting parent ports to regs: "
            + ", ".join(sorted(port_promotions))
            + ".",
        )

    conversion = _promote_connected_output_regs(transformed, selected_module, composed_port_map, expr_map)
    if conversion is not None:
        return conversion

    copied_functions, function_name_map = _copy_referenced_child_functions(
        selected_module,
        [
            *selected_module.parameters,
            *selected_module.continuous_assigns,
            *selected_module.always_blocks,
            *selected_module.initial_blocks,
        ],
        existing_names,
        name_prefix=rename_prefix,
        expr_map=expr_map,
    )
    if isinstance(copied_functions, RefactorDiagnostic):
        return copied_functions
    localparams = _copied_localparams(selected_module, rename_map, expr_map)
    nets = _copied_nets(selected_module, rename_map, expr_map)
    variables = _copied_variables(selected_module, rename_map, expr_map)
    continuous_assigns = _copied_nodes(selected_module.continuous_assigns, expr_map)
    always_blocks = _copied_nodes(selected_module.always_blocks, expr_map)
    initial_blocks = _copied_nodes(selected_module.initial_blocks, expr_map)
    child_instances = _copied_child_instances(selected_module, rename_map, expr_map)
    for collection in (
        localparams,
        nets,
        variables,
        continuous_assigns,
        always_blocks,
        initial_blocks,
        child_instances,
    ):
        _rewrite_function_call_names(collection, function_name_map)

    promoted_edits = _promoted_parent_net_edits(target_parent, transformed, promoted_parent_names)
    if isinstance(promoted_edits, RefactorDiagnostic):
        return promoted_edits

    replacement = _module_body_text(
        parameters=localparams,
        nets=nets,
        variables=variables,
        continuous_assigns=continuous_assigns,
        instances=child_instances,
        always_blocks=always_blocks,
        initial_blocks=initial_blocks,
        functions=copied_functions,
    )
    if replacement and not replacement.endswith("\n"):
        replacement += "\n"
    return (
        *promoted_edits,
        TextEditPlan(
            file=outermost_instance.loc.file if outermost_instance.loc and outermost_instance.loc.file else "",
            range=instance_range,
            original=instance_source,
            replacement=replacement,
        ),
    )


def _validate_target_parent_on_path(target_parent_path: str, instance_path: str) -> RefactorDiagnostic | None:
    target_parts = [part for part in target_parent_path.split("/") if part]
    instance_parts = [part for part in instance_path.split("/") if part]
    if not target_parts:
        return None
    if len(target_parts) >= len(instance_parts) or instance_parts[: len(target_parts)] != target_parts:
        return RefactorDiagnostic(
            "target-parent-not-on-instance-path",
            (f"Target parent {target_parent_path!r} is not a strict ancestor of selection {instance_path!r}."),
            severity="error",
        )
    return None


def _resolve_pull_up_chain(
    design: Design, instance_path: str, target_parent_path: str
) -> list[tuple[Module, Instance, Module]] | RefactorDiagnostic:
    instance_parts = [part for part in instance_path.split("/") if part]
    if len(instance_parts) < _MIN_INSTANCE_PATH_PARTS:
        return RefactorDiagnostic("instance-not-found", f"Instance path not found: {instance_path}.", severity="error")
    if target_parent_path:
        target_parts = [part for part in target_parent_path.split("/") if part]
        if len(target_parts) >= len(instance_parts) or instance_parts[: len(target_parts)] != target_parts:
            return RefactorDiagnostic(
                "target-parent-not-on-instance-path",
                (f"Target parent {target_parent_path!r} is not a strict ancestor of selection {instance_path!r}."),
                severity="error",
            )
        start_index = len(target_parts)
    else:
        start_index = len(instance_parts) - 1

    target_parent_module = design.get_module(instance_parts[0])
    if target_parent_module is None:
        return RefactorDiagnostic("instance-not-found", f"Instance path not found: {instance_path}.", severity="error")
    walker = target_parent_module
    for inst_name in instance_parts[1:start_index]:
        match = next((i for i in walker.instances if i.instance_name == inst_name), None)
        if match is None or match.resolved_module is None:
            return RefactorDiagnostic(
                "instance-not-found", f"Instance path not found: {instance_path}.", severity="error"
            )
        walker = match.resolved_module

    chain: list[tuple[Module, Instance, Module]] = []
    for inst_name in instance_parts[start_index:]:
        match = next((i for i in walker.instances if i.instance_name == inst_name), None)
        if match is None or match.resolved_module is None:
            return RefactorDiagnostic(
                "instance-not-found", f"Instance path not found: {instance_path}.", severity="error"
            )
        chain.append((walker, match, match.resolved_module))
        walker = match.resolved_module
    return chain


def _intermediate_erasable_diagnostic(module: Module, expected_child_instance_name: str) -> RefactorDiagnostic | None:
    issues: list[str] = []
    if len(module.instances) != 1:
        issues.append(f"has {len(module.instances)} instances (expected 1)")
    elif module.instances[0].instance_name != expected_child_instance_name:
        issues.append(
            f"single instance {module.instances[0].instance_name!r} does not match "
            f"chain step {expected_child_instance_name!r}"
        )
    if module.continuous_assigns:
        issues.append("contains continuous assigns")
    if module.always_blocks:
        issues.append("contains always blocks")
    if module.initial_blocks:
        issues.append("contains initial blocks")
    if module.nets:
        issues.append("contains nets")
    if module.variables:
        issues.append("contains variables")
    if any(p.is_local for p in module.parameters):
        issues.append("contains localparams")
    base = _unsupported_pull_up_features(module)
    if base is not None:
        issues.append(base.message)
    if not issues:
        return None
    return RefactorDiagnostic(
        "intermediate-wrapper-not-erasable",
        f"Intermediate wrapper {module.name!r} cannot be erased for multi-level pull-up: " + "; ".join(issues) + ".",
        severity="error",
    )


def _unsupported_pull_up_features(module: Module) -> RefactorDiagnostic | None:
    unsupported: list[str] = []
    if module.interface_instances:
        unsupported.append("interface instances")
    if module.generate_blocks:
        unsupported.append("generate blocks")
    if module.specify_blocks:
        unsupported.append("specify blocks")
    if module.typedefs:
        unsupported.append("typedefs")
    if module.imports:
        unsupported.append("imports")
    if module.tasks:
        unsupported.append("tasks")
    if not unsupported:
        return None
    return RefactorDiagnostic(
        "unsupported-pull-up-body",
        "Pull-up rewrite does not yet support child modules with " + ", ".join(unsupported) + ".",
    )


def _parameter_expression_map(module: Module, instance: Instance) -> dict[str, Expression] | RefactorDiagnostic:
    values: dict[str, Expression] = {}
    nonlocal_params = [param for param in module.parameters if not param.is_local]
    for param in nonlocal_params:
        if param.default_value is None:
            return RefactorDiagnostic(
                "parameter-default-required",
                f"Parameter {param.name!r} requires a value before pull-up rewrite.",
                severity="error",
            )
        values[param.name] = copy.deepcopy(param.default_value)
    for index, binding in enumerate(instance.parameter_bindings):
        name = binding.name
        if name is None:
            if index >= len(nonlocal_params):
                return RefactorDiagnostic(
                    "parameter-binding-out-of-range",
                    f"Ordered parameter binding {index} has no matching parameter.",
                    severity="error",
                )
            name = nonlocal_params[index].name
        if binding.value is None:
            return RefactorDiagnostic(
                "parameter-binding-value-required",
                f"Parameter binding {name!r} does not have a value.",
                severity="error",
            )
        values[name] = copy.deepcopy(binding.value)
    return values


def _port_expression_map(
    module: Module, instance: Instance, *, require_all_ports: bool = True
) -> dict[str, Expression] | RefactorDiagnostic:
    values: dict[str, Expression] = {}
    for index, conn in enumerate(instance.port_connections):
        name = conn.port_name
        if not conn.is_named:
            if index >= len(module.ports):
                return RefactorDiagnostic(
                    "port-connection-out-of-range",
                    f"Ordered port connection {index} has no matching port.",
                    severity="error",
                )
            name = module.ports[index].name
        if name is None:
            return RefactorDiagnostic(
                "port-name-required", "Pull-up rewrite requires named or resolvable ports.", severity="error"
            )
        if conn.expression is None:
            if require_all_ports:
                return RefactorDiagnostic(
                    "unconnected-port-unsupported",
                    f"Pull-up rewrite does not yet support unconnected port {name!r}.",
                )
            continue
        values[name] = copy.deepcopy(conn.expression)
    missing = [port.name for port in module.ports if port.name not in values]
    if require_all_ports and missing:
        return RefactorDiagnostic(
            "missing-port-connections",
            "Pull-up rewrite requires explicit connections for all child ports: " + ", ".join(missing) + ".",
        )
    return values


def _design_wide_child_module_edits(
    child_module: Module,
    transformed_child: Module,
    selected_declarations,
    *selected_groups: list[object],
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic | None:
    if not _can_localize_design_wide_child_edit(child_module, selected_declarations, *selected_groups):
        return None

    edits: list[TextEditPlan] = []
    header_edit = _module_header_edit(child_module, transformed_child)
    if isinstance(header_edit, RefactorDiagnostic):
        return header_edit
    if header_edit is not None:
        edits.append(header_edit)

    removed_names = _removed_top_level_child_names(child_module, transformed_child)
    removal_items = _top_level_child_removals(child_module, selected_declarations, removed_names, *selected_groups)
    seen_ranges: set[tuple[int, int, int, int]] = set()
    for node, description in removal_items:
        source, edit_range = _node_source_and_range(
            node,
            error_code="child-node-location-required",
            description=description,
            whole_lines=True,
            include_leading_attribute_lines=True,
        )
        if isinstance(source, RefactorDiagnostic):
            return source
        key = (
            int(((edit_range.get("start", {}) if isinstance(edit_range, dict) else {}).get("line", 0))),
            int(((edit_range.get("start", {}) if isinstance(edit_range, dict) else {}).get("character", 0))),
            int(((edit_range.get("end", {}) if isinstance(edit_range, dict) else {}).get("line", 0))),
            int(((edit_range.get("end", {}) if isinstance(edit_range, dict) else {}).get("character", 0))),
        )
        if key in seen_ranges:
            continue
        seen_ranges.add(key)
        edits.append(
            TextEditPlan(
                file=node.loc.file if node.loc and node.loc.file else "",
                range=edit_range,
                original=source,
                replacement="",
            )
        )
    return tuple(edits)


def _can_localize_design_wide_child_edit(
    child_module: Module,
    selected_declarations,
    *selected_groups: list[object],
) -> bool:
    top_level_localparams = {id(param) for param in child_module.parameters if param.is_local}
    top_level_nets = {id(net) for net in child_module.nets}
    top_level_variables = {id(variable) for variable in child_module.variables}
    top_level_nodes = (
        {id(assign) for assign in child_module.continuous_assigns}
        | {id(block) for block in child_module.always_blocks}
        | {id(block) for block in child_module.initial_blocks}
        | {id(instance) for instance in child_module.instances}
    )
    if any(param.is_local and id(param) not in top_level_localparams for param in selected_declarations.parameters):
        return False
    if any(id(net) not in top_level_nets for net in selected_declarations.nets):
        return False
    if any(id(variable) not in top_level_variables for variable in selected_declarations.variables):
        return False
    return all(id(item) in top_level_nodes for group in selected_groups for item in group)


def _removed_top_level_child_names(child_module: Module, transformed_child: Module) -> dict[str, set[str]]:
    return {
        "localparams": {param.name for param in child_module.parameters if param.is_local}
        - {param.name for param in transformed_child.parameters if param.is_local},
        "nets": {net.name for net in child_module.nets} - {net.name for net in transformed_child.nets},
        "variables": {variable.name for variable in child_module.variables}
        - {variable.name for variable in transformed_child.variables},
    }


def _top_level_child_removals(
    child_module: Module,
    selected_declarations,
    removed_names: dict[str, set[str]],
    *selected_groups: list[object],
) -> list[tuple[VerilogNode, str]]:
    removals: list[tuple[VerilogNode, str]] = []
    selected_group_ids = {id(item) for group in selected_groups for item in group}
    removals.extend(
        (param, f"child localparam {param.name!r}")
        for param in child_module.parameters
        if param.is_local and param.name in removed_names["localparams"]
    )
    removals.extend((net, f"child net {net.name!r}") for net in child_module.nets if net.name in removed_names["nets"])
    removals.extend(
        (variable, f"child variable {variable.name!r}")
        for variable in child_module.variables
        if variable.name in removed_names["variables"]
    )
    removals.extend(
        (assign, "selected child assign")
        for assign in child_module.continuous_assigns
        if id(assign) in selected_group_ids
    )
    removals.extend(
        (block, "selected child always block")
        for block in child_module.always_blocks
        if id(block) in selected_group_ids
    )
    removals.extend(
        (block, "selected child initial block")
        for block in child_module.initial_blocks
        if id(block) in selected_group_ids
    )
    removals.extend(
        (instance, f"selected child instance {instance.instance_name!r}")
        for instance in child_module.instances
        if id(instance) in selected_group_ids
    )
    return removals


def _module_declared_names(module: Module) -> set[str]:
    names = {param.name for param in module.parameters}
    names.update(port.name for port in module.ports)
    names.update(net.name for net in module.nets)
    names.update(var.name for var in module.variables)
    names.update(inst.instance_name for inst in module.instances)
    names.update(fn.name for fn in module.functions)
    names.update(task.name for task in module.tasks)
    names.update(_generate_declared_names(module))
    return names


def _child_internal_names(module: Module) -> set[str]:
    names = {param.name for param in module.parameters if param.is_local}
    names.update(net.name for net in module.nets)
    names.update(var.name for var in module.variables)
    names.update(inst.instance_name for inst in module.instances)
    return names


def _unique_name(base: str, existing: set[str]) -> str:
    candidate = base
    suffix = 1
    while candidate in existing:
        suffix += 1
        candidate = f"{base}_{suffix}"
    existing.add(candidate)
    return candidate


def _instance_targets_module(instance: Instance, module_name: str) -> bool:
    resolved = instance.resolved_module
    if resolved is not None:
        return resolved.name == module_name
    return instance.module_name == module_name


def _generate_declared_names(module: Module) -> set[str]:
    names: set[str] = set()
    for generate in module.generate_blocks:
        names.update(param.name for param in generate.find(Parameter))
        names.update(net.name for net in generate.find(Net))
        names.update(var.name for var in generate.find(Variable))
        names.update(inst.instance_name for inst in generate.find(Instance))
        for genvar in generate.find(GenvarDecl):
            names.update(genvar.names)
    return names


def _find_transformed_instance_site(
    transformed_parent: Module, parent_module: Module, parent_instance: Instance
) -> tuple[Instance, GenerateBlock | None, int | None] | RefactorDiagnostic:
    for inst in transformed_parent.instances:
        if _same_instance_site(inst, parent_instance):
            return inst, None, None
    for generate in transformed_parent.generate_blocks:
        found = _find_instance_site_in_generate_construct(generate, parent_instance)
        if found is not None:
            return found
    return RefactorDiagnostic(
        "selected-instance-not-found",
        f"Instance {parent_instance.instance_name!r} was not found in transformed parent {parent_module.name!r}.",
        severity="error",
    )


def _find_instance_site_in_generate_construct(
    construct: GenerateFor | GenerateIf | GenerateCase | GenvarDecl,
    target: Instance,
) -> tuple[Instance, GenerateBlock, int] | None:
    if isinstance(construct, GenerateFor):
        return _find_instance_site_in_generate_block(construct.body, target)
    if isinstance(construct, GenerateIf):
        if construct.then_body is not None:
            found = _find_instance_site_in_generate_block(construct.then_body, target)
            if found is not None:
                return found
        if construct.else_body is not None:
            return _find_instance_site_in_generate_block(construct.else_body, target)
        return None
    if isinstance(construct, GenerateCase):
        for item in construct.items:
            if item.body is None:
                continue
            found = _find_instance_site_in_generate_block(item.body, target)
            if found is not None:
                return found
    return None


def _find_instance_site_in_generate_block(
    block: GenerateBlock, target: Instance
) -> tuple[Instance, GenerateBlock, int] | None:
    for index, item in enumerate(block.items):
        if isinstance(item, Instance) and _same_instance_site(item, target):
            return item, block, index
        if isinstance(item, (GenerateFor, GenerateIf, GenerateCase)):
            found = _find_instance_site_in_generate_construct(item, target)
            if found is not None:
                return found
    return None


def _same_instance_site(candidate: Instance, target: Instance) -> bool:
    if candidate.instance_name != target.instance_name or candidate.module_name != target.module_name:
        return False
    candidate_loc = candidate.loc
    target_loc = target.loc
    if candidate_loc is None or target_loc is None:
        return True
    return (
        candidate_loc.file == target_loc.file
        and candidate_loc.line == target_loc.line
        and candidate_loc.column == target_loc.column
        and candidate_loc.end_line == target_loc.end_line
        and candidate_loc.end_column == target_loc.end_column
    )


def _connected_output_reg_parent_names(child: Module, port_map: dict[str, Expression]) -> set[str]:
    names: set[str] = set()
    for port in child.ports:
        if port.direction != PortDirection.OUTPUT or (port.data_type != "reg" and port.default_value is None):
            continue
        parent_name = _simple_identifier_name(port_map.get(port.name))
        if parent_name is not None:
            names.add(parent_name)
    return names


def _promoted_parent_net_edits(
    parent: Module,
    transformed: Module,
    promoted_parent_names: set[str],
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic:
    edits: list[TextEditPlan] = []
    for name in sorted(promoted_parent_names):
        original_net = parent.get_net(name)
        if original_net is None:
            continue
        transformed_var = transformed.get_variable(name)
        if transformed_var is None:
            return RefactorDiagnostic(
                "promoted-variable-not-found",
                f"Promoted parent signal {name!r} was not found in the transformed parent.",
                severity="error",
            )
        original_source, original_range = _node_source_and_range(
            original_net,
            error_code="promoted-net-location-required",
            description=f"promoted parent net {name!r}",
        )
        if isinstance(original_source, RefactorDiagnostic):
            return original_source
        edits.append(
            TextEditPlan(
                file=original_net.loc.file,
                range=original_range,
                original=original_source,
                replacement=_module_body_text(variables=[transformed_var]),
            )
        )
    return tuple(edits)


def _promote_connected_output_regs(
    parent: Module,
    child: Module,
    port_map: dict[str, Expression],
    expr_map: dict[str, Expression],
) -> RefactorDiagnostic | None:
    for port in child.ports:
        if port.direction == PortDirection.INOUT:
            return RefactorDiagnostic(
                "inout-port-unsupported",
                f"Pull-up rewrite does not yet support inout port {port.name!r}.",
            )
        if port.direction != PortDirection.OUTPUT or (port.data_type != "reg" and port.default_value is None):
            continue
        parent_name = _simple_identifier_name(port_map.get(port.name))
        if parent_name is None:
            return RefactorDiagnostic(
                "output-reg-connection-unsupported",
                f"Output reg port {port.name!r} must connect to a simple parent signal for pull-up rewrite.",
                severity="error",
            )
        _promote_parent_signal_to_variable(parent, parent_name, port, expr_map)
    return None


def _promote_parent_signal_to_variable(
    parent: Module,
    parent_name: str,
    child_port: Port,
    expr_map: dict[str, Expression],
) -> None:
    initial_value = (
        _rewrite_expression(copy.deepcopy(child_port.default_value), expr_map) if child_port.default_value else None
    )
    for port in parent.ports:
        if port.name == parent_name:
            if child_port.data_type is not None:
                port.data_type = child_port.data_type
                port.net_type = None
            elif child_port.default_value is not None:
                port.data_type = "reg"
                port.net_type = None
            if initial_value is not None and port.default_value is None:
                port.default_value = initial_value
            return
    for variable in parent.variables:
        if variable.name == parent_name:
            if initial_value is not None and variable.initial_value is None:
                variable.initial_value = initial_value
            return
    for index, net in enumerate(parent.nets):
        if net.name != parent_name:
            continue
        variable = Variable(
            parent_name,
            VariableKind.REG,
            width=copy.deepcopy(net.width) if net.width is not None else _rewritten_range(child_port.width, expr_map),
            signed=net.signed or child_port.signed,
            dimensions=copy.deepcopy(net.dimensions),
            initial_value=initial_value,
            loc=copy.deepcopy(net.loc),
        )
        variable.comments = copy.deepcopy(net.comments)
        variable.attributes = copy.deepcopy(net.attributes)
        parent.nets.pop(index)
        parent.variables.append(variable)
        return
    parent.variables.append(
        Variable(
            parent_name,
            VariableKind.REG,
            width=_rewritten_range(child_port.width, expr_map),
            signed=child_port.signed,
            dimensions=copy.deepcopy(child_port.dimensions),
            initial_value=initial_value,
        )
    )


def _copied_localparams(module: Module, rename_map: dict[str, str], expr_map: dict[str, Expression]) -> list[Parameter]:
    copied: list[Parameter] = []
    for param in module.parameters:
        if not param.is_local:
            continue
        clone = copy.deepcopy(param)
        clone.name = rename_map[param.name]
        _rewrite_node_expressions(clone, expr_map)
        copied.append(clone)
    return copied


def _copied_nets(module: Module, rename_map: dict[str, str], expr_map: dict[str, Expression]) -> list[Net]:
    copied: list[Net] = []
    for net in module.nets:
        clone = copy.deepcopy(net)
        clone.name = rename_map[net.name]
        _rewrite_node_expressions(clone, expr_map)
        copied.append(clone)
    return copied


def _copied_variables(module: Module, rename_map: dict[str, str], expr_map: dict[str, Expression]) -> list[Variable]:
    copied: list[Variable] = []
    for variable in module.variables:
        clone = copy.deepcopy(variable)
        clone.name = rename_map[variable.name]
        _rewrite_node_expressions(clone, expr_map)
        copied.append(clone)
    return copied


def _copied_nodes(nodes: list, expr_map: dict[str, Expression]) -> list:
    copied = []
    for node in nodes:
        clone = copy.deepcopy(node)
        _rewrite_node_expressions(clone, expr_map)
        copied.append(clone)
    return copied


def _copied_child_instances(
    child: Module,
    rename_map: dict[str, str],
    expr_map: dict[str, Expression],
) -> list[Instance]:
    instances: list[Instance] = []
    for child_instance in child.instances:
        clone = copy.deepcopy(child_instance)
        clone.instance_name = rename_map[child_instance.instance_name]
        _rewrite_node_expressions(clone, expr_map)
        instances.append(clone)
    return instances


def _copied_selected_instances(
    selected_instances: list[Instance],
    instance_name_map: dict[str, str],
    expr_map: dict[str, Expression],
) -> list[Instance]:
    instances: list[Instance] = []
    for selected_instance in selected_instances:
        clone = copy.deepcopy(selected_instance)
        clone.instance_name = instance_name_map[selected_instance.instance_name]
        _rewrite_node_expressions(clone, expr_map)
        instances.append(clone)
    return instances


def _copy_referenced_child_functions(
    child_module: Module,
    roots: list[object],
    existing_names: set[str],
    *,
    name_prefix: str,
    expr_map: dict[str, Expression],
) -> tuple[list[FunctionDecl], dict[str, str]] | RefactorDiagnostic:
    if not child_module.functions:
        return [], {}

    child_functions = {fn.name: fn for fn in child_module.functions}
    referenced_names: set[str] = set()
    pending = list(_subroutine_refs(*roots))
    while pending:
        name = pending.pop()
        if name in referenced_names:
            continue
        function = child_functions.get(name)
        if function is None:
            return RefactorDiagnostic(
                "unsupported-child-subroutine-reference",
                (
                    f"Pulled-up logic references user-defined subroutine {name!r}, but only child-module "
                    "functions can be copied during hierarchy-up today."
                ),
                severity="error",
            )
        referenced_names.add(name)
        pending.extend(_subroutine_refs(function))

    if not referenced_names:
        return [], {}

    ordered = [fn for fn in child_module.functions if fn.name in referenced_names]
    function_name_map = {fn.name: _unique_name(f"{name_prefix}__{fn.name}", existing_names) for fn in ordered}
    copied_functions: list[FunctionDecl] = []
    for function in ordered:
        clone = copy.deepcopy(function)
        clone.name = function_name_map[function.name]
        _rewrite_node_expressions(clone, expr_map)
        _rewrite_function_call_names(clone, function_name_map)
        copied_functions.append(clone)
    return copied_functions, function_name_map


def _pulled_up_instances(
    parent_instances: list[Instance],
    selected_instance: Instance,
    child: Module,
    rename_map: dict[str, str],
    expr_map: dict[str, Expression],
) -> list[Instance]:
    result: list[Instance] = []
    for instance in parent_instances:
        if instance is not selected_instance:
            result.append(instance)
            continue
        for child_instance in child.instances:
            clone = copy.deepcopy(child_instance)
            clone.instance_name = rename_map[child_instance.instance_name]
            _rewrite_node_expressions(clone, expr_map)
            result.append(clone)
    return result


def _module_body_text(  # noqa: PLR0913
    *,
    parameters: list[Parameter] | None = None,
    nets: list[Net] | None = None,
    variables: list[Variable] | None = None,
    continuous_assigns: list | None = None,
    instances: list[Instance] | None = None,
    always_blocks: list | None = None,
    initial_blocks: list | None = None,
    functions: list[FunctionDecl] | None = None,
) -> str:
    module = Module(
        "__pulled_up__",
        parameters=parameters or [],
        nets=nets or [],
        variables=variables or [],
        instances=instances or [],
        continuous_assigns=continuous_assigns or [],
    )
    module.always_blocks = always_blocks or []
    module.initial_blocks = initial_blocks or []
    module.functions = functions or []
    lines = emit_module(module, emit_comments=True).splitlines()
    body = lines[1:-1]
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body) + ("\n" if body else "")


def _has_top_level_instance_separator(text: str) -> bool:
    depth = 0
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            return True
        elif char == ";" and depth == 0:
            return False
    return False


_REWRITE_SKIP_SLOTS = frozenset(
    {
        "_parse_tree",
        "attributes",
        "comments",
        "drivers",
        "loads",
        "loc",
        "parent",
        "resolved",
        "resolved_module",
        "resolved_port",
    }
)


def _rewrite_node_expressions(node: object, expr_map: dict[str, Expression]) -> object:
    if isinstance(node, Expression):
        return _rewrite_expression(node, expr_map)
    if isinstance(node, Range):
        node.msb = _rewrite_expression(node.msb, expr_map)
        node.lsb = _rewrite_expression(node.lsb, expr_map)
        return node
    if isinstance(node, list):
        for index, item in enumerate(node):
            node[index] = _rewrite_node_expressions(item, expr_map)
        return node
    if isinstance(node, tuple):
        return tuple(_rewrite_node_expressions(item, expr_map) for item in node)
    if not isinstance(node, VerilogNode):
        return node
    for klass in type(node).__mro__:
        for slot in getattr(klass, "__slots__", ()):
            if slot in _REWRITE_SKIP_SLOTS:
                continue
            try:
                value = getattr(node, slot)
            except AttributeError:
                continue
            rewritten = _rewrite_node_expressions(value, expr_map)
            if rewritten is not value:
                object.__setattr__(node, slot, rewritten)
    return node


def _rewrite_expression(expr: Expression | None, expr_map: dict[str, Expression]) -> Expression | None:
    if expr is None:
        return None
    if isinstance(expr, Identifier) and not expr.hierarchy and expr.name in expr_map:
        return copy.deepcopy(expr_map[expr.name])
    for klass in type(expr).__mro__:
        for slot in getattr(klass, "__slots__", ()):
            if slot in _REWRITE_SKIP_SLOTS:
                continue
            try:
                value = getattr(expr, slot)
            except AttributeError:
                continue
            rewritten = _rewrite_node_expressions(value, expr_map)
            if rewritten is not value:
                object.__setattr__(expr, slot, rewritten)
    return expr


def _rewrite_function_call_names(node: object, function_name_map: dict[str, str]) -> object:  # noqa: PLR0911, PLR0912
    if not function_name_map:
        return node
    if isinstance(node, FunctionCall):
        node.arguments = [_rewrite_function_call_names(argument, function_name_map) for argument in node.arguments]
        if not node.is_system and node.name in function_name_map:
            object.__setattr__(node, "name", function_name_map[node.name])
        return node
    if isinstance(node, Range):
        node.msb = _rewrite_function_call_names(node.msb, function_name_map)
        node.lsb = _rewrite_function_call_names(node.lsb, function_name_map)
        return node
    if isinstance(node, list):
        for index, item in enumerate(node):
            node[index] = _rewrite_function_call_names(item, function_name_map)
        return node
    if isinstance(node, tuple):
        return tuple(_rewrite_function_call_names(item, function_name_map) for item in node)
    if not isinstance(node, VerilogNode):
        return node
    for klass in type(node).__mro__:
        for slot in getattr(klass, "__slots__", ()):
            if slot in _REWRITE_SKIP_SLOTS:
                continue
            try:
                value = getattr(node, slot)
            except AttributeError:
                continue
            rewritten = _rewrite_function_call_names(value, function_name_map)
            if rewritten is not value:
                object.__setattr__(node, slot, rewritten)
    return node


def _rewritten_range(range_: Range | None, expr_map: dict[str, Expression]) -> Range | None:
    if range_ is None:
        return None
    clone = copy.deepcopy(range_)
    clone.msb = _rewrite_expression(clone.msb, expr_map)
    clone.lsb = _rewrite_expression(clone.lsb, expr_map)
    return clone


def _count_module_instance_sites(design: Design, module_name: str) -> int:
    return len(_collect_all_module_instance_sites(design, module_name))
