"""SystemVerilog package and import model classes.

Represents ``package`` declarations and ``import`` statements,
which provide namespace-based code organization.

Example Verilog::

    package my_pkg;
        localparam WIDTH = 8;
        typedef enum logic [1:0] {IDLE, RUN, DONE} state_t;
    endpackage

    module top;
        import my_pkg::*;
        import my_pkg::WIDTH;
    endmodule
"""

from __future__ import annotations

from .base import SourceLocation, VerilogNode
from .functions import FunctionDecl, TaskDecl
from .parameters import Parameter
from .sv_types import TypedefDecl


class ImportDecl(VerilogNode):
    """A single import item within an import statement.

    Example: ``import my_pkg::WIDTH;`` or ``import my_pkg::*;``
    """

    __slots__ = ("item_name", "package_name")

    def __init__(
        self,
        package_name: str,
        item_name: str,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.package_name = package_name
        self.item_name = item_name  # "*" for wildcard

    @property
    def is_wildcard(self) -> bool:
        """True if this is a wildcard import (``import pkg::*;``)."""
        return self.item_name == "*"

    def __repr__(self) -> str:
        return f"ImportDecl({self.package_name}::{self.item_name})"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["package_name"] = self.package_name
        d["item_name"] = self.item_name
        return d


class Package(VerilogNode):
    """A SystemVerilog package declaration.

    Contains parameters, typedefs, functions, and tasks that can be
    imported into modules and interfaces.
    """

    __slots__ = (
        "functions",
        "imports",
        "name",
        "parameters",
        "tasks",
        "typedefs",
    )

    def __init__(  # noqa: PLR0913
        self,
        name: str,
        *,
        parameters: list[Parameter] | None = None,
        typedefs: list[TypedefDecl] | None = None,
        functions: list[FunctionDecl] | None = None,
        tasks: list[TaskDecl] | None = None,
        imports: list[ImportDecl] | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.parameters: list[Parameter] = parameters or []
        self.typedefs: list[TypedefDecl] = typedefs or []
        self.functions: list[FunctionDecl] = functions or []
        self.tasks: list[TaskDecl] = tasks or []
        self.imports: list[ImportDecl] = imports or []

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = []
        nodes.extend(self.parameters)
        nodes.extend(self.typedefs)
        nodes.extend(self.functions)
        nodes.extend(self.tasks)
        nodes.extend(self.imports)
        return nodes

    def __repr__(self) -> str:
        parts = [f"Package({self.name!r}"]
        if self.parameters:
            parts.append(f"params={len(self.parameters)}")
        if self.typedefs:
            parts.append(f"typedefs={len(self.typedefs)}")
        if self.functions:
            parts.append(f"functions={len(self.functions)}")
        return ", ".join(parts) + ")"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        if self.parameters:
            d["parameters"] = [p.to_dict() for p in self.parameters]
        if self.typedefs:
            d["typedefs"] = [t.to_dict() for t in self.typedefs]
        if self.functions:
            d["functions"] = [f.to_dict() for f in self.functions]
        if self.tasks:
            d["tasks"] = [t.to_dict() for t in self.tasks]
        if self.imports:
            d["imports"] = [i.to_dict() for i in self.imports]
        return d
