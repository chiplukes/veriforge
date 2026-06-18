"""Expression tree nodes for the Verilog semantic model.

Expressions are used in many contexts: port widths, parameter defaults,
assignments, always blocks. This module defines the full expression hierarchy
with __slots__ for Cython compatibility.

Phase 1 uses these minimally (for Range MSB/LSB and Parameter defaults).
The full set is defined now for forward compatibility.
"""

from __future__ import annotations

from .base import SourceLocation, VerilogNode


class Expression(VerilogNode):  # cm:7a5d8b
    """Base class for all expressions."""

    __slots__ = ("inferred_width",)

    def __init__(self, *, loc: SourceLocation | None = None, inferred_width: int | None = None):
        super().__init__(loc=loc)
        self.inferred_width = inferred_width

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.inferred_width is not None:
            d["inferred_width"] = self.inferred_width
        return d


class Identifier(Expression):  # cm:1e3f2c
    """A simple or hierarchical identifier: a, a.b.c"""

    __slots__ = ("hierarchy", "name", "resolved")

    def __init__(self, name: str, *, hierarchy: list[str] | None = None, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.name = name
        self.hierarchy = hierarchy
        self.resolved: VerilogNode | None = None  # Populated by Layer 3 analysis

    def __repr__(self) -> str:
        return f"Identifier({self.name!r})"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        if self.hierarchy:
            d["hierarchy"] = self.hierarchy
        return d


class Literal(Expression):
    """A numeric literal: 8'hFF, 32, 4'b1010, etc."""

    __slots__ = ("base", "is_x", "is_z", "original_text", "signed", "value", "width")

    def __init__(
        self,
        value: int | float | str,
        *,
        width: int | None = None,
        base: str | None = None,
        signed: bool = False,
        is_x: bool = False,
        is_z: bool = False,
        original_text: str = "",
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.value = value
        self.width = width
        self.base = base
        self.signed = signed
        self.is_x = is_x
        self.is_z = is_z
        self.original_text = original_text

    def __repr__(self) -> str:
        if self.original_text:
            return f"Literal({self.original_text!r})"
        return f"Literal({self.value!r})"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["value"] = self.value
        if self.width is not None:
            d["width"] = self.width
        if self.base:
            d["base"] = self.base
        if self.signed:
            d["signed"] = True
        if self.is_x:
            d["is_x"] = True
        if self.is_z:
            d["is_z"] = True
        if self.original_text:
            d["original_text"] = self.original_text
        return d


class StringLiteral(Expression):
    """A string literal: "hello" """

    __slots__ = ("value",)

    def __init__(self, value: str, *, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.value = value

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["value"] = self.value
        return d


class UnaryOp(Expression):
    """Unary operation: ~a, !a, &a, etc."""

    __slots__ = ("op", "operand")

    def __init__(self, op: str, operand: Expression, *, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.op = op
        self.operand = operand

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.operand]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["op"] = self.op
        d["operand"] = self.operand.to_dict()
        return d


class BinaryOp(Expression):  # cm:c6b4a9
    """Binary operation: a + b, a == b, etc."""

    __slots__ = ("left", "op", "right")

    def __init__(self, op: str, left: Expression, right: Expression, *, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.op = op
        self.left = left
        self.right = right

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.left, self.right]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["op"] = self.op
        d["left"] = self.left.to_dict()
        d["right"] = self.right.to_dict()
        return d


class TernaryOp(Expression):
    """Ternary conditional: cond ? true_expr : false_expr"""

    __slots__ = ("condition", "false_expr", "true_expr")

    def __init__(
        self,
        condition: Expression,
        true_expr: Expression,
        false_expr: Expression,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.condition = condition
        self.true_expr = true_expr
        self.false_expr = false_expr

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.condition, self.true_expr, self.false_expr]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["condition"] = self.condition.to_dict()
        d["true_expr"] = self.true_expr.to_dict()
        d["false_expr"] = self.false_expr.to_dict()
        return d


class Concatenation(Expression):
    """{a, b, c}"""

    __slots__ = ("parts",)

    def __init__(self, parts: list[Expression], *, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.parts = parts

    def _child_nodes(self) -> list[VerilogNode]:
        return list(self.parts)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["parts"] = [p.to_dict() for p in self.parts]
        return d


class AssignmentPattern(Expression):
    """SystemVerilog assignment pattern: '{field: val, ...} or '{val, ...} or '{default: val}"""

    __slots__ = ("default_value", "named_pairs", "positional")

    def __init__(
        self,
        *,
        named_pairs: list[tuple[str, Expression]] | None = None,
        positional: list[Expression] | None = None,
        default_value: Expression | None = None,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.named_pairs: list[tuple[str, Expression]] = named_pairs or []
        self.positional: list[Expression] | None = positional
        self.default_value: Expression | None = default_value

    def __repr__(self) -> str:
        parts = []
        for name, val in self.named_pairs:
            parts.append(f"{name}: {val!r}")
        if self.positional:
            parts.extend(repr(v) for v in self.positional)
        if self.default_value is not None:
            parts.append(f"default: {self.default_value!r}")
        return "AssignmentPattern('{" + ", ".join(parts) + "})"

    def _child_nodes(self) -> list[VerilogNode]:
        nodes: list[VerilogNode] = [v for _, v in self.named_pairs]
        if self.positional:
            nodes.extend(self.positional)
        if self.default_value is not None:
            nodes.append(self.default_value)
        return nodes

    def to_dict(self) -> dict:
        d = super().to_dict()
        if self.named_pairs:
            d["named_pairs"] = [(n, v.to_dict()) for n, v in self.named_pairs]
        if self.positional:
            d["positional"] = [v.to_dict() for v in self.positional]
        if self.default_value is not None:
            d["default_value"] = self.default_value.to_dict()
        return d


class Replication(Expression):
    """{4{data}}"""

    __slots__ = ("count", "value")

    def __init__(self, count: Expression, value: Expression, *, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.count = count
        self.value = value

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.count, self.value]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["count"] = self.count.to_dict()
        d["value"] = self.value.to_dict()
        return d


class BitSelect(Expression):
    """a[3]"""

    __slots__ = ("index", "target")

    def __init__(self, target: Expression, index: Expression, *, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.target = target
        self.index = index

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.target, self.index]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["target"] = self.target.to_dict()
        d["index"] = self.index.to_dict()
        return d


class RangeSelect(Expression):
    """a[7:0]"""

    __slots__ = ("lsb", "msb", "target")

    def __init__(self, target: Expression, msb: Expression, lsb: Expression, *, loc: SourceLocation | None = None):
        super().__init__(loc=loc)
        self.target = target
        self.msb = msb
        self.lsb = lsb

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.target, self.msb, self.lsb]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["target"] = self.target.to_dict()
        d["msb"] = self.msb.to_dict()
        d["lsb"] = self.lsb.to_dict()
        return d


class PartSelect(Expression):
    """a[base +: width] or a[base -: width]"""

    __slots__ = ("base", "direction", "target", "width")

    def __init__(
        self,
        target: Expression,
        base: Expression,
        width: Expression,
        direction: str,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.target = target
        self.base = base
        self.width = width
        self.direction = direction  # "+:" or "-:"

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.target, self.base, self.width]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["target"] = self.target.to_dict()
        d["base"] = self.base.to_dict()
        d["width"] = self.width.to_dict()
        d["direction"] = self.direction
        return d


class FunctionCall(Expression):
    """foo(a, b) or $clog2(WIDTH)"""

    __slots__ = ("arguments", "is_system", "name")

    def __init__(
        self,
        name: str,
        arguments: list[Expression],
        *,
        is_system: bool = False,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.name = name
        self.arguments = arguments
        self.is_system = is_system

    def _child_nodes(self) -> list[VerilogNode]:
        return list(self.arguments)

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["name"] = self.name
        d["arguments"] = [a.to_dict() for a in self.arguments]
        if self.is_system:
            d["is_system"] = True
        return d


class Mintypmax(Expression):
    """min:typ:max expression."""

    __slots__ = ("max_val", "min_val", "typ_val")

    def __init__(
        self,
        min_val: Expression,
        typ_val: Expression,
        max_val: Expression,
        *,
        loc: SourceLocation | None = None,
    ):
        super().__init__(loc=loc)
        self.min_val = min_val
        self.typ_val = typ_val
        self.max_val = max_val

    def _child_nodes(self) -> list[VerilogNode]:
        return [self.min_val, self.typ_val, self.max_val]

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["min_val"] = self.min_val.to_dict()
        d["typ_val"] = self.typ_val.to_dict()
        d["max_val"] = self.max_val.to_dict()
        return d


class Range:
    """Bit range [msb:lsb] or dimension [left:right].

    Not a VerilogNode — lightweight container used in Port/Net/Variable.
    """

    __slots__ = ("loc", "lsb", "msb")

    def __init__(self, msb: Expression, lsb: Expression, loc: SourceLocation | None = None):
        self.msb = msb
        self.lsb = lsb
        self.loc = loc

    def __repr__(self) -> str:
        return f"Range({self.msb!r}:{self.lsb!r})"

    def to_dict(self) -> dict:
        d: dict = {"msb": self.msb.to_dict(), "lsb": self.lsb.to_dict()}
        if self.loc:
            d["loc"] = self.loc.to_dict()
        return d
