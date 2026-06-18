"""textDocument/documentSymbol and workspace/symbol handlers."""

from __future__ import annotations

import logging
from typing import Any

from pygls.lsp.server import LanguageServer

from veriforge_lsp.protocol import loc_to_lsp_range, make_location, uri_to_path

log = logging.getLogger(__name__)

# LSP SymbolKind values (subset we use)
_SK_MODULE = 2
_SK_VARIABLE = 13
_SK_OBJECT = 19  # instance
_SK_EVENT = 24  # always block
_SK_CONSTANT = 14  # parameter


def register(ls: LanguageServer) -> None:
    from lsprotocol.types import (
        TEXT_DOCUMENT_DOCUMENT_SYMBOL,
        WORKSPACE_SYMBOL,
        DocumentSymbol,
        DocumentSymbolParams,
        SymbolInformation,
        WorkspaceSymbolParams,
    )

    @ls.feature(TEXT_DOCUMENT_DOCUMENT_SYMBOL)
    def document_symbol(params: DocumentSymbolParams) -> list[DocumentSymbol] | None:
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return None
        path = uri_to_path(params.text_document.uri)
        design = ws.design
        result: list[DocumentSymbol] = []
        for mod in design.modules:
            if not (mod.loc and mod.loc.file and _same_path(mod.loc.file, path)):
                continue
            result.append(_module_to_symbol(mod))
        return result or None

    @ls.feature(WORKSPACE_SYMBOL)
    def workspace_symbol(params: WorkspaceSymbolParams) -> list[SymbolInformation] | None:
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return None
        query = (params.query or "").lower()
        design = ws.design
        results: list[SymbolInformation] = []
        for mod in design.modules:
            if not mod.loc or not mod.loc.file:
                continue
            if query and query not in mod.name.lower():
                continue
            results.append(
                SymbolInformation(
                    name=mod.name,
                    kind=_SK_MODULE,  # type: ignore[arg-type]
                    location=make_location(mod.loc),  # type: ignore[arg-type]
                )
            )
            for port in mod.ports or []:
                if query and query not in port.name.lower():
                    continue
                if port.loc and port.loc.file:
                    results.append(
                        SymbolInformation(
                            name=port.name,
                            kind=_SK_VARIABLE,  # type: ignore[arg-type]
                            location=make_location(port.loc),  # type: ignore[arg-type]
                            container_name=mod.name,
                        )
                    )
            for net in mod.nets or []:
                if query and query not in net.name.lower():
                    continue
                if net.loc and net.loc.file:
                    results.append(
                        SymbolInformation(
                            name=net.name,
                            kind=_SK_VARIABLE,  # type: ignore[arg-type]
                            location=make_location(net.loc),  # type: ignore[arg-type]
                            container_name=mod.name,
                        )
                    )
        return results[:200] if results else None  # cap for large designs


# ------------------------------------------------------------------
# DocumentSymbol builders
# ------------------------------------------------------------------


def _module_to_symbol(mod: Any) -> Any:
    from lsprotocol.types import DocumentSymbol

    children: list[DocumentSymbol] = []

    for port in mod.ports or []:
        if port.loc:
            direction = str(getattr(port, "direction", "")).replace("PortDirection.", "").lower()
            width = _width_str(port)
            children.append(
                DocumentSymbol(
                    name=port.name,
                    kind=_SK_VARIABLE,  # type: ignore[arg-type]
                    range=loc_to_lsp_range(port.loc),  # type: ignore[arg-type]
                    selection_range=loc_to_lsp_range(port.loc),  # type: ignore[arg-type]
                    detail=f"{direction} {width}".strip(),
                )
            )

    for net in mod.nets or []:
        if net.loc:
            kind_str = str(getattr(net, "kind", "")).split(".")[-1].lower()
            width = _width_str(net)
            children.append(
                DocumentSymbol(
                    name=net.name,
                    kind=_SK_VARIABLE,  # type: ignore[arg-type]
                    range=loc_to_lsp_range(net.loc),  # type: ignore[arg-type]
                    selection_range=loc_to_lsp_range(net.loc),  # type: ignore[arg-type]
                    detail=f"{kind_str} {width}".strip(),
                )
            )

    for var in mod.variables or []:
        if var.loc:
            kind_str = str(getattr(var, "kind", "")).split(".")[-1].lower()
            children.append(
                DocumentSymbol(
                    name=var.name,
                    kind=_SK_VARIABLE,  # type: ignore[arg-type]
                    range=loc_to_lsp_range(var.loc),  # type: ignore[arg-type]
                    selection_range=loc_to_lsp_range(var.loc),  # type: ignore[arg-type]
                    detail=kind_str,
                )
            )

    for param in mod.parameters or []:
        if param.loc:
            children.append(
                DocumentSymbol(
                    name=param.name,
                    kind=_SK_CONSTANT,  # type: ignore[arg-type]
                    range=loc_to_lsp_range(param.loc),  # type: ignore[arg-type]
                    selection_range=loc_to_lsp_range(param.loc),  # type: ignore[arg-type]
                    detail="parameter",
                )
            )

    for inst in mod.instances or []:
        if inst.loc:
            children.append(
                DocumentSymbol(
                    name=inst.instance_name,
                    kind=_SK_OBJECT,  # type: ignore[arg-type]
                    range=loc_to_lsp_range(inst.loc),  # type: ignore[arg-type]
                    selection_range=loc_to_lsp_range(inst.loc),  # type: ignore[arg-type]
                    detail=inst.module_name,
                )
            )

    for ab in mod.always_blocks or []:
        if ab.loc:
            label = f"always @({_sensitivity_str(ab)})"
            children.append(
                DocumentSymbol(
                    name=label,
                    kind=_SK_EVENT,  # type: ignore[arg-type]
                    range=loc_to_lsp_range(ab.loc),  # type: ignore[arg-type]
                    selection_range=loc_to_lsp_range(ab.loc),  # type: ignore[arg-type]
                )
            )

    return DocumentSymbol(
        name=mod.name,
        kind=_SK_MODULE,  # type: ignore[arg-type]
        range=loc_to_lsp_range(mod.loc),  # type: ignore[arg-type]
        selection_range=loc_to_lsp_range(mod.loc),  # type: ignore[arg-type]
        children=children,
    )


def _width_str(node: Any) -> str:
    rng = getattr(node, "width", None)
    if rng is None:
        return ""
    msb = getattr(rng, "msb", None)
    lsb = getattr(rng, "lsb", None)
    if msb is None or lsb is None:
        return ""
    return f"[{msb}:{lsb}]"


def _sensitivity_str(ab: Any) -> str:
    sens = getattr(ab, "sensitivity_list", None)
    if sens is None:
        return "*"
    return str(sens)[:40]


def _same_path(a: str, b: str) -> bool:
    import os

    return os.path.normpath(a) == os.path.normpath(b)
