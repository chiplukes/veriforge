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


def _flip(right: int, index: Expression) -> Expression:
    """``right - index``, folded when constant."""
    if isinstance(index, Literal) and not index.is_x and not getattr(index, "is_z", False):
        try:
            return Literal(right - int(index.value), width=32)
        except (TypeError, ValueError):
            pass
    return BinaryOp("-", Literal(right, width=32), index)


def _ascending_decls(decls: list, env: dict[str, int]) -> dict[str, tuple[int, int, int]]:
    """name -> (left, right, unpacked dims) for ascending single-packed-dim
    declarations."""
    out: dict[str, tuple[int, int, int]] = {}
    for decl in decls:
        width = getattr(decl, "width", None)
        if width is None or getattr(decl, "packed_dim_count", 0) > 1:
            continue
        left, right = const_int(width.msb, env), const_int(width.lsb, env)
        if left is None or right is None or left >= right:
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
            return BitSelect(expr.target, _flip(info[1], expr.index), loc=expr.loc)
        if isinstance(expr, RangeSelect):
            info = target_info(expr.target)
            if info is None:
                return None
            right = info[1]
            return RangeSelect(expr.target, _flip(right, expr.msb), _flip(right, expr.lsb), loc=expr.loc)
        if isinstance(expr, PartSelect):
            info = target_info(expr.target)
            if info is None:
                return None
            # Ascending `a[b +: w]` is bits b..b+w-1 with b leftmost (the
            # highest physical position): `[(R-b) -: w]`; `-:` likewise.
            direction = "-:" if expr.direction == "+:" else "+:"
            return PartSelect(expr.target, _flip(info[1], expr.base), expr.width, direction, loc=expr.loc)
        return None

    return lower


def _redeclare(decls: list, asc: dict[str, tuple[int, int, int]]) -> None:
    for decl in decls:
        info = asc.get(decl.name)
        if info is not None and decl.width is not None:
            left, right, _unpacked = info
            decl.width = Range(Literal(right - left, width=32), Literal(0, width=32))


def lower_ascending_ranges(module: Module) -> Module:
    """Rewrite *module*'s ascending packed ranges, in place (see the module
    docstring)."""
    from .elaborate import _build_param_env  # noqa: PLC0415

    env = {k: v for k, v in _build_param_env(module).items() if isinstance(v, int)}
    module_decls: list[Port | Net | Variable] = [*module.ports, *module.nets, *module.variables]
    asc = _ascending_decls(module_decls, env)
    scopes = []
    tfs: list[FunctionDecl | TaskDecl] = [*module.functions, *module.tasks]
    for tf in tfs:
        local = [*tf.ports, *getattr(tf, "locals", ()), *getattr(tf, "local_vars", ())]
        local_names = {d.name for d in local}
        scope_asc = {k: v for k, v in asc.items() if k not in local_names}
        scope_asc.update(_ascending_decls(local, env))
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
