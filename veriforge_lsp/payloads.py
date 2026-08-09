"""Typed request/response payloads for the veriforge_lsp custom commands.

These dataclasses type the *envelope* of each ``workspace/executeCommand``
command implemented in ``handlers/extended.py`` — the top-level
request/response dict a client sends and receives. Deeply-nested payloads
that already come from another module's own ``.to_dict()`` (an extract/
collapse/boundary-move preview, a ``WorkspaceEdit``) stay as
``dict[str, Any]`` inside the typed envelope; retyping those lives in
``veriforge.refactor``, out of scope here.

Wire format reference: ``notes/veriforge_lsp.md`` "Custom Extensions".

Every dataclass implements ``to_dict()``; request dataclasses also implement
a tolerant ``from_dict(d)`` classmethod. Requests whose underlying commands
accept many historical field-name aliases (``instancePath``/``instance``,
``moduleName``/``module``, signal-based vs. range-based selections, ...) keep
the original request dict verbatim in a ``raw`` field and route it unchanged
into the existing alias-resolution helpers in ``extended.py`` — ``to_dict()``
on those returns ``raw`` byte-for-byte, so parsing into and back out of the
dataclass never drops a field the legacy handlers still rely on. The named
fields on those dataclasses are typed convenience accessors for the common
case, not the full parse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# ----------------------------------------------------------------------
# Shared
# ----------------------------------------------------------------------


@dataclass
class ErrorPayload:
    """Shared error envelope returned by every command on failure.

    Matches ``_error_payload``'s actual return shape: ``{"ok": False,
    "diagnostics": [RefactorDiagnostic.to_dict()]}``.
    """

    code: str
    message: str
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "diagnostics": [{"code": self.code, "message": self.message, "severity": self.severity}],
        }


@dataclass
class SelectionRequest:
    """The ``{kind, instancePath?, moduleName?, file?, ...}`` shape shared
    across pull-up/push-down/boundary-move/extract selections.

    Documents the common selection sub-object; the real kind-inference and
    alias-resolution logic stays in ``_selection_from_request`` /
    ``_boundary_selection_from_request`` in ``extended.py``, operating on
    ``raw`` so no accepted field is lost.
    """

    kind: str = ""
    instance_path: str = ""
    module_name: str = ""
    file: str = ""
    start_line: int = 0
    end_line: int = 0
    signal: str = ""
    signal_module: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Any) -> SelectionRequest:
        d = _as_dict(d)
        raw_selection = _as_dict(d.get("selection")) or d
        range_info = _as_dict(raw_selection.get("range"))
        start = _as_dict(range_info.get("start"))
        end = _as_dict(range_info.get("end"))
        return cls(
            kind=str(raw_selection.get("kind") or ""),
            instance_path=str(raw_selection.get("instancePath") or raw_selection.get("instance") or ""),
            module_name=str(raw_selection.get("moduleName") or raw_selection.get("module") or ""),
            file=str(raw_selection.get("file") or ""),
            start_line=int(start.get("line", 0)) if start else int(raw_selection.get("startLine", 0) or 0),
            end_line=int(end.get("line", 0)) if end else int(raw_selection.get("endLine", 0) or 0),
            signal=str(raw_selection.get("signal") or ""),
            signal_module=str(raw_selection.get("signalModule") or ""),
            raw=dict(d),
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)


# ----------------------------------------------------------------------
# 1. verilog/setTopModule
# ----------------------------------------------------------------------


@dataclass
class SetTopModuleRequest:
    module_name: str | None = None

    @classmethod
    def from_dict(cls, d: Any) -> SetTopModuleRequest:
        d = _as_dict(d)
        return cls(module_name=d.get("moduleName"))


@dataclass
class SetTopModuleResponse:
    ok: bool
    hierarchy_tree: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok}
        if self.hierarchy_tree is not None:
            result["hierarchyTree"] = self.hierarchy_tree
        return result


# ----------------------------------------------------------------------
# 2. verilog/resolveHierarchyChildren
# ----------------------------------------------------------------------


@dataclass
class ResolveHierarchyChildrenRequest:
    module_name: str = ""
    instance_path: str = ""

    @classmethod
    def from_dict(cls, d: Any) -> ResolveHierarchyChildrenRequest:
        d = _as_dict(d)
        module_name = str(d.get("moduleName", ""))
        return cls(module_name=module_name, instance_path=str(d.get("instancePath", module_name)))


@dataclass
class ResolveHierarchyChildrenResponse:
    children: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"children": self.children}


# ----------------------------------------------------------------------
# 3. verilog/hierarchyGraph
# ----------------------------------------------------------------------


@dataclass
class HierarchyGraphRequest:
    top: str | None = None
    max_depth: int | None = 8
    format: str = "json"

    @classmethod
    def from_dict(cls, d: Any) -> HierarchyGraphRequest:
        d = _as_dict(d)
        return cls(top=d.get("top") or None, max_depth=d.get("maxDepth", 8), format=str(d.get("format", "json")))


@dataclass
class HierarchyGraphResponse:
    hierarchy_graph: dict[str, Any]
    visualization: str | None = None
    format: str | None = None
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok, "hierarchyGraph": self.hierarchy_graph}
        if self.visualization is not None:
            result["visualization"] = self.visualization
        if self.format is not None:
            result["format"] = self.format
        return result


# ----------------------------------------------------------------------
# 4. verilog/traceSignal
# ----------------------------------------------------------------------


@dataclass
class TraceSignalRequest:
    uri: str = ""
    line: int = 0
    character: int = 0

    @classmethod
    def from_dict(cls, d: Any) -> TraceSignalRequest:
        d = _as_dict(d)
        text_document = _as_dict(d.get("textDocument"))
        pos = _as_dict(d.get("position"))
        return cls(
            uri=str(text_document.get("uri", "")), line=int(pos.get("line", 0)), character=int(pos.get("character", 0))
        )


@dataclass
class TraceSignalInfo:
    name: str
    width: str
    module: str
    file: str
    definition_range: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Any) -> TraceSignalInfo:
        d = _as_dict(d)
        return cls(
            name=str(d.get("name", "")),
            width=str(d.get("width", "")),
            module=str(d.get("module", "")),
            file=str(d.get("file", "")),
            definition_range=_as_dict(d.get("definitionRange")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "width": self.width,
            "module": self.module,
            "file": self.file,
            "definitionRange": self.definition_range,
        }


@dataclass
class TraceEntry:
    kind: str
    label: str
    file: str
    range: dict[str, Any] = field(default_factory=dict)
    instance_path: str = ""
    preview: str = ""
    signal_chain: list[str] = field(default_factory=list)
    style: str = ""
    indent: int = 0

    @classmethod
    def from_dict(cls, d: Any) -> TraceEntry:
        d = _as_dict(d)
        return cls(
            kind=str(d.get("kind", "")),
            label=str(d.get("label", "")),
            file=str(d.get("file", "")),
            range=_as_dict(d.get("range")),
            instance_path=str(d.get("instancePath", "")),
            preview=str(d.get("preview", "")),
            signal_chain=list(d.get("signalChain") or []),
            style=str(d.get("style", "")),
            indent=int(d.get("indent", 0)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "file": self.file,
            "range": self.range,
            "instancePath": self.instance_path,
            "preview": self.preview,
            "signalChain": self.signal_chain,
            "style": self.style,
            "indent": self.indent,
        }


@dataclass
class TraceSignalResponse:
    signal: TraceSignalInfo
    drivers: list[TraceEntry] = field(default_factory=list)
    loads: list[TraceEntry] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Any) -> TraceSignalResponse:
        d = _as_dict(d)
        return cls(
            signal=TraceSignalInfo.from_dict(d.get("signal")),
            drivers=[TraceEntry.from_dict(e) for e in d.get("drivers") or []],
            loads=[TraceEntry.from_dict(e) for e in d.get("loads") or []],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal.to_dict(),
            "drivers": [entry.to_dict() for entry in self.drivers],
            "loads": [entry.to_dict() for entry in self.loads],
        }


# ----------------------------------------------------------------------
# 5. verilog/previewHierarchyBoundaryMove, verilog/applyHierarchyBoundaryMove
# ----------------------------------------------------------------------


@dataclass
class BoundaryMoveLspRequest:
    """Unified boundary-move request envelope (preview and apply share it).

    Named to avoid colliding with ``refactor.hierarchy_boundary.
    BoundaryMoveRequest``, the domain object this LSP request gets parsed
    into by ``_boundary_move_request_from_dict``.
    """

    direction: str = ""
    target_parent_path: str = ""
    new_module_name: str = ""
    new_instance_name: str = ""
    extracted_module_name: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Any) -> BoundaryMoveLspRequest:
        d = _as_dict(d)
        return cls(
            direction=str(d.get("direction") or ""),
            target_parent_path=str(d.get("targetParentPath") or d.get("targetParent") or ""),
            new_module_name=str(d.get("newModuleName") or d.get("name") or ""),
            new_instance_name=str(d.get("newInstanceName") or d.get("instanceName") or ""),
            extracted_module_name=str(d.get("extractedModuleName") or d.get("name") or ""),
            raw=dict(d),
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)


@dataclass
class BoundaryMoveLspResponse:
    ok: bool
    preview: dict[str, Any]
    details: dict[str, Any] | None = None
    edit: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    applied: bool | None = None
    applied_by_server: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok, "preview": self.preview}
        if self.details is not None:
            result["details"] = self.details
        if self.edit is not None:
            result["edit"] = self.edit
        if self.review is not None:
            result["review"] = self.review
        if self.applied is not None:
            result["applied"] = self.applied
        if self.applied_by_server is not None:
            result["appliedByServer"] = self.applied_by_server
        return result


# ----------------------------------------------------------------------
# 6-9. Legacy collapse / extract shims
# ----------------------------------------------------------------------


@dataclass
class CollapseRequest:
    """Shared request shape for ``previewCollapseHierarchy`` and
    ``applyCollapseHierarchy`` (identical parsing; apply is preview + a
    server-side no-write marker)."""

    instance_path: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Any) -> CollapseRequest:
        d = _as_dict(d)
        return cls(instance_path=str(d.get("instancePath") or d.get("instance_path") or ""), raw=dict(d))

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)


PreviewCollapseRequest = CollapseRequest
ApplyCollapseRequest = CollapseRequest


@dataclass
class ExtractRequest:
    """Shared request shape for ``previewExtractModule`` and
    ``applyExtractModule``."""

    extracted_module_name: str = ""
    new_instance_name: str = ""
    module_name: str = ""
    file: str = ""
    uri: str = ""
    signal: str = ""
    signal_module: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Any) -> ExtractRequest:
        d = _as_dict(d)
        raw_selection = _as_dict(d.get("selection")) or d
        text_document = _as_dict(raw_selection.get("textDocument"))
        return cls(
            extracted_module_name=str(
                d.get("extractedModuleName") or d.get("name") or d.get("moduleNewName") or "extracted_logic"
            ),
            new_instance_name=str(d.get("instanceName") or d.get("instance") or ""),
            module_name=str(
                raw_selection.get("moduleName") or raw_selection.get("module") or d.get("moduleName") or ""
            ),
            file=str(raw_selection.get("file") or ""),
            uri=str(text_document.get("uri", "")),
            signal=str(raw_selection.get("signal") or ""),
            signal_module=str(raw_selection.get("module") or raw_selection.get("signalModule") or ""),
            raw=dict(d),
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)


PreviewExtractRequest = ExtractRequest
ApplyExtractRequest = ExtractRequest


@dataclass
class LegacyRefactorResponse:
    """Shared envelope for legacy collapse/extract preview and apply
    responses: ``{ok, preview, edit?, review?, appliedByServer?}``."""

    ok: bool
    preview: dict[str, Any]
    edit: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    applied_by_server: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok, "preview": self.preview}
        if self.edit is not None:
            result["edit"] = self.edit
        if self.review is not None:
            result["review"] = self.review
        if self.applied_by_server is not None:
            result["appliedByServer"] = self.applied_by_server
        return result


CollapseResponse = LegacyRefactorResponse
ExtractResponse = LegacyRefactorResponse


# ----------------------------------------------------------------------
# 10-11. Legacy pull-up / push-down shims (preview-only, no apply command)
# ----------------------------------------------------------------------


@dataclass
class PreviewPullUpRequest:
    target_parent_path: str = ""
    selection: SelectionRequest = field(default_factory=SelectionRequest)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Any) -> PreviewPullUpRequest:
        d = _as_dict(d)
        return cls(
            target_parent_path=str(d.get("targetParentPath") or d.get("targetParent") or ""),
            selection=SelectionRequest.from_dict(d),
            raw=dict(d),
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)


@dataclass
class PreviewPushDownRequest:
    new_module_name: str = ""
    new_instance_name: str = ""
    target_parent_path: str = ""
    selection: SelectionRequest = field(default_factory=SelectionRequest)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: Any) -> PreviewPushDownRequest:
        d = _as_dict(d)
        return cls(
            new_module_name=str(d.get("newModuleName") or d.get("name") or ""),
            new_instance_name=str(d.get("newInstanceName") or d.get("instanceName") or ""),
            target_parent_path=str(d.get("targetParentPath") or d.get("targetParent") or ""),
            selection=SelectionRequest.from_dict(d),
            raw=dict(d),
        )

    def to_dict(self) -> dict[str, Any]:
        return dict(self.raw)


@dataclass
class BoundaryLegacyResponse:
    """Shared envelope for legacy pull-up/push-down preview responses:
    ``{ok, preview, edit?, review?}`` — no ``appliedByServer`` (these two
    directions have no legacy apply command; only the unified
    ``applyHierarchyBoundaryMove`` applies pull-up/push-down)."""

    ok: bool
    preview: dict[str, Any]
    edit: dict[str, Any] | None = None
    review: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"ok": self.ok, "preview": self.preview}
        if self.edit is not None:
            result["edit"] = self.edit
        if self.review is not None:
            result["review"] = self.review
        return result


# ----------------------------------------------------------------------
# 12. verilog.reparse
# ----------------------------------------------------------------------


@dataclass
class ReparseResponse:
    ok: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok}
