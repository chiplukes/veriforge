"""Preview-only hierarchy collapse transforms."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from ..codegen import emit_module
from ..model.base import SourceLocation
from ..model.design import Design, Module
from ..model.expressions import Expression
from ..model.instances import Instance, PortConnection
from .diagnostics import RefactorDiagnostic
from .hierarchy_graph import classify_wrapper_module
from ._refactor_utils import (
    TextEditPlan,
    _UnionFind,
    _apply_text_edit,
    _loc_range,
    _simple_identifier_name,
    _unified_diff,
)

_MIN_INSTANCE_PATH_PARTS = 2


@dataclass(frozen=True)
class CollapsePreview:
    """Preview result for a hierarchy-collapse request."""

    instance_path: str
    confidence: str
    diagnostics: tuple[RefactorDiagnostic, ...] = ()
    edits: tuple[TextEditPlan, ...] = ()
    renames: tuple[dict[str, str], ...] = ()
    diff: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(diag.severity == "error" for diag in self.diagnostics) and bool(self.edits)

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": "collapseHierarchy",
            "instancePath": self.instance_path,
            "confidence": self.confidence,
            "ok": self.ok,
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "edits": [edit.to_dict() for edit in self.edits],
            "renames": list(self.renames),
            "diff": self.diff,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class CollapseApplyResult:
    """Result from applying a collapse preview to source files."""

    applied: bool
    diagnostics: tuple[RefactorDiagnostic, ...] = ()
    written_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "diagnostics": [diag.to_dict() for diag in self.diagnostics],
            "writtenFiles": list(self.written_files),
        }


def preview_collapse_hierarchy(design: Design, instance_path: str) -> CollapsePreview:
    """Preview collapsing a pure pass-through wrapper instance."""

    resolved = _resolve_instance_path(design, instance_path)
    if resolved is None:
        return _blocked(instance_path, "instance-not-found", f"Instance path not found: {instance_path}")
    parent, wrapper_inst = resolved
    wrapper_module = wrapper_inst.resolved_module
    classification = classify_wrapper_module(wrapper_module)
    if classification.wrapper_class != "pure_pass_through":
        return CollapsePreview(
            instance_path=instance_path,
            confidence="blocked",
            diagnostics=(
                RefactorDiagnostic(
                    "unsupported-wrapper-class",
                    f"Only pure_pass_through wrappers can be collapsed in preview mode; got "
                    f"{classification.wrapper_class}.",
                    severity="error",
                ),
                *classification.diagnostics,
            ),
            metadata={"wrapperClass": classification.wrapper_class},
        )
    if wrapper_module is None:
        return _blocked(instance_path, "unresolved-module", "Wrapper instance target is unresolved.")
    if wrapper_inst.parameter_bindings or wrapper_module.parameters:
        return _blocked(
            instance_path,
            "parameterized-wrapper",
            "Parameterized wrapper collapse is not supported by the first preview implementation.",
        )

    composition = _compose_parent_connections(wrapper_inst, wrapper_module)
    if isinstance(composition, RefactorDiagnostic):
        return CollapsePreview(instance_path=instance_path, confidence="blocked", diagnostics=(composition,))

    transformed_parent, renames = _build_transformed_parent(parent, wrapper_inst, composition)
    original_text = _source_text_for_loc(parent.loc)
    replacement = emit_module(transformed_parent, emit_comments=True).rstrip() + "\n"
    edit = TextEditPlan(
        file=parent.loc.file if parent.loc and parent.loc.file else "",
        range=_loc_range(parent.loc),
        original=original_text,
        replacement=replacement,
    )
    diff = _unified_diff(edit.file, original_text, replacement)
    return CollapsePreview(
        instance_path=instance_path,
        confidence="safe",
        edits=(edit,),
        renames=tuple(renames),
        diff=diff,
        metadata={
            "parentModule": parent.name,
            "wrapperModule": wrapper_module.name,
            "wrapperInstance": wrapper_inst.instance_name,
        },
    )


def apply_collapse_preview(preview: CollapsePreview) -> CollapseApplyResult:
    """Apply a collapse preview after validating the source text still matches."""

    if not preview.ok:
        return CollapseApplyResult(
            applied=False,
            diagnostics=(
                RefactorDiagnostic(
                    "preview-not-applicable",
                    "Collapse preview has blocking diagnostics and cannot be applied.",
                    severity="error",
                ),
                *preview.diagnostics,
            ),
        )

    written: list[str] = []
    for edit in preview.edits:
        diagnostic = _apply_text_edit(edit)
        if diagnostic is not None:
            return CollapseApplyResult(applied=False, diagnostics=(diagnostic,), written_files=tuple(written))
        written.append(edit.file)
    return CollapseApplyResult(applied=True, written_files=tuple(written))


def _resolve_instance_path(design: Design, instance_path: str) -> tuple[Module, Instance] | None:
    parts = [part for part in instance_path.split("/") if part]
    if len(parts) < _MIN_INSTANCE_PATH_PARTS:
        return None
    current = design.get_module(parts[0])
    if current is None:
        return None
    for inst_name in parts[1:]:
        match = next((inst for inst in current.instances if inst.instance_name == inst_name), None)
        if match is None:
            return None
        if inst_name == parts[-1]:
            return current, match
        current = match.resolved_module
        if current is None:
            return None
    return None


def _compose_parent_connections(
    wrapper_inst: Instance,
    wrapper_module: Module,
) -> dict[str, Expression] | RefactorDiagnostic:
    port_map: dict[str, Expression] = {}
    for index, conn in enumerate(wrapper_inst.port_connections):
        port_name = conn.port_name
        if not conn.is_named:
            if index >= len(wrapper_module.ports):
                return RefactorDiagnostic(
                    "port-connection-out-of-range",
                    f"Ordered port connection {index} has no matching wrapper port.",
                    severity="error",
                )
            port_name = wrapper_module.ports[index].name
        if port_name is None or conn.expression is None:
            return RefactorDiagnostic(
                "unsupported-port-connection",
                "Unconnected wrapper ports are not supported by collapse preview.",
                severity="error",
            )
        port_map[port_name] = conn.expression

    aliases = _alias_groups(wrapper_module)
    if aliases is None:
        return RefactorDiagnostic(
            "unsupported-alias-expression",
            "Only direct identifier pass-through assigns are supported by collapse preview.",
            severity="error",
        )

    signal_map = dict(port_map)
    for signal_name in _module_signal_names(wrapper_module):
        mapped = _mapped_parent_expression(signal_name, port_map, aliases)
        if mapped is not None:
            signal_map[signal_name] = mapped
    return signal_map


def _build_transformed_parent(
    parent: Module,
    wrapper_inst: Instance,
    signal_map: dict[str, Expression],
) -> tuple[Module, list[dict[str, str]]]:
    wrapper_module = wrapper_inst.resolved_module
    if wrapper_module is None:
        msg = "wrapper instance target must be resolved before building transformed parent"
        raise ValueError(msg)
    transformed = copy.deepcopy(parent)
    transformed_instances: list[Instance] = []
    renames: list[dict[str, str]] = []
    for inst in parent.instances:
        if inst is not wrapper_inst:
            transformed_instances.append(copy.deepcopy(inst))
            continue
        for child in wrapper_module.instances:
            new_child = copy.deepcopy(child)
            old_name = new_child.instance_name
            new_child.instance_name = f"{wrapper_inst.instance_name}__{old_name}"
            renames.append({"from": f"{wrapper_inst.instance_name}/{old_name}", "to": new_child.instance_name})
            new_child.port_connections = [_remap_connection(conn, signal_map) for conn in child.port_connections]
            transformed_instances.append(new_child)
    transformed.instances = transformed_instances
    return transformed, renames


def _remap_connection(conn: PortConnection, signal_map: dict[str, Expression]) -> PortConnection:
    remapped = copy.deepcopy(conn)
    signal_name = _simple_identifier_name(conn.expression)
    if signal_name is not None and signal_name in signal_map:
        remapped.expression = copy.deepcopy(signal_map[signal_name])
    return remapped


def _alias_groups(module: Module) -> _UnionFind | None:
    aliases = _UnionFind()
    for assign in module.continuous_assigns:
        lhs = _simple_identifier_name(assign.lhs)
        rhs = _simple_identifier_name(assign.rhs)
        if lhs is None or rhs is None:
            return None
        aliases.union(lhs, rhs)
    return aliases


def _mapped_parent_expression(name: str, port_map: dict[str, Expression], aliases: _UnionFind) -> Expression | None:
    for port_name, expr in port_map.items():
        if aliases.find(name) == aliases.find(port_name):
            return expr
    return None


def _module_signal_names(module: Module) -> set[str]:
    names = {port.name for port in module.ports}
    names.update(net.name for net in module.nets)
    names.update(var.name for var in module.variables)
    return names


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


def _blocked(instance_path: str, code: str, message: str) -> CollapsePreview:
    return CollapsePreview(
        instance_path=instance_path,
        confidence="blocked",
        diagnostics=(RefactorDiagnostic(code, message, severity="error"),),
    )
