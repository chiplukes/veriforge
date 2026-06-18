"""Continuous assignment model class for the Verilog semantic model.

Continuous assigns:
    assign y = a & b;
    assign {c, d} = {e, f};
"""

from __future__ import annotations

from .base import SourceLocation, VerilogNode
from .expressions import Expression


class ContinuousAssign(VerilogNode):
    """A continuous assignment statement: assign lhs = rhs;"""

    __slots__ = ("lhs", "rhs")

    def __init__(
        self,
        lhs: Expression,
        rhs: Expression,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.lhs = lhs
        self.rhs = rhs

    def __repr__(self) -> str:
        return f"ContinuousAssign({self.lhs!r} = {self.rhs!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.lhs, self.rhs]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["lhs"] = self.lhs.to_dict()
        d["rhs"] = self.rhs.to_dict()
        return d
