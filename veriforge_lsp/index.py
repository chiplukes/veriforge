"""
Location index: maps file positions to VerilogNodes and tracks all reference locations.

After each parse, build() rebuilds both maps so handlers can answer:
  - "which node is at this cursor position?"
  - "where is this node referenced?"
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from intervaltree import IntervalTree

from veriforge.model.base import SourceLocation, VerilogNode
from veriforge.model.design import Design
from veriforge.model.expressions import Identifier
from veriforge.model.instances import PortConnection

log = logging.getLogger(__name__)

# sentinel for interval endpoints — IntervalTree requires begin < end
_MIN_SPAN = 1


def _loc_to_interval(loc: SourceLocation) -> tuple[int, int] | None:
    """Convert a SourceLocation to a flat byte-ish offset pair for IntervalTree.

    We use a simple line*10000+column encoding so we can do 2D containment
    queries without a 2D tree.  Good enough for files < 10 000 columns wide.
    """
    if not loc or not loc.line:
        return None
    start = loc.line * 10_000 + (loc.column or 0)
    if loc.end_line:
        end = loc.end_line * 10_000 + (loc.end_column or 0)
    else:
        end = start + _MIN_SPAN
    if end <= start:
        end = start + _MIN_SPAN
    return start, end


class LocationIndex:
    """Dual index for position→node and node→reference-locations lookups."""

    def __init__(self) -> None:
        # file_path → IntervalTree of (start, end, VerilogNode)
        self._position_index: dict[str, IntervalTree] = {}
        # id(node) → list of SourceLocation where it is referenced
        self._reference_index: dict[int, list[SourceLocation]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self, design: Design) -> None:
        self._position_index.clear()
        self._reference_index.clear()
        for module in design.modules:
            self._index_node(module)
            for node in module.walk():
                self._index_node(node)
                # Identifiers that resolve to a definition: record the reference
                if isinstance(node, Identifier) and node.resolved is not None:
                    ref_loc = getattr(node, "loc", None)
                    if ref_loc and ref_loc.file:
                        self._reference_index[id(node.resolved)].append(ref_loc)
                # PortConnections: record connection site as reference to the port
                if isinstance(node, PortConnection) and node.resolved_port is not None:
                    ref_loc = getattr(node, "loc", None)
                    if ref_loc and ref_loc.file:
                        self._reference_index[id(node.resolved_port)].append(ref_loc)
        log.debug("LocationIndex built: %d files indexed", len(self._position_index))

    def _index_node(self, node: VerilogNode) -> None:
        loc: SourceLocation | None = getattr(node, "loc", None)
        if not loc or not loc.file:
            return
        interval = _loc_to_interval(loc)
        if interval is None:
            return
        tree = self._position_index.setdefault(loc.file, IntervalTree())
        tree.addi(interval[0], interval[1], node)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def node_at(self, file_path: str, lsp_line: int, lsp_char: int) -> VerilogNode | None:
        """Return the innermost VerilogNode covering (lsp_line, lsp_char)."""
        tree = self._position_index.get(file_path)
        if tree is None:
            return None
        # Convert 0-based LSP to 1-based Lark
        point = (lsp_line + 1) * 10_000 + (lsp_char + 1)
        hits = tree.at(point)
        if not hits:
            return None
        # Pick the smallest (most specific) interval
        best = min(hits, key=lambda iv: iv.end - iv.begin)
        return best.data  # type: ignore[return-value]

    def references_of(self, node: Any) -> list[SourceLocation]:
        """Return all source locations that reference this node."""
        return list(self._reference_index.get(id(node), []))

    def files(self) -> list[str]:
        return list(self._position_index.keys())
