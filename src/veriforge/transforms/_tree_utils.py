"""Shared parse-tree utilities for model transforms."""

from __future__ import annotations

from lark import Token, Tree

from ..model.base import SourceLocation

_SOURCE_TEXT_CACHE: dict[str, str] = {}


def _loc_from_tree(tree: Tree, source_file: str | None = None) -> SourceLocation:
    """Extract SourceLocation from a Lark tree node's meta."""
    meta = getattr(tree, "meta", None)
    if meta and hasattr(meta, "line"):
        return SourceLocation.from_meta(meta, file=source_file)
    return SourceLocation(file=source_file)


def _collect_text(tree: Tree) -> str:
    """Recursively collect all token text from a tree, preserving order."""
    parts: list[str] = []
    for child in tree.children:
        if isinstance(child, Token):
            parts.append(str(child))
        elif isinstance(child, Tree):
            parts.append(_collect_text(child))
    return "".join(parts)


def _collect_real_number_text(tree: Tree) -> str:
    """Reconstruct text for a real_number parse tree node.

    The grammar ``exp: "e" | "E"`` uses anonymous terminals, so ``_collect_text``
    drops the ``e``/``E`` character. This function inserts it back.
    """
    parts: list[str] = []
    for child in tree.children:
        if isinstance(child, Token):
            parts.append(str(child))
        elif isinstance(child, Tree):
            if child.data == "exp":
                parts.append("e")
            elif child.data == "sign":
                parts.append(_collect_text(child))
            else:
                parts.append(_collect_text(child))
    return "".join(parts)
