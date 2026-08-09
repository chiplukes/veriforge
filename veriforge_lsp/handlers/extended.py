"""
Custom Verilog LSP extensions:
  verilog/hierarchyTree       — push full instantiation tree after parse
  verilog/hierarchyGraph      — return hierarchy graph with wrapper metadata
  verilog/setTopModule        — pin/unpin top module, writes .veriforge_lsp.json
  verilog/resolveHierarchyChildren — lazy-load children for a module
  verilog/traceSignal         — driver/load connectivity trace for a signal
  verilog/previewHierarchyBoundaryMove — canonical hierarchy preview entry point
  verilog/applyHierarchyBoundaryMove — canonical hierarchy apply entry point
  verilog/previewCollapseHierarchy — deprecated shim for collapse preview
  verilog/applyCollapseHierarchy — deprecated shim for collapse apply
  verilog/previewExtractModule — deprecated shim for extract preview
  verilog/applyExtractModule — deprecated shim for extract apply
  verilog/previewHierarchyPullUp — deprecated shim for pull-up preview
  verilog/previewHierarchyPushDown — deprecated shim for push-down preview
  verilog.reparse             — force full workspace re-parse

Payload shapes live in ``veriforge_lsp.payloads``; the builders themselves
are split across ``handlers/hierarchy.py`` (tree/graph/trace) and
``handlers/refactor.py`` (collapse/extract/pull-up/push-down/boundary-move).
This module stays the single ``register()`` aggregator and dispatcher, and
re-exports the handful of hierarchy/refactor helpers that tests import
directly from here.
"""

from __future__ import annotations

from typing import Any

from lsprotocol.types import TEXT_DOCUMENT_CODE_ACTION, WORKSPACE_EXECUTE_COMMAND, ExecuteCommandParams
from pygls.lsp.server import LanguageServer

from veriforge_lsp.handlers.hierarchy import (  # noqa: F401 -- _build_trace/_module_node/_read_preview/_width_str re-exported for tests
    _build_trace,
    _build_tree,
    _hierarchy_graph_payload,
    _module_children,
    _module_node,
    _read_preview,
    _trace_signal_payload,
    _width_str,
    push_hierarchy_tree,
)
from veriforge_lsp.handlers.refactor import (
    _apply_collapse_payload,
    _apply_extract_payload,
    _collapse_code_actions,
    _extract_code_actions,
    _preview_collapse_payload,
    _preview_extract_payload,
    _preview_hierarchy_pull_up_payload,
    _preview_hierarchy_push_down_payload,
    _unified_boundary_move_payload,
    _warn_legacy_hierarchy_command,
)
from veriforge_lsp.payloads import (
    BoundaryMoveLspRequest,
    CollapseRequest,
    ErrorPayload,
    ExtractRequest,
    PreviewPullUpRequest,
    PreviewPushDownRequest,
    ReparseResponse,
    ResolveHierarchyChildrenRequest,
    ResolveHierarchyChildrenResponse,
    SetTopModuleRequest,
    SetTopModuleResponse,
)


def register(ls: LanguageServer) -> None:  # noqa: PLR0915
    @ls.command("verilog/setTopModule")
    def set_top_module(params: dict) -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return SetTopModuleResponse(ok=False).to_dict()
        request = SetTopModuleRequest.from_dict(params)
        ws.set_top_module(request.module_name)
        roots = _build_tree(ws)
        return SetTopModuleResponse(ok=True, hierarchy_tree={"roots": roots}).to_dict()

    @ls.command("verilog/resolveHierarchyChildren")
    def resolve_children(params: dict) -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return ResolveHierarchyChildrenResponse().to_dict()
        request = ResolveHierarchyChildrenRequest.from_dict(params)
        design = ws.design
        mod = next((m for m in design.modules if m.name == request.module_name), None)
        if mod is None:
            return ResolveHierarchyChildrenResponse().to_dict()
        children = _module_children(mod, design, depth=3, parent_path=request.instance_path)
        return ResolveHierarchyChildrenResponse(children=children).to_dict()

    @ls.command("verilog/hierarchyGraph")
    def hierarchy_graph(params: dict) -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return _error_payload("workspace-unavailable", "Workspace is not initialized.")
        return _hierarchy_graph_payload(ws, params)

    @ls.command("verilog/traceSignal")
    def trace_signal(params: dict) -> dict | None:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return None
        return _trace_signal_payload(ws, params)

    @ls.command("verilog/previewCollapseHierarchy")
    def preview_collapse(params: dict) -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return _error_payload("workspace-unavailable", "Workspace is not initialized.")
        _warn_legacy_hierarchy_command("verilog/previewCollapseHierarchy")
        return _preview_collapse_payload(ws, CollapseRequest.from_dict(params).to_dict())

    @ls.command("verilog/applyCollapseHierarchy")
    def apply_collapse(params: dict) -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return _error_payload("workspace-unavailable", "Workspace is not initialized.")
        _warn_legacy_hierarchy_command("verilog/applyCollapseHierarchy")
        return _apply_collapse_payload(ws, CollapseRequest.from_dict(params).to_dict())

    @ls.command("verilog/previewExtractModule")
    def preview_extract(params: dict) -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return _error_payload("workspace-unavailable", "Workspace is not initialized.")
        _warn_legacy_hierarchy_command("verilog/previewExtractModule")
        return _preview_extract_payload(ws, ExtractRequest.from_dict(params).to_dict())

    @ls.command("verilog/applyExtractModule")
    def apply_extract(params: dict) -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return _error_payload("workspace-unavailable", "Workspace is not initialized.")
        _warn_legacy_hierarchy_command("verilog/applyExtractModule")
        return _apply_extract_payload(ws, ExtractRequest.from_dict(params).to_dict())

    @ls.command("verilog/previewHierarchyPullUp")
    def preview_hierarchy_pull_up(params: dict) -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return _error_payload("workspace-unavailable", "Workspace is not initialized.")
        _warn_legacy_hierarchy_command("verilog/previewHierarchyPullUp")
        return _preview_hierarchy_pull_up_payload(ws, PreviewPullUpRequest.from_dict(params).to_dict())

    @ls.command("verilog/previewHierarchyPushDown")
    def preview_hierarchy_push_down(params: dict) -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return _error_payload("workspace-unavailable", "Workspace is not initialized.")
        _warn_legacy_hierarchy_command("verilog/previewHierarchyPushDown")
        return _preview_hierarchy_push_down_payload(ws, PreviewPushDownRequest.from_dict(params).to_dict())

    @ls.command("verilog/previewHierarchyBoundaryMove")
    def preview_hierarchy_boundary_move_cmd(params: dict) -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return _error_payload("workspace-unavailable", "Workspace is not initialized.")
        return _unified_boundary_move_payload(ws, BoundaryMoveLspRequest.from_dict(params).to_dict(), apply=False)

    @ls.command("verilog/applyHierarchyBoundaryMove")
    def apply_hierarchy_boundary_move_cmd(params: dict) -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return _error_payload("workspace-unavailable", "Workspace is not initialized.")
        return _unified_boundary_move_payload(ws, BoundaryMoveLspRequest.from_dict(params).to_dict(), apply=True)

    @ls.command("verilog.reparse")
    def reparse() -> dict:  # type: ignore[type-arg]
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return _error_payload("workspace-unavailable", "Workspace is not initialized.")
        ws.parse_workspace_async()
        return ReparseResponse().to_dict()

    @ls.feature(TEXT_DOCUMENT_CODE_ACTION)
    def code_actions(params: Any) -> list[dict]:
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return []
        return [*_collapse_code_actions(ws, params), *_extract_code_actions(ws, params)]

    @ls.feature(WORKSPACE_EXECUTE_COMMAND)
    def execute_command(params: ExecuteCommandParams) -> Any:
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return _error_payload("workspace-unavailable", "Workspace is not initialized.")
        return _execute_command_payload(ws, params.command, getattr(params, "arguments", None))


