"""Ascending packed ranges (``reg [0:7] a``) lowered to descending ones.

In ``[L:R]`` with ``L < R`` the leftmost bit -- the MSB -- is bit ``L``, so a
select of bit ``i`` reads physical position ``R - i``. Every engine mapped
indices as ``i - min(L, R)``, which is right only for descending ranges:
``a[0]`` of ``reg [0:7] a`` read x and writes landed in the wrong bits.

This pass rewrites each such declaration as ``[R-L:0]`` and every select on
it -- bit, range and part selects, on the right- and left-hand side, on
vectors and on memory elements -- to the equivalent descending select, so
the engines only ever see descending ranges. Whole-vector values are
unchanged (the leftmost bit is the MSB either way), so concatenations,
assignments and port connections need nothing. Runs after
``lower_sv_functions``, which folds ``$left``/``$right`` from the declared
ranges.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Expression,
    Identifier,
    Literal,
    PartSelect,
    Range,
    RangeSelect,
    TernaryOp,
)
from veriforge.semantics import const_int

from .sv_functions import _rewrite

if TYPE_CHECKING:
    from veriforge.model.design import Module
    from veriforge.model.functions import FunctionDecl, TaskDecl
    from veriforge.model.nets import Net
    from veriforge.model.ports import Port
    from veriforge.model.variables import Variable


def _name(expr: Expression) -> str | None:
    if isinstance(expr, Identifier):
        return ".".join([*expr.hierarchy, expr.name]) if expr.hierarchy else expr.name
    return None


def _index_literal(value: int) -> Literal:
    # (Decimal text so the DSL converter writes `a[3]`, not `a[lit("32'h3")]`.)
    # Signed, like the plain decimal it stands for: a signed index stays
    # signed (`$signed(i) + 2` of `f [-2:1]` must not zero-extend `i`).
    return Literal(value, width=32, signed=True, original_text=str(value) if value >= 0 else "")


def _const_index(index: Expression) -> int | None:
    if isinstance(index, Literal) and (index.is_x or getattr(index, "is_z", False)):
        return None
    return const_int(index, {})


def _flip(right: int, index: Expression) -> Expression:
    """``right - index``, folded when constant."""
    value = _const_index(index)
    if value is not None:
        return _index_literal(right - value)
    return BinaryOp("-", _index_literal(right), index)


def _offset(lsb: int, index: Expression) -> Expression:
    """``index - lsb``, folded when constant."""
    value = _const_index(index)
    if value is not None:
        return _index_literal(value - lsb)
    if lsb < 0:
        return BinaryOp("+", index, _index_literal(-lsb))
    return BinaryOp("-", index, _index_literal(lsb))


def _position(info: tuple[int, int], index: Expression) -> Expression:
    """Where bit *index* of a ``[left:right]`` vector sits in ``[w-1:0]``."""
    left, right = info
    return _flip(right, index) if left < right else _offset(right, index)


def _ascending_decls(decls: list, env: dict[str, int], offsets: bool = False) -> dict[str, tuple[int, int, int]]:
    """name -> (left, right, unpacked dims) for ascending single-packed-dim
    declarations (and, with *offsets*, descending ones not ending at 0)."""
    out: dict[str, tuple[int, int, int]] = {}
    for decl in decls:
        width = getattr(decl, "width", None)
        if width is None or getattr(decl, "packed_dim_count", 0) > 1:
            continue
        left, right = const_int(width.msb, env), const_int(width.lsb, env)
        if left is None or right is None or (left >= right and not (offsets and right != 0)):
            continue
        unpacked = len(decl.dimensions) - getattr(decl, "packed_dim_count", 0)
        out[decl.name] = (left, right, unpacked)
    return out


def _make_lower(asc: dict[str, tuple[int, int, int]]):
    def target_info(target: Expression) -> tuple[int, int] | None:
        depth = 0
        node = target
        while isinstance(node, BitSelect):
            node = node.target
            depth += 1
        name = _name(node)
        if name is None or name not in asc:
            return None
        left, right, unpacked = asc[name]
        if depth != unpacked:
            return None  # a memory word select, not a bit of an element
        return left, right

    def lower(expr: Expression) -> Expression | None:
        if isinstance(expr, BitSelect):
            info = target_info(expr.target)
            if info is None:
                return None
            return BitSelect(expr.target, _position(info, expr.index), loc=expr.loc)
        if isinstance(expr, RangeSelect):
            info = target_info(expr.target)
            if info is None:
                return None
            return RangeSelect(expr.target, _position(info, expr.msb), _position(info, expr.lsb), loc=expr.loc)
        if isinstance(expr, PartSelect):
            info = target_info(expr.target)
            if info is None:
                return None
            # Ascending `a[b +: w]` is bits b..b+w-1 with b leftmost (the
            # highest physical position): `[(R-b) -: w]`; `-:` likewise.
            # An offset descending range keeps the direction.
            direction = expr.direction
            if info[0] < info[1]:
                direction = "-:" if expr.direction == "+:" else "+:"
            return PartSelect(expr.target, _position(info, expr.base), expr.width, direction, loc=expr.loc)
        return None

    return lower


def _redeclare(decls: list, asc: dict[str, tuple[int, int, int]]) -> None:
    for decl in decls:
        info = asc.get(decl.name)
        if info is not None and decl.width is not None:
            left, right, _unpacked = info
            decl.width = Range(_index_literal(abs(right - left)), _index_literal(0))


def lower_ascending_ranges(module: Module, *, offsets: bool = False) -> Module:
    """Rewrite *module*'s ascending packed ranges, in place (see the module
    docstring). With *offsets*, descending ranges not ending at 0
    (``[15:8]``) become ``[w-1:0]`` too, for the DSL converter, whose
    signals are always ``[w-1:0]``."""
    from .elaborate import _build_param_env  # noqa: PLC0415

    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    module_decls: list[Port | Net | Variable] = [*module.ports, *module.nets, *module.variables]
    asc = _ascending_decls(module_decls, env, offsets)
    scopes = []
    tfs: list[FunctionDecl | TaskDecl] = [*module.functions, *module.tasks]
    for tf in tfs:
        local = [*tf.ports, *getattr(tf, "locals", ()), *getattr(tf, "local_vars", ())]
        local_names = {d.name for d in local}
        scope_asc = {k: v for k, v in asc.items() if k not in local_names}
        scope_asc.update(_ascending_decls(local, env, offsets))
        if getattr(tf, "return_range", None) is not None:
            # (a function's return variable has the function's name)
            scope_asc.pop(tf.name, None)
        scopes.append((tf, local, scope_asc))
    if not asc and not any(scope_asc for _tf, _local, scope_asc in scopes):
        return module
    seen: set[int] = set()
    lower = _make_lower(asc)
    for container in (module.always_blocks, module.initial_blocks, module.continuous_assigns):
        for node in container:
            _rewrite(node, lower, seen)
    for decl in module_decls:
        init = getattr(decl, "initial_value", None)
        if init is not None:
            _rewrite(decl, lower, seen)
            new = lower(init)
            if new is not None:
                setattr(decl, "initial_value", new)  # noqa: B010 (not every declaration kind has one)
    for tf, local, scope_asc in scopes:
        _rewrite(tf, _make_lower(scope_asc), seen)
        _redeclare(local, scope_asc)
    _redeclare(module_decls, asc)
    return module


def _memory_bases(decls: list, env: dict[str, int]) -> dict[str, list[int]]:
    """name -> per-unpacked-dimension low bound, for memories with any
    dimension not starting at 0 (``reg [7:0] m [1:4]``)."""
    out: dict[str, list[int]] = {}
    for decl in decls:
        unpacked = list(decl.dimensions)[getattr(decl, "packed_dim_count", 0) :]
        if not unpacked:
            continue
        bases: list[int] = []
        for dim in unpacked:
            left, right = const_int(dim.msb, env), const_int(dim.lsb, env)
            if left is None or right is None:
                bases = []
                break
            bases.append(min(left, right))
        if any(bases):
            out[decl.name] = bases
    return out


def _make_word_lower(bases: dict[str, list[int]]):
    def lower(expr: Expression) -> Expression | None:
        if not isinstance(expr, BitSelect):
            return None
        level = 0
        node = expr.target
        while isinstance(node, BitSelect):
            node = node.target
            level += 1
        name = _name(node)
        if name is None or name not in bases or level >= len(bases[name]) or not bases[name][level]:
            return None
        return BitSelect(expr.target, _offset(bases[name][level], expr.index), loc=expr.loc)

    return lower


def lower_memory_bases(module: Module) -> Module:
    """Rewrite memories whose unpacked dimensions don't start at 0 to start
    at 0, subtracting the base from every word index, in place.

    Every engine used a memory word index as the slot number and ignored
    the declared low bound: in ``reg [7:0] m [1:4]`` the word ``m[4]`` was
    out of range (x) on reference/vm/vm-fast, and compiled's natively
    compiled code and its reference-executor fallback disagreed, reading
    every word one off. Declared direction is kept (``[4:1]`` -> ``[3:0]``).
    Not handled: ``@address`` lines in a ``$readmem`` file (raw slot numbers).
    """
    from .elaborate import _build_param_env  # noqa: PLC0415

    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    module_decls: list[Port | Net | Variable] = [*module.ports, *module.nets, *module.variables]
    bases = _memory_bases(module_decls, env)
    scopes = []
    tfs: list[FunctionDecl | TaskDecl] = [*module.functions, *module.tasks]
    for tf in tfs:
        local = [*tf.ports, *getattr(tf, "locals", ()), *getattr(tf, "local_vars", ())]
        local_names = {d.name for d in local}
        scope_bases = {k: v for k, v in bases.items() if k not in local_names}
        scope_bases.update(_memory_bases(local, env))
        scopes.append((tf, local, scope_bases))
    if not bases and not any(sb for _tf, _local, sb in scopes):
        return module
    seen: set[int] = set()
    lower = _make_word_lower(bases)
    for container in (module.always_blocks, module.initial_blocks, module.continuous_assigns):
        for node in container:
            _rewrite(node, lower, seen)
    for tf, local, scope_bases in scopes:
        _rewrite(tf, _make_word_lower(scope_bases), seen)
        _rebase_env(local, scope_bases, env)
    _rebase_env(module_decls, bases, env)
    return module


def _chain(expr: BitSelect) -> tuple[str | None, list[Expression]]:
    """``m[i][j][k]`` -> ("m", [i, j, k])."""
    indices: list[Expression] = []
    node: Expression = expr
    while isinstance(node, BitSelect):
        indices.append(node.index)
        node = node.target
    return _name(node), indices[::-1]


def _flat_index(indices: list[Expression], sizes: list[int]) -> Expression:
    """Row-major ``((i0 * n1) + i1) * n2 + i2``, guarded per dimension: an
    out-of-range index must read x (and write nothing), not alias another
    row, so it maps to ``total`` -- one past the end."""
    total = 1
    for n in sizes:
        total *= n
    consts = [_const_index(i) for i in indices]
    if all(c is not None for c in consts):
        flat = 0
        for c, n in zip(consts, sizes, strict=True):
            if not 0 <= c < n:  # type: ignore[operator]
                return _index_literal(total)
            flat = flat * n + c  # type: ignore[operator]
        return _index_literal(flat)
    flat_expr: Expression = indices[0]
    guard: Expression | None = None
    for k, (index, n) in enumerate(zip(indices, sizes, strict=True)):
        if k:
            flat_expr = BinaryOp("+", BinaryOp("*", flat_expr, _index_literal(n)), index)
        in_range = BinaryOp("&&", BinaryOp(">=", index, _index_literal(0)), BinaryOp("<", index, _index_literal(n)))
        guard = in_range if guard is None else BinaryOp("&&", guard, in_range)
    assert guard is not None
    return TernaryOp(guard, flat_expr, _index_literal(total))


def flatten_unpacked_arrays(module: Module) -> Module:
    """Rewrite multi-dimensional unpacked arrays (``reg [7:0] m [0:1][0:2]``)
    as one-dimensional ones (``m [0:5]``, row-major), in place, every
    ``m[i][j]`` becoming ``m[i * 3 + j]``. Runs after `lower_memory_bases`
    (every dimension then starts at 0).

    Every engine sized such a memory as the product of its dimensions but
    indexed only the first: ``m[i][j]`` read bit ``j`` of word ``i``.
    A memory with a sub-array access (``m[i]`` alone) is left as it was.
    """
    from .elaborate import _build_param_env  # noqa: PLC0415

    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    shapes: dict[str, list[int]] = {}
    decls: list[Port | Net | Variable] = [*module.ports, *module.nets, *module.variables]
    tfs: list[FunctionDecl | TaskDecl] = [*module.functions, *module.tasks]
    for tf in tfs:
        decls.extend([*tf.ports, *getattr(tf, "locals", ()), *getattr(tf, "local_vars", ())])
    for decl in decls:
        unpacked = list(decl.dimensions)[getattr(decl, "packed_dim_count", 0) :]
        if len(unpacked) < 2:  # noqa: PLR2004
            continue
        sizes: list[int] = []
        for dim in unpacked:
            left, right = const_int(dim.msb, env), const_int(dim.lsb, env)
            if left is None or right is None or min(left, right) != 0:
                sizes = []
                break
            sizes.append(abs(left - right) + 1)
        if sizes:
            shapes[decl.name] = sizes
    if not shapes:
        return module

    # Count chain nodes per level: a sub-array access leaves a level-0 node
    # without a matching full-depth one.
    levels: dict[str, list[int]] = {name: [0] * len(sizes) for name, sizes in shapes.items()}

    def count(expr: Expression) -> None:  # (returns None: nothing replaced)
        if isinstance(expr, BitSelect):
            name, indices = _chain(expr)
            if name in levels and len(indices) <= len(levels[name]):
                levels[name][len(indices) - 1] += 1

    def visit(fn) -> None:
        seen: set[int] = set()
        for container in (
            module.always_blocks,
            module.initial_blocks,
            module.continuous_assigns,
            module.functions,
            module.tasks,
        ):
            for item in container:
                _rewrite(item, fn, seen)
        for decl in decls:
            if getattr(decl, "initial_value", None) is not None:
                _rewrite(decl, fn, seen)

    visit(count)
    flat = {name: sizes for name, sizes in shapes.items() if len(set(levels[name])) <= 1}
    if not flat:
        return module

    def lower(expr: Expression) -> Expression | None:
        if not isinstance(expr, BitSelect):
            return None
        name, indices = _chain(expr)
        sizes = flat.get(name) if name is not None else None
        if sizes is None or len(indices) != len(sizes):
            return None
        root: Expression = expr
        for _ in indices:
            root = root.target  # type: ignore[attr-defined]
        return BitSelect(root, _flat_index(indices, sizes), loc=expr.loc)

    visit(lower)
    for decl in decls:
        shape = flat.get(decl.name)
        if shape is None:
            continue
        first = getattr(decl, "packed_dim_count", 0)
        total = 1
        for n in shape:
            total *= n
        decl.dimensions[first:] = [Range(_index_literal(0), _index_literal(total - 1))]
    return module


def _rebase_env(decls: list, bases: dict[str, list[int]], env: dict[str, int]) -> None:
    for decl in decls:
        lows = bases.get(decl.name)
        if lows is None:
            continue
        first = getattr(decl, "packed_dim_count", 0)
        for k, low in enumerate(lows):
            dim = decl.dimensions[first + k]
            left, right = const_int(dim.msb, env), const_int(dim.lsb, env)
            if left is None or right is None:
                continue
            decl.dimensions[first + k] = Range(_index_literal(left - low), _index_literal(right - low))
