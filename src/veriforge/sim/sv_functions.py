"""SystemVerilog query / bit-vector system functions, lowered before any
engine sees the module (``Simulator.__init__``), so every engine supports
them the same way:

- ``$size``/``$high``/``$low``/``$left``/``$right`` (IEEE 1800 20.7) fold to
  integer literals from the declarations (optional dimension argument; the
  unpacked dimensions come first, then the packed ones).
- ``$countones``/``$onehot``/``$onehot0``/``$isunknown`` (20.9) become
  ordinary expressions with Icarus's x semantics: x/z bits are not counted
  as ones (``x === 1'b1`` per bit), and ``$isunknown`` is ``(^e) === 1'bx``.

Before this, no engine implemented them: reference returned x and compiled
silently 0.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

from veriforge.model.base import VerilogNode
from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    Range,
    UnaryOp,
)
from veriforge.semantics import const_int, expr_width

if TYPE_CHECKING:
    from veriforge.model.design import Module
    from veriforge.model.nets import Net
    from veriforge.model.ports import Port
    from veriforge.model.variables import Variable

_RANGE_QUERIES = {"$size", "$high", "$low", "$left", "$right"}
_BIT_FUNCS = {"$countones", "$onehot", "$onehot0", "$isunknown"}
_SKIP_SLOTS = {"_parse_tree", "attributes", "comments", "loc", "parent", "drivers", "loads"}


def lower_sv_functions(module: Module) -> Module:
    """Rewrite the functions above everywhere in *module*, in place."""
    from .elaborate import _build_param_env  # noqa: PLC0415

    if not _mentions(module):
        return module
    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    decls = _declarations(module, env)

    def width_of(name: str) -> int:
        d = decls.get(name)
        return d[2] if d is not None else 1

    def lower(expr: Expression) -> Expression | None:
        if not isinstance(expr, FunctionCall):
            return None
        name = expr.name.lower()
        if name in _RANGE_QUERIES:
            return _fold_range_query(name, expr, decls, env)
        if name in _BIT_FUNCS and len(expr.arguments) == 1:
            arg = expr.arguments[0]
            if name == "$isunknown":
                return BinaryOp("===", UnaryOp("^", arg), Literal(0, width=1, is_x=True, original_text="1'bx"))
            count = _count_ones(arg, decls, env, width_of)
            if count is None:
                return None
            if name == "$countones":
                return count
            op = "==" if name == "$onehot" else "<="
            return BinaryOp(op, count, Literal(1, width=32, original_text="32'd1"))
        return None

    _rewrite(module, lower, set())
    return module


def _mentions(module: Module) -> bool:
    return any(
        isinstance(call, FunctionCall) and call.name.lower() in _RANGE_QUERIES | _BIT_FUNCS
        for call in iter_nodes(module, set())
    )


def iter_nodes(obj: object, seen: set[int]) -> Iterator[object]:
    """Every node below *obj* (``__slots__`` walk, like ``_rewrite``).
    Unlike ``VerilogNode.find`` it skips non-node list items (a hand-built
    model may hold plain strings there)."""
    if id(obj) in seen:
        return
    seen.add(id(obj))
    yield obj
    for klass in type(obj).__mro__:
        for slot in getattr(klass, "__slots__", ()):
            if slot in _SKIP_SLOTS:
                continue
            value = getattr(obj, slot, None)
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, (VerilogNode, Range)):
                    yield from iter_nodes(item, seen)


def _rewrite(obj: object, fn, seen: set[int]) -> None:
    """Post-order, in place: replace every expression *e* below *obj* for
    which ``fn(e)`` returns a node."""
    if id(obj) in seen:
        return
    seen.add(id(obj))
    slots: list[str] = []
    for klass in type(obj).__mro__:
        slots.extend(s for s in getattr(klass, "__slots__", ()) if s not in _SKIP_SLOTS)
    for slot in slots:
        value = getattr(obj, slot, None)
        if isinstance(value, (VerilogNode, Range)):
            _rewrite(value, fn, seen)
            if isinstance(value, Expression):
                new = fn(value)
                if new is not None:
                    setattr(obj, slot, new)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                if isinstance(item, (VerilogNode, Range)):
                    _rewrite(item, fn, seen)
                    if isinstance(item, Expression):
                        new = fn(item)
                        if new is not None:
                            value[i] = new


def _dims(width: Range | None, dimensions: list[Range], packed_count: int) -> list[Range]:
    """Dimensions in SystemVerilog query order: unpacked, then packed."""
    element = width if width is not None else Range(Literal(0, width=32), Literal(0, width=32))
    return [*dimensions[packed_count:], *dimensions[:packed_count], element]


def _declarations(module: Module, env: dict[str, int]) -> dict[str, tuple[list[tuple[int, int]], int, int]]:
    """name -> (dims as (left, right) pairs, number of unpacked dims,
    packed width)."""
    out: dict[str, tuple[list[tuple[int, int]], int, int]] = {}
    all_decls: list[Port | Net | Variable] = [*module.ports, *module.nets, *module.variables]
    for decl in all_decls:
        dims = _dims(decl.width, list(decl.dimensions), decl.packed_dim_count)
        pairs = []
        for rng in dims:
            left, right = const_int(rng.msb, env), const_int(rng.lsb, env)
            if left is None or right is None:
                break
            pairs.append((left, right))
        else:
            n_unpacked = len(decl.dimensions) - decl.packed_dim_count
            packed = 1
            for left, right in pairs[n_unpacked:]:
                packed *= abs(left - right) + 1
            out.setdefault(decl.name, (pairs, n_unpacked, packed))
    return out


def _arg_name(arg: Expression) -> str | None:
    if isinstance(arg, Identifier):
        return ".".join([*arg.hierarchy, arg.name]) if arg.hierarchy else arg.name
    return None


def _fold_range_query(name: str, call: FunctionCall, decls, env) -> Expression | None:
    if not call.arguments:
        return None
    decl = decls.get(_arg_name(call.arguments[0]) or "")
    if decl is None:
        return None
    pairs = decl[0]
    dim = 1
    if len(call.arguments) > 1:
        d = const_int(call.arguments[1], env)
        if d is None:
            return None
        dim = d
    if not 1 <= dim <= len(pairs):
        return None
    left, right = pairs[dim - 1]
    value = {
        "$left": left,
        "$right": right,
        "$high": max(left, right),
        "$low": min(left, right),
        "$size": abs(left - right) + 1,
    }[name]
    return Literal(value, original_text=str(value))


def _count_ones(arg: Expression, decls, env, width_of) -> Expression | None:
    """``32'd0 + (bit === 1'b1) + ...`` over every bit of *arg*."""
    one = Literal(1, width=1, original_text="1'b1")
    bits: list[Expression] = []
    name = _arg_name(arg)
    decl = decls.get(name or "")
    if name is not None and decl is not None and decl[1] == 0 and len(decl[0]) == 1:
        # A plain vector: select each bit in its own declared coordinates.
        left, right = decl[0][0]
        bits = [BitSelect(arg, Literal(i, width=32)) for i in range(min(left, right), max(left, right) + 1)]
    elif (
        isinstance(arg, BitSelect)
        and (target := decls.get(_arg_name(arg.target) or "")) is not None
        and target[1] == 1
        and len(target[0]) == 2
    ):
        # One element of a 1-D memory with a plain packed element.
        left, right = target[0][1]
        bits = [BitSelect(arg, Literal(i, width=32)) for i in range(min(left, right), max(left, right) + 1)]
    else:
        # Any other expression: bit i is (arg >> i) & 1.
        w = expr_width(arg, width_of, env)
        bits = [BinaryOp("&", BinaryOp(">>", arg, Literal(i, width=32)), Literal(1, width=1)) for i in range(w)]
    total: Expression = Literal(0, width=32, original_text="32'd0")
    for bit in bits:
        total = BinaryOp("+", total, BinaryOp("===", bit, one))
    return total
