"""Push-down hierarchy boundary move engine."""

from __future__ import annotations

import copy
import re

from ..codegen import emit_module
from ..model.design import Design, Module
from ..model.expressions import Identifier
from ..model.instances import Instance, ParameterBinding, PortConnection

from .diagnostics import RefactorDiagnostic
from ._refactor_utils import TextEditPlan
from ._boundary_models import (
    BoundaryEndpoint,
    BoundaryMovePreview,
    BoundaryMoveRequest,
)
from ._boundary_selection import _module_item_summary, _source_line_range, _source_text_for_range_payload
from ._pull_up_engine import _count_module_instance_sites, _module_declared_names


def _preview_push_down(
    design: Design,
    request: BoundaryMoveRequest,
    source: BoundaryEndpoint,
    parent: BoundaryEndpoint | None,
    selected_module: Module,
) -> BoundaryMovePreview:
    if request.target_parent_path:
        return BoundaryMovePreview(
            request=request,
            confidence="blocked",
            source=source,
            parent=parent,
            diagnostics=(
                RefactorDiagnostic(
                    "push-down-target-not-supported",
                    "Push-down does not yet support targetParentPath; the rewrite always wraps the resolved module in place.",
                    severity="error",
                ),
            ),
        )

    instance_name = request.new_instance_name or f"u_{request.new_module_name}"
    rewrite = _build_push_down_edit(design, request, selected_module, instance_name)
    edits: tuple[TextEditPlan, ...] = ()
    diagnostics: list[RefactorDiagnostic] = []
    metadata: dict[str, object] = {"rewriteStatus": "not-implemented", "applyBlockedReason": "preview-contract-only"}
    if isinstance(rewrite, tuple):
        edits = rewrite
        metadata = {"rewriteStatus": "apply-ready", "rewrittenModule": selected_module.name}
    elif isinstance(rewrite, RefactorDiagnostic):
        diagnostics.append(rewrite)
        metadata = {"rewriteStatus": "not-implemented", "applyBlockedReason": rewrite.code}

    if edits and request.selection.kind in {"instance", "subtree"}:
        site_count = _count_module_instance_sites(design, selected_module.name)
        if site_count > 1:
            diagnostics.append(
                RefactorDiagnostic(
                    "push-down-module-multi-instance",
                    (
                        f"Module {selected_module.name!r} is instantiated at {site_count} sites; "
                        "push-down rewrites the module definition, so every instantiation will use the new wrapper."
                    ),
                    severity="warning",
                )
            )
            metadata["instanceSiteCount"] = site_count

    confidence: str
    has_error = any(diag.severity == "error" for diag in diagnostics)
    if has_error:
        confidence = "blocked"
    elif edits:
        confidence = "safe"
    else:
        confidence = "planning"

    after_hierarchy: dict[str, object] = {
        "createdModule": request.new_module_name,
        "createdInstance": instance_name,
        "rewrittenModule": selected_module.name,
    }

    return BoundaryMovePreview(
        request=request,
        confidence=confidence,
        diagnostics=tuple(diagnostics),
        source=source,
        parent=parent,
        target=None,
        before_hierarchy={
            "sourcePath": source.instance_path or source.module_name,
            "sourceModule": selected_module.name,
        },
        after_hierarchy=after_hierarchy,
        moved_items=_module_item_summary(selected_module),
        edits=edits,
        metadata=metadata,
    )


