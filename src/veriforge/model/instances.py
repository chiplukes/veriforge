"""Instance model class for the Verilog semantic model.

Instances represent module instantiations:
    counter #(.WIDTH(8)) u1 (.clk(clk), .rst(rst), .count(cnt));
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import SourceLocation, VerilogNode
from .expressions import Expression, Range

if TYPE_CHECKING:
    from .design import Module
    from .ports import Port


class PortConnection(VerilogNode):
    """A port connection on a module instance.

    Named:      .clk(sys_clk)     port_name="clk", expression=Identifier("sys_clk"), is_named=True
    Positional: sys_clk           port_name=None, expression=Identifier("sys_clk"), is_named=False
    Unconnected: .data()          port_name="data", expression=None, is_named=True
    """

    __slots__ = ("expression", "is_named", "port_name", "resolved_port")

    def __init__(
        self,
        *,
        port_name: str | None = None,
        expression: Expression | None = None,
        is_named: bool = True,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.port_name = port_name
        self.expression = expression
        self.is_named = is_named
        self.resolved_port: Port | None = None  # Populated by Layer 3 analysis

    def __repr__(self) -> str:
        if self.is_named:
            expr_str = "unconnected" if self.expression is None else repr(self.expression)
            return f"PortConnection(.{self.port_name}({expr_str}))"
        return f"PortConnection({self.expression!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        if self.expression is not None:
            return [self.expression]
        return []

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.port_name is not None:
            d["port_name"] = self.port_name
        if self.expression is not None:
            d["expression"] = self.expression.to_dict()
        d["is_named"] = self.is_named
        return d


class ParameterBinding(VerilogNode):
    """A parameter override on an instance.

    Named:      .WIDTH(8)    name="WIDTH", value=Literal(8)
    Positional: 8            name=None, value=Literal(8)
    """

    __slots__ = ("name", "value")

    def __init__(
        self,
        *,
        name: str | None = None,
        value: Expression | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.value = value

    def __repr__(self) -> str:
        if self.name:
            return f"ParameterBinding(.{self.name}({self.value!r}))"
        return f"ParameterBinding({self.value!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        if self.value is not None:
            return [self.value]
        return []

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.name is not None:
            d["name"] = self.name
        if self.value is not None:
            d["value"] = self.value.to_dict()
        return d


class Instance(VerilogNode):
    """A module instantiation.

    counter #(.WIDTH(8)) u1 (.clk(clk), .rst(rst), .count(cnt));
    """

    __slots__ = (
        "attributes",
        "has_parameter_override",
        "instance_array",
        "instance_name",
        "module_name",
        "parameter_bindings",
        "port_connections",
        "resolved_module",
    )

    def __init__(  # noqa: PLR0913
        self,
        module_name: str,
        instance_name: str,
        *,
        instance_array: Range | None = None,
        has_parameter_override: bool = False,
        parameter_bindings: list[ParameterBinding] | None = None,
        port_connections: list[PortConnection] | None = None,
        attributes: dict[str, str] | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.module_name = module_name
        self.instance_name = instance_name
        self.instance_array = instance_array
        self.has_parameter_override = has_parameter_override
        self.parameter_bindings = parameter_bindings or []
        self.port_connections = port_connections or []
        self.attributes = attributes or {}  # type: ignore[assignment]
        self.resolved_module: Module | None = None  # Populated by Layer 3 analysis

    def __repr__(self) -> str:
        return (
            f"Instance({self.module_name} {self.instance_name}, "
            f"params={len(self.parameter_bindings)}, ports={len(self.port_connections)})"
        )

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = []
        nodes.extend(self.parameter_bindings)
        nodes.extend(self.port_connections)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["module_name"] = self.module_name
        d["instance_name"] = self.instance_name
        if self.instance_array:
            d["instance_array"] = self.instance_array.to_dict()
        if self.has_parameter_override:
            d["has_parameter_override"] = True
        if self.parameter_bindings:
            d["parameter_bindings"] = [b.to_dict() for b in self.parameter_bindings]
        if self.port_connections:
            d["port_connections"] = [c.to_dict() for c in self.port_connections]
        if self.attributes:
            d["attributes"] = dict(self.attributes)
        return d