def _execute_command_payload(ws: Any, command: str, arguments: list[Any] | None = None) -> Any:  # noqa: PLR0911, PLR0912
    first = arguments[0] if arguments else None
    if command == "verilog/setTopModule":
        request = SetTopModuleRequest.from_dict(first)
        ws.set_top_module(request.module_name)
        roots = _build_tree(ws)
        return SetTopModuleResponse(ok=True, hierarchy_tree={"roots": roots}).to_dict()
    if command == "verilog/resolveHierarchyChildren":
        request = ResolveHierarchyChildrenRequest.from_dict(first)
        mod = next((m for m in ws.design.modules if m.name == request.module_name), None)
        if mod is None:
            return ResolveHierarchyChildrenResponse().to_dict()
        children = _module_children(mod, ws.design, depth=3, parent_path=request.instance_path)
        return ResolveHierarchyChildrenResponse(children=children).to_dict()
    if command == "verilog/hierarchyGraph":
        return _hierarchy_graph_payload(ws, first if isinstance(first, dict) else None)
    if command == "verilog/traceSignal":
        return _trace_signal_payload(ws, first if isinstance(first, dict) else None)
    if command == "verilog/previewCollapseHierarchy":
        _warn_legacy_hierarchy_command(command)
        return _preview_collapse_payload(ws, CollapseRequest.from_dict(first).to_dict())
    if command == "verilog/applyCollapseHierarchy":
        _warn_legacy_hierarchy_command(command)
        return _apply_collapse_payload(ws, CollapseRequest.from_dict(first).to_dict())
    if command == "verilog/previewExtractModule":
        _warn_legacy_hierarchy_command(command)
        return _preview_extract_payload(ws, ExtractRequest.from_dict(first).to_dict())
    if command == "verilog/applyExtractModule":
        _warn_legacy_hierarchy_command(command)
        return _apply_extract_payload(ws, ExtractRequest.from_dict(first).to_dict())
    if command == "verilog/previewHierarchyPullUp":
        _warn_legacy_hierarchy_command(command)
        return _preview_hierarchy_pull_up_payload(ws, PreviewPullUpRequest.from_dict(first).to_dict())
    if command == "verilog/previewHierarchyPushDown":
        _warn_legacy_hierarchy_command(command)
        return _preview_hierarchy_push_down_payload(ws, PreviewPushDownRequest.from_dict(first).to_dict())
    if command == "verilog/previewHierarchyBoundaryMove":
        return _unified_boundary_move_payload(ws, BoundaryMoveLspRequest.from_dict(first).to_dict(), apply=False)
    if command == "verilog/applyHierarchyBoundaryMove":
        return _unified_boundary_move_payload(ws, BoundaryMoveLspRequest.from_dict(first).to_dict(), apply=True)
    if command == "verilog.reparse":
        ws.parse_workspace_async()
        return ReparseResponse().to_dict()
    return _error_payload("unknown-command", f"Unsupported Verilog command: {command}")


def _error_payload(code: str, message: str) -> dict:
    return ErrorPayload(code, message).to_dict()
