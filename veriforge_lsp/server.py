"""
veriforge_lsp.server — main LSP server entry point.

Usage:
    python -m veriforge_lsp           # stdio (for peovim / any LSP client)
    python -m veriforge_lsp --tcp     # TCP mode for debugging
"""

from __future__ import annotations

import argparse
import logging

from lsprotocol.types import (
    INITIALIZE,
    INITIALIZED,
    SHUTDOWN,
    Diagnostic,
    DiagnosticSeverity,
    InitializeParams,
    InitializeResult,
    Position,
    PublishDiagnosticsParams,
    Range,
    ServerCapabilities,
    WorkDoneProgressBegin,
    WorkDoneProgressEnd,
    WorkDoneProgressReport,
)
from pygls.lsp.server import LanguageServer

from veriforge_lsp.handlers import extended, navigation, symbols, text_sync
from veriforge_lsp.protocol import uri_to_path
from veriforge_lsp.workspace import Workspace

log = logging.getLogger(__name__)

NAME = "veriforge-lsp"
VERSION = "0.1.0"

_CAPABILITIES = ServerCapabilities(
    text_document_sync={  # type: ignore[arg-type]
        "openClose": True,
        "change": 1,  # FULL
        "save": {"includeText": False},
    },
    hover_provider=True,
    definition_provider=True,
    references_provider=True,
    document_symbol_provider=True,
    workspace_symbol_provider=True,
    code_action_provider=True,
    completion_provider={"triggerCharacters": [".", "$"], "resolveProvider": False},  # type: ignore[arg-type]
    execute_command_provider={
        "commands": [  # type: ignore[arg-type]
            "verilog.reparse",
            "verilog/setTopModule",
            "verilog/hierarchyGraph",
            "verilog/resolveHierarchyChildren",
            "verilog/traceSignal",
            "verilog/previewCollapseHierarchy",
            "verilog/applyCollapseHierarchy",
            "verilog/previewExtractModule",
            "verilog/applyExtractModule",
            "verilog/previewHierarchyPullUp",
            "verilog/previewHierarchyPushDown",
            "verilog/previewHierarchyBoundaryMove",
            "verilog/applyHierarchyBoundaryMove",
        ]
    },
)


class VerilogLanguageServer(LanguageServer):
    """LanguageServer subclass that carries a Workspace instance."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.workspace_manager: Workspace | None = None
        self.verible_rules: list[str] = []  # set by main() from --verible-rules arg

    def init_workspace(self, root_uri: str, verible_rules: list[str] | None = None) -> None:
        root_path = uri_to_path(root_uri) if root_uri else "."

        def _progress_cb(token: str, kind: str, value: dict) -> None:
            try:
                # Skip self.progress.create(token) — it sends window/workDoneProgress/create
                # as a blocking server→client REQUEST. Clients that don't implement the
                # server-initiated progress flow (e.g. peovim) never respond, hanging the
                # parse thread forever. begin/report/end are pure notifications, so they
                # work without the create handshake.
                if kind == "begin":
                    self.progress.begin(token, WorkDoneProgressBegin(title=value.get("title", "")))
                elif kind == "report":
                    self.progress.report(token, WorkDoneProgressReport(message=value.get("message", "")))
                elif kind == "end":
                    self.progress.end(token, WorkDoneProgressEnd(message=value.get("message", "")))
            except Exception as e:
                log.debug("progress error: %s", e)

        def _diag_cb(uri: str, diagnostics: list) -> None:
            lsp_diags: list[Diagnostic] = []
            for d in diagnostics:
                rng = d.get("range", {})
                start = rng.get("start", {})
                end = rng.get("end", {})
                lsp_diags.append(
                    Diagnostic(
                        range=Range(
                            start=Position(line=start.get("line", 0), character=start.get("character", 0)),
                            end=Position(line=end.get("line", 0), character=end.get("character", 0)),
                        ),
                        severity=DiagnosticSeverity(d.get("severity", 1)),
                        source=d.get("source", "veriforge-lsp"),
                        code=d.get("code"),
                        message=d.get("message", ""),
                    )
                )
            self.text_document_publish_diagnostics(PublishDiagnosticsParams(uri=uri, diagnostics=lsp_diags))

        def _on_parse_complete() -> None:
            extended.push_hierarchy_tree(self)

        self.workspace_manager = Workspace(
            root_path,
            progress_cb=_progress_cb,
            on_parse_complete=_on_parse_complete,
            verible_rules=verible_rules,
        )
        self.workspace_manager.register_diag_callback(_diag_cb)


ls = VerilogLanguageServer(NAME, VERSION)


@ls.feature(INITIALIZE)
def on_initialize(params: InitializeParams) -> InitializeResult:
    root_uri = params.root_uri or ""
    ls.init_workspace(root_uri, verible_rules=ls.verible_rules)
    return InitializeResult(capabilities=_CAPABILITIES)


@ls.feature(INITIALIZED)
def on_initialized(params) -> None:  # type: ignore[no-untyped-def]
    if ls.workspace_manager is not None:
        ls.workspace_manager.parse_workspace_async()


@ls.feature(SHUTDOWN)
def on_shutdown(params) -> None:  # type: ignore[no-untyped-def]
    if ls.workspace_manager is not None:
        ls.workspace_manager.shutdown()


text_sync.register(ls)
navigation.register(ls)
symbols.register(ls)
extended.register(ls)


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Verilog LSP server")
    parser.add_argument("--tcp", action="store_true", help="Use TCP instead of stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2087)
    parser.add_argument("--log-level", default="warning")
    parser.add_argument(
        "--verible-rules",
        default="",
        help="Comma-separated Verible rule overrides, e.g. -line-length,-no-tabs",
    )
    args = parser.parse_args()

    level = getattr(logging, args.log_level.upper(), logging.WARNING)
    logging.getLogger("veriforge_lsp").setLevel(level)

    if args.verible_rules:
        ls.verible_rules = [r.strip() for r in args.verible_rules.split(",") if r.strip()]

    if args.tcp:
        ls.start_tcp(args.host, args.port)
    else:
        ls.start_io()
