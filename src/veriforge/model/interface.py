"""SystemVerilog interface and modport model classes.

Represents ``interface`` declarations with ``modport`` definitions,
parameters, signals, typedefs, and continuous assigns.

Example Verilog::

    interface axi_lite #(parameter ADDR_W = 32, DATA_W = 32) ();
        logic [ADDR_W-1:0] awaddr;
        logic               awvalid;
        logic               awready;

        modport master(output awaddr, output awvalid, input awready);
        modport slave(input awaddr, input awvalid, output awready);
    endinterface
"""

from __future__ import annotations

from .base import SourceLocation, VerilogNode
from .assignments import ContinuousAssign
from .nets import Net
from .parameters import Parameter
from .package import ImportDecl
from .ports import PortDirection
from .sv_types import TypedefDecl
from .variables import Variable


class ModportPort(VerilogNode):
    """A single port entry within a modport declaration.

    Example: ``input awaddr`` or ``output awvalid``
    """

    __slots__ = ("direction", "name")

    def __init__(
        self,
        name: str,
        direction: PortDirection,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.direction = direction

    def __repr__(self) -> str:
        return f"ModportPort({self.direction.value} {self.name})"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        d["direction"] = self.direction.value
        return d


class Modport(VerilogNode):
    """A modport declaration within an interface.

    Example: ``modport master(output data, input ready);``
    """

    __slots__ = ("name", "ports")

    def __init__(
        self,
        name: str,
        ports: list[ModportPort] | None = None,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.ports: list[ModportPort] = ports or []

    def _child_nodes(self) -> list[VerilogNode]:
        return list(self.ports)

    def __repr__(self) -> str:
        return f"Modport({self.name!r}, ports={len(self.ports)})"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        d["ports"] = [p.to_dict() for p in self.ports]
        return d


class Interface(VerilogNode):
    """A SystemVerilog interface declaration.

    Structurally similar to a Module, but used to bundle signals
    and provide directional views via modports.
    """

    __slots__ = (
        "continuous_assigns",
        "imports",
        "modports",
        "name",
        "nets",
        "parameters",
        "typedefs",
        "variables",
    )

    def __init__(  # noqa: PLR0913
        self,
        name: str,
        *,
        parameters: list[Parameter] | None = None,
        nets: list[Net] | None = None,
        variables: list[Variable] | None = None,
        continuous_assigns: list[ContinuousAssign] | None = None,
        modports: list[Modport] | None = None,
        typedefs: list[TypedefDecl] | None = None,
        imports: list[ImportDecl] | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.parameters: list[Parameter] = parameters or []
        self.nets: list[Net] = nets or []
        self.variables: list[Variable] = variables or []
        self.continuous_assigns: list[ContinuousAssign] = continuous_assigns or []
        self.modports: list[Modport] = modports or []
        self.typedefs: list[TypedefDecl] = typedefs or []
        self.imports: list[ImportDecl] = imports or []

    def get_modport(self, name: str) -> Modport | None:
        """Look up a modport by name."""
        for mp in self.modports:
            if mp.name == name:
                return mp
        return None

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = []
        nodes.extend(self.parameters)
        nodes.extend(self.nets)
        nodes.extend(self.variables)
        nodes.extend(self.continuous_assigns)
        nodes.extend(self.modports)
        nodes.extend(self.typedefs)
        nodes.extend(self.imports)
        return nodes

    def __repr__(self) -> str:
        parts = [f"Interface({self.name!r}"]
        if self.modports:
            parts.append(f"modports={len(self.modports)}")
        parts.append(f"nets={len(self.nets)}")
        parts.append(f"vars={len(self.variables)}")
        return ", ".join(parts) + ")"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        if self.parameters:
            d["parameters"] = [p.to_dict() for p in self.parameters]
        if self.nets:
            d["nets"] = [n.to_dict() for n in self.nets]
        if self.variables:
            d["variables"] = [v.to_dict() for v in self.variables]
        if self.continuous_assigns:
            d["continuous_assigns"] = [a.to_dict() for a in self.continuous_assigns]
        if self.modports:
            d["modports"] = [m.to_dict() for m in self.modports]
        if self.typedefs:
            d["typedefs"] = [t.to_dict() for t in self.typedefs]
        if self.imports:
            d["imports"] = [i.to_dict() for i in self.imports]
        return d
