"""Lower packed dimension selectors to slices of a memory word.

The engines store a pure packed array as one memory dimension whose words
contain the remaining packed dimensions. A real unpacked memory instead
keeps its address dimensions and packs the complete element into each word.
Only selectors change; whole-array bit order and values stay unchanged.
"""

from __future__ import annotations

from math import prod
from typing import TYPE_CHECKING

from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    PartSelect,
    Range,
    RangeSelect,
)
from veriforge.semantics import const_int

from .ascending import _flat_index, _flip, _index_literal, _name, _position
from .sv_functions import _rewrite, iter_nodes

if TYPE_CHECKING:
    from veriforge.model.base import SourceLocation
    from veriforge.model.design import Module
    from veriforge.model.functions import FunctionDecl, TaskDecl
    from veriforge.model.nets import Net
    from veriforge.model.ports import Port
    from veriforge.model.variables import Variable

    _Declaration = Port | Net | Variable
    _Shape = tuple[list[Range], list[tuple[int, int]], bool]

_SELECTS = (BitSelect, RangeSelect, PartSelect)


def _shape(decl: _Declaration, env: dict[str, int]) -> _Shape | None:
    if not (decl.packed_dim_count or decl.packed_only) or decl.width is None:
        return None
    if not decl.packed_dim_count:
        # Ordinary two-dimensional descending arrays already have the
        # correct word layout. Ascending ranges need remapping, and signed
        # arrays need an unsigned packed-select wrapper around word reads.
        left, right = const_int(decl.dimensions[0].msb, env), const_int(decl.dimensions[0].lsb, env)
        if left is None or right is None or (left >= right and not decl.signed):
            return None
    if decl.packed_only:
        addresses, packed = [decl.dimensions[0]], [*decl.dimensions[1:], decl.width]
    else:
        addresses = decl.dimensions[decl.packed_dim_count :]
        packed = [*decl.dimensions[: decl.packed_dim_count], decl.width]
    bounds = []
    for dim in packed:
        left, right = const_int(dim.msb, env), const_int(dim.lsb, env)
        if left is None or right is None:
            return None
        bounds.append((left, right))
    return addresses, bounds, decl.packed_only


def _select_chain(expr: Expression) -> tuple[Expression, list[BitSelect | RangeSelect | PartSelect]]:
    selects: list[BitSelect | RangeSelect | PartSelect] = []
    while isinstance(expr, _SELECTS):
        selects.append(expr)
        expr = expr.target
    return expr, selects[::-1]


def _add(left: Expression, right: Expression) -> Expression:
    if const_int(left, {}) == 0:
        return right
    return BinaryOp("+", left, right)


def _range_elements(select: RangeSelect | PartSelect, info: tuple[int, int], env: dict[str, int]) -> list[Expression]:
    if isinstance(select, RangeSelect):
        left, right = const_int(select.msb, env), const_int(select.lsb, env)
        if left is None or right is None:
            raise ValueError("packed array range bounds must be constant")
        step = 1 if left <= right else -1
        return [_index_literal(i) for i in range(left, right + step, step)]
    count = const_int(select.width, env)
    if count is None or count <= 0:
        raise ValueError("packed array part-select width must be a positive constant")
    # Numeric +:/-: chooses the same indices in either declared direction;
    # emit them in declared (most-significant-first) order.
    offsets = list(range(count) if select.direction == "+:" else range(1 - count, 1))
    if info[0] >= info[1]:
        offsets.reverse()
    return [_add(select.base, _index_literal(i)) for i in offsets]


def _packed_select(
    word: Expression,
    bounds: list[tuple[int, int]],
    selects: list[BitSelect | RangeSelect | PartSelect],
    env: dict[str, int],
    loc: SourceLocation | None,
) -> Expression:
    """One slice for scalar indices; concatenated slices for array ranges.

    Guard each dimension before combining offsets: an invalid inner index
    must not alias its adjacent row. Range elements use the same guard, so
    a partially out-of-range slice retains its in-range bits.
    """
    sizes = [abs(left - right) + 1 for left, right in bounds]

    def slice_at(indices: list[Expression], count: int = 1) -> Expression:
        stride = prod(sizes[len(indices) :])
        index = _flat_index(indices, sizes[: len(indices)])
        base = index if stride == 1 else BinaryOp("*", index, _index_literal(stride))
        return PartSelect(word, base, _index_literal(count * stride), "+:", loc=loc)

    def lower(indices: list[Expression], remaining: list[BitSelect | RangeSelect | PartSelect]) -> Expression:
        if not remaining:
            return slice_at(indices)
        select = remaining[0]
        dimension = len(indices)
        info = bounds[dimension]
        if isinstance(select, BitSelect):
            return lower([*indices, _position(info, select.index)], remaining[1:])
        if len(remaining) != 1:
            raise ValueError("a packed array range must be the final selector")
        elements = _range_elements(select, info, env)
        positions = [_position(info, index) for index in elements]
        constants = [const_int(index, env) for index in positions]
        low = constants[-1]
        if low is not None and 0 <= low and low + len(positions) <= sizes[dimension]:
            if constants == list(range(low + len(positions) - 1, low - 1, -1)):
                return slice_at([*indices, _index_literal(low)], len(positions))
        parts = [slice_at([*indices, index]) for index in positions]
        return parts[0] if len(parts) == 1 else Concatenation(parts, loc=loc)

    return lower([], selects)


