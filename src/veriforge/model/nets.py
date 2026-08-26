"""Net model classes for the Verilog semantic model."""

from __future__ import annotations

from enum import Enum

from .base import SourceLocation, VerilogNode
from .expressions import Expression, Range


class NetKind(Enum):
    """Verilog net types."""

    WIRE = "wire"
    TRI = "tri"
    WAND = "wand"
    WOR = "wor"
    TRIAND = "triand"
    TRIOR = "trior"
    TRI0 = "tri0"
    TRI1 = "tri1"
    SUPPLY0 = "supply0"
    SUPPLY1 = "supply1"
    UWIRE = "uwire"
    TRIREG = "trireg"
    # SystemVerilog
    LOGIC = "logic"


class Net(VerilogNode):
    """A net declaration (wire, tri, etc.)."""

    __slots__ = (
        "dimensions",
        "drivers",
        "initial_value",
        "kind",
        "loads",
        "name",
        "packed_dim_count",
        "signed",
        "width",
    )

    def __init__(
        self,
        name: str,
        kind: NetKind = NetKind.WIRE,
        *,
        width: Range | None = None,
        signed: bool = False,
        dimensions: list[Range] | None = None,
        packed_dim_count: int = 0,
        initial_value: Expression | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.kind = kind
        self.width = width
        self.signed = signed
        self.dimensions = dimensions or []
        # How many of `dimensions`' LEADING entries are extra PACKED
        # dims (from this signal's own multi-dim packed declaration, e.g.
        # the outer `[3:0]` in `logic [3:0][7:0] mem [3:0]`) rather than
        # genuinely UNPACKED (separately addressable) array dimensions --
        # see `sim/scheduler.py`'s `_memory_shape` for why this
        # distinction matters and can't be recovered from `dimensions`
        # alone. Default 0 (every dimension is a genuine address level)
        # matches a plain single-dimension declaration either way, and
        # preserves prior behavior for any caller that builds a `Net`
        # directly without setting this.
        self.packed_dim_count = packed_dim_count
        self.initial_value = initial_value
        # Connectivity — populated by Layer 3 analysis
        self.drivers: list = []
        self.loads: list = []

    def __repr__(self) -> str:
        parts = [self.kind.value]
        if self.width:
            parts.append(f"[{self.width}]")
        parts.append(self.name)
        return f"Net({' '.join(parts)})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = []
        if self.width:
            nodes.append(self.width.msb)
            nodes.append(self.width.lsb)
        for dim in self.dimensions:
            nodes.append(dim.msb)
            nodes.append(dim.lsb)
        if self.initial_value:
            nodes.append(self.initial_value)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        d["kind"] = self.kind.value
        if self.width:
            d["width"] = self.width.to_dict()
        if self.signed:
            d["signed"] = True
        if self.dimensions:
            d["dimensions"] = [dim.to_dict() for dim in self.dimensions]
        if self.packed_dim_count:
            d["packed_dim_count"] = self.packed_dim_count
        if self.initial_value:
            d["initial_value"] = self.initial_value.to_dict()
        return d
