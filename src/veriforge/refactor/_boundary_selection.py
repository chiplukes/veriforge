"""Selection resolution and source text utilities for hierarchy boundary moves."""

from __future__ import annotations

from bisect import bisect_right
import os
import re
from pathlib import Path

from ..model.base import SourceLocation, VerilogNode
from ..model.design import Design, Module
from ..model.instances import Instance

from .diagnostics import RefactorDiagnostic
from ._refactor_utils import _loc_range
from ._boundary_models import BoundaryEndpoint, BoundaryMoveSelection, _MIN_INSTANCE_PATH_PARTS


def _resolve_pull_up_range_selection(  # noqa: PLR0911
    design: Design,
    selection: BoundaryMoveSelection,
) -> tuple[Module, Instance, Module] | RefactorDiagnostic:
    parent_candidates = [
        module for module in design.modules if _loc_contains_selection(getattr(module, "loc", None), selection)
    ]
    if not parent_candidates:
        return RefactorDiagnostic(
            "selection-module-not-found",
            "Could not resolve the selected source range to a containing module.",
            severity="error",
        )
    parent = min(parent_candidates, key=lambda module: _loc_span(getattr(module, "loc", None)))

    selected_instances = [inst for inst in parent.instances if _selection_within_loc(inst.loc, selection)]
    if not selected_instances:
        overlapping_instances = [inst for inst in parent.instances if _selection_overlaps_loc(inst.loc, selection)]
        if overlapping_instances:
            names = ", ".join(inst.instance_name for inst in overlapping_instances)
            return RefactorDiagnostic(
                "partial-instance-selection",
                f"Selection partially overlaps instance(s): {names}. Select the complete instance.",
                severity="error",
            )
        return RefactorDiagnostic(
            "no-pull-up-instance-selection",
            "Selection does not contain a complete instance to pull up.",
            severity="error",
        )
    if len(selected_instances) > 1:
        names = ", ".join(inst.instance_name for inst in selected_instances)
        return RefactorDiagnostic(
            "multiple-instances-selected",
            f"Selection contains multiple instances: {names}. Select exactly one instance for hierarchy-up.",
            severity="error",
        )

    selected_logic = (
        [assign for assign in parent.continuous_assigns if _selection_within_loc(assign.loc, selection)]
        + [block for block in parent.always_blocks if _selection_within_loc(block.loc, selection)]
        + [block for block in parent.initial_blocks if _selection_within_loc(block.loc, selection)]
    )
    if selected_logic:
        return RefactorDiagnostic(
            "mixed-selection-unsupported",
            "Hierarchy-up range preview supports exactly one complete instance selection at a time.",
            severity="error",
        )

    selected_instance = selected_instances[0]
    if selected_instance.resolved_module is None:
        return RefactorDiagnostic(
            "unresolved-instance-module",
            f"Selected instance {selected_instance.instance_name!r} does not resolve to a module.",
            severity="error",
        )
    return parent, selected_instance, selected_instance.resolved_module


def _resolve_selection(  # noqa: PLR0911
    design: Design,
    selection: BoundaryMoveSelection,
) -> tuple[BoundaryEndpoint, BoundaryEndpoint | None, Module] | RefactorDiagnostic:
    if selection.kind in {"instance", "subtree"}:
        if not selection.instance_path:
            return RefactorDiagnostic(
                "instance-path-required",
                "Instance and subtree hierarchy boundary selections require instancePath.",
                severity="error",
            )
        resolved = _resolve_instance_path(design, selection.instance_path)
        if resolved is None:
            return RefactorDiagnostic(
                "instance-not-found",
                f"Instance path not found: {selection.instance_path}.",
                severity="error",
            )
        parent, instance = resolved
        if instance.resolved_module is None:
            return RefactorDiagnostic(
                "unresolved-instance-module",
                f"Instance {selection.instance_path!r} does not resolve to a module.",
                severity="error",
            )
        return (
            _instance_endpoint(selection.instance_path, instance),
            _module_endpoint(parent, _parent_path(selection.instance_path)),
            instance.resolved_module,
        )

    if selection.kind == "module":
        if not selection.module_name:
            return RefactorDiagnostic(
                "module-name-required",
                "Module hierarchy boundary selections require moduleName.",
                severity="error",
            )
        module = design.get_module(selection.module_name)
        if module is None:
            return RefactorDiagnostic(
                "module-not-found", f"Module not found: {selection.module_name}.", severity="error"
            )
        return _module_endpoint(module, module.name), None, module

    if not selection.file:
        return RefactorDiagnostic(
            "file-required",
            "File hierarchy boundary selections require file.",
            severity="error",
        )
    matches = [_module for _module in design.modules if _same_path(_module.loc.file, selection.file)]
    if not matches:
        return RefactorDiagnostic(
            "file-module-not-found",
            f"No modules were found in selected file: {selection.file}.",
            severity="error",
        )
    if len(matches) > 1:
        names = ", ".join(module.name for module in matches)
        return RefactorDiagnostic(
            "ambiguous-file-selection",
            f"Selected file contains multiple modules; choose one explicitly: {names}.",
            severity="error",
        )
    module = matches[0]
    return _module_endpoint(module, module.name), None, module