def _lower_scope(roots: list[object], shapes: dict[str, _Shape], env: dict[str, int]) -> None:
    if not shapes:
        return
    # Rewrite maximal selector chains. Rewriting a prefix first would erase
    # the shape needed to interpret its next selector.
    targets: set[int] = set()
    seen: set[int] = set()
    for root in roots:
        for node in iter_nodes(root, seen):
            if isinstance(node, _SELECTS):
                targets.add(id(node.target))

    def lower(expr: Expression) -> Expression | None:
        if not isinstance(expr, _SELECTS) or id(expr) in targets:
            return None
        root, selects = _select_chain(expr)
        name = _name(root)
        shape = shapes.get(name) if name is not None else None
        if shape is None:
            return None
        addresses, bounds, packed_only = shape
        depth = len(addresses)
        outer_left = const_int(addresses[0].msb, env) if packed_only else None
        outer_right = const_int(addresses[0].lsb, env) if packed_only else None

        def outer_index(index: Expression) -> Expression:
            if outer_left is not None and outer_right is not None and outer_left < outer_right:
                return _flip(outer_left + outer_right, index)
            return index

        if packed_only and len(selects) == 1 and isinstance(selects[0], (RangeSelect, PartSelect)):
            if outer_left is None or outer_right is None:
                raise ValueError("packed array dimensions must be constant")
            elements = _range_elements(selects[0], (outer_left, outer_right), env)
            parts: list[Expression] = [BitSelect(root, outer_index(index), loc=expr.loc) for index in elements]
            return parts[0] if len(parts) == 1 else Concatenation(parts, loc=expr.loc)
        if len(selects) < depth or any(not isinstance(s, BitSelect) for s in selects[:depth]):
            return None
        word = root
        for level, select in enumerate(selects[:depth]):
            assert isinstance(select, BitSelect)
            index = select.index
            if packed_only and level == 0:
                index = outer_index(index)
            word = BitSelect(word, index, loc=select.loc)
        packed = selects[depth:]
        if not packed:
            if packed_only:
                # A packed select is unsigned, even if its backing memory
                # word inherits signedness from the whole declaration.
                width = prod(abs(left - right) + 1 for left, right in bounds)
                return PartSelect(word, _index_literal(0), _index_literal(width), "+:", loc=expr.loc)
            return word
        if len(packed) > len(bounds):
            raise ValueError("too many selectors for packed array")
        return _packed_select(word, bounds, packed, env, expr.loc)

    seen = set()
    for root in roots:
        _rewrite(root, lower, seen)


# cm:7cb5e2
def lower_packed_dimensions(module: Module, reversals: dict[str, int] | None = None) -> Module:
    """Normalize extra packed dimensions before all engine-specific lowering.

    ``logic [1:0][3:0][7:0] a`` becomes two 32-bit words, and ``a[g][k]``
    selects an 8-bit slice of word ``g``. For ``logic [3:0][7:0] m [0:1]``,
    the unpacked address still selects a 32-bit word and its next index
    selects a byte. Function/task locals use their own declaration scope.
    """
    from .elaborate import _build_param_env  # noqa: PLC0415

    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    decls: list[_Declaration] = [*module.ports, *module.nets, *module.variables]
    shapes = {decl.name: shape for decl in decls if (shape := _shape(decl, env)) is not None}
    scopes = [(decls, shapes)]
    roots: list[object] = [*module.always_blocks, *module.initial_blocks, *module.continuous_assigns, *decls]
    _lower_scope(roots, shapes, env)
    tfs: list[FunctionDecl | TaskDecl] = [*module.functions, *module.tasks]
    for tf in tfs:
        local: list[_Declaration] = [*tf.ports, *tf.locals]
        names = {decl.name for decl in local}
        local_shapes = {decl.name: shape for decl in local if (shape := _shape(decl, env)) is not None}
        scope = {name: shape for name, shape in shapes.items() if name not in names}
        scope.update(local_shapes)
        _lower_scope([tf], scope, env)
        scopes.append((local, local_shapes))
    for declarations, scope in scopes:
        for decl in declarations:
            shape = scope.get(decl.name)
            if shape is None:
                continue
            addresses, bounds, packed_only = shape
            if reversals is not None and packed_only and declarations is decls:
                left, right = const_int(addresses[0].msb, env), const_int(addresses[0].lsb, env)
                if left is not None and right is not None and left < right:
                    reversals[decl.name] = left + right
            width = prod(abs(left - right) + 1 for left, right in bounds)
            decl.width = Range(_index_literal(width - 1), _index_literal(0))
            decl.dimensions = list(addresses)
            decl.packed_dim_count = 0
    return module
