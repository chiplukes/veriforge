"""textDocument/did{Open,Change,Save,Close} handlers."""

from __future__ import annotations

import logging

from pygls.lsp.server import LanguageServer

log = logging.getLogger(__name__)


def register(ls: LanguageServer) -> None:
    from lsprotocol.types import (
        TEXT_DOCUMENT_DID_CHANGE,
        TEXT_DOCUMENT_DID_CLOSE,
        TEXT_DOCUMENT_DID_OPEN,
        TEXT_DOCUMENT_DID_SAVE,
        DidChangeTextDocumentParams,
        DidCloseTextDocumentParams,
        DidOpenTextDocumentParams,
        DidSaveTextDocumentParams,
    )

    @ls.feature(TEXT_DOCUMENT_DID_OPEN)
    def did_open(params: DidOpenTextDocumentParams) -> None:
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return
        uri = params.text_document.uri
        text = params.text_document.text
        ws.on_did_open(uri, text)
        # Run Verible on first open so the user gets immediate diagnostics
        ws.on_did_change(uri, text, schedule_verible=True)

    @ls.feature(TEXT_DOCUMENT_DID_CHANGE)
    def did_change(params: DidChangeTextDocumentParams) -> None:
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return
        uri = params.text_document.uri
        # We requested FULL sync so there is exactly one content change
        changes = params.content_changes
        text = changes[-1].text if changes else ""
        ws.on_did_change(uri, text, schedule_verible=True)

    @ls.feature(TEXT_DOCUMENT_DID_SAVE)
    def did_save(params: DidSaveTextDocumentParams) -> None:
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return
        ws.on_did_save(params.text_document.uri)

    @ls.feature(TEXT_DOCUMENT_DID_CLOSE)
    def did_close(params: DidCloseTextDocumentParams) -> None:
        ws = ls.workspace_manager  # type: ignore[attr-defined]
        if ws is None:
            return
        ws.on_did_close(params.text_document.uri)
