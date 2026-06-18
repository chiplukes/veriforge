"""Generate construct model classes.

Grammar reference (IEEE 1364-2005, A.4.2):
    generate_region ::= generate { module_or_generate_item } endgenerate
    loop_generate_construct ::=
        for ( genvar_initialization ; genvar_expression ; genvar_iteration )
            generate_block
    conditional_generate_construct ::= if_generate_construct | case_generate_construct
    if_generate_construct ::=
        if ( constant_expression ) generate_block_or_null
        [ else generate_block_or_null ]
    case_generate_construct ::=
        case ( constant_expression ) case_generate_item { case_generate_item } endcase
    generate_block ::=
        module_or_generate_item
        | begin [ : generate_block_identifier ] { module_or_generate_item } end
"""

from __future__ import annotations

from .base import SourceLocation, VerilogNode
from .expressions import Expression


class GenerateBlock(VerilogNode):
    """A generate block containing module items.

    Represents either:
    - A single module_or_generate_item (unnamed)
    - begin [:name] ... end (optionally named)
    """

    __slots__ = ("items", "name")

    def __init__(
        self,
        *,
        name: str | None = None,
        items: list[VerilogNode] | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.items: list[VerilogNode] = items or []

    def __repr__(self) -> str:
        name_str = f" {self.name!r}" if self.name else ""
        return f"GenerateBlock({name_str}, items={len(self.items)})"

    def _child_nodes(self) -> list[VerilogNode]:
        return list(self.items)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "GenerateBlock"
        if self.name:
            d["name"] = self.name
        if self.items:
            d["items"] = [i.to_dict() for i in self.items]
        return d


class GenerateFor(VerilogNode):
    """A generate-for loop construct.

    for (genvar_init; genvar_expr; genvar_iter) generate_block

    SV extensions:
    - Inline genvar: for (genvar i = 0; ...)
    - Iteration operators: i++, i--, ++i, --i, i+=expr, i-=expr, etc.
    """

    __slots__ = ("body", "condition", "genvar", "genvar_local", "init_value", "update", "update_op")

    def __init__(  # noqa: PLR0913
        self,
        genvar: str,
        init_value: Expression,
        condition: Expression,
        update: Expression | None,
        body: GenerateBlock,
        *,
        update_op: str = "=",
        genvar_local: bool = False,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.genvar = genvar
        self.init_value = init_value
        self.condition = condition
        self.update = update
        self.update_op = update_op
        self.genvar_local = genvar_local
        self.body = body

    def __repr__(self) -> str:
        return f"GenerateFor({self.genvar!r}, op={self.update_op!r})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = [self.init_value, self.condition]
        if self.update is not None:
            nodes.append(self.update)
        nodes.append(self.body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "GenerateFor"
        d["genvar"] = self.genvar
        d["genvar_local"] = self.genvar_local
        d["init_value"] = self.init_value.to_dict()
        d["condition"] = self.condition.to_dict()
        d["update_op"] = self.update_op
        if self.update is not None:
            d["update"] = self.update.to_dict()
        d["body"] = self.body.to_dict()
        return d


class GenerateIf(VerilogNode):
    """A generate-if conditional construct.

    if (constant_expression) generate_block_or_null [else generate_block_or_null]
    """

    __slots__ = ("condition", "else_body", "then_body")

    def __init__(
        self,
        condition: Expression,
        then_body: GenerateBlock | None,
        else_body: GenerateBlock | None = None,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.condition = condition
        self.then_body = then_body
        self.else_body = else_body

    def __repr__(self) -> str:
        else_str = ", else" if self.else_body else ""
        return f"GenerateIf({else_str})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = [self.condition]
        if self.then_body:
            nodes.append(self.then_body)
        if self.else_body:
            nodes.append(self.else_body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "GenerateIf"
        d["condition"] = self.condition.to_dict()
        if self.then_body:
            d["then_body"] = self.then_body.to_dict()
        if self.else_body:
            d["else_body"] = self.else_body.to_dict()
        return d


class GenerateCaseItem(VerilogNode):
    """A case item within a generate-case construct."""

    __slots__ = ("body", "is_default", "values")

    def __init__(
        self,
        *,
        values: list[Expression] | None = None,
        is_default: bool = False,
        body: GenerateBlock | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.values = values or []
        self.is_default = is_default
        self.body = body

    def __repr__(self) -> str:
        if self.is_default:
            return "GenerateCaseItem(default)"
        vals = ", ".join(str(v) for v in self.values)
        return f"GenerateCaseItem({vals})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = list(self.values)
        if self.body:
            nodes.append(self.body)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "GenerateCaseItem"
        d["is_default"] = self.is_default
        if self.values:
            d["values"] = [v.to_dict() for v in self.values]
        if self.body:
            d["body"] = self.body.to_dict()
        return d


class GenerateCase(VerilogNode):
    """A generate-case construct.

    [unique|unique0|priority] case (constant_expression)
        case_generate_item { case_generate_item } endcase
    """

    __slots__ = ("expression", "items", "qualifier")

    def __init__(
        self,
        expression: Expression,
        items: list[GenerateCaseItem],
        *,
        qualifier: str | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.expression = expression
        self.items = items
        self.qualifier = qualifier

    def __repr__(self) -> str:
        qual = f"{self.qualifier} " if self.qualifier else ""
        return f"GenerateCase({qual}items={len(self.items)})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = [self.expression]
        nodes.extend(self.items)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "GenerateCase"
        if self.qualifier:
            d["qualifier"] = self.qualifier
        d["expression"] = self.expression.to_dict()
        d["items"] = [i.to_dict() for i in self.items]
        return d


class GenvarDecl(VerilogNode):
    """A genvar declaration: genvar i, j;"""

    __slots__ = ("names",)

    def __init__(
        self,
        names: list[str],
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.names = names

    def __repr__(self) -> str:
        return f"GenvarDecl({', '.join(self.names)})"

    def _child_nodes(self) -> list[VerilogNode]:
        return []

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["type"] = "GenvarDecl"
        d["names"] = self.names
        return d
