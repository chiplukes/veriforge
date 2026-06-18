"""Statement model classes for the Verilog semantic model.

Statements form a tree within always/initial blocks. Each statement
preserves its source location for precise code generation.

Grammar reference (IEEE 1364-2005, A.6.4):
    statement ::= blocking_assignment ;
        | case_statement
        | conditional_statement
        | disable_statement
        | event_trigger
        | loop_statement
        | nonblocking_assignment ;
        | par_block
        | procedural_continuous_assignments ;
        | procedural_timing_control_statement
        | seq_block
        | system_task_enable
        | task_enable
        | wait_statement
"""

from __future__ import annotations

from .base import SourceLocation, VerilogNode
from .expressions import Expression
from .variables import Variable


class Statement(VerilogNode):
    """Base class for all procedural statements."""

    __slots__ = ()


class BlockingAssign(Statement):
    """Blocking assignment: lhs = rhs;"""

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
        return f"BlockingAssign({self.lhs!r} = {self.rhs!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.lhs, self.rhs]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "BlockingAssign"
        d["lhs"] = self.lhs.to_dict()
        d["rhs"] = self.rhs.to_dict()
        return d


class NonblockingAssign(Statement):
    """Nonblocking assignment: lhs <= rhs;"""

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
        return f"NonblockingAssign({self.lhs!r} <= {self.rhs!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.lhs, self.rhs]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "NonblockingAssign"
        d["lhs"] = self.lhs.to_dict()
        d["rhs"] = self.rhs.to_dict()
        return d


