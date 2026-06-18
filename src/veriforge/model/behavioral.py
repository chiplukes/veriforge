"""Behavioral model classes for always/initial blocks.

These wrap a Statement body and carry sensitivity analysis metadata.
"""

from __future__ import annotations

from enum import Enum, auto

from .base import SourceLocation, VerilogNode
from .statements import SensitivityEdge, Statement


class SensitivityType(Enum):
    """Classification of an always block's sensitivity."""

    COMBINATIONAL = auto()  # @(*) or @(a or b) with all level-sensitive
    SEQUENTIAL = auto()  # @(posedge clk) — clock-edge-triggered
    LATCH = auto()  # inferred latch (incomplete if in combinational)
    UNKNOWN = auto()  # cannot determine


class AlwaysBlock(VerilogNode):  # cm:2f7d5e
    """An always block: always @(...) statement"""

    __slots__ = ("body", "sensitivity_list", "sensitivity_type")

    def __init__(
        self,
        body: Statement,
        *,
        sensitivity_list: list[SensitivityEdge] | None = None,
        sensitivity_type: SensitivityType = SensitivityType.UNKNOWN,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.body = body
        self.sensitivity_list = sensitivity_list or []
        self.sensitivity_type = sensitivity_type

    def __repr__(self) -> str:
        stype = self.sensitivity_type.name.lower()
        return f"AlwaysBlock({stype}, {len(self.sensitivity_list)} edges)"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = list(self.sensitivity_list)
        nodes.append(self.body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "AlwaysBlock"
        d["sensitivity_type"] = self.sensitivity_type.name.lower()
        if self.sensitivity_list:
            d["sensitivity_list"] = [e.to_dict() for e in self.sensitivity_list]
        d["body"] = self.body.to_dict()
        return d


class InitialBlock(VerilogNode):  # cm:8c3a1f
    """An initial block: initial statement"""

    __slots__ = ("body",)

    def __init__(self, body: Statement, *, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.body = body

    def __repr__(self) -> str:
        return f"InitialBlock({self.body!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.body]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "InitialBlock"
        d["body"] = self.body.to_dict()
        return d