def _selection_within_loc(loc: SourceLocation | None, selection: BoundaryMoveSelection) -> bool:
    if loc is None or not loc.file or not selection.file:
        return False
    if not _same_path(loc.file, selection.file):
        return False
    start_line = loc.line or 0
    end_line = loc.end_line or start_line
    if start_line <= 0 or end_line <= 0:
        return False
    return selection.start_line <= start_line and end_line <= selection.end_line


def _loc_contains_selection(loc: SourceLocation | None, selection: BoundaryMoveSelection) -> bool:
    if loc is None or not loc.file or not selection.file:
        return False
    if not _same_path(loc.file, selection.file):
        return False
    start_line = loc.line or 0
    end_line = loc.end_line or start_line
    if start_line <= 0 or end_line <= 0:
        return False
    return start_line <= selection.start_line and selection.end_line <= end_line


def _selection_overlaps_loc(loc: SourceLocation | None, selection: BoundaryMoveSelection) -> bool:
    if loc is None or not loc.file or not selection.file:
        return False
    if not _same_path(loc.file, selection.file):
        return False
    start_line = loc.line or 0
    end_line = loc.end_line or start_line
    if start_line <= 0 or end_line <= 0:
        return False
    return not (selection.end_line < start_line or selection.start_line > end_line)


def _loc_span(loc: SourceLocation | None) -> int:
    if loc is None:
        return 1 << 30
    start_line = loc.line or 0
    end_line = loc.end_line or start_line
    return max(1, end_line - start_line + 1)


def _resolve_target_parent(design: Design, target_parent_path: str) -> BoundaryEndpoint | RefactorDiagnostic:
    parts = [part for part in target_parent_path.split("/") if part]
    if len(parts) == 1:
        module = design.get_module(parts[0])
        if module is not None:
            return _module_endpoint(module, module.name)
    resolved = _resolve_instance_path(design, target_parent_path)
    if resolved is None:
        return RefactorDiagnostic(
            "target-parent-not-found",
            f"Target parent path not found: {target_parent_path}.",
            severity="error",
        )
    _, instance = resolved
    if instance.resolved_module is None:
        return RefactorDiagnostic(
            "unresolved-target-parent",
            f"Target parent path {target_parent_path!r} does not resolve to a module.",
            severity="error",
        )
    return _instance_endpoint(target_parent_path, instance)


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


def _module_endpoint(module: Module, instance_path: str = "") -> BoundaryEndpoint:
    return BoundaryEndpoint(
        module_name=module.name,
        instance_path=instance_path,
        file=module.loc.file if module.loc and module.loc.file else "",
        range=_loc_range(module.loc),
    )


def _instance_endpoint(instance_path: str, instance: Instance) -> BoundaryEndpoint:
    module = instance.resolved_module
    return BoundaryEndpoint(
        module_name=module.name if module is not None else instance.module_name,
        instance_path=instance_path,
        instance_name=instance.instance_name,
        file=module.loc.file if module is not None and module.loc and module.loc.file else "",
        range=_loc_range(module.loc if module is not None else None),
    )


def _module_item_summary(module: Module) -> dict[str, object]:
    return {
        "ports": [port.name for port in module.ports],
        "parameters": [param.name for param in module.parameters],
        "nets": [net.name for net in module.nets],
        "variables": [variable.name for variable in module.variables],
        "instances": [inst.instance_name for inst in module.instances],
        "continuousAssignments": len(module.continuous_assigns),
        "alwaysBlocks": len(module.always_blocks),
        "initialBlocks": len(module.initial_blocks),
    }


def _parent_path(instance_path: str) -> str:
    parts = [part for part in instance_path.split("/") if part]
    return "/".join(parts[:-1])


def _same_path(left: str | None, right: str) -> bool:
    if not left:
        return False
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


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


def _source_text_for_range_payload(file_path: str, range_payload: dict[str, object]) -> str:
    path = Path(file_path)
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    start = range_payload.get("start", {}) if isinstance(range_payload, dict) else {}
    end = range_payload.get("end", {}) if isinstance(range_payload, dict) else {}
    if not isinstance(start, dict) or not isinstance(end, dict):
        return ""
    start_offset = _line_char_offset(text, int(start.get("line", 0)), int(start.get("character", 0)))
    end_offset = _line_char_offset(text, int(end.get("line", 0)), int(end.get("character", 0)))
    return text[start_offset:end_offset]


def _node_source_and_range(  # noqa: PLR0913
    node: VerilogNode,
    *,
    error_code: str,
    description: str,
    whole_lines: bool = False,
    include_leading_attribute_lines: bool = False,
    start_line_override: int | None = None,
) -> tuple[str | RefactorDiagnostic, dict[str, object]]:
    return _loc_source_and_range(
        getattr(node, "loc", None),
        error_code=error_code,
        description=description,
        whole_lines=whole_lines,
        include_leading_attribute_lines=include_leading_attribute_lines,
        start_line_override=start_line_override,
    )