def _build_push_down_edit(  # noqa: PLR0911
    design: Design,
    request: BoundaryMoveRequest,
    selected_module: Module,
    instance_name: str,
) -> tuple[TextEditPlan, ...] | RefactorDiagnostic:
    new_module_name = request.new_module_name
    if not new_module_name:
        return RefactorDiagnostic(
            "new-module-name-required",
            "Push-down rewrite requires a new child module name.",
            severity="error",
        )
    if not _is_valid_identifier(new_module_name):
        return RefactorDiagnostic(
            "new-module-name-invalid",
            f"New module name {new_module_name!r} is not a valid Verilog identifier.",
            severity="error",
        )
    if not _is_valid_identifier(instance_name):
        return RefactorDiagnostic(
            "new-instance-name-invalid",
            f"New instance name {instance_name!r} is not a valid Verilog identifier.",
            severity="error",
        )
    if design.get_module(new_module_name) is not None:
        return RefactorDiagnostic(
            "new-module-name-collision",
            f"Push-down rewrite cannot create module {new_module_name!r}: a module with that name already exists.",
            severity="error",
        )
    if new_module_name == selected_module.name:
        return RefactorDiagnostic(
            "new-module-name-collision",
            f"New child module name {new_module_name!r} must differ from the selected module name.",
            severity="error",
        )
    declared = _module_declared_names(selected_module)
    if instance_name in declared:
        return RefactorDiagnostic(
            "new-instance-name-collision",
            f"New instance name {instance_name!r} collides with an existing name inside {selected_module.name!r}.",
            severity="error",
        )
    unsupported = _unsupported_push_down_features(selected_module)
    if unsupported is not None:
        return unsupported
    if _module_body_is_empty(selected_module):
        return RefactorDiagnostic(
            "empty-module-body",
            f"Push-down rewrite cannot wrap module {selected_module.name!r} because its body is empty.",
            severity="error",
        )
    if selected_module.loc is None or not selected_module.loc.file:
        return RefactorDiagnostic(
            "module-location-required",
            f"Cannot rewrite push-down because module {selected_module.name!r} has no source location.",
            severity="error",
        )
    edit_range = _source_line_range(selected_module.loc)
    original_text = _source_text_for_range_payload(selected_module.loc.file, edit_range)
    if not original_text:
        return RefactorDiagnostic(
            "module-source-unavailable",
            f"Cannot read source for module {selected_module.name!r} at {selected_module.loc.file}.",
            severity="error",
        )

    pass_through = _build_pass_through_instance(selected_module, new_module_name, instance_name)

    wrapper_module = copy.deepcopy(selected_module)
    wrapper_module.parameters = [copy.deepcopy(p) for p in selected_module.parameters if not p.is_local]
    wrapper_module.nets = []
    wrapper_module.variables = []
    wrapper_module.continuous_assigns = []
    wrapper_module.always_blocks = []
    wrapper_module.initial_blocks = []
    wrapper_module.instances = [pass_through]
    wrapper_module.functions = []
    wrapper_module.tasks = []
    wrapper_module.typedefs = []
    wrapper_module.imports = list(selected_module.imports)
    wrapper_module.generate_blocks = []
    wrapper_module.specify_blocks = []
    wrapper_module.interface_instances = []

    child_module = copy.deepcopy(selected_module)
    child_module.name = new_module_name

    wrapper_text = emit_module(wrapper_module, emit_comments=True).rstrip("\n")
    child_text = emit_module(child_module, emit_comments=True).rstrip("\n")
    replacement = wrapper_text + "\n\n" + child_text
    if original_text.endswith("\n") and not replacement.endswith("\n"):
        replacement += "\n"

    return (
        TextEditPlan(
            file=selected_module.loc.file,
            range=edit_range,
            original=original_text,
            replacement=replacement,
        ),
    )


def _unsupported_push_down_features(module: Module) -> RefactorDiagnostic | None:
    unsupported: list[str] = []
    if module.interface_instances:
        unsupported.append("interface instances")
    if module.generate_blocks:
        unsupported.append("generate blocks")
    if module.specify_blocks:
        unsupported.append("specify blocks")
    if module.typedefs:
        unsupported.append("typedefs")
    if module.functions:
        unsupported.append("functions")
    if module.tasks:
        unsupported.append("tasks")
    if not unsupported:
        return None
    return RefactorDiagnostic(
        "unsupported-push-down-body",
        "Push-down rewrite does not yet support modules containing " + ", ".join(unsupported) + ".",
    )


def _module_body_is_empty(module: Module) -> bool:
    return not (
        module.nets
        or module.variables
        or module.instances
        or module.continuous_assigns
        or module.always_blocks
        or module.initial_blocks
        or any(p.is_local for p in module.parameters)
    )


def _build_pass_through_instance(module: Module, new_module_name: str, instance_name: str) -> Instance:
    parameter_bindings: list[ParameterBinding] = []
    for param in module.parameters:
        if param.is_local:
            continue
        parameter_bindings.append(ParameterBinding(name=param.name, value=Identifier(param.name)))
    port_connections: list[PortConnection] = []
    for port in module.ports:
        port_connections.append(PortConnection(port_name=port.name, expression=Identifier(port.name), is_named=True))
    return Instance(
        new_module_name,
        instance_name,
        has_parameter_override=bool(parameter_bindings),
        parameter_bindings=parameter_bindings,
        port_connections=port_connections,
    )


_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _is_valid_identifier(name: str) -> bool:
    return bool(_IDENTIFIER_RE.match(name))
