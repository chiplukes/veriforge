"""Function and Task declaration model classes.

Grammar reference (IEEE 1364-2005, A.2.6-A.2.7):
    function_declaration ::=
        function [automatic] [function_range_or_type] function_identifier ;
            function_item_declaration { function_item_declaration }
            function_statement
        endfunction
        | function [automatic] [function_range_or_type] function_identifier ( function_port_list ) ;
            { block_item_declaration }
            function_statement
        endfunction

    task_declaration ::=
        task [automatic] task_identifier ;
            { task_item_declaration }
            statement_or_null
        endtask
        | task [automatic] task_identifier ( [task_port_list] ) ;
            { block_item_declaration }
            statement_or_null
        endtask
"""

from __future__ import annotations

from .base import SourceLocation, VerilogNode
from .expressions import Range
from .ports import Port
from .statements import Statement
from .variables import Variable


class FunctionDecl(VerilogNode):
    """A Verilog function declaration."""

    __slots__ = ("body", "is_automatic", "locals", "name", "ports", "return_kind", "return_range")

    def __init__(  # noqa: PLR0913
        self,
        name: str,
        *,
        return_range: Range | None = None,
        return_kind: str | None = None,
        is_automatic: bool = False,
        ports: list[Port] | None = None,
        local_vars: list[Variable] | None = None,
        body: Statement | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.return_range = return_range
        self.return_kind = return_kind
        self.is_automatic = is_automatic
        self.ports = ports or []
        self.locals = local_vars or []
        self.body: Statement | None = body

    def __repr__(self) -> str:
        parts = [f"FunctionDecl({self.name!r}"]
        if self.is_automatic:
            parts.append("automatic")
        if self.return_kind:
            parts.append(f"kind={self.return_kind!r}")
        if self.return_range:
            parts.append(f"range={self.return_range!r}")
        parts.append(f"ports={len(self.ports)}")
        return ", ".join(parts) + ")"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = list(self.ports)
        if self.body:
            nodes.append(self.body)
        nodes.extend(self.locals)
        if self.return_range:
            if self.return_range.msb:
                nodes.append(self.return_range.msb)
            if self.return_range.lsb:
                nodes.append(self.return_range.lsb)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "FunctionDecl"
        d["name"] = self.name
        d["is_automatic"] = self.is_automatic
        if self.return_kind:
            d["return_kind"] = self.return_kind
        if self.return_range:
            d["return_range"] = {
                "msb": self.return_range.msb.to_dict() if self.return_range.msb else None,
                "lsb": self.return_range.lsb.to_dict() if self.return_range.lsb else None,
            }
        if self.ports:
            d["ports"] = [p.to_dict() for p in self.ports]
        if self.locals:
            d["locals"] = [v.to_dict() for v in self.locals]
        if self.body:
            d["body"] = self.body.to_dict()
        return d


class TaskDecl(VerilogNode):
    """A Verilog task declaration."""

    __slots__ = ("body", "is_automatic", "name", "ports")

    def __init__(
        self,
        name: str,
        *,
        is_automatic: bool = False,
        ports: list[Port] | None = None,
        body: Statement | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.is_automatic = is_automatic
        self.ports = ports or []
        self.body: Statement | None = body

    def __repr__(self) -> str:
        parts = [f"TaskDecl({self.name!r}"]
        if self.is_automatic:
            parts.append("automatic")
        parts.append(f"ports={len(self.ports)}")
        return ", ".join(parts) + ")"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = list(self.ports)
        if self.body:
            nodes.append(self.body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "TaskDecl"
        d["name"] = self.name
        d["is_automatic"] = self.is_automatic
        if self.ports:
            d["ports"] = [p.to_dict() for p in self.ports]
        if self.body:
            d["body"] = self.body.to_dict()
        return d