def _loc_source_and_range(  # noqa: PLR0913
    loc: SourceLocation | None,
    *,
    error_code: str,
    description: str,
    whole_lines: bool = False,
    include_leading_attribute_lines: bool = False,
    start_line_override: int | None = None,
) -> tuple[str | RefactorDiagnostic, dict[str, object]]:
    if loc is None or not loc.file:
        return (
            RefactorDiagnostic(
                error_code,
                f"Cannot minimally rewrite {description} because its source location is missing.",
                severity="error",
            ),
            {},
        )
    path = Path(loc.file)
    if not path.is_file():
        return (
            RefactorDiagnostic(
                error_code,
                f"Cannot read source for {description}: {loc.file}",
                severity="error",
            ),
            {},
        )
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    resolved_start_line = start_line_override
    if whole_lines and resolved_start_line is None and include_leading_attribute_lines:
        resolved_start_line = _include_leading_attribute_lines(lines, max(0, (loc.line or 1) - 1))
    edit_range = (
        _source_line_range(loc, lines=lines, start_line_override=resolved_start_line)
        if whole_lines
        else _loc_range(loc)
    )
    return _source_text_for_range_payload(loc.file, edit_range), edit_range


def _line_char_offset(text: str, line: int, character: int) -> int:
    line_offsets = [0]
    for index, char in enumerate(text):
        if char == "\n":
            line_offsets.append(index + 1)
    if not line_offsets:
        return 0
    clamped_line = max(0, min(line, len(line_offsets) - 1))
    line_start = line_offsets[clamped_line]
    if clamped_line + 1 < len(line_offsets):
        line_end = max(line_start, line_offsets[clamped_line + 1] - 1)
    else:
        line_end = len(text)
    return max(line_start, min(line_start + max(0, character), line_end))


def _offset_to_position(line_offsets: list[int], offset: int) -> dict[str, int]:
    if not line_offsets:
        return {"line": 0, "character": 0}
    clamped_offset = max(0, offset)
    line = max(0, min(bisect_right(line_offsets, clamped_offset) - 1, len(line_offsets) - 1))
    return {"line": line, "character": max(0, clamped_offset - line_offsets[line])}


def _instance_source_and_range(instance: Instance) -> tuple[str | RefactorDiagnostic, dict[str, object]]:
    loc = instance.loc
    if loc is None or not loc.file:
        return (
            RefactorDiagnostic(
                "instance-location-required",
                "Cannot minimally rewrite pull-up because the selected instance has no source location.",
                severity="error",
            ),
            {},
        )
    path = Path(loc.file)
    if not path.is_file():
        return (
            RefactorDiagnostic(
                "instance-location-required",
                f"Cannot read source for selected instance: {loc.file}",
                severity="error",
            ),
            {},
        )
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    start_line = _instance_statement_start_line(lines, loc, instance.module_name)
    return _node_source_and_range(
        instance,
        error_code="instance-location-required",
        description="the selected instance",
        whole_lines=True,
        start_line_override=start_line,
    )


def _instance_statement_start_line(lines: list[str], loc: SourceLocation, module_name: str) -> int:
    start_line = max(0, (loc.line or 1) - 1)
    header_pattern = re.compile(rf"^\s*(?:\(\*.*\*\)\s*)*{re.escape(module_name)}\b")
    search_start = max(0, start_line - 64)
    for line_index in range(start_line, search_start - 1, -1):
        if header_pattern.search(lines[line_index]):
            return _include_leading_attribute_lines(lines, line_index)
    return start_line


def _include_leading_attribute_lines(lines: list[str], start_line: int) -> int:
    line_index = start_line
    while line_index > 0:
        prev = lines[line_index - 1].strip()
        if not prev or prev.startswith("//"):
            break
        if prev.startswith("(*") and "*)" in prev and not prev.endswith("*)"):
            break
        if prev.startswith("(*") or prev.endswith("*)"):
            line_index -= 1
            continue
        break
    return line_index


def _source_line_range(
    loc: SourceLocation | None,
    *,
    lines: list[str] | None = None,
    start_line_override: int | None = None,
) -> dict[str, object]:
    if loc is None:
        return {}
    start_line = start_line_override if start_line_override is not None else max(0, (loc.line or 1) - 1)
    end_line = loc.end_line if loc.end_line else loc.line
    if lines is None and loc.file:
        path = Path(loc.file)
        if path.is_file():
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    if lines is not None:
        if end_line < len(lines):
            return {
                "start": {"line": start_line, "character": 0},
                "end": {"line": end_line, "character": 0},
            }
        if lines and end_line - 1 < len(lines):
            return {
                "start": {"line": start_line, "character": 0},
                "end": {"line": max(0, end_line - 1), "character": len(lines[end_line - 1])},
            }
    return {
        "start": {"line": start_line, "character": 0},
        "end": {"line": max(0, end_line - 1), "character": 0},
    }
