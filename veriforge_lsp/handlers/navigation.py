"""textDocument/definition, references, and hover handlers."""

from __future__ import annotations

import logging
from typing import Any

from pygls.lsp.server import LanguageServer

from veriforge_lsp.protocol import loc_to_lsp_range, make_location, uri_to_path

log = logging.getLogger(__name__)

_STALE_NOTE = "\n\n> ⚠ Stale: file has syntax errors; showing last clean analysis."


def register(ls: LanguageServer) -> None:
    from lsprotocol.types import (
        TEXT_DOCUMENT_DEFINITION,
        TEXT_DOCUMENT_HOVER,
        TEXT_DOCUMENT_REFERENCES,
        Hover,
        Location,
        MarkupContent,
        MarkupKind,
        ReferenceParams,
        TextDocumentPositionParams,
    )

    @ls.feature(TEXT_DOCUMENT_DEFINITION)
    def definition(params: TextDocumentPositionParams) -> list[Location] | None:
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return None
        path = uri_to_path(params.text_document.uri)
        line = params.position.line
        char = params.position.character
        node = ws.index.node_at(path, line, char)
        if node is None:
            return None
        target = _resolve_definition(node)
        if target is None:
            return None
        loc = getattr(target, "loc", None)
        if not loc or not loc.file:
            return None
        return [Location(uri=make_location(loc)["uri"], range=loc_to_lsp_range(loc))]  # type: ignore[arg-type]

    @ls.feature(TEXT_DOCUMENT_REFERENCES)
    def references(params: ReferenceParams) -> list[Location] | None:
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return None
        path = uri_to_path(params.text_document.uri)
        line = params.position.line
        char = params.position.character
        node = ws.index.node_at(path, line, char)
        if node is None:
            return None
        # Resolve to definition first so we look up the canonical node
        target = _resolve_definition(node) or node
        ref_locs = ws.index.references_of(target)
        result: list[Location] = []
        if params.context.include_declaration:
            def_loc = getattr(target, "loc", None)
            if def_loc and def_loc.file:
                result.append(Location(uri=make_location(def_loc)["uri"], range=loc_to_lsp_range(def_loc)))  # type: ignore[arg-type]
        for ref_loc in ref_locs:
            if ref_loc.file:
                result.append(Location(uri=make_location(ref_loc)["uri"], range=loc_to_lsp_range(ref_loc)))  # type: ignore[arg-type]
        return result or None

    @ls.feature(TEXT_DOCUMENT_HOVER)
    def hover(params: TextDocumentPositionParams) -> Hover | None:
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return None
        path = uri_to_path(params.text_document.uri)
        line = params.position.line
        char = params.position.character
        node = ws.index.node_at(path, line, char)
        if node is None:
            return None
        text = _hover_text(node)
        if not text:
            return None
        if ws.is_stale(path):
            text += _STALE_NOTE
        return Hover(contents=MarkupContent(kind=MarkupKind.Markdown, value=text))


# ------------------------------------------------------------------
# Node helpers
# ------------------------------------------------------------------


def _resolve_definition(node: Any) -> Any:
    """Follow resolved pointers to the definition node."""
    from veriforge.model.expressions import Identifier
    from veriforge.model.instances import Instance, PortConnection

    if isinstance(node, Identifier) and node.resolved is not None:
        return node.resolved
    if isinstance(node, Instance) and node.resolved_module is not None:
        return node.resolved_module
    if isinstance(node, PortConnection) and node.resolved_port is not None:
        return node.resolved_port
    return node


def _hover_text(node: Any) -> str:
    """Build markdown hover text for a node."""
    from veriforge.model.design import Module
    from veriforge.model.expressions import Identifier
    from veriforge.model.instances import Instance
    from veriforge.model.nets import Net
    from veriforge.model.ports import Port
    from veriforge.model.variables import Variable

    # Follow to the definition for richer info
    target = _resolve_definition(node)

    if isinstance(target, Port):
        direction = str(getattr(target, "direction", "")).replace("PortDirection.", "").lower()
        width = _width_str(target)
        net_type = getattr(target, "net_type", "") or ""
        parts = [f"**port** `{target.name}`"]
        parts.append(f"`{direction}` {net_type} {width}".strip())
        parent = getattr(target, "parent", None)
        if parent and hasattr(parent, "name"):
            parts.append(f"module: `{parent.name}`")
        return "\n\n".join(parts)

    if isinstance(target, (Net, Variable)):
        kind = str(getattr(target, "kind", "")).split(".")[-1].lower()
        width = _width_str(target)
        drivers = len(getattr(target, "drivers", []) or [])
        loads = len(getattr(target, "loads", []) or [])
        parts = [f"**{kind}** `{target.name}`"]
        parts.append(f"{width}  drivers: {drivers}  loads: {loads}".strip())
        return "\n\n".join(parts)

    if isinstance(target, Instance):
        mod_name = target.module_name
        params = getattr(target, "parameter_bindings", []) or []
        param_str = ", ".join(f"{p.name}={p.value}" for p in params) if params else ""
        text = f"**instance** `{target.instance_name}` of `{mod_name}`"
        if param_str:
            text += f"\n\nparams: `{param_str}`"
        return text

    if isinstance(target, Module):
        ports = len(getattr(target, "ports", []) or [])
        instances = len(getattr(target, "instances", []) or [])
        fname = getattr(getattr(target, "loc", None), "file", "") or ""
        return f"**module** `{target.name}`\n\nports: {ports}  instances: {instances}\n\n`{fname}`"

    if isinstance(node, Identifier):
        return f"`{node.name}`"

    return ""


def _width_str(node: Any) -> str:
    rng = getattr(node, "width", None)
    if rng is None:
        return ""
    msb = getattr(rng, "msb", None)
    lsb = getattr(rng, "lsb", None)
    if msb is None or lsb is None:
        return ""
    return f"[{msb}:{lsb}]"
