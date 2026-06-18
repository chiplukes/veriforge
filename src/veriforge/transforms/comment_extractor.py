"""Extract comments from Verilog source before parsing.

Lark's Earley parser with the dynamic lexer discards comments via %ignore
before the parse tree is built. Since the dynamic lexer does not support
lexer_callbacks, we extract comments with a regex pre-pass, replace them
with whitespace so parser line/column numbers stay correct, then attach
captured comments to the nearest model nodes by position proximity.

Usage:
    source = Path("design.v").read_text()
    cleaned, comments = extract_comments(source, source_file="design.v")
    tree = parser.build_tree(cleaned)
    design = tree_to_design(tree, source_file="design.v")
    attach_comments(design, comments)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..model.base import Comment, SourceLocation

if TYPE_CHECKING:
    from ..model.base import VerilogNode

# Matches // line comments and /* block */ comments.
# The block comment pattern handles nested newlines correctly.
_COMMENT_RE = re.compile(r"//[^\n]*|/\*[\s\S]*?\*/")


def extract_comments(source: str, source_file: str | None = None) -> tuple[str, list[Comment]]:
    """Extract all comments from Verilog source text.

    Returns a tuple of (cleaned_source, comments) where cleaned_source has
    comments replaced with same-length whitespace (preserving newlines) and
    comments is a list of Comment objects in source order.

    Args:
        source: The original Verilog source text.
        source_file: Optional file path for SourceLocation tracking.

    Returns:
        Tuple of (cleaned source text, list of Comment objects).
    """
    comments: list[Comment] = []

    def _replace_and_capture(match: re.Match) -> str:
        raw = match.group()
        start = match.start()

        # Calculate line and column (1-based)
        prefix = source[:start]
        line = prefix.count("\n") + 1
        last_nl = prefix.rfind("\n")
        column = start - last_nl if last_nl >= 0 else start + 1

        # Determine kind and strip comment markers
        if raw.startswith("//"):
            kind = "line"
            text = raw[2:].strip()
        else:
            kind = "block"
            text = raw[2:-2].strip()

        # Calculate end position
        end_prefix = source[: match.end()]
        end_line = end_prefix.count("\n") + 1
        last_nl_end = end_prefix.rfind("\n")
        end_column = match.end() - last_nl_end if last_nl_end >= 0 else match.end() + 1

        loc = SourceLocation(
            file=source_file,
            line=line,
            column=column,
            end_line=end_line,
            end_column=end_column,
        )
        comments.append(Comment(text=text, loc=loc, kind=kind))

        # Replace with whitespace, preserving newlines for line number stability
        return re.sub(r"[^\n]", " ", raw)

    cleaned = _COMMENT_RE.sub(_replace_and_capture, source)
    return cleaned, comments


def attach_comments(root: VerilogNode, comments: list[Comment]) -> None:
    """Attach extracted comments to the nearest model nodes by position.

    For each comment, finds the closest VerilogNode by line number and
    attaches it as leading (comment before node), trailing (comment on
    same line after node start), or detached (assigned to nearest node).

    Comments that appear before the first node or after the last node
    are attached to the nearest node.

    Args:
        root: The root model node (typically a Design).
        comments: List of Comment objects from extract_comments().
    """
    if not comments:
        return

    # Collect all nodes with valid source locations, sorted by line
    nodes = _collect_located_nodes(root)
    if not nodes:
        # No nodes with location info — attach all comments to root
        root.comments.extend(comments)
        return

    for comment in comments:
        _attach_single_comment(comment, nodes)


def _collect_located_nodes(root: VerilogNode) -> list[VerilogNode]:
    """Collect all nodes with valid line numbers, sorted by line.

    At the same position, deeper (more specific) nodes come first so that
    comments attach to e.g. Module rather than Design.
    """
    nodes: list[tuple[int, int, int, VerilogNode]] = []
    _collect_with_depth(root, 0, nodes)
    # Sort by (line, column, -depth) so deeper nodes win at same position
    nodes.sort(key=lambda t: (t[0], t[1], -t[2]))
    return [n for _, _, _, n in nodes]


def _collect_with_depth(node: VerilogNode, depth: int, out: list[tuple[int, int, int, VerilogNode]]) -> None:
    """Recursively collect nodes with their depth."""
    if node.loc and node.loc.line > 0:
        out.append((node.loc.line, node.loc.column, depth, node))
    for child in node._child_nodes():
        if hasattr(child, "_child_nodes"):
            _collect_with_depth(child, depth + 1, out)


def _attach_single_comment(comment: Comment, nodes: list[VerilogNode]) -> None:
    """Attach a single comment to the best matching node.

    Position assignment rules:
    - "trailing": comment is on the same line as a node's start (after it)
    - "leading": comment is on a line before the next node
    - Falls back to closest by line distance
    """
    comment_line = comment.loc.line

    # Check for trailing: same line as a node start, comment column > node column
    for node in nodes:
        if node.loc.line == comment_line and comment.loc.column > node.loc.column:
            comment.position = "trailing"
            node.comments.append(comment)
            return

    # Check for leading: find the first node on a line >= comment_line
    for node in nodes:
        if node.loc.line >= comment_line:
            comment.position = "leading"
            node.comments.append(comment)
            return

    # Comment is after all nodes — attach to last node
    comment.position = "trailing"
    nodes[-1].comments.append(comment)
