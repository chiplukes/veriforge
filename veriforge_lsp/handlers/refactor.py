"""Preview/apply payload builders for the hierarchy-refactor commands.

Split out of ``handlers/extended.py`` (work plan item 4.4): collapse,
extract, pull-up, push-down, and the unified boundary-move API, plus the
code actions and request-parsing helpers they share. ``extended.py`` keeps
``register()`` as the sole ``@ls.command`` aggregator and re-exports the
pieces it needs from here.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from veriforge.refactor import (
    BoundaryMoveRequest,
    BoundaryMoveSelection,
    ExtractSelection,
    RefactorDiagnostic,
    classify_wrapper_module,
    preview_hierarchy_boundary_move,
)
from veriforge_lsp.payloads import (
    BoundaryLegacyResponse,
    BoundaryMoveLspResponse,
    CollapseResponse,
    ErrorPayload,
    ExtractResponse,
)
from veriforge_lsp.protocol import path_to_uri, uri_to_path

log = logging.getLogger(__name__)

_LEGACY_HIERARCHY_COMMANDS: dict[str, str] = {
    "verilog/previewCollapseHierarchy": "verilog/previewHierarchyBoundaryMove (direction='collapse')",
    "verilog/applyCollapseHierarchy": "verilog/applyHierarchyBoundaryMove (direction='collapse')",
    "verilog/previewExtractModule": "verilog/previewHierarchyBoundaryMove (direction='extract')",
    "verilog/applyExtractModule": "verilog/applyHierarchyBoundaryMove (direction='extract')",
    "verilog/previewHierarchyPullUp": "verilog/previewHierarchyBoundaryMove (direction='pull_up')",
    "verilog/previewHierarchyPushDown": "verilog/previewHierarchyBoundaryMove (direction='push_down')",
}


def _warn_legacy_hierarchy_command(command: str) -> None:
    replacement = _LEGACY_HIERARCHY_COMMANDS.get(command)
    if replacement is None:
        return
    log.warning("veriforge-lsp: deprecated command %s invoked; use %s instead", command, replacement)


def _error_payload(code: str, message: str) -> dict:
    return ErrorPayload(code, message).to_dict()


def _preview_collapse_payload(ws: Any, params: dict | None = None) -> dict:
    request = params if isinstance(params, dict) else {}
    instance_path = request.get("instancePath") or request.get("instance_path")
    if not instance_path:
        return _error_payload("missing-instance-path", "Collapse preview requires an instancePath.")

    preview = preview_hierarchy_boundary_move(
        ws.design,
        BoundaryMoveRequest(
            direction="collapse",
            selection=BoundaryMoveSelection(kind="instance", instance_path=str(instance_path)),
        ),
    )
    return _legacy_collapse_payload_from_unified(ws, preview)


def _apply_collapse_payload(ws: Any, params: dict | None = None) -> dict:
    payload = _preview_collapse_payload(ws, params)
    payload["appliedByServer"] = False
    return payload


def _preview_extract_payload(ws: Any, params: dict | None = None) -> dict:
    request = params if isinstance(params, dict) else {}
    selection = _boundary_extract_selection_from_request(ws, request)
    if isinstance(selection, dict):
        return selection
    extracted_module_name = str(
        request.get("extractedModuleName") or request.get("name") or request.get("moduleNewName") or "extracted_logic"
    )
    new_instance_name = request.get("instanceName") or request.get("instance") or ""
    preview = preview_hierarchy_boundary_move(
        ws.design,
        BoundaryMoveRequest(
            direction="extract",
            selection=selection,
            extracted_module_name=extracted_module_name,
            new_instance_name=str(new_instance_name) if new_instance_name else "",
        ),
    )
    return _legacy_extract_payload_from_unified(ws, preview)


def _apply_extract_payload(ws: Any, params: dict | None = None) -> dict:
    payload = _preview_extract_payload(ws, params)
    payload["appliedByServer"] = False
    return payload


def _preview_hierarchy_pull_up_payload(ws: Any, params: dict | None = None) -> dict:
    request = params if isinstance(params, dict) else {}
    selection = _boundary_selection_from_request(request)
    if isinstance(selection, dict):
        return selection
    preview = preview_hierarchy_boundary_move(
        ws.design,
        BoundaryMoveRequest(
            direction="pull_up",
            selection=selection,
            target_parent_path=str(request.get("targetParentPath") or request.get("targetParent") or ""),
        ),
    )
    return _legacy_boundary_payload_from_unified(ws, preview)


def _has_range_push_down_selection(request: dict) -> bool:
    raw_selection = request.get("selection") if isinstance(request.get("selection"), dict) else request
    if not isinstance(raw_selection, dict):
        return False
    if str(raw_selection.get("kind") or "").lower() == "range":
        return True
    range_info = raw_selection.get("range")
    if isinstance(range_info, dict) and isinstance(range_info.get("start"), dict):
        return True
    return raw_selection.get("startLine") is not None and raw_selection.get("endLine") is not None


def _push_down_range_extract_selection(ws: Any, request: dict) -> BoundaryMoveSelection | dict:
    """Build a unified extract selection from a range push-down request.

    Push-down clients pre-Phase-11 used 1-based ``startLine``/``endLine`` fields
    inside the selection envelope. Translate them into a synthetic LSP range so
    ``_boundary_extract_selection_from_request`` can consume them uniformly.
    """

    raw_selection = request.get("selection") if isinstance(request.get("selection"), dict) else {}
    if not isinstance(raw_selection, dict):
        raw_selection = {}
    range_info = raw_selection.get("range")
    if not isinstance(range_info, dict) or not isinstance(range_info.get("start"), dict):
        start_line = raw_selection.get("startLine")
        end_line = raw_selection.get("endLine")
        if start_line is not None and end_line is not None:
            range_info = {
                "start": {"line": max(int(start_line) - 1, 0)},
                "end": {"line": max(int(end_line) - 1, 0)},
            }
    bridge_request: dict[str, object] = {
        "selection": {
            **{
                key: value
                for key, value in raw_selection.items()
                if key in ("file", "textDocument", "moduleName", "module")
            },
            "range": range_info,
        },
        "moduleName": raw_selection.get("moduleName") or raw_selection.get("module") or request.get("moduleName") or "",
    }
    return _boundary_extract_selection_from_request(ws, bridge_request)


def _preview_hierarchy_push_down_payload(ws: Any, params: dict | None = None) -> dict:
    request = params if isinstance(params, dict) else {}
    if _has_range_push_down_selection(request):
        if str(request.get("targetParentPath") or request.get("targetParent") or ""):
            return _error_payload(
                "push-down-target-not-supported",
                "Range push-down does not support targetParentPath.",
            )
        selection = _push_down_range_extract_selection(ws, request)
        if isinstance(selection, dict):
            return selection
        extracted_module_name = str(request.get("newModuleName") or request.get("name") or "extracted_logic")
        new_instance_name = request.get("newInstanceName") or request.get("instanceName") or ""
        preview = preview_hierarchy_boundary_move(
            ws.design,
            BoundaryMoveRequest(
                direction="extract",
                selection=selection,
                extracted_module_name=extracted_module_name,
                new_instance_name=str(new_instance_name) if new_instance_name else "",
            ),
        )
        result = _legacy_extract_payload_from_unified(ws, preview)
        # Stamp push-down origin metadata on the inner extract preview so editors
        # can distinguish range-push-down from a standalone extract.
        preview_inner = result.get("preview")
        if isinstance(preview_inner, dict):
            metadata = preview_inner.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
                preview_inner["metadata"] = metadata
            metadata["pushDownMode"] = "range"
            metadata["origin"] = "extract"
        return result
    selection = _boundary_selection_from_request(request)
    if isinstance(selection, dict):
        return selection
    new_module_name = str(request.get("newModuleName") or request.get("name") or "")
    if not new_module_name:
        return _error_payload("new-module-name-required", "Push-down preview requires newModuleName.")
    preview = preview_hierarchy_boundary_move(
        ws.design,
        BoundaryMoveRequest(
            direction="push_down",
            selection=selection,
            new_module_name=new_module_name,
            new_instance_name=str(request.get("newInstanceName") or request.get("instanceName") or ""),
            target_parent_path=str(request.get("targetParentPath") or request.get("targetParent") or ""),
        ),
    )
    return _legacy_boundary_payload_from_unified(ws, preview)


def _unified_boundary_move_payload(ws: Any, params: dict | None = None, *, apply: bool = False) -> dict:
    """Canonical preview/apply for the unified boundary-movement API.

    Accepts a request dict matching ``BoundaryMoveRequest`` shape (with LSP-style
    aliases). Returns ``{ok, preview, details?, edit?, review?, applied?,
    appliedByServer?, writtenFiles?, createdFiles?}``. The ``details`` field
    carries the engine-specific preview payload (extract / collapse) when the
    underlying engine is not the native boundary engine. As with all LSP apply
    handlers, server never writes; clients apply the returned WorkspaceEdit.
    """

    request = params if isinstance(params, dict) else {}
    # Range push-down is a thin alias for extract: clients send
    # ``direction == "push_down"`` with a range selection and the unified API
    # routes to the extract engine, mirroring the legacy
    # ``verilog/previewHierarchyPushDown`` router. Stamp ``pushDownMode``
    # metadata so editors can distinguish range push-down from a standalone
    # extract.
    is_range_push_down = str(request.get("direction") or "").strip() == "push_down" and _has_range_push_down_selection(
        request
    )
    if is_range_push_down:
        request = {**request, "direction": "extract"}
        if "extractedModuleName" not in request:
            new_module = request.get("newModuleName") or request.get("name")
            if new_module:
                request["extractedModuleName"] = new_module
    parsed = _boundary_move_request_from_dict(ws, request)
    if isinstance(parsed, dict):
        return parsed
    preview = preview_hierarchy_boundary_move(ws.design, parsed)
    preview_payload = preview.to_dict()

    # Stale-source enrichment (matches legacy collapse/extract behavior).
    stale_diagnostics = _stale_edit_diagnostics(ws, preview_payload)
    if stale_diagnostics:
        preview_payload["ok"] = False
        preview_payload["diagnostics"] = [*preview_payload["diagnostics"], *stale_diagnostics]

    details: dict[str, object] | None = None
    if preview.engine_kind != "boundary" and preview.engine_preview is not None:
        details_obj = preview.engine_preview
        if hasattr(details_obj, "to_dict"):
            details_payload = details_obj.to_dict()
            # Parity with the legacy per-engine adapters: the extract LSP UX
            # (partial-selection picker, formatted preview) reads
            # ``details["presentation"]``. Decorate here so unified-API clients
            # get the same surface as the legacy ``previewExtractModule`` path.
            if preview.engine_kind == "extract" and isinstance(details_payload, dict):
                details_payload["presentation"] = _extract_preview_presentation(details_payload)
                if is_range_push_down:
                    metadata = details_payload.get("metadata")
                    if not isinstance(metadata, dict):
                        metadata = {}
                        details_payload["metadata"] = metadata
                    metadata["pushDownMode"] = "range"
                    metadata["origin"] = "extract"
            details = details_payload
    edit = None
    review = None
    if preview_payload["ok"] and (preview_payload.get("applyReady") or preview.engine_kind != "boundary"):
        edit = _workspace_edit_from_preview(preview_payload)
        review = _review_payload_from_workspace_edit(edit)
    response = BoundaryMoveLspResponse(
        ok=bool(preview_payload["ok"]), preview=preview_payload, details=details, edit=edit, review=review
    )
    if apply:
        # LSP convention: never server-write; surface the WorkspaceEdit instead.
        response.applied = False
        response.applied_by_server = False
    return response.to_dict()


def _boundary_move_request_from_dict(ws: Any, request: dict) -> BoundaryMoveRequest | dict:
    """Parse an LSP-style request dict into a ``BoundaryMoveRequest``."""

    direction = str(request.get("direction") or "").strip()
    if direction not in {"pull_up", "push_down", "collapse", "extract"}:
        return _error_payload(
            "invalid-direction",
            f"Unsupported direction for unified boundary move: {direction!r}",
        )
    if direction == "extract":
        selection_or_err = _boundary_extract_selection_from_request(ws, request)
    else:
        selection_or_err = _boundary_selection_from_request(request)
    if isinstance(selection_or_err, dict):
        return selection_or_err
    return BoundaryMoveRequest(
        direction=direction,
        selection=selection_or_err,
        target_parent_path=str(request.get("targetParentPath") or request.get("targetParent") or ""),
        new_module_name=str(request.get("newModuleName") or request.get("name") or ""),
        new_instance_name=str(request.get("newInstanceName") or request.get("instanceName") or ""),
        extracted_module_name=str(request.get("extractedModuleName") or request.get("name") or ""),
    )


def _boundary_extract_selection_from_request(ws: Any, request: dict) -> BoundaryMoveSelection | dict:
    """Bridge the LSP extract request shape into a unified extract selection."""

    extract_sel_or_err = _selection_from_request(request)
    if isinstance(extract_sel_or_err, dict):
        return extract_sel_or_err
    sel = extract_sel_or_err
    if sel.signal:
        # Outer moduleName (when set) becomes the explicit extract-module hint so
        # mismatches against selection.module surface as `trace-module-mismatch`.
        explicit_module = str(request.get("moduleName") or request.get("module") or "")
        return BoundaryMoveSelection(
            kind="signal",
            signal=sel.signal,
            signal_module=sel.signal_module,
            module_name=explicit_module,
        )
    raw = request.get("selection") if isinstance(request.get("selection"), dict) else request
    module_name = str(
        (raw.get("moduleName") if isinstance(raw, dict) else None)
        or (raw.get("module") if isinstance(raw, dict) else None)
        or request.get("moduleName")
        or ""
    )
    if not module_name:
        module = _module_for_selection(ws.design, sel)
        if module is not None:
            module_name = module.name
    return BoundaryMoveSelection(
        kind="range",
        file=sel.file,
        start_line=sel.start_line,
        end_line=sel.end_line,
        module_name=module_name,
    )


def _legacy_extract_payload_from_unified(ws: Any, preview: object) -> dict:
    """Adapter: produce legacy ``_preview_extract_payload``-shaped response.

    Phase 6 introduces this helper for use by Phases 7-10 when migrating legacy
    handlers to call the unified core. It expects a ``BoundaryMovePreview`` with
    ``engine_kind == "extract"`` and re-applies the existing LSP enrichments
    (stale-source diagnostics + ``presentation`` decoration).
    """

    inner = getattr(preview, "engine_preview", None)
    if inner is None or not hasattr(inner, "to_dict"):
        return _error_payload("engine-preview-missing", "Extract adapter requires a wrapped ExtractPreview.")
    preview_payload = inner.to_dict()
    stale_diagnostics = _stale_edit_diagnostics(ws, preview_payload)
    if stale_diagnostics:
        preview_payload["ok"] = False
        preview_payload["diagnostics"] = [*preview_payload["diagnostics"], *stale_diagnostics]
    preview_payload["presentation"] = _extract_preview_presentation(preview_payload)
    edit = None
    review = None
    if preview_payload["ok"]:
        edit = _workspace_edit_from_preview(preview_payload)
        review = _review_payload_from_workspace_edit(edit)
    return ExtractResponse(ok=bool(preview_payload["ok"]), preview=preview_payload, edit=edit, review=review).to_dict()


def _legacy_collapse_payload_from_unified(ws: Any, preview: object) -> dict:
    """Adapter: produce legacy ``_preview_collapse_payload``-shaped response."""

    inner = getattr(preview, "engine_preview", None)
    if inner is None or not hasattr(inner, "to_dict"):
        return _error_payload("engine-preview-missing", "Collapse adapter requires a wrapped CollapsePreview.")
    preview_payload = inner.to_dict()
    stale_diagnostics = _stale_edit_diagnostics(ws, preview_payload)
    if stale_diagnostics:
        preview_payload["ok"] = False
        preview_payload["diagnostics"] = [*preview_payload["diagnostics"], *stale_diagnostics]
    edit = None
    review = None
    if preview_payload["ok"]:
        edit = _workspace_edit_from_preview(preview_payload)
        review = _review_payload_from_workspace_edit(edit)
    return CollapseResponse(ok=bool(preview_payload["ok"]), preview=preview_payload, edit=edit, review=review).to_dict()


def _legacy_boundary_payload_from_unified(_ws: Any, preview: object) -> dict:
    """Adapter: produce legacy pull-up / push-down payload shape."""

    if not hasattr(preview, "to_dict"):
        return _error_payload("invalid-preview", "Boundary adapter requires a BoundaryMovePreview.")
    preview_payload = preview.to_dict()
    edit = None
    review = None
    if preview_payload.get("applyReady"):
        edit = _workspace_edit_from_preview(preview_payload)
        review = _review_payload_from_workspace_edit(edit)
    return BoundaryLegacyResponse(
        ok=bool(preview_payload["ok"]), preview=preview_payload, edit=edit, review=review
    ).to_dict()


def _collapse_code_actions(ws: Any, params: Any) -> list[dict]:
    text_document = _param_value(params, "text_document") or _param_value(params, "textDocument", {})
    uri = str(_param_value(text_document, "uri", ""))
    if not uri:
        return []
    request_range = _param_value(params, "range", {})
    start = _param_value(request_range, "start", {})
    line = int(_param_value(start, "line", 0))
    character = int(_param_value(start, "character", 0))
    instance = _instance_at_position(ws.design, uri_to_path(uri), line, character)
    if instance is None:
        return []
    classification = classify_wrapper_module(instance.resolved_module)
    if "previewCollapse" not in classification.refactor_actions:
        return []
    instance_path = _instance_path_for_instance(ws.design, instance, getattr(ws, "top_module", None))
    if instance_path is None:
        return []
    arguments = [{"direction": "collapse", "selection": {"kind": "instance", "instancePath": instance_path}}]
    return [
        {
            "title": f"Preview collapse wrapper {instance_path}",
            "kind": "refactor.rewrite",
            "command": {
                "title": f"Preview collapse wrapper {instance_path}",
                "command": "verilog/previewHierarchyBoundaryMove",
                "arguments": arguments,
            },
        },
        {
            "title": f"Apply collapse wrapper {instance_path}",
            "kind": "refactor.rewrite",
            "command": {
                "title": f"Apply collapse wrapper {instance_path}",
                "command": "verilog/applyHierarchyBoundaryMove",
                "arguments": arguments,
            },
        },
    ]


def _extract_code_actions(ws: Any, params: Any) -> list[dict]:
    text_document = _param_value(params, "text_document") or _param_value(params, "textDocument", {})
    uri = str(_param_value(text_document, "uri", ""))
    request_range = _param_value(params, "range", {})
    if not uri or not _is_non_empty_lsp_range(request_range):
        return []
    arguments = [
        {
            "direction": "extract",
            "textDocument": {"uri": uri},
            "range": request_range,
            "extractedModuleName": "extracted_logic",
        }
    ]
    return [
        {
            "title": "Preview extract selected logic to submodule",
            "kind": "refactor.extract",
            "command": {
                "title": "Preview extract selected logic to submodule",
                "command": "verilog/previewHierarchyBoundaryMove",
                "arguments": arguments,
            },
        },
        {
            "title": "Apply extract selected logic to submodule",
            "kind": "refactor.extract",
            "command": {
                "title": "Apply extract selected logic to submodule",
                "command": "verilog/applyHierarchyBoundaryMove",
                "arguments": arguments,
            },
        },
    ]


def _workspace_edit_from_preview(preview_payload: dict[str, object]) -> dict[str, object]:
    changes: dict[str, list[dict[str, object]]] = {}
    edits = preview_payload.get("edits", [])
    if not isinstance(edits, list):
        return {"changes": changes}
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        file_path = str(edit.get("file") or "")
        if not file_path:
            continue
        changes.setdefault(path_to_uri(file_path), []).append(
            {
                "range": edit.get("range", {}),
                "newText": str(edit.get("replacement") or ""),
            }
        )
    return {"changes": changes}


def _review_payload_from_workspace_edit(workspace_edit: dict[str, object]) -> dict[str, object]:
    files: list[dict[str, object]] = []
    changes = workspace_edit.get("changes", {})
    if not isinstance(changes, dict):
        return {
            "files": files,
            "atomic": True,
            "applyStrategy": "workspace-edit",
            "fileCount": 0,
            "message": "Apply the returned WorkspaceEdit as one atomic refactor; review files are presentation-only.",
        }
    for uri, edits in changes.items():
        if not isinstance(uri, str) or not isinstance(edits, list):
            continue
        path = uri_to_path(uri)
        try:
            current_text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            current_text = ""
        proposed_text = _apply_lsp_text_edits(current_text, edits)
        files.append(
            {
                "uri": uri,
                "file": path,
                "currentLabel": f"current {Path(path).name}",
                "proposedLabel": f"proposed {Path(path).name}",
                "currentText": current_text,
                "proposedText": proposed_text,
                "presentationOnly": True,
                "acceptsWholeEdit": True,
            }
        )
    return {
        "files": files,
        "atomic": True,
        "applyStrategy": "workspace-edit",
        "fileCount": len(files),
        "message": "Apply the returned WorkspaceEdit as one atomic refactor; review files are presentation-only.",
    }


def _extract_preview_presentation(preview_payload: dict[str, object]) -> dict[str, object]:
    selection_text = _extract_selection_text(preview_payload)
    replacement_text = _extract_parent_replacement_text(preview_payload)
    normalized_lines = _extract_normalized_selection_lines(preview_payload)
    boundary_lines = _extract_boundary_lines(preview_payload)
    diagnostics = preview_payload.get("diagnostics", [])
    diagnostic_lines = _diagnostic_lines(diagnostics if isinstance(diagnostics, list) else [])
    suggestion_lines = _extract_selection_suggestion_lines(preview_payload)
    generated_module = str(preview_payload.get("generatedModule") or "")
    diff_text = str(preview_payload.get("diff") or "")
    generated_file = _param_value(_param_value(preview_payload, "metadata", {}), "generatedModuleFile", "")

    sections: list[dict[str, str]] = []
    if selection_text:
        sections.append({"kind": "selected-source", "title": "Selected source", "text": selection_text})
    if normalized_lines:
        sections.append(
            {
                "kind": "normalized-selection",
                "title": "Normalized semantic nodes",
                "text": "\n".join(normalized_lines),
            }
        )
    if boundary_lines:
        sections.append({"kind": "boundary", "title": "Boundary ports", "text": "\n".join(boundary_lines)})
    if generated_module:
        section_title = f"Generated module: {Path(str(generated_file or 'generated.v')).name}"
        sections.append({"kind": "generated-module", "title": section_title, "text": generated_module})
    if replacement_text:
        sections.append({"kind": "parent-replacement", "title": "Parent replacement", "text": replacement_text})
    if diagnostic_lines:
        sections.append({"kind": "diagnostics", "title": "Diagnostics", "text": "\n".join(diagnostic_lines)})
    if suggestion_lines:
        sections.append(
            {
                "kind": "selection-suggestions",
                "title": "Suggested expanded selections",
                "text": "\n".join(suggestion_lines),
            }
        )
    if diff_text:
        sections.append({"kind": "diff", "title": "Unified diff", "text": diff_text})

    return {
        "title": f"Extract preview: {preview_payload.get('moduleName', '')} -> {preview_payload.get('extractedModuleName', '')}",
        "selectionText": selection_text,
        "replacementText": replacement_text,
        "normalizedLines": normalized_lines,
        "boundaryLines": boundary_lines,
        "diagnosticLines": diagnostic_lines,
        "suggestionLines": suggestion_lines,
        "generatedModuleText": generated_module,
        "diffText": diff_text,
        "sections": sections,
    }


def _extract_selection_text(preview_payload: dict[str, object]) -> str:
    selection = _param_value(preview_payload, "selection", {})
    if not isinstance(selection, dict):
        return ""
    file_path = str(selection.get("file") or "")
    if not file_path:
        return ""
    try:
        lines = (
            Path(file_path)
            .read_text(encoding="utf-8", errors="replace")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
            .split("\n")
        )
    except OSError:
        return ""
    start_line = max(1, int(selection.get("startLine", 1)))
    end_line = max(start_line, int(selection.get("endLine", start_line)))
    return "\n".join(lines[start_line - 1 : end_line]).rstrip()


def _extract_parent_replacement_text(preview_payload: dict[str, object]) -> str:
    selection = _param_value(preview_payload, "selection", {})
    if not isinstance(selection, dict):
        return ""
    file_path = str(selection.get("file") or "")
    if not file_path:
        return ""
    start_line = max(1, int(selection.get("startLine", 1)))
    end_line = max(start_line, int(selection.get("endLine", start_line)))
    edits = preview_payload.get("edits", [])
    if not isinstance(edits, list):
        return ""
    for edit in edits:
        if not isinstance(edit, dict) or str(edit.get("file") or "") != file_path:
            continue
        replacement = str(edit.get("replacement") or "").rstrip()
        if not replacement:
            continue
        edit_range = edit.get("range", {})
        if not isinstance(edit_range, dict):
            continue
        start = edit_range.get("start", {})
        end = edit_range.get("end", {})
        if not isinstance(start, dict) or not isinstance(end, dict):
            continue
        edit_start_line = int(start.get("line", 0)) + 1
        edit_end_line = int(end.get("line", edit_start_line - 1)) + 1
        if edit_start_line <= end_line and edit_end_line >= start_line:
            return replacement
    return ""


def _extract_normalized_selection_lines(preview_payload: dict[str, object]) -> list[str]:
    metadata = _param_value(preview_payload, "metadata", {})
    normalization = _param_value(metadata, "selectionNormalization", {})
    items = _param_value(normalization, "items", [])
    if not isinstance(items, list):
        return []
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "?")
        name = str(item.get("name") or "?")
        support = str(item.get("support") or "")
        status = "supported" if item.get("supported") else "blocked"
        detail = f" ({support})" if support else ""
        lines.append(f"- {kind}: {name} [{status}]{detail}")
    return lines


def _extract_selection_suggestion_lines(preview_payload: dict[str, object]) -> list[str]:
    metadata = _param_value(preview_payload, "metadata", {})
    normalization = _param_value(metadata, "selectionNormalization", {})
    suggestions = _param_value(normalization, "suggestions", [])
    if not isinstance(suggestions, list):
        return []
    lines: list[str] = []
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        kind = str(suggestion.get("kind") or "?")
        label = str(suggestion.get("label") or "")
        start_line = suggestion.get("startLine")
        end_line = suggestion.get("endLine")
        try:
            start_int = int(start_line) if start_line is not None else 0
            end_int = int(end_line) if end_line is not None else 0
        except (TypeError, ValueError):
            start_int, end_int = 0, 0
        node_kind = str(suggestion.get("nodeKind") or "")
        node_name = str(suggestion.get("nodeName") or "")
        descriptor = label or f"{kind}"
        if node_kind and node_name:
            descriptor = f"{descriptor} [{node_kind} {node_name}]"
        elif node_kind:
            descriptor = f"{descriptor} [{node_kind}]"
        if start_int and end_int:
            lines.append(f"- {descriptor} (lines {start_int}-{end_int})")
        else:
            lines.append(f"- {descriptor}")
    return lines


def _extract_boundary_lines(preview_payload: dict[str, object]) -> list[str]:
    boundary = preview_payload.get("boundary", {})
    if not isinstance(boundary, dict):
        return []
    lines: list[str] = []
    for key in ("inputs", "outputs", "internals"):
        values = boundary.get(key, [])
        if not isinstance(values, list):
            continue
        label = key.capitalize()
        rendered = ", ".join(str(value) for value in values) if values else "-"
        lines.append(f"{label}: {rendered}")
    return lines


def _diagnostic_lines(diagnostics: list[object]) -> list[str]:
    lines: list[str] = []
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict):
            continue
        severity = str(diagnostic.get("severity") or "info").upper()
        code = str(diagnostic.get("code") or "diagnostic")
        message = str(diagnostic.get("message") or "")
        lines.append(f"[{severity}] {code}: {message}")
    return lines


def _apply_lsp_text_edits(text: str, edits: list[object]) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    line_offsets = _line_offsets(normalized)

    def edit_key(edit: dict[str, object]) -> tuple[int, int]:
        rng = edit.get("range", {})
        start = rng.get("start", {}) if isinstance(rng, dict) else {}
        if not isinstance(start, dict):
            return (0, 0)
        return int(start.get("line", 0)), int(start.get("character", 0))

    valid_edits = [edit for edit in edits if isinstance(edit, dict)]
    for edit in sorted(valid_edits, key=edit_key, reverse=True):
        rng = edit.get("range", {})
        if not isinstance(rng, dict):
            continue
        start = rng.get("start", {})
        end = rng.get("end", {})
        if not isinstance(start, dict) or not isinstance(end, dict):
            continue
        start_offset = _lsp_position_to_offset(
            line_offsets,
            normalized,
            int(start.get("line", 0)),
            int(start.get("character", 0)),
        )
        end_offset = _lsp_position_to_offset(
            line_offsets,
            normalized,
            int(end.get("line", 0)),
            int(end.get("character", 0)),
        )
        normalized = f"{normalized[:start_offset]}{edit.get('newText', '')}{normalized[end_offset:]}"
        line_offsets = _line_offsets(normalized)
    return normalized


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
    return max(line_start, min(line_start + max(0, character), line_end))


def _stale_edit_diagnostics(ws: Any, preview_payload: dict[str, object]) -> list[dict[str, object]]:
    is_stale = getattr(ws, "is_stale", None)
    if not callable(is_stale):
        return []
    diagnostics: list[dict[str, object]] = []
    edits = preview_payload.get("edits", [])
    if not isinstance(edits, list):
        return diagnostics
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        file_path = str(edit.get("file") or "")
        if file_path and is_stale(file_path):
            diagnostics.append(
                RefactorDiagnostic(
                    "stale-source",
                    f"Source file has unsaved or stale changes and cannot produce a safe collapse edit: {file_path}",
                    severity="error",
                ).to_dict()
            )
    return diagnostics


def _param_value(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _selection_from_request(request: dict) -> ExtractSelection | dict:  # noqa: PLR0911
    raw_selection = request.get("selection") if isinstance(request.get("selection"), dict) else request

    # Trace-neighborhood selection: {signal: name, module: modname} (range/file optional).
    signal_name = raw_selection.get("signal")
    if isinstance(signal_name, str) and signal_name:
        signal_module = raw_selection.get("module") or raw_selection.get("signalModule") or ""
        if not isinstance(signal_module, str) or not signal_module:
            # Fallback: outer moduleName from request envelope.
            outer_module = request.get("moduleName") or request.get("module")
            signal_module = str(outer_module) if outer_module else ""
        if not signal_module:
            return _error_payload(
                "missing-trace-module",
                "Trace-neighborhood selection requires a module name.",
            )
        return ExtractSelection(
            file="",
            start_line=0,
            end_line=0,
            signal=signal_name,
            signal_module=signal_module,
        )

    file_path = str(raw_selection.get("file") or "")
    text_document = raw_selection.get("textDocument", {})
    if not file_path and isinstance(text_document, dict):
        file_path = uri_to_path(str(text_document.get("uri", "")))
    range_info = raw_selection.get("range", {})
    if not file_path:
        return _error_payload("missing-selection-file", "Extract preview requires a file or textDocument URI.")
    if not isinstance(range_info, dict):
        return _error_payload("missing-selection-range", "Extract preview requires an LSP range.")
    start = range_info.get("start", {})
    end = range_info.get("end", {})
    if not isinstance(start, dict) or not isinstance(end, dict):
        return _error_payload("missing-selection-range", "Extract preview requires range start and end positions.")
    start_line = int(start.get("line", 0)) + 1
    end_line = int(end.get("line", start.get("line", 0))) + 1
    if end_line < start_line:
        return _error_payload("invalid-selection-range", "Extract selection range must be ordered.")
    return ExtractSelection(file_path, start_line, end_line)


def _boundary_selection_from_request(request: dict) -> BoundaryMoveSelection | dict:
    raw_selection = request.get("selection") if isinstance(request.get("selection"), dict) else request
    kind = str(raw_selection.get("kind") or "")
    instance_path = str(raw_selection.get("instancePath") or raw_selection.get("instance") or "")
    module_name = str(raw_selection.get("moduleName") or raw_selection.get("module") or "")
    file_path = str(raw_selection.get("file") or "")
    text_document = raw_selection.get("textDocument", {})
    range_info = raw_selection.get("range", {})
    if not file_path and isinstance(text_document, dict):
        file_path = uri_to_path(str(text_document.get("uri", "")))

    if not kind:
        if instance_path:
            kind = "instance"
        elif module_name:
            kind = "module"
        elif file_path and isinstance(range_info, dict):
            kind = "range"
        elif file_path:
            kind = "file"
    if not kind:
        return _error_payload(
            "missing-boundary-selection",
            "Hierarchy pull-up preview requires a selection kind plus instancePath, moduleName, or file.",
        )
    start_line = 0
    end_line = 0
    if kind == "range":
        if not file_path:
            return _error_payload(
                "missing-selection-file", "Range hierarchy selections require a file or textDocument URI."
            )
        if not isinstance(range_info, dict):
            return _error_payload("missing-selection-range", "Range hierarchy selections require an LSP range.")
        start = range_info.get("start", {})
        end = range_info.get("end", {})
        if not isinstance(start, dict) or not isinstance(end, dict):
            return _error_payload(
                "missing-selection-range", "Range hierarchy selections require range start and end positions."
            )
        start_line = int(start.get("line", 0)) + 1
        end_line = int(end.get("line", start.get("line", 0))) + 1
        if end_line < start_line:
            return _error_payload("invalid-selection-range", "Range hierarchy selection must be ordered.")
    return BoundaryMoveSelection(
        kind=kind,
        instance_path=instance_path,
        module_name=module_name,
        file=file_path,
        start_line=start_line,
        end_line=end_line,
    )


def _module_for_selection(design: Any, selection: ExtractSelection) -> Any:
    candidates = [
        module for module in design.modules if _source_range_contains_selection(getattr(module, "loc", None), selection)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda module: _loc_span(getattr(module, "loc", None)))


def _source_range_contains_selection(loc: Any, selection: ExtractSelection) -> bool:
    if loc is None or not loc.file:
        return False
    if os.path.normcase(os.path.normpath(loc.file)) != os.path.normcase(os.path.normpath(selection.file)):
        return False
    start_line = loc.line or 1
    end_line = loc.end_line or start_line
    return start_line <= selection.start_line and end_line >= selection.end_line


def _is_non_empty_lsp_range(range_info: Any) -> bool:
    start = _param_value(range_info, "start", {})
    end = _param_value(range_info, "end", {})
    return (
        int(_param_value(start, "line", 0)),
        int(_param_value(start, "character", 0)),
    ) != (
        int(_param_value(end, "line", 0)),
        int(_param_value(end, "character", 0)),
    )


def _instance_at_position(design: Any, path: str, line: int, character: int) -> Any:
    candidates: list[Any] = []
    for module in design.modules:
        for instance in module.instances:
            if _loc_contains_lsp_position(getattr(instance, "loc", None), path, line, character):
                candidates.append(instance)
    if not candidates:
        return None
    return min(candidates, key=lambda inst: _loc_span(getattr(inst, "loc", None)))


def _loc_contains_lsp_position(loc: Any, path: str, line: int, character: int) -> bool:
    if loc is None or not loc.file:
        return False
    if os.path.normcase(os.path.normpath(loc.file)) != os.path.normcase(os.path.normpath(path)):
        return False
    source_line = line + 1
    source_col = character + 1
    start_line = loc.line or 1
    start_col = loc.column or 1
    end_line = loc.end_line or start_line
    end_col = loc.end_column or start_col
    if source_line < start_line or source_line > end_line:
        return False
    if source_line == start_line and source_col < start_col:
        return False
    return not (source_line == end_line and source_col > end_col)


def _loc_span(loc: Any) -> int:
    if loc is None:
        return 0
    start = (loc.line or 1) * 10_000 + (loc.column or 1)
    end = (loc.end_line or loc.line or 1) * 10_000 + (loc.end_column or loc.column or 1)
    return max(1, end - start)


def _instance_path_for_instance(design: Any, target: Any, top: str | None) -> str | None:
    roots = []
    if top:
        root = design.get_module(top)
        if root is not None:
            roots.append(root)
    if not roots:
        roots = design.get_top_modules() or list(design.modules)
    for root in roots:
        path = _instance_path_from_module(root, root.name, target, stack=())
        if path is not None:
            return path
    return None


def _instance_path_from_module(module: Any, module_path: str, target: Any, stack: tuple[str, ...]) -> str | None:
    if module.name in stack:
        return None
    for instance in module.instances:
        instance_path = f"{module_path}/{instance.instance_name}"
        if instance is target:
            return instance_path
        resolved = instance.resolved_module
        if resolved is None:
            continue
        child_path = _instance_path_from_module(resolved, instance_path, target, (*stack, module.name))
        if child_path is not None:
            return child_path
    return None
