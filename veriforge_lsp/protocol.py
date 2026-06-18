"""LSP <-> veriforge type conversion helpers."""

from __future__ import annotations

import os
from urllib.parse import unquote, urlparse
from urllib.request import pathname2url

from veriforge.model.base import SourceLocation


def uri_to_path(uri: str) -> str:
    """Convert a file:// URI to an OS path."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return uri
    path = unquote(parsed.path)
    if os.name == "nt" and path.startswith("/"):
        path = path[1:]  # strip leading / on Windows: /C:/foo → C:/foo
    return os.path.normpath(path)


def path_to_uri(path: str) -> str:
    """Convert an OS path to a file:// URI."""
    return "file:///" + pathname2url(os.path.abspath(path)).lstrip("/")


# ---------------------------------------------------------------------------
# SourceLocation ↔ LSP Position/Range
# Lark reports 1-based lines and 1-based columns; LSP uses 0-based for both.
# ---------------------------------------------------------------------------


def loc_to_lsp_range(loc: SourceLocation) -> dict:
    """Convert a SourceLocation to an LSP Range dict."""
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


def loc_to_lsp_position(loc: SourceLocation) -> dict:
    return {"line": max(0, (loc.line or 1) - 1), "character": max(0, (loc.column or 1) - 1)}


def lsp_pos_to_offset(line: int, character: int) -> tuple[int, int]:
    """Convert 0-based LSP position to 1-based Lark-style position."""
    return line + 1, character + 1


def make_location(loc: SourceLocation) -> dict:
    """Build an LSP Location object from a SourceLocation."""
    return {
        "uri": path_to_uri(loc.file) if loc.file else "",
        "range": loc_to_lsp_range(loc),
    }
