"""Shared text-editing primitives for all hierarchy refactor modules."""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

from ..model.base import SourceLocation
from ..model.expressions import Expression, Identifier
from .diagnostics import RefactorDiagnostic


@dataclass(frozen=True)
class TextEditPlan:
    """A source edit proposed by a refactor preview."""

    file: str
    range: dict[str, object]
    replacement: str
    original: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "file": self.file,
            "range": self.range,
            "replacement": self.replacement,
            "original": self.original,
        }


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, name: str) -> str:
        parent = self._parent.setdefault(name, name)
        if parent != name:
            self._parent[name] = self.find(parent)
        return self._parent[name]

    def union(self, left: str, right: str) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root

    def group_contains_any(self, name: str, candidates: set[str]) -> bool:
        root = self.find(name)
        return any(self.find(candidate) == root for candidate in candidates)


def _simple_identifier_name(expr: Expression | None) -> str | None:
    if isinstance(expr, Identifier) and not expr.hierarchy:
        return expr.name
    return None


def _loc_range(loc: SourceLocation | None) -> dict[str, object]:
    if loc is None:
        return {}
    start_line = max(0, (loc.line or 1) - 1)
    start_char = max(0, (loc.column or 1) - 1)
    if loc.end_line:
        end_line = max(0, loc.end_line - 1)
        end_char = max(0, loc.end_column - 1) if loc.end_column else start_char
    else:
        end_line = start_line
        end_char = start_char
    return {
        "start": {"line": start_line, "character": start_char},
        "end": {"line": end_line, "character": end_char},
    }


def _unified_diff(file_path: str, before: str, after: str) -> str:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=file_path or "before",
            tofile=file_path or "after",
        )
    )


def _apply_text_edit(edit: TextEditPlan) -> RefactorDiagnostic | None:
    """Apply a single line-range edit to a source file in place."""
    if not edit.file:
        return RefactorDiagnostic("missing-edit-file", "Edit plan does not identify a source file.", severity="error")
    path = Path(edit.file)
    if not path.is_file():
        return RefactorDiagnostic("missing-edit-file", f"Source file does not exist: {edit.file}", severity="error")
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    edit_range = edit.range
    start = edit_range.get("start", {}) if isinstance(edit_range, dict) else {}
    end = edit_range.get("end", {}) if isinstance(edit_range, dict) else {}
    start_line = int(start.get("line", 0))  # type: ignore[attr-defined]
    end_line = int(end.get("line", start_line)) + 1  # type: ignore[attr-defined]
    current = "".join(lines[start_line:end_line])
    if current != edit.original:
        return RefactorDiagnostic(
            "stale-preview",
            f"Source file changed after preview was computed: {edit.file}",
            severity="error",
        )
    replacement_lines = edit.replacement.splitlines(keepends=True)
    path.write_text("".join([*lines[:start_line], *replacement_lines, *lines[end_line:]]), encoding="utf-8")
    return None


def _line_offsets(text: str) -> list[int]:
    offsets = [0]
    for index, char in enumerate(text):
        if char == "\n":
            offsets.append(index + 1)
    return offsets


def _lsp_position_to_offset(line_offsets: list[int], text: str, line: int, character: int) -> int:
    if not line_offsets:
        return 0
    clamped_line = max(0, min(line, len(line_offsets) - 1))
    line_start = line_offsets[clamped_line]
    if clamped_line + 1 < len(line_offsets):
        line_end = max(line_start, line_offsets[clamped_line + 1] - 1)
    else:
        line_end = len(text)
    return max(line_start, min(line_start + character, line_end))


def _is_empty_file_edit(edit: TextEditPlan) -> bool:
    if not isinstance(edit.range, dict):
        return False
    start = edit.range.get("start", {})
    end = edit.range.get("end", {})
    if not isinstance(start, dict) or not isinstance(end, dict):
        return False
    return (
        int(start.get("line", 0)) == 0
        and int(start.get("character", 0)) == 0
        and int(end.get("line", 0)) == 0
        and int(end.get("character", 0)) == 0
    )


def _current_text_for_edit(text: str, edit: TextEditPlan) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    line_off = _line_offsets(normalized)
    if not isinstance(edit.range, dict):
        return ""
    start = edit.range.get("start", {})
    end = edit.range.get("end", {})
    if not isinstance(start, dict) or not isinstance(end, dict):
        return ""
    start_offset = _lsp_position_to_offset(
        line_off,
        normalized,
        int(start.get("line", 0)),
        int(start.get("character", 0)),
    )
    end_offset = _lsp_position_to_offset(
        line_off,
        normalized,
        int(end.get("line", 0)),
        int(end.get("character", 0)),
    )
    return normalized[start_offset:end_offset]


def _apply_edit_plans(text: str, edits: list[TextEditPlan]) -> str:
    ordered = sorted(
        edits,
        key=lambda edit: (
            int(((edit.range.get("start", {}) if isinstance(edit.range, dict) else {}).get("line", 0))),  # type: ignore[attr-defined]
            int(((edit.range.get("start", {}) if isinstance(edit.range, dict) else {}).get("character", 0))),  # type: ignore[attr-defined]
            int(((edit.range.get("end", {}) if isinstance(edit.range, dict) else {}).get("line", 0))),  # type: ignore[attr-defined]
            int(((edit.range.get("end", {}) if isinstance(edit.range, dict) else {}).get("character", 0))),  # type: ignore[attr-defined]
        ),
        reverse=True,
    )
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    line_off = _line_offsets(normalized)
    for edit in ordered:
        if not isinstance(edit.range, dict):
            continue
        start = edit.range.get("start", {})
        end = edit.range.get("end", {})
        if not isinstance(start, dict) or not isinstance(end, dict):
            continue
        start_offset = _lsp_position_to_offset(
            line_off,
            normalized,
            int(start.get("line", 0)),
            int(start.get("character", 0)),
        )
        end_offset = _lsp_position_to_offset(
            line_off,
            normalized,
            int(end.get("line", 0)),
            int(end.get("character", 0)),
        )
        normalized = f"{normalized[:start_offset]}{edit.replacement}{normalized[end_offset:]}"
        line_off = _line_offsets(normalized)
    return normalized


def _group_edits_by_file(
    edits: tuple[TextEditPlan, ...] | list[TextEditPlan],
) -> dict[str, list[TextEditPlan]]:
    grouped: dict[str, list[TextEditPlan]] = {}
    for edit in edits:
        grouped.setdefault(edit.file, []).append(edit)
    return grouped


def _apply_text_edits(file_path: str, edits: list[TextEditPlan]) -> RefactorDiagnostic | None:
    """Apply multiple character-offset edits to a source file in place."""
    if not file_path:
        return RefactorDiagnostic("missing-edit-file", "Edit plan does not identify a source file.", severity="error")

    path = Path(file_path)
    if not path.exists():
        if len(edits) == 1 and edits[0].original == "" and _is_empty_file_edit(edits[0]):
            path.write_text(edits[0].replacement, encoding="utf-8")
            return None
        return RefactorDiagnostic("missing-edit-file", f"Source file does not exist: {file_path}", severity="error")

    current = path.read_text(encoding="utf-8", errors="replace")
    for edit in edits:
        expected = edit.original.replace("\r\n", "\n").replace("\r", "\n")
        actual = _current_text_for_edit(current, edit)
        if actual != expected:
            return RefactorDiagnostic(
                "stale-preview",
                f"Source file changed after preview was computed: {file_path}",
                severity="error",
            )

    updated = _apply_edit_plans(current, edits)
    path.write_text(updated, encoding="utf-8")
    return None
