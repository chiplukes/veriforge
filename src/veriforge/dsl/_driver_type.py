"""Width and signedness of a wire's driver, for ``x = m.wire(expr)``.

The width is the narrowest one that holds every bit the expression can
produce, so declaring the wire at that width and assigning the expression
gives the same bits as any wider declaration. That is well defined for
compares, logic, bitwise operations, selects, concatenations, right shifts
and muxes. It is not for operations that carry or grow (``+``, ``-``, ``*``,
``/``, ``%``, ``**``, ``<<``, unary ``-``): there the intended width is a
design decision, so they raise :class:`NeedWidth` and the caller asks for
``width=``. A bare integer in a bitwise operation or mux counts at the width
its value needs (one more bit in a signed expression); a negative one in an
unsigned expression, or an expression of integers only, needs ``width=``.

Signedness follows Verilog: a compare, select or concatenation is unsigned;
a bitwise operation or mux is signed only if every operand is (a bare
integer is signed, as an unsized decimal is).
"""

from __future__ import annotations

from collections.abc import Callable

from ..model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    PartSelect,
    RangeSelect,
    Replication,
    TernaryOp,
    UnaryOp,
    WidthCast,
)

# name -> (width, signed, is a memory); width is None when it isn't a plain int
Lookup = Callable[[str], tuple[int | None, bool, bool]]

_ONE_BIT_BINARY = {"==", "!=", "===", "!==", "==?", "!=?", "<", "<=", ">", ">=", "&&", "||", "->", "<->"}
_BITWISE = {"&", "|", "^", "~^", "^~"}
_RIGHT_SHIFTS = {">>", ">>>"}
_ONE_BIT_UNARY = {"!", "&", "|", "^", "~&", "~|", "~^", "^~"}


class NeedWidth(Exception):
    """The driver's width is a design decision (or unknown): pass ``width=``."""


def driver_signed(node: Expression, lookup: Lookup) -> bool:
    """Signedness of *node*, or :class:`NeedWidth` with the reason."""
    return _signed(node, lookup)


def driver_width(node: Expression, lookup: Lookup) -> int:
    """Width of *node*, or :class:`NeedWidth` with the reason."""
    return _width(node, lookup, _signed(node, lookup))


def _const(node: Expression, what: str) -> int:
    if isinstance(node, Literal) and isinstance(node.value, int) and not (node.is_x or node.is_z):
        return node.value
    raise NeedWidth(f"{what} is not a constant")


def _signed(node: Expression, lookup: Lookup) -> bool:
    if isinstance(node, Identifier):
        return lookup(node.name)[1]
    if isinstance(node, Literal):
        return node.signed
    if isinstance(node, UnaryOp):
        return False if node.op in _ONE_BIT_UNARY else _signed(node.operand, lookup)
    if isinstance(node, BinaryOp):
        if node.op in _ONE_BIT_BINARY:
            return False
        if node.op in _RIGHT_SHIFTS or node.op in ("<<", "<<<"):
            return _signed(node.left, lookup)
        return _signed(node.left, lookup) and _signed(node.right, lookup)
    if isinstance(node, TernaryOp):
        return _signed(node.true_expr, lookup) and _signed(node.false_expr, lookup)
    if isinstance(node, (Concatenation, Replication, RangeSelect, PartSelect)):
        return False
    if isinstance(node, BitSelect):
        if isinstance(node.target, Identifier):
            _, signed, memory = lookup(node.target.name)
            return signed if memory else False
        return False
    if isinstance(node, WidthCast):
        return _signed(node.operand, lookup)
    if isinstance(node, FunctionCall) and node.name in ("$signed", "$unsigned"):
        return node.name == "$signed"
    raise NeedWidth(f"can't tell the signedness of {type(node).__name__}")


def _width(node: Expression, lookup: Lookup, signed: bool) -> int:  # noqa: PLR0911, PLR0912
    """Bits *node* produces in an expression whose signedness is *signed*."""
    if isinstance(node, Identifier):
        width = lookup(node.name)[0]
        if width is None:
            raise NeedWidth(f"{node.name!r} has a parameterized width")
        return width
    if isinstance(node, Literal):
        if node.width is not None:
            return node.width
        value = _const(node, "the literal")
        if value < 0 and not signed:
            raise NeedWidth(f"the negative integer {value} in an unsigned expression is 32 bits")
        return max(1, (value if value >= 0 else ~value).bit_length() + (1 if signed else 0))
    if isinstance(node, UnaryOp):
        if node.op in _ONE_BIT_UNARY:
            return 1
        if node.op in ("~", "+"):
            return _width(node.operand, lookup, signed)
        raise NeedWidth(f"unary '{node.op}' can grow")
    if isinstance(node, BinaryOp):
        if node.op in _ONE_BIT_BINARY:
            return 1
        if node.op in _BITWISE:
            if _all_bare_ints(node):
                raise NeedWidth("an expression of integers only is 32 bits")
            return max(_width(node.left, lookup, signed), _width(node.right, lookup, signed))
        if node.op in _RIGHT_SHIFTS:
            return _width(node.left, lookup, signed)
        raise NeedWidth(f"'{node.op}' can carry or grow")
    if isinstance(node, TernaryOp):
        if _all_bare_ints(node.true_expr) and _all_bare_ints(node.false_expr):
            raise NeedWidth("a mux of integers only is 32 bits")
        return max(_width(node.true_expr, lookup, signed), _width(node.false_expr, lookup, signed))
    if isinstance(node, Concatenation):
        if any(isinstance(p, Literal) and p.width is None for p in node.parts):
            raise NeedWidth("an unsized integer in a concatenation")
        return sum(_width(p, lookup, _signed(p, lookup)) for p in node.parts)
    if isinstance(node, Replication):
        return _const(node.count, "the replication count") * _width(node.value, lookup, _signed(node.value, lookup))
    if isinstance(node, BitSelect):
        if isinstance(node.target, Identifier) and lookup(node.target.name)[2]:
            return _width(node.target, lookup, signed)  # a memory word
        return 1
    if isinstance(node, RangeSelect):
        return abs(_const(node.msb, "the select's msb") - _const(node.lsb, "the select's lsb")) + 1
    if isinstance(node, PartSelect):
        return _const(node.width, "the part select's width")
    if isinstance(node, WidthCast):
        return _const(node.width, "the cast's width")
    if isinstance(node, FunctionCall) and node.name in ("$signed", "$unsigned") and len(node.arguments) == 1:
        return _width(node.arguments[0], lookup, node.name == "$signed")
    raise NeedWidth(f"can't size {type(node).__name__}")


def _all_bare_ints(node: Expression) -> bool:
    if isinstance(node, Literal):
        return node.width is None
    if isinstance(node, BinaryOp) and node.op in _BITWISE:
        return _all_bare_ints(node.left) and _all_bare_ints(node.right)
    if isinstance(node, TernaryOp):
        return _all_bare_ints(node.true_expr) and _all_bare_ints(node.false_expr)
    if isinstance(node, UnaryOp) and node.op in ("~", "+"):
        return _all_bare_ints(node.operand)
    return False
