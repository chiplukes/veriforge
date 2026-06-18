"""Specify block model class (opaque representation).

Specify blocks contain timing constraints (path delays, timing checks,
specparams) that are non-synthesizable. Rather than fully modeling the
~50 specify grammar rules, the block is stored as an opaque Lark parse
tree and reconstructed verbatim during emission.

Grammar reference (IEEE 1364-2005, A.7):
    specify_block ::= specify { specify_item } endspecify
    specify_item ::= specparam_declaration
                   | pulsestyle_declaration
                   | showcancelled_declaration
                   | path_declaration
                   | system_timing_check
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import SourceLocation, VerilogNode

if TYPE_CHECKING:
    from lark import Tree


class SpecifyBlock(VerilogNode):
    """Opaque representation of a Verilog specify block.

    The raw Lark parse tree is preserved so the block can be
    reconstructed faithfully during code emission. No semantic
    analysis is performed on the specify contents.

    When ``source_text`` is provided (extracted from the original
    Verilog source using tree position metadata), it is used for
    faithful round-trip emission. Otherwise a best-effort
    reconstruction from the tree tokens is performed.
    """

    __slots__ = ("raw_tree", "source_text")

    def __init__(
        self,
        raw_tree: Tree,
        *,
        source_text: str | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.raw_tree = raw_tree
        self.source_text = source_text

    def __repr__(self) -> str:
        return "SpecifyBlock()"

    def _child_nodes(self) -> list[VerilogNode]:
        return []

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "SpecifyBlock"
        # No detailed structure — just note its presence
        return d
