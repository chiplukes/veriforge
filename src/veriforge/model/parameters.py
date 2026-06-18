"""Parameter model classes for the Verilog semantic model."""

from __future__ import annotations

from .base import SourceLocation, VerilogNode
from .expressions import Expression, Range


class Parameter(VerilogNode):
    """A parameter or localparam declaration."""

    __slots__ = ("default_value", "is_local", "name", "param_type", "signed", "width")

    def __init__(
        self,
        name: str,
        *,
        param_type: str | None = None,
        width: Range | None = None,
        signed: bool = False,
        default_value: Expression | None = None,
        is_local: bool = False,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.param_type = param_type  # "integer", "real", etc.
        self.width = width
        self.signed = signed
        self.default_value = default_value
        self.is_local = is_local

    def __repr__(self) -> str:
        kw = "localparam" if self.is_local else "parameter"
        return f"Parameter({kw} {self.name})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = []
        if self.width:
            nodes.append(self.width.msb)
            nodes.append(self.width.lsb)
        if self.default_value:
            nodes.append(self.default_value)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        if self.is_local:
            d["is_local"] = True
        if self.param_type:
            d["param_type"] = self.param_type
        if self.width:
            d["width"] = self.width.to_dict()
        if self.signed:
            d["signed"] = True
        if self.default_value:
            d["default_value"] = self.default_value.to_dict()
        return d
