"""Port model classes for the Verilog semantic model."""

from __future__ import annotations

from enum import Enum

from .base import SourceLocation, VerilogNode
from .expressions import Expression, Range


class PortDirection(Enum):  # cm:4e2c6f
    """Port direction."""

    INPUT = "input"
    OUTPUT = "output"
    INOUT = "inout"


class Port(VerilogNode):  # cm:9f1b3d
    """A module port declaration."""

    __slots__ = (
        "data_type",
        "default_value",
        "dimensions",
        "direction",
        "drivers",
        "loads",
        "name",
        "net_type",
        "signed",
        "width",
    )

    def __init__(
        self,
        name: str,
        direction: PortDirection,
        *,
        net_type: str | None = None,
        data_type: str | None = None,
        width: Range | None = None,
        dimensions: list[Range] | None = None,
        signed: bool = False,
        default_value: Expression | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.direction = direction
        self.net_type = net_type  # "wire", "tri", etc.
        self.data_type = data_type  # "reg", "integer", etc.
        self.width = width
        self.dimensions = list(dimensions) if dimensions else []
        self.signed = signed
        self.default_value = default_value
        # Connectivity — populated by Layer 3 analysis (same as Net/Variable)
        self.drivers: list = []
        self.loads: list = []

    def __repr__(self) -> str:
        parts = [self.direction.value]
        if self.data_type:
            parts.append(self.data_type)
        if self.width:
            parts.append(f"[{self.width}]")
        parts.append(self.name)
        for dim in self.dimensions:
            parts.append(f"[{dim}]")
        return f"Port({' '.join(parts)})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = []
        if self.width:
            nodes.append(self.width.msb)
            nodes.append(self.width.lsb)
        for dim in self.dimensions:
            nodes.append(dim.msb)
            nodes.append(dim.lsb)
        if self.default_value:
            nodes.append(self.default_value)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        d["direction"] = self.direction.value
        if self.net_type:
            d["net_type"] = self.net_type
        if self.data_type:
            d["data_type"] = self.data_type
        if self.width:
            d["width"] = self.width.to_dict()
        if self.dimensions:
            d["dimensions"] = [dim.to_dict() for dim in self.dimensions]
        if self.signed:
            d["signed"] = True
        if self.default_value:
            d["default_value"] = self.default_value.to_dict()
        return d