class IfStatement(Statement):
    """if (condition) then_body [else else_body]"""

    __slots__ = ("condition", "else_body", "then_body")

    def __init__(
        self,
        condition: Expression,
        then_body: Statement | None,
        else_body: Statement | None = None,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.condition = condition
        self.then_body = then_body
        self.else_body = else_body

    def __repr__(self) -> str:
        if self.else_body:
            return f"IfStatement({self.condition!r}, then=..., else=...)"
        return f"IfStatement({self.condition!r}, then=...)"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = [self.condition]
        if self.then_body:
            nodes.append(self.then_body)
        if self.else_body:
            nodes.append(self.else_body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "IfStatement"
        d["condition"] = self.condition.to_dict()
        if self.then_body:
            d["then_body"] = self.then_body.to_dict()
        if self.else_body:
            d["else_body"] = self.else_body.to_dict()
        return d


class CaseItem(VerilogNode):
    """A single case item: expression {, expression} : statement_or_null  |  default : ..."""

    __slots__ = ("body", "is_default", "values")

    def __init__(
        self,
        values: list[Expression] | None,
        body: Statement | None,
        *,
        is_default: bool = False,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.values = values or []
        self.body = body
        self.is_default = is_default

    def __repr__(self) -> str:
        if self.is_default:
            return "CaseItem(default)"
        return f"CaseItem({self.values!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = list(self.values)
        if self.body:
            nodes.append(self.body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "CaseItem"
        if self.is_default:
            d["is_default"] = True
        else:
            d["values"] = [v.to_dict() for v in self.values]
        if self.body:
            d["body"] = self.body.to_dict()
        return d


class CaseStatement(Statement):
    """case/casex/casez (expression) case_item ... endcase"""

    __slots__ = ("case_type", "expression", "items")

    def __init__(
        self,
        case_type: str,
        expression: Expression,
        items: list[CaseItem],
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.case_type = case_type  # "case", "casex", "casez"
        self.expression = expression
        self.items = items

    def __repr__(self) -> str:
        return f"CaseStatement({self.case_type}, {len(self.items)} items)"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = [self.expression]
        nodes.extend(self.items)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "CaseStatement"
        d["case_type"] = self.case_type
        d["expression"] = self.expression.to_dict()
        d["items"] = [i.to_dict() for i in self.items]
        return d


class ForLoop(Statement):
    """for (init; condition; update) body"""

    __slots__ = ("body", "condition", "declares_var", "init", "signed_var", "update")

    def __init__(
        self,
        init: BlockingAssign,
        condition: Expression,
        update: BlockingAssign,
        body: Statement | None,
        *,
        declares_var: bool = False,
        signed_var: bool = False,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.init = init
        self.condition = condition
        self.update = update
        self.body = body
        self.declares_var = declares_var
        self.signed_var = signed_var

    def __repr__(self) -> str:
        return f"ForLoop({self.init!r}; {self.condition!r}; {self.update!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = [self.init, self.condition, self.update]
        if self.body:
            nodes.append(self.body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "ForLoop"
        d["init"] = self.init.to_dict()
        d["condition"] = self.condition.to_dict()
        d["update"] = self.update.to_dict()
        if self.declares_var:
            d["declares_var"] = True
        if self.body:
            d["body"] = self.body.to_dict()
        return d


class WhileLoop(Statement):
    """while (condition) body"""

    __slots__ = ("body", "condition")

    def __init__(
        self,
        condition: Expression,
        body: Statement | None,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.condition = condition
        self.body = body

    def __repr__(self) -> str:
        return f"WhileLoop({self.condition!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = [self.condition]
        if self.body:
            nodes.append(self.body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "WhileLoop"
        d["condition"] = self.condition.to_dict()
        if self.body:
            d["body"] = self.body.to_dict()
        return d


class ForeverLoop(Statement):
    """forever body"""

    __slots__ = ("body",)

    def __init__(self, body: Statement | None, *, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.body = body

    def __repr__(self) -> str:
        return "ForeverLoop(...)"

    def _child_nodes(self) -> list[VerilogNode]:
        if self.body:
            return [self.body]
        return []

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "ForeverLoop"
        if self.body:
            d["body"] = self.body.to_dict()
        return d


class RepeatLoop(Statement):
    """repeat (count) body"""

    __slots__ = ("body", "count")

    def __init__(
        self,
        count: Expression,
        body: Statement | None,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.count = count
        self.body = body

    def __repr__(self) -> str:
        return f"RepeatLoop({self.count!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = [self.count]
        if self.body:
            nodes.append(self.body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "RepeatLoop"
        d["count"] = self.count.to_dict()
        if self.body:
            d["body"] = self.body.to_dict()
        return d


class SeqBlock(Statement):
    """begin ... end"""

    __slots__ = ("local_vars", "name", "statements")

    def __init__(
        self,
        statements: list[Statement] | None = None,
        *,
        local_vars: list[Variable] | None = None,
        name: str | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.local_vars = local_vars or []
        self.statements = statements or []

    def __repr__(self) -> str:
        name_part = f" : {self.name}" if self.name else ""
        locals_part = f", {len(self.local_vars)} locals" if self.local_vars else ""
        return f"SeqBlock({len(self.statements)} stmts{locals_part}{name_part})"

    def _child_nodes(self) -> list[VerilogNode]:
        return [*self.local_vars, *self.statements]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "SeqBlock"
        if self.name:
            d["name"] = self.name
        if self.local_vars:
            d["locals"] = [v.to_dict() for v in self.local_vars]
        d["statements"] = [s.to_dict() for s in self.statements]
        return d


class ParBlock(Statement):
    """fork ... join"""

    __slots__ = ("local_vars", "name", "statements")

    def __init__(
        self,
        statements: list[Statement] | None = None,
        *,
        local_vars: list[Variable] | None = None,
        name: str | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.local_vars = local_vars or []
        self.statements = statements or []

    def __repr__(self) -> str:
        name_part = f" : {self.name}" if self.name else ""
        locals_part = f", {len(self.local_vars)} locals" if self.local_vars else ""
        return f"ParBlock({len(self.statements)} stmts{locals_part}{name_part})"

    def _child_nodes(self) -> list[VerilogNode]:
        return [*self.local_vars, *self.statements]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "ParBlock"
        if self.name:
            d["name"] = self.name
        if self.local_vars:
            d["locals"] = [v.to_dict() for v in self.local_vars]
        d["statements"] = [s.to_dict() for s in self.statements]
        return d


class WaitStatement(Statement):
    """wait (expression) statement_or_null"""

    __slots__ = ("body", "condition")

    def __init__(
        self,
        condition: Expression,
        body: Statement | None = None,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.condition = condition
        self.body = body

    def __repr__(self) -> str:
        return f"WaitStatement({self.condition!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = [self.condition]
        if self.body:
            nodes.append(self.body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "WaitStatement"
        d["condition"] = self.condition.to_dict()
        if self.body:
            d["body"] = self.body.to_dict()
        return d


class DisableStatement(Statement):
    """disable block_or_task_name;"""

    __slots__ = ("target",)

    def __init__(self, target: str, *, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.target = target

    def __repr__(self) -> str:
        return f"DisableStatement({self.target!r})"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "DisableStatement"
        d["target"] = self.target
        return d


class EventTrigger(Statement):
    """-> event_name;"""

    __slots__ = ("event",)

    def __init__(self, event: str, *, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.event = event

    def __repr__(self) -> str:
        return f"EventTrigger({self.event!r})"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "EventTrigger"
        d["event"] = self.event
        return d


class TaskEnable(Statement):
    """my_task(arg1, arg2);"""

    __slots__ = ("arguments", "task_name")

    def __init__(
        self,
        task_name: str,
        arguments: list[Expression] | None = None,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.task_name = task_name
        self.arguments = arguments or []

    def __repr__(self) -> str:
        return f"TaskEnable({self.task_name!r}, {len(self.arguments)} args)"

    def _child_nodes(self) -> list[VerilogNode]:
        return list(self.arguments)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "TaskEnable"
        d["task_name"] = self.task_name
        if self.arguments:
            d["arguments"] = [a.to_dict() for a in self.arguments]
        return d


class SystemTaskCall(Statement):
    """$display("hello"); $finish;"""

    __slots__ = ("arguments", "task_name")

    def __init__(
        self,
        task_name: str,
        arguments: list[Expression] | None = None,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.task_name = task_name
        self.arguments = arguments or []

    def __repr__(self) -> str:
        return f"SystemTaskCall({self.task_name!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        return list(self.arguments)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "SystemTaskCall"
        d["task_name"] = self.task_name
        if self.arguments:
            d["arguments"] = [a.to_dict() for a in self.arguments]
        return d


class DelayControl(Statement):
    """#5 statement;  (procedural delay)"""

    __slots__ = ("body", "delay")

    def __init__(
        self,
        delay: Expression,
        body: Statement | None = None,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.delay = delay
        self.body = body

    def __repr__(self) -> str:
        return f"DelayControl(#{self.delay!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = [self.delay]
        if self.body:
            nodes.append(self.body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "DelayControl"
        d["delay"] = self.delay.to_dict()
        if self.body:
            d["body"] = self.body.to_dict()
        return d


class EventControl(Statement):
    """@(posedge clk) statement;  (procedural event control)"""

    __slots__ = ("body", "events")

    def __init__(
        self,
        events: list[SensitivityEdge] | None = None,
        body: Statement | None = None,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.events = events or []
        self.body = body

    def __repr__(self) -> str:
        return f"EventControl({len(self.events)} events)"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = list(self.events)
        if self.body:
            nodes.append(self.body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "EventControl"
        if self.events:
            d["events"] = [e.to_dict() for e in self.events]
        if self.body:
            d["body"] = self.body.to_dict()
        return d


class SensitivityEdge(VerilogNode):
    """An edge in a sensitivity list: posedge clk, negedge rst, or level signal."""

    __slots__ = ("edge", "signal")

    def __init__(
        self,
        edge: str,
        signal: Expression,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.edge = edge  # "posedge", "negedge", or "level"
        self.signal = signal

    def __repr__(self) -> str:
        if self.edge == "level":
            return f"SensitivityEdge({self.signal!r})"
        return f"SensitivityEdge({self.edge} {self.signal!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.signal]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["edge"] = self.edge
        d["signal"] = self.signal.to_dict()
        return d
