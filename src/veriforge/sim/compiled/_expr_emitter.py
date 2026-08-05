"""Expression codegen emitter mixin for CythonCodegen.

Contains _emit_expr, _emit_py_expr, _emit_mask_expr, _emit_binary, _emit_unary,
_emit_concat, _emit_replication, _emit_assignment_pattern, _emit_func_call,
_expr_width, _emit_index_expr, _emit_mask_expr, _walk_signals, and helpers.
CythonCodegen inherits from _ExprEmitterMixin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from veriforge.model.expressions import (
    AssignmentPattern,
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    Mintypmax,
    PartSelect,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from veriforge.semantics import range_width as _range_width
from veriforge.sim.compiled._codegen_utils import (
    _WORD_BITS,
    _cy_lit,
    _cy_hex,
    _cy_u64_hex,
    _const_int,
    _safe_ident,
    _BINARY_VALUE_OP,
    _COMPARISON_OPS,
    _NATURAL_WIDTH_OPS,
    _UNARY_PREFIX,
    _REDUCTION_OPS,
)
from veriforge.sim.value import Value

if TYPE_CHECKING:
    from veriforge.model.design import Module
    from veriforge.model.expressions import Range
    from veriforge.model.variables import Variable

_FIXED_SELF_DETERMINED_UNOPS = _REDUCTION_OPS | frozenset({"!"})


def _is_fixed_self_determined(expr: Expression) -> bool:
    """True when *expr*'s own result width is ALWAYS fixed at 1 bit
    (IEEE 1364-2005 Table 5-22's self-determined operators: comparisons,
    &&/||, reduction ops, !) regardless of any enclosing context.

    Used by `_emit_unary`'s `~`/unary `-` branch: such an operand must
    never be widened to match `~`/`-`'s enclosing context width BEFORE the
    operator runs -- unlike a regular signal or an arithmetic operand
    (where extension commutes with the operation), extending a bitwise-
    complement or negation's operand changes which bits get flipped/
    negated. Only `~`/`-`'s own RESULT should be extended, after the
    operator runs at the operand's fixed width. Mirrors the identical
    helper in `sim/evaluator.py`/`sim/vm/compiler.py`.
    """
    etype = type(expr)
    if etype is BinaryOp:
        return expr.op in _COMPARISON_OPS
    if etype is UnaryOp:
        return expr.op in _FIXED_SELF_DETERMINED_UNOPS
    return False


class _ExprEmitterMixin:
    """Mixin providing expression and signal-walk emitters for CythonCodegen."""

    __slots__ = ()

    def _emit_signal_init_lines(self, lines: list[str], sid: int, val: int, mask: int) -> None:
        """Emit initializer lines for a scalar or wide signal."""
        width = self._signal_widths[sid]
        if width > _WORD_BITS:
            words = (width + _WORD_BITS - 1) // _WORD_BITS
            offset_expr = f"self.ctx.wide_offset[{sid}]"
            for word_index in range(words):
                word_lsb = word_index * _WORD_BITS
                remaining = width - word_lsb
                word_width = min(_WORD_BITS, remaining)
                word_mask = (1 << word_width) - 1
                word_val = (val >> word_lsb) & word_mask
                word_m = (mask >> word_lsb) & word_mask
                lines.append(f"        self.ctx.wide_val[{offset_expr} + {word_index}] = {_cy_u64_hex(word_val)}")
                lines.append(f"        self.ctx.wide_mask[{offset_expr} + {word_index}] = {_cy_u64_hex(word_m)}")
            lines.append(f"        self.ctx.val[{sid}] = <long long>self.ctx.wide_val[{offset_expr}]")
            lines.append(f"        self.ctx.mask[{sid}] = <long long>self.ctx.wide_mask[{offset_expr}]")
        else:
            lines.append(f"        self.ctx.val[{sid}] = {val}")
            lines.append(f"        self.ctx.mask[{sid}] = {mask}")
        lines.append(f"        self.ctx.dirty[{sid}] = 1")

    # Expression codegen

    def _emit_expr_mask(self, expr: Expression) -> str:
        """Return a Cython expression for the x/z mask of expr."""
        from veriforge.sim.value import Value as _Value  # noqa: PLC0415

        etype = type(expr)
        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            if sid is not None:
                return f"c.mask[{sid}]"
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is not None:
                if struct_info[0] == "signal":
                    base_sid, offset, field_width = struct_info[1], struct_info[3], struct_info[4]
                    if offset >= _WORD_BITS or offset + field_width > _WORD_BITS:
                        return self._emit_signal_slice_expr(base_sid, str(offset), field_width, mask=True)
                    wmask = _cy_lit((1 << field_width) - 1)
                    return f"((c.mask[{base_sid}] >> {offset}) & {wmask})"
                index_expr = self._emit_struct_storage_index_expr(struct_info[2])
                if index_expr is None:
                    return "0"
                return self._emit_mem_slice_expr(
                    struct_info[1],
                    index_expr,
                    struct_info[3],
                    struct_info[4],
                    mask=True,
                    elem_width=self._mem_info[struct_info[1]][0],
                )
            return "0"
        if etype is Literal:
            if expr.original_text:
                v = _Value.from_verilog(expr.original_text)
                return _cy_lit(v.mask)
            if expr.is_x or expr.is_z:
                w = expr.width if expr.width else 32
                return _cy_lit((1 << w) - 1)
            return "0"
        if etype is BitSelect:
            mem_access = self._resolve_memory_element_access(expr)
            if mem_access is not None:
                mid, idx, _name, _indices = mem_access
                return f"c.mem_{mid}_mask[({idx})]"
            if isinstance(expr.target, Identifier):
                tname = expr.target.name
                if expr.target.hierarchy:
                    tname = ".".join(expr.target.hierarchy) + "." + tname
                sid = self._signal_map.get(tname)
                if sid is not None:
                    index = self._emit_index_expr(expr.index)
                    base = self._signal_bases.get(tname, 0)
                    if base != 0:
                        index = f"(({index}) - {base})"
                    return self._emit_signal_slice_expr(sid, index, 1, mask=True)
                struct_info = self._resolve_struct_access(tname)
                if struct_info is not None:
                    base_sid, offset, _field_width = struct_info
                    index = self._emit_index_expr(expr.index)
                    return self._emit_signal_slice_expr(base_sid, f"{offset} + ({index})", 1, mask=True)
            return "0"
        if etype is RangeSelect:
            # Mirrors `_emit_expr`'s RangeSelect handling (the VALUE side)
            # -- this node type was previously entirely unhandled here,
            # silently falling through to the "0" (always-known) default
            # below regardless of the sliced signal's actual mask. Any
            # part-select selector or operand reaching a mask computation
            # (e.g. a `casex`/`casez` selector, which needs its OWN mask
            # to correctly wildcard-match x/z bits) therefore always read
            # as fully known, even when the underlying signal was fully
            # x. Confirmed against Icarus (cross-engine) for
            # `casex (a5[39:2]) 37'b1z11xx00...: ...` with `a5` fully x:
            # the selector's mask hard-coded to 0 instead of the real
            # (fully-x) mask, making the wildcard item wrongly fail to
            # match and falling through to `default`.
            if isinstance(expr.target, Identifier):
                tname = self._identifier_name(expr.target)
                sid = self._signal_map.get(tname)
                sig_base = self._signal_bases.get(tname, 0)
                if sid is not None:
                    if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                        msb_val = int(expr.msb.value) - sig_base
                        lsb_val = int(expr.lsb.value) - sig_base
                        sel_w = msb_val - lsb_val + 1
                        return self._emit_signal_slice_expr(sid, lsb_val, sel_w, mask=True)
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    if sig_base != 0:
                        msb = f"(({msb}) - {sig_base})"
                        lsb = f"(({lsb}) - {sig_base})"
                    sel_w_expr = f"(({msb}) - ({lsb}) + 1)"
                    return self._emit_signal_slice_expr(sid, f"({lsb})", sel_w_expr, mask=True)
                struct_info = self._resolve_struct_access(tname)
                if struct_info is not None:
                    base_sid, offset, _field_width = struct_info
                    if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                        msb_val = int(expr.msb.value)
                        lsb_val = int(expr.lsb.value)
                        sel_w = msb_val - lsb_val + 1
                        return self._emit_signal_slice_expr(base_sid, f"{offset} + {lsb_val}", sel_w, mask=True)
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    sel_w_expr = f"(({msb}) - ({lsb}) + 1)"
                    return self._emit_signal_slice_expr(base_sid, f"{offset} + ({lsb})", sel_w_expr, mask=True)
            return "0"
        return "0"

    def _shift_amount_width(self, right: Expression) -> int:
        """Return the width to request when evaluating a shift's COUNT
        operand as a self-determined (IEEE 1364-2005 Table 5-22 / SS5.6)
        expression.

        Normally this is just the operand's own `_expr_width` -- passing
        anything wider risks a nested context-determined operator (e.g.
        `~` in `~(cond ? a : b)`) wrongly treating that width as its own
        enclosing context and widening its operand before the operator
        runs. But `_expr_width`'s `+`/`-` case deliberately uses a
        max-of-operands rule with no headroom for the carry bit (by
        design -- see its own docstring), which is normally fine because
        callers elsewhere pass a wider *enclosing* context width anyway;
        for a shift amount evaluated at its OWN tight self-width, that
        missing bit is real: `_emit_binary`'s `+`/`-` case masks its
        result to exactly the requested width, so a genuine carry-out
        (e.g. `1 + 1023` needing 11 bits, not `max(1, 10) = 10`) gets
        silently truncated away, corrupting the shift amount. Confirmed
        against Icarus for `a2 >> ((^(a1 ? a5[4:3] : a5)) + a3[25:16])`.
        A single `max(lw, rw) + 1` is enough for one `+`/`-` node (adding
        an N-bit and an M<=N-bit number can carry out by at most 1 bit);
        deeper chains of `+`/`-` could in principle need more, but that's
        the same pre-existing, documented limitation of `_expr_width`
        itself, not something this shift-specific helper attempts to fix.
        """
        if isinstance(right, BinaryOp) and right.op in ("+", "-"):
            return self._expr_width(right) + 1
        return self._expr_width(right)

    def _emit_signed_widen(self, val_expr: str, sid: int, sel_width: int, context_width: int) -> str:
        """No-op: a bit-select/range-select/part-select is always unsigned
        (IEEE 1364-2005 §5.5.1) regardless of the sliced signal's own
        declared signedness -- kept as a passthrough so call sites don't
        need to change.
        """
        del sid, sel_width, context_width
        return val_expr

    def _emit_expr(self, expr: Expression, width: int, signed_override: bool | None = None) -> str:  # noqa: PLR0911, PLR0912
        """Return a Cython value expression string for *expr*.

        *width* is the context width used for masking arithmetic results.
        *signed_override*, when not ``None``, forces sign- (True) or zero-
        (False) extension of a narrower Identifier/UnaryOp(~,+,-)/BinaryOp
        (arithmetic) result, overriding that node's own declared/computed
        signedness -- used by TernaryOp to apply its own combined
        signedness (IEEE 1364-2005 §5.5.1) to whichever branch is
        evaluated, since a branch that is itself a context-determined
        operator needs its *operand* extended using the override before
        the operator runs, not just its self-determined-width result
        wrapped in `_sign_ext` afterward (those are not equivalent).
        """
        etype = type(expr)

        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            if sid is not None:
                sig_width = self._signal_widths[sid]
                val = f"c.val[{sid}]"
                eff_signed = (
                    signed_override
                    if signed_override is not None
                    else (sid < len(self._signal_signed) and self._signal_signed[sid])
                )
                if width > sig_width and eff_signed:
                    val = f"_sign_ext({val}, {sig_width})"
                return val
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is not None:
                if struct_info[0] == "signal":
                    base_sid, offset, field_width = struct_info[1], struct_info[3], struct_info[4]
                    extract_width = (
                        field_width
                        if field_width <= _WORD_BITS
                        else min(field_width, width if 0 < width <= _WORD_BITS else _WORD_BITS)
                    )
                    if offset >= _WORD_BITS or offset + field_width > _WORD_BITS:
                        return self._emit_signal_slice_expr(base_sid, str(offset), extract_width)
                    wmask = _cy_lit((1 << extract_width) - 1)
                    return f"((c.val[{base_sid}] >> {offset}) & {wmask})"
                index_expr = self._emit_struct_storage_index_expr(struct_info[2])
                if index_expr is None:
                    return "0"
                return self._emit_mem_slice_expr(
                    struct_info[1],
                    index_expr,
                    struct_info[3],
                    struct_info[4],
                    elem_width=self._mem_info[struct_info[1]][0],
                )
            # Local loop variable (e.g. for-loop iterator)
            lv = self._local_vars.get(expr.name)
            if lv is not None:
                return lv
            return "0"

        if etype is Literal:
            lit_val = 0
            if expr.original_text:
                try:
                    lit_val = Value.from_verilog(expr.original_text).val
                except ValueError:
                    pass
                else:
                    return _cy_lit(lit_val)
            if isinstance(expr.value, (int, float)):
                lit_val = int(expr.value)
            elif isinstance(expr.value, str):
                if expr.value.strip():
                    try:
                        lit_val = int(expr.value.strip(), 0)
                    except (ValueError, TypeError):
                        lit_val = 0
            return _cy_lit(lit_val)

        if etype is BinaryOp:
            cached_v = self._et_node_vals.get(id(expr))
            if cached_v is not None:
                return cached_v
            return self._emit_binary(expr, width, signed_override)

        if etype is UnaryOp:
            return self._emit_unary(expr, width, signed_override)

        if etype is TernaryOp:
            # Check if this node's value was already hoisted to a named temp.
            # Both value and mask are cached together (see hoist block below and
            # the symmetric block in _emit_mask_expr), so whichever emitter runs
            # first for a given node caches both — preventing 2^k recursion in
            # right-recursive TernaryOp chains where _emit_ternary_value_mask_exprs
            # calls both _emit_expr and _emit_mask_expr on the same false branch.
            cached_v = self._et_node_vals.get(id(expr))
            if cached_v is not None:
                return cached_v
            ternary_exprs = self._emit_ternary_value_mask_exprs(expr, width, py=False, signed_override=signed_override)
            assert ternary_exprs is not None
            value_str, mask_str = ternary_exprs
            if self._et_pending is not None:
                n = self._et_count
                self._et_count += 1
                self._et_pending.append(f"cdef long long _et{n}_v = {value_str}")
                self._et_pending.append(f"cdef long long _et{n}_m = {mask_str}")
                self._et_node_vals[id(expr)] = f"_et{n}_v"
                self._et_node_masks[id(expr)] = f"_et{n}_m"
                return f"_et{n}_v"
            return value_str

        if etype is Concatenation:
            return self._emit_concat(expr, width)

        if etype is Replication:
            return self._emit_replication(expr)

        if etype is BitSelect:
            mem_access = self._resolve_memory_element_access(expr)
            if mem_access is not None:
                mid, idx, _name, _indices = mem_access
                return f"c.mem_{mid}_val[({idx})]"
            if isinstance(expr.target, Identifier):
                tname = self._identifier_name(expr.target)
                sid = self._signal_map.get(tname)
            else:
                tname = None
                sid = None
            if sid is not None:
                index = self._emit_index_expr(expr.index)
                base = self._signal_bases.get(tname, 0)
                if base != 0:
                    index = f"(({index}) - {base})"
                return self._emit_signal_slice_expr(sid, index, 1)
            if isinstance(expr.target, Identifier):
                struct_info = self._resolve_struct_access(tname)
                if struct_info is not None:
                    base_sid, offset, _field_width = struct_info
                    index = self._emit_index_expr(expr.index)
                    return self._emit_signal_slice_expr(base_sid, f"{offset} + ({index})", 1)
                storage_info = self._resolve_struct_storage_access(tname)
                if storage_info is not None and storage_info[0] == "memory":
                    index_expr = self._emit_struct_storage_index_expr(storage_info[2])
                    if index_expr is None:
                        return "0"
                    index = self._emit_index_expr(expr.index)
                    return self._emit_mem_slice_expr(
                        storage_info[1],
                        index_expr,
                        f"{storage_info[3]} + ({index})",
                        1,
                        elem_width=self._mem_info[storage_info[1]][0],
                    )
            plain_mem_target = self._resolve_memory_element_access(expr.target)
            if plain_mem_target is not None:
                mid, idx, name, _indices = plain_mem_target
                elem_width = self._mem_info[mid][0]
                if elem_width > _WORD_BITS:
                    index = self._emit_index_expr(expr.index)
                    bit_base = self._memory_bases.get(name, 0)
                    if bit_base != 0:
                        index = f"(({index}) - {bit_base})"
                    return f"(_wmem{mid}_extract_val(c, ({idx}), {index}) & 1)"
            target = self._emit_expr(expr.target, self._expr_width(expr.target))
            index = self._emit_index_expr(expr.index)
            base = self._select_base(expr.target)
            if base != 0:
                index = f"(({index}) - {base})"
            return f"(({target}) >> ({index})) & 1"

        if etype is RangeSelect:
            if isinstance(expr.target, Identifier):
                tname = self._identifier_name(expr.target)
                sid = self._signal_map.get(tname)
                sig_base = self._signal_bases.get(tname, 0)
                if sid is not None:
                    if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                        msb_val = int(expr.msb.value) - sig_base
                        lsb_val = int(expr.lsb.value) - sig_base
                        sel_w = msb_val - lsb_val + 1
                        result = self._emit_signal_slice_expr(sid, lsb_val, sel_w)
                        return self._emit_signed_widen(result, sid, sel_w, width)
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    if sig_base != 0:
                        msb = f"(({msb}) - {sig_base})"
                        lsb = f"(({lsb}) - {sig_base})"
                    sel_w_expr = f"(({msb}) - ({lsb}) + 1)"
                    result = self._emit_signal_slice_expr(sid, f"({lsb})", sel_w_expr)
                    if sid < len(self._signal_signed) and self._signal_signed[sid]:
                        result = f"_sign_ext({result}, {sel_w_expr})"
                    return result
                struct_info = self._resolve_struct_access(tname)
                if struct_info is not None:
                    base_sid, offset, _field_width = struct_info
                    if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                        msb_val = int(expr.msb.value)
                        lsb_val = int(expr.lsb.value)
                        sel_w = msb_val - lsb_val + 1
                        result = self._emit_signal_slice_expr(base_sid, f"{offset} + {lsb_val}", sel_w)
                        return self._emit_signed_widen(result, base_sid, sel_w, width)
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    sel_w_expr = f"(({msb}) - ({lsb}) + 1)"
                    result = self._emit_signal_slice_expr(base_sid, f"{offset} + ({lsb})", sel_w_expr)
                    if base_sid < len(self._signal_signed) and self._signal_signed[base_sid]:
                        result = f"_sign_ext({result}, {sel_w_expr})"
                    return result
                storage_info = self._resolve_struct_storage_access(tname)
                if storage_info is not None and storage_info[0] == "memory":
                    index_expr = self._emit_struct_storage_index_expr(storage_info[2])
                    if index_expr is None:
                        return "0"
                    mid = storage_info[1]
                    offset = storage_info[3]
                    if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                        msb_val = int(expr.msb.value)
                        lsb_val = int(expr.lsb.value)
                        sel_w = msb_val - lsb_val + 1
                        return self._emit_mem_slice_expr(
                            mid, index_expr, offset + lsb_val, sel_w, elem_width=self._mem_info[mid][0]
                        )
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    sel_w = f"(({msb}) - ({lsb}) + 1)"
                    if width > _WORD_BITS:
                        return self._emit_wide_mem_dynamic_slice_expr(
                            mid,
                            index_expr,
                            f"{offset} + ({lsb})",
                            sel_w,
                        )
                    return f"(_wmem{mid}_extract_val(c, ({index_expr}), {offset} + ({lsb})) & _word_mask64({sel_w}))"
            plain_mem_target = self._resolve_memory_element_access(expr.target)
            if plain_mem_target is not None:
                mid, idx, name, _indices = plain_mem_target
                elem_width = self._mem_info[mid][0]
                if elem_width > _WORD_BITS:
                    bit_base = self._memory_bases.get(name, 0)
                    if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                        msb_val = int(expr.msb.value) - bit_base
                        lsb_val = int(expr.lsb.value) - bit_base
                        sel_w = msb_val - lsb_val + 1
                        return self._emit_mem_slice_expr(mid, idx, lsb_val, sel_w, elem_width=elem_width)
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    if bit_base != 0:
                        msb = f"(({msb}) - {bit_base})"
                        lsb = f"(({lsb}) - {bit_base})"
                    sel_w = f"(({msb}) - ({lsb}) + 1)"
                    if width > _WORD_BITS:
                        return self._emit_wide_mem_dynamic_slice_expr(mid, idx, f"({lsb})", sel_w)
                    return f"(_wmem{mid}_extract_val(c, ({idx}), ({lsb})) & _word_mask64({sel_w}))"
            target = self._emit_expr(expr.target, self._expr_width(expr.target))
            sig_base = self._select_base(expr.target)
            if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                msb_val = int(expr.msb.value) - sig_base
                lsb_val = int(expr.lsb.value) - sig_base
                sel_w = msb_val - lsb_val + 1
                mask_hex = _cy_hex((1 << sel_w) - 1)
                result = f"(({target}) >> {lsb_val}) & {mask_hex}"
                if self._expr_signed(expr) and width > sel_w:
                    result = f"_sign_ext({result}, {sel_w})"
                return result
            msb = self._emit_expr(expr.msb, 32)
            lsb = self._emit_expr(expr.lsb, 32)
            if sig_base != 0:
                msb = f"(({msb}) - {sig_base})"
                lsb = f"(({lsb}) - {sig_base})"
            sel_w_expr = f"(({msb}) - ({lsb}) + 1)"
            result = f"(({target}) >> ({lsb})) & wmask({sel_w_expr})"
            if self._expr_signed(expr):
                result = f"_sign_ext({result}, {sel_w_expr})"
            return result

        if etype is PartSelect:
            if isinstance(expr.target, Identifier):
                tname = self._identifier_name(expr.target)
                sid = self._signal_map.get(tname)
                sig_base = self._signal_bases.get(tname, 0)
                if sid is not None:
                    base = self._emit_expr(expr.base, 32)
                    if isinstance(expr.width, Literal):
                        width_expr = str(int(expr.width.value))
                    else:
                        width_expr = self._emit_expr(expr.width, 32)
                    if sig_base != 0:
                        base = f"(({base}) - {sig_base})"
                    if expr.direction == "+:":
                        lsb_expr = base
                    else:
                        lsb_expr = f"({base}) - ({width_expr}) + 1"
                    # A part-select is always unsigned (IEEE 1364-2005
                    # §5.5.1) regardless of the sliced signal's own
                    # declared signedness -- no sign-extension here.
                    return self._emit_signal_slice_expr(sid, lsb_expr, width_expr)
                struct_info = self._resolve_struct_access(tname)
                if struct_info is not None:
                    base_sid, offset, _field_width = struct_info
                    base = self._emit_expr(expr.base, 32)
                    if isinstance(expr.width, Literal):
                        width_expr = str(int(expr.width.value))
                    else:
                        width_expr = self._emit_expr(expr.width, 32)
                    if expr.direction == "+:":
                        lsb_expr = f"{offset} + ({base})"
                    else:
                        lsb_expr = f"{offset} + ({base}) - ({width_expr}) + 1"
                    # Always unsigned -- see the comment in the plain-signal
                    # branch above.
                    return self._emit_signal_slice_expr(base_sid, lsb_expr, width_expr)
                storage_info = self._resolve_struct_storage_access(tname)
                if storage_info is not None and storage_info[0] == "memory":
                    index_expr = self._emit_struct_storage_index_expr(storage_info[2])
                    if index_expr is None:
                        return "0"
                    mid = storage_info[1]
                    offset = storage_info[3]
                    base = self._emit_expr(expr.base, 32)
                    if isinstance(expr.width, Literal):
                        width_expr = str(int(expr.width.value))
                    else:
                        width_expr = self._emit_expr(expr.width, 32)
                    if expr.direction == "+:":
                        lsb_expr = f"{offset} + ({base})"
                    else:
                        lsb_expr = f"{offset} + ({base}) - ({width_expr}) + 1"
                    width_arg: int | str = int(expr.width.value) if isinstance(expr.width, Literal) else width_expr
                    if width > _WORD_BITS:
                        return self._emit_wide_mem_dynamic_slice_expr(mid, index_expr, lsb_expr, width_arg)
                    return f"(_wmem{mid}_extract_val(c, ({index_expr}), {lsb_expr}) & _word_mask64({width_expr}))"
            plain_mem_target = self._resolve_memory_element_access(expr.target)
            if plain_mem_target is not None:
                mid, idx, name, _indices = plain_mem_target
                elem_width = self._mem_info[mid][0]
                if elem_width > _WORD_BITS:
                    base = self._emit_expr(expr.base, 32)
                    bit_base = self._memory_bases.get(name, 0)
                    if bit_base != 0:
                        base = f"(({base}) - {bit_base})"
                    if isinstance(expr.width, Literal):
                        width_expr = str(int(expr.width.value))
                    else:
                        width_expr = self._emit_expr(expr.width, 32)
                    if expr.direction == "+:":
                        lsb_expr = base
                    else:
                        lsb_expr = f"({base}) - ({width_expr}) + 1"
                    width_arg: int | str = int(expr.width.value) if isinstance(expr.width, Literal) else width_expr
                    if width > _WORD_BITS:
                        return self._emit_wide_mem_dynamic_slice_expr(mid, idx, lsb_expr, width_arg)
                    return f"(_wmem{mid}_extract_val(c, ({idx}), {lsb_expr}) & _word_mask64({width_expr}))"
            target = self._emit_expr(expr.target, self._expr_width(expr.target))
            base = self._emit_expr(expr.base, 32)
            sig_base = self._select_base(expr.target)
            if sig_base != 0:
                base = f"(({base}) - {sig_base})"
            if isinstance(expr.width, Literal):
                pw = int(expr.width.value)
                mask_hex = _cy_hex((1 << pw) - 1)
                sel_w = pw
            else:
                width_expr = self._emit_expr(expr.width, 32)
                mask_hex = f"wmask({width_expr})"
                sel_w = None
            if expr.direction == "+:":
                result = f"(({target}) >> ({base})) & {mask_hex}"
            else:
                result = f"(({target}) >> (({base}) - ({self._emit_expr(expr.width, 32)}) + 1)) & {mask_hex}"
            if sel_w is not None:
                if self._expr_signed(expr) and width > sel_w:
                    result = f"_sign_ext({result}, {sel_w})"
            elif self._expr_signed(expr):
                result = f"_sign_ext({result}, {width_expr})"
            return result

        if etype is FunctionCall:
            return self._emit_func_call(expr, width)

        if etype is StringLiteral:
            val = 0
            for ch in expr.value:
                val = (val << 8) | ord(ch)
            return _cy_lit(val)

        if etype is Mintypmax:
            return self._emit_expr(expr.typ_val, width)

        if etype is AssignmentPattern:
            return self._emit_assignment_pattern(expr, width)

        return "0"

    @staticmethod
    def _emit_py_width_mask(width: int) -> str:
        return f"((((<object>1) << {width})) - 1)"

    def _emit_ternary_value_mask_exprs(
        self, expr: TernaryOp, width: int, *, py: bool, signed_override: bool | None = None
    ) -> tuple[str, str] | None:
        # The condition is self-determined (IEEE 1364-2005 Table 5-22): it
        # must be evaluated at its OWN natural width, not forced down to 1
        # bit. Most node types (comparisons, reductions, `!`) are already
        # self-determined 1-bit results regardless of the width passed in,
        # so passing 1 "worked" for those by coincidence -- but a condition
        # that is itself a further TernaryOp/Concatenation/Replication uses
        # the incoming width to size its OWN internal merge/shift
        # computation (e.g. a nested ternary condition's `wmask(width)`
        # truncating its ambiguous-branch merge down to 1 bit, corrupting
        # every bit but the LSB). Confirmed wrong against Icarus for
        # `a0 * (((|a1[0]) ? a4 : {3{a0}}) ? a3 : (~|a5[58:17]))`.
        cond_w = self._expr_width(expr.condition)
        cond = self._emit_expr(expr.condition, cond_w)
        # IEEE 1364-2005 §5.5.1: the ternary's OWN combined signedness
        # (signed only if BOTH branches are signed) governs sign- vs
        # zero-extension of whichever branch is selected -- not each
        # branch's own individual signedness. This is threaded into the
        # branch's own evaluation as `signed_override` (forced True/False,
        # never None -- None would mean "fall back to the branch's own
        # signedness", exactly the bug being fixed here), not applied as a
        # post-hoc `_sign_ext` wrap around a self-determined-width result:
        # a branch that is itself a context-determined operator (UnaryOp
        # ~/+/-, arithmetic BinaryOp, or a signed Identifier)
        # needs its *operand(s)* extended using the override before the
        # operator runs -- computing it self-determined then wrapping the
        # result afterward is not equivalent (e.g. `~a` where `a` is 1 bit:
        # self-determined-then-wrapped gives a 1-bit `~a` sign-extended,
        # which is just `a`'s own single bit replicated -- not the same
        # value as sign-extending `a` to the full width first and THEN
        # complementing).
        # `signed_override`, when set, is a decision already forced by an
        # even-further-out caller (e.g. a comparison's own combined-
        # signedness, or an enclosing ternary's own combined decision when
        # this TernaryOp is itself a branch of another ternary) -- it must
        # win over this ternary's OWN `_expr_signed` computation, exactly
        # like every other `signed_override` use in this file. Previously
        # ignored entirely (no parameter existed), so an outer forced
        # decision never reached a nested ternary. Confirmed wrong (cross-
        # engine, against the reference oracle) for `(a0 <= (a2 ? a0 :
        # a6))`: the comparison's own combined-signedness decision (both
        # `a0` and `a6` declared signed) never propagated into the
        # ternary's branch-selection, so the selected `a0` branch was
        # zero- rather than sign-extended to the ternary's 80-bit combined
        # width, corrupting the comparison.
        own_signed = signed_override if signed_override is not None else self._expr_signed(expr)
        tw = self._expr_width(expr.true_expr)
        fw = self._expr_width(expr.false_expr)
        if py:
            # The Python-bignum sub-emitter (_emit_py_expr/_emit_py_mask_expr,
            # used for elaboration-time evaluation, not the hot simulation
            # loop) does not yet support signed_override threading -- keep
            # the previous self-determined-width + post-hoc-wrap
            # approximation here. It is a real, documented residual gap
            # (see notes/known_issues.md) for a branch that is itself a
            # context-determined operator whose OWN signedness disagrees
            # with the ternary's combined signedness, evaluated in this
            # specific code path.
            true_expr = self._emit_py_expr(expr.true_expr, tw)
            false_expr = self._emit_py_expr(expr.false_expr, fw)
            cond_mask = self._emit_py_mask_expr(expr.condition, cond_w)
            true_mask = self._emit_py_mask_expr(expr.true_expr, tw)
            false_mask = self._emit_py_mask_expr(expr.false_expr, fw)
            width_mask = self._emit_py_width_mask(width)

            if own_signed:
                if width > tw and true_expr is not None:
                    true_expr = f"_sign_ext({true_expr}, {tw})"
                if width > fw and false_expr is not None:
                    false_expr = f"_sign_ext({false_expr}, {fw})"
        else:
            # `own_signed` is meant to govern WIDENING a branch's
            # independently-computed value up to `width` -- not to
            # override how a CONTEXT-DETERMINED arithmetic branch
            # (`+`/`-`/`*`) types ITS OWN operands. Those ops already
            # extend directly to whatever width they're asked for
            # (`op_width == width` here, always -- unlike `&`/`|`/`^`,
            # which have their own smaller natural width and a genuinely
            # separate later widening step), so there is no legitimate
            # "widen afterward" use for `signed_override` to serve here at
            # all -- forwarding it only reaches down into
            # `combined_override`/each operand's own extension decision
            # (mirrors `_emit_binary`'s identical per-operand logic),
            # silently overriding an operand's own declared type.
            # Division/modulus keep the ternary's override (their
            # dedicated `div_mod_override` computation is a deliberate,
            # separate, already-confirmed exception -- IEEE 1364-2005
            # SS5.5.1 genuinely requires a division's ENTIRE divisor
            # sub-expression read uniformly per its own combined
            # decision). Confirmed against Icarus (cross-engine) for
            # `cond ? a5 : ({3{{a5, a7, a0}}} - a2)` with `a5` unsigned
            # and `a2` a signed identifier: the ternary's own combined
            # type is unsigned (replication is always unsigned, so not
            # both branches are signed), and forwarding that into the
            # subtraction forced `a2` to zero- instead of sign-extend,
            # even though IEEE governs `a2`'s OWN extension by its own
            # declared type here, independent of the ternary.
            t_signed_override = (
                None if isinstance(expr.true_expr, BinaryOp) and expr.true_expr.op in ("+", "-", "*") else own_signed
            )
            f_signed_override = (
                None if isinstance(expr.false_expr, BinaryOp) and expr.false_expr.op in ("+", "-", "*") else own_signed
            )
            true_expr = self._emit_expr(expr.true_expr, width, t_signed_override)
            false_expr = self._emit_expr(expr.false_expr, width, f_signed_override)
            cond_mask = self._emit_mask_expr(expr.condition, cond_w)
            # `cond`/`cond_mask` (the latter just above) are only ever
            # consumed downstream (`cond_known1`/`cond_mask_zero`, below)
            # through a "reduce to one known-truth scalar" lens -- exactly
            # what `wide_logical_truth` computes. When the condition's own
            # self-determined width exceeds 64 bits, or it internally
            # computes wider than that (a nested TernaryOp/Concatenation/
            # Replication condition, or a reduction reading a wide signal),
            # `_emit_expr`/`_emit_mask_expr` above silently only produced
            # the LOW 64 bits -- this narrow (Cython hot-loop) path is the
            # only one affected; the `py` branch above already uses
            # arbitrary-precision Python-bignum evaluation and needs no
            # such substitution. Confirmed against Icarus for `(({a3[28:10],
            # (~^a4), a6} ? a4[54:20] : {3{(a6 != a6)}}) <= a0)`, whose
            # ternary condition is over 100 bits wide.
            wide_cond = self._emit_wide_truthy_to_value(expr.condition)
            if wide_cond is not None:
                cond, cond_mask = wide_cond
            # A `+`/`-`/`*`/`/`/`%` branch's VALUE was just computed
            # directly AT the outer `width` above (`op_width == width`
            # always for these ops in `_emit_binary`, per this function's
            # own comment a few dozen lines up: "those ops already extend
            # directly to whatever width they're asked for... unlike
            # `&`/`|`/`^`, which have their own smaller natural width and
            # a genuinely separate later widening step") -- its MASK query
            # must match that SAME width, not the branch's own self-
            # determined `tw`/`fw`, or the mask ends up bounded to a
            # NARROWER width than the value it's describing, silently
            # leaving the outer bits beyond that (definitely-x-or-not)
            # unaccounted for. This is independent of `own_signed`/
            # `t_signed_override` above (which only decides WHICH
            # signedness decision governs the branch's own internal
            # extension, not what OUTER width it was computed at in the
            # first place) -- `/`/`%` need it too despite keeping their
            # own `own_signed`-forced override, since they share the same
            # "compute directly at `width`" `op_width` rule as `+`/`-`/`*`.
            # Confirmed against Icarus (cross-engine, `vm`/`vm-fast`/
            # `reference` all already agreed) for `cond ? ((a >> b) *
            # $signed(a2)) : {2{a4[52]}}` with `a2` fully x: the `*`
            # branch's mask, queried at its own 63-bit self-width instead
            # of the ternary's 64-bit outer width, left bit 63 spuriously
            # looking definite (0) instead of x.
            true_mask_w = (
                width if isinstance(expr.true_expr, BinaryOp) and expr.true_expr.op in ("+", "-", "*", "/", "%") else tw
            )
            false_mask_w = (
                width
                if isinstance(expr.false_expr, BinaryOp) and expr.false_expr.op in ("+", "-", "*", "/", "%")
                else fw
            )
            true_mask = self._emit_mask_expr(expr.true_expr, true_mask_w)
            false_mask = self._emit_mask_expr(expr.false_expr, false_mask_w)
            # `_emit_mask_expr` has no `signed_override` parameter (unlike
            # `_emit_expr`), so `true_mask`/`false_mask` above are computed
            # at each branch's own self-width `tw`/`fw` ONLY -- they never
            # see the ternary's `own_signed` sign-extension that
            # `true_expr`/`false_expr` just received via `_emit_expr`'s
            # own internal `_sign_ext` call. When `own_signed` and the
            # branch is narrower than `width`, an ambiguous (masked) sign
            # bit's unknown-ness must propagate into every newly-filled
            # upper bit too, exactly like `_emit_mask_expr`'s dedicated
            # `$signed(...)` case above -- sign-extend the mask here to
            # match. (When NOT `own_signed`, the value was zero-extended,
            # and the mask's own upper bits are already correctly 0 by
            # the same "unsigned values keep upper native-register bits
            # zero" invariant used throughout this file -- no action
            # needed.) Confirmed wrong (cross-engine, against the
            # reference oracle) for `(a2 ? a0 : a4)` with `a0` a signed
            # 1-bit register value x and `a2` selecting it: the selected
            # branch's mask stayed 1-bit x instead of being sign-extended
            # to x across the full 64-bit ternary width, so the ambiguity
            # was silently dropped from the newly-filled upper bits.
            if own_signed:
                if width > tw:
                    true_mask = f"_sign_ext({true_mask}, {tw})"
                if width > fw:
                    false_mask = f"_sign_ext({false_mask}, {fw})"
            width_mask = f"wmask({width})"
        if true_expr is None or false_expr is None or cond_mask is None or true_mask is None or false_mask is None:
            return None
        known_mask = f"((~((({true_expr}) ^ ({false_expr})) | ({true_mask}) | ({false_mask}))) & {width_mask})"
        merged_value = f"(({true_expr}) & ({known_mask}))"
        merged_mask = f"(({width_mask}) ^ ({known_mask}))"
        # A known-1 bit anywhere in the condition makes it definitely true
        # regardless of unrelated x/z bits elsewhere (mirrors Value.reduce_or
        # / TernaryOp in sim/evaluator.py) -- checking `cond_mask` alone here
        # treated ANY x/z bit in the condition as fully ambiguous (triggering
        # the merge below) even when a known-1 bit elsewhere already
        # determined the outcome. Only "cond_mask == 0" (fully defined, and
        # since cond_known1 is false, definitely zero) selects the false
        # branch outright.
        #
        # `cond`/`cond_mask` must be masked to `cond_w` bits before this
        # check: `_emit_expr`'s raw C `long long` result is only meaningful
        # within its own `cond_w` bits -- e.g. `$signed(1'b1)` emits
        # `_sign_ext(1, 1)`, which is -1 (ALL 64 bits set) as a raw C value,
        # matching the natural C representation of "signed -1" but NOT
        # scoped to cond_w. Without masking, those spurious high bits
        # (cond_mask=0 there, so never excluded by `~cond_mask`) get read
        # as a bogus "known-1", forcing a branch to be selected outright
        # instead of correctly falling through to the ambiguous merge.
        # Confirmed against Icarus for
        # `-($signed((a7 == a2[4])) ? ... : ...)`.
        cond_bits = f"wmask({cond_w})"
        cond_known1 = f"((({cond}) & {cond_bits}) & ~(({cond_mask}) & {cond_bits}))"
        cond_mask_zero = f"((({cond_mask}) & {cond_bits}) == 0)"
        value_expr = (
            f"(({true_expr}) if ({cond_known1}) else (({false_expr}) if {cond_mask_zero} else ({merged_value})))"
        )
        mask_expr = f"(({true_mask}) if ({cond_known1}) else (({false_mask}) if {cond_mask_zero} else ({merged_mask})))"
        return value_expr, mask_expr

    def _emit_py_expr(self, expr: Expression, width: int) -> str | None:  # noqa: PLR0911
        etype = type(expr)

        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            if sid is not None:
                sig_width = self._signal_widths[sid]
                if width > sig_width and sid < len(self._signal_signed) and self._signal_signed[sid]:
                    mask = self._emit_py_width_mask(sig_width)
                    sign_bit = f"((<object>1) << {sig_width - 1})"
                    return f"(((_sig_py_unsigned(c, {sid}) & {mask}) ^ {sign_bit}) - {sign_bit})"
                return f"_sig_py_unsigned(c, {sid})"
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is not None:
                if struct_info[0] == "signal":
                    base_sid, offset, field_width = struct_info[1], struct_info[3], struct_info[4]
                    return f"((_sig_py_unsigned(c, {base_sid}) >> {offset}) & {self._emit_py_width_mask(field_width)})"
                index_expr = self._emit_struct_storage_index_expr(struct_info[2])
                if index_expr is None:
                    return None
                if self._mem_info[struct_info[1]][0] <= _WORD_BITS:
                    return self._emit_mem_slice_expr(
                        struct_info[1],
                        index_expr,
                        struct_info[3],
                        struct_info[4],
                        elem_width=self._mem_info[struct_info[1]][0],
                    )
                return f"_wmem{struct_info[1]}_py_extract_val(c, ({index_expr}), {struct_info[3]}, {struct_info[4]})"
            return None

        if etype is Literal:
            lit_val = 0
            if expr.original_text:
                try:
                    lit_val = Value.from_verilog(expr.original_text).val
                except ValueError:
                    pass
                else:
                    return str(lit_val)
            if isinstance(expr.value, (int, float)):
                lit_val = int(expr.value)
            elif isinstance(expr.value, str) and expr.value.strip():
                try:
                    lit_val = int(expr.value.strip(), 0)
                except (ValueError, TypeError):
                    lit_val = 0
            return str(lit_val)

        if etype is BitSelect:
            mem_access = self._resolve_memory_element_access(expr)
            if mem_access is not None:
                mid, idx, _name, _indices = mem_access
                elem_width = self._mem_info[mid][0]
                if elem_width <= _WORD_BITS:
                    return self._emit_mem_slice_expr(mid, idx, 0, elem_width, elem_width=elem_width)
                return f"_wmem{mid}_py_extract_val(c, ({idx}), 0, {elem_width})"
            return None

        if etype is FunctionCall:
            name = expr.name.lower()
            if name in {"$signed", "$unsigned"} and len(expr.arguments) == 1:
                return self._emit_py_expr(expr.arguments[0], self._expr_width(expr.arguments[0]))
            return None

        if etype is RangeSelect:
            target_width = self._expr_width(expr.target)
            target = self._emit_py_expr(expr.target, target_width)
            if target is None:
                return None
            sig_base = 0
            if isinstance(expr.target, Identifier):
                sig_base = self._signal_bases.get(expr.target.name, 0)
            if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                msb_val = int(expr.msb.value) - sig_base
                lsb_val = int(expr.lsb.value) - sig_base
                sel_w = msb_val - lsb_val + 1
                return f"((({target}) >> {lsb_val}) & {self._emit_py_width_mask(sel_w)})"
            msb = self._emit_expr(expr.msb, 32)
            lsb = self._emit_expr(expr.lsb, 32)
            if sig_base != 0:
                msb = f"(({msb}) - {sig_base})"
                lsb = f"(({lsb}) - {sig_base})"
            return f"((({target}) >> ({lsb})) & ((((<object>1) << (({msb}) - ({lsb}) + 1)) - 1)))"

        if etype is PartSelect:
            target_width = self._expr_width(expr.target)
            target = self._emit_py_expr(expr.target, target_width)
            if target is None:
                return None
            base = self._emit_expr(expr.base, 32)
            sig_base = 0
            if isinstance(expr.target, Identifier):
                sig_base = self._signal_bases.get(expr.target.name, 0)
                if sig_base != 0:
                    base = f"(({base}) - {sig_base})"
            if isinstance(expr.width, Literal):
                width_expr = str(int(expr.width.value))
            else:
                width_expr = self._emit_expr(expr.width, 32)
            mask_expr = f"((((<object>1) << ({width_expr})) - 1))"
            if expr.direction == "+:":
                return f"((({target}) >> ({base})) & {mask_expr})"
            return f"((({target}) >> (({base}) - ({width_expr}) + 1)) & {mask_expr})"

        if etype is Concatenation:
            parts = expr.parts
            widths = self._concat_eval_widths(parts, width)
            total_width = sum(widths)
            result_parts: list[str] = []
            shift = total_width
            for part, part_width in zip(parts, widths):
                shift -= part_width
                part_expr = self._emit_py_expr(part, part_width)
                if part_expr is None:
                    return None
                part_mask = self._emit_py_width_mask(part_width)
                packed = f"(({part_expr}) & {part_mask})"
                if shift > 0:
                    result_parts.append(f"(({packed}) << {shift})")
                else:
                    result_parts.append(packed)
            return "(" + " | ".join(result_parts) + ")" if result_parts else "0"

        if etype is Replication:
            if isinstance(expr.count, Literal):
                count = int(expr.count.value)
            else:
                resolved = _const_int(expr.count, self._param_env)
                if resolved is None:
                    return None
                count = resolved
            value_width = self._expr_width(expr.value)
            value_expr = self._emit_py_expr(expr.value, value_width)
            if value_expr is None:
                return None
            value_mask = self._emit_py_width_mask(value_width)
            packed = f"(({value_expr}) & {value_mask})"
            if count <= 1:
                return packed
            parts = []
            for i in range(count):
                shift = value_width * (count - 1 - i)
                if shift > 0:
                    parts.append(f"(({packed}) << {shift})")
                else:
                    parts.append(packed)
            return "(" + " | ".join(parts) + ")" if parts else "0"

        if etype is AssignmentPattern:
            return self._emit_py_assignment_pattern(expr, width)

        if etype is UnaryOp:
            operand_width = self._expr_width(expr.operand)
            operand = self._emit_py_expr(expr.operand, operand_width)
            if operand is None:
                return None
            width_mask = self._emit_py_width_mask(width)
            if expr.op == "~":
                operand_mask = self._emit_py_width_mask(operand_width)
                return f"(~({operand})) & {operand_mask}"
            if expr.op == "+":
                return f"({operand}) & {width_mask}"
            if expr.op == "-":
                return f"(-({operand})) & {width_mask}"
            if expr.op == "!":
                operand_mask = self._emit_py_width_mask(operand_width)
                return f"(1 if (({operand}) & {operand_mask}) else 0)"
            return None

        if etype is BinaryOp:
            if expr.op in _COMPARISON_OPS:
                op_width = max(self._expr_width(expr.left), self._expr_width(expr.right))
            else:
                op_width = width
            left = self._emit_py_expr(expr.left, op_width)
            right = self._emit_py_expr(expr.right, op_width)
            if left is None or right is None:
                return None
            # Cache operand value strings so _emit_py_mask_expr for + or |/&
            # can reuse them without re-expanding the same sub-tree.
            if left is not None:
                self._py_val_cache.setdefault(id(expr.left), left)
            if right is not None:
                self._py_val_cache.setdefault(id(expr.right), right)
            width_mask = self._emit_py_width_mask(width)
            if expr.op in {"+", "-"}:
                lm = self._emit_py_mask_expr(expr.left, op_width)
                rm = self._emit_py_mask_expr(expr.right, op_width)
                if lm is None or rm is None:
                    return None
                return f"(0 if (({lm}) | ({rm})) else ((({left}) {expr.op} ({right})) & {width_mask}))"
            if expr.op in {"&", "|", "^", "+", "-"}:
                return f"(({left}) {expr.op} ({right})) & {width_mask}"
            if expr.op in {"<<", "<<<"}:
                return f"((({left}) << ({right})) & {width_mask})"
            if expr.op == ">>":
                return f"(({left}) >> ({right})) & {width_mask}"
            if expr.op == ">>>":
                shift_expr = self._emit_expr(expr.right, self._expr_width(expr.right))
                if (
                    isinstance(expr.left, FunctionCall)
                    and expr.left.name.lower() == "$signed"
                    and len(expr.left.arguments) == 1
                ):
                    signed_arg = expr.left.arguments[0]
                    signed_width = self._expr_width(signed_arg)
                    signed_left = self._emit_py_expr(signed_arg, signed_width)
                    if signed_left is None:
                        return None
                    signed_mask = self._emit_py_width_mask(signed_width)
                    sign_bit = f"(((<object>1) << {signed_width - 1}))"
                    signed_value = f"(((({signed_left}) & {signed_mask}) ^ {sign_bit}) - {sign_bit})"
                    return f"((({signed_value}) >> ({shift_expr})) & {width_mask})"
                return f"(({left}) >> ({shift_expr})) & {width_mask}"
            if expr.op in _COMPARISON_OPS:
                py_op = _BINARY_VALUE_OP[expr.op][0]
                return f"(1 if (({left}) {py_op} ({right})) else 0)"
            return None

        if etype is TernaryOp:
            # Check cache — populated below or by _emit_py_mask_expr (whichever
            # runs first) so that a right-recursive TernaryOp chain does not
            # produce 2^k calls via _emit_ternary_value_mask_exprs.
            cached_v = self._py_val_cache.get(id(expr))
            if cached_v is not None:
                return cached_v
            ternary_exprs = self._emit_ternary_value_mask_exprs(expr, width, py=True)
            if ternary_exprs is None:
                return None
            self._py_val_cache[id(expr)] = ternary_exprs[0]
            self._py_mask_cache[id(expr)] = ternary_exprs[1]
            return ternary_exprs[0]

        return None

    def _emit_py_mask_expr(self, expr: Expression, width: int) -> str | None:  # noqa: PLR0911
        etype = type(expr)

        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            if sid is not None:
                return f"_sig_py_mask(c, {sid})"
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is None:
                return None
            if struct_info[0] == "signal":
                base_sid, offset, field_width = struct_info[1], struct_info[3], struct_info[4]
                if offset >= _WORD_BITS or offset + field_width > _WORD_BITS:
                    return f"((_sig_py_mask(c, {base_sid}) >> {offset}) & {self._emit_py_width_mask(field_width)})"
                wmask = _cy_lit((1 << field_width) - 1)
                return f"((c.mask[{base_sid}] >> {offset}) & {wmask})"
            index_expr = self._emit_struct_storage_index_expr(struct_info[2])
            if index_expr is None:
                return None
            if self._mem_info[struct_info[1]][0] <= _WORD_BITS:
                return self._emit_mem_slice_expr(
                    struct_info[1],
                    index_expr,
                    struct_info[3],
                    struct_info[4],
                    mask=True,
                    elem_width=self._mem_info[struct_info[1]][0],
                )
            return f"_wmem{struct_info[1]}_py_extract_mask(c, ({index_expr}), {struct_info[3]}, {struct_info[4]})"

        if etype is BitSelect:
            mem_access = self._resolve_memory_element_access(expr)
            if mem_access is not None:
                mid, idx, _name, _indices = mem_access
                elem_width = self._mem_info[mid][0]
                if elem_width <= _WORD_BITS:
                    return self._emit_mem_slice_expr(mid, idx, 0, elem_width, mask=True, elem_width=elem_width)
                return f"_wmem{mid}_py_extract_mask(c, ({idx}), 0, {elem_width})"
            return None

        if etype is Literal:
            if (hasattr(expr, "is_x") and expr.is_x) or (hasattr(expr, "is_z") and expr.is_z):
                return self._emit_py_width_mask(width)
            return "0"

        if etype is FunctionCall:
            name = expr.name.lower()
            if name in {"$signed", "$unsigned"} and len(expr.arguments) == 1:
                return self._emit_py_mask_expr(expr.arguments[0], self._expr_width(expr.arguments[0]))
            return None

        if etype is RangeSelect:
            target_width = self._expr_width(expr.target)
            target_mask = self._emit_py_mask_expr(expr.target, target_width)
            if target_mask is None:
                return None
            sig_base = 0
            if isinstance(expr.target, Identifier):
                sig_base = self._signal_bases.get(expr.target.name, 0)
            if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                msb_val = int(expr.msb.value) - sig_base
                lsb_val = int(expr.lsb.value) - sig_base
                sel_w = msb_val - lsb_val + 1
                return f"((({target_mask}) >> {lsb_val}) & {self._emit_py_width_mask(sel_w)})"
            msb = self._emit_expr(expr.msb, 32)
            lsb = self._emit_expr(expr.lsb, 32)
            if sig_base != 0:
                msb = f"(({msb}) - {sig_base})"
                lsb = f"(({lsb}) - {sig_base})"
            return f"((({target_mask}) >> ({lsb})) & ((((<object>1) << (({msb}) - ({lsb}) + 1)) - 1)))"

        if etype is PartSelect:
            target_width = self._expr_width(expr.target)
            target_mask = self._emit_py_mask_expr(expr.target, target_width)
            if target_mask is None:
                return None
            base = self._emit_expr(expr.base, 32)
            sig_base = 0
            if isinstance(expr.target, Identifier):
                sig_base = self._signal_bases.get(expr.target.name, 0)
                if sig_base != 0:
                    base = f"(({base}) - {sig_base})"
            if isinstance(expr.width, Literal):
                width_expr = str(int(expr.width.value))
            else:
                width_expr = self._emit_expr(expr.width, 32)
            mask_expr = f"((((<object>1) << ({width_expr})) - 1))"
            if expr.direction == "+:":
                return f"((({target_mask}) >> ({base})) & {mask_expr})"
            return f"((({target_mask}) >> (({base}) - ({width_expr}) + 1)) & {mask_expr})"

        if etype is Concatenation:
            parts = expr.parts
            widths = self._concat_eval_widths(parts, width)
            total_width = sum(widths)
            result_parts: list[str] = []
            shift = total_width
            for part, part_width in zip(parts, widths):
                shift -= part_width
                part_mask_expr = self._emit_py_mask_expr(part, part_width)
                if part_mask_expr is None:
                    return None
                part_mask = self._emit_py_width_mask(part_width)
                packed = f"(({part_mask_expr}) & {part_mask})"
                if shift > 0:
                    result_parts.append(f"(({packed}) << {shift})")
                else:
                    result_parts.append(packed)
            return "(" + " | ".join(result_parts) + ")" if result_parts else "0"

        if etype is Replication:
            if isinstance(expr.count, Literal):
                count = int(expr.count.value)
            else:
                resolved = _const_int(expr.count, self._param_env)
                if resolved is None:
                    return None
                count = resolved
            value_width = self._expr_width(expr.value)
            value_mask_expr = self._emit_py_mask_expr(expr.value, value_width)
            if value_mask_expr is None:
                return None
            value_mask = self._emit_py_width_mask(value_width)
            packed = f"(({value_mask_expr}) & {value_mask})"
            if count <= 1:
                return packed
            parts = []
            for i in range(count):
                shift = value_width * (count - 1 - i)
                if shift > 0:
                    parts.append(f"(({packed}) << {shift})")
                else:
                    parts.append(packed)
            return "(" + " | ".join(parts) + ")" if parts else "0"

        if etype is AssignmentPattern:
            return self._emit_py_assignment_pattern_mask(expr, width)

        if etype is UnaryOp:
            operand_width = self._expr_width(expr.operand)
            return self._emit_py_mask_expr(expr.operand, operand_width)

        if etype is BinaryOp:
            if expr.op in _COMPARISON_OPS:
                op_width = max(self._expr_width(expr.left), self._expr_width(expr.right))
            else:
                op_width = width
            lm = self._emit_py_mask_expr(expr.left, op_width)
            rm = self._emit_py_mask_expr(expr.right, op_width)
            if lm is None or rm is None:
                return None
            width_mask = self._emit_py_width_mask(width)
            if expr.op in {"+", "-"}:
                return f"({width_mask} if (({lm}) | ({rm})) else 0)"
            if (
                expr.op == ">>>"
                and isinstance(expr.left, FunctionCall)
                and expr.left.name.lower() == "$signed"
                and len(expr.left.arguments) == 1
                and self._expr_width(expr.left.arguments[0]) > _WORD_BITS
            ):
                return f"({width_mask} if (({lm}) | ({rm})) else 0)"
            if expr.op == "|":
                # Use cached value strings if available — avoids re-expanding the
                # left sub-tree that _emit_py_mask_expr(left) already traversed.
                lv = self._py_val_cache.get(id(expr.left))
                if lv is None:
                    lv = self._emit_py_expr(expr.left, op_width)
                    if lv is None:
                        return None
                    self._py_val_cache[id(expr.left)] = lv
                rv = self._py_val_cache.get(id(expr.right))
                if rv is None:
                    rv = self._emit_py_expr(expr.right, op_width)
                    if rv is None:
                        return None
                    self._py_val_cache[id(expr.right)] = rv
                return f"(((({lm}) | ({rm})) & ~(({lv}) & ~({lm})) & ~(({rv}) & ~({rm}))) & {width_mask})"
            if expr.op == "&":
                lv = self._py_val_cache.get(id(expr.left))
                if lv is None:
                    lv = self._emit_py_expr(expr.left, op_width)
                    if lv is None:
                        return None
                    self._py_val_cache[id(expr.left)] = lv
                rv = self._py_val_cache.get(id(expr.right))
                if rv is None:
                    rv = self._emit_py_expr(expr.right, op_width)
                    if rv is None:
                        return None
                    self._py_val_cache[id(expr.right)] = rv
                return f"(((({lm}) | ({rm})) & ~(~({lv}) & ~({lm})) & ~(~({rv}) & ~({rm}))) & {width_mask})"
            if expr.op in {"^"}:
                return f"((({lm}) | ({rm})) & {width_mask})"
            if expr.op in {"<<", "<<<"}:
                # x bits shift positionally; for constant amounts use exact shift
                shift_const = _const_int(expr.right, self._param_env)
                if shift_const is not None and shift_const >= 0:
                    return f"((({lm}) << {shift_const}) & {width_mask})"
                return f"((({lm}) | ({rm})) & {width_mask})"
            if expr.op == ">>":
                # x bits shift positionally; for constant amounts use exact shift
                shift_const = _const_int(expr.right, self._param_env)
                if shift_const is not None and shift_const >= 0:
                    return f"(({lm}) >> {shift_const}) & {width_mask}"
                return f"((({lm}) | ({rm})) & {width_mask})"
            if expr.op == ">>>":
                return f"((({lm}) | ({rm})) & {width_mask})"
            if expr.op in _COMPARISON_OPS:
                return f"((({lm}) | ({rm})) & 1)"
            return None

        if etype is TernaryOp:
            # Symmetric with _emit_py_expr: whichever runs first caches both.
            cached_m = self._py_mask_cache.get(id(expr))
            if cached_m is not None:
                return cached_m
            ternary_exprs = self._emit_ternary_value_mask_exprs(expr, width, py=True)
            if ternary_exprs is None:
                return None
            self._py_val_cache[id(expr)] = ternary_exprs[0]
            self._py_mask_cache[id(expr)] = ternary_exprs[1]
            return ternary_exprs[1]

        return None

    def _emit_binary(self, expr: BinaryOp, width: int, signed_override: bool | None = None) -> str:  # noqa: PLR0911
        op_info = _BINARY_VALUE_OP.get(expr.op)
        if op_info is None:
            return "0"
        c_op, needs_mask = op_info

        # In 2-state compiled mode x/z values don't exist, so identity
        # comparisons with x/z literals have a known constant result:
        #   anything === x  ΓåÆ  0  (never identical)
        #   anything !== x  ΓåÆ  1  (always different)
        if expr.op in ("===", "!=="):
            if (isinstance(expr.left, Literal) and (expr.left.is_x or expr.left.is_z)) or (
                isinstance(expr.right, Literal) and (expr.right.is_x or expr.right.is_z)
            ):
                return "0" if expr.op == "===" else "1"

        # Comparison and bitwise ops must see all bits of their operands.
        # Passing the surrounding context width (e.g. 1 for an if-condition)
        # into compound sub-expressions like (a+b) would mask them to the
        # context width before the operation, discarding upper bits.
        if expr.op in _NATURAL_WIDTH_OPS:
            op_width = max(self._expr_width(expr.left), self._expr_width(expr.right))
        else:
            op_width = width

        # `>>` is ALWAYS a logical (unsigned/zero-fill) shift in Verilog
        # regardless of the left operand's own declared signedness -- only
        # `>>>` sign-extends. Force unsigned explicitly for `>>` only;
        # `<<`/`<<<`/`>>>` keep deferring to the operand's own signedness
        # (or an active override). Confirmed against Icarus for `a2 >>
        # (...)` with `a2` declared `signed [15:0]` and a shift amount >=
        # 16; mirrors the identical fix in `_wide_emitter.py`.
        # `+`/`-`/`*`/`/`/`%` ALL need the OPERATOR's combined signedness
        # (signed only if BOTH operands are signed, IEEE 1364-2005 §5.5.1)
        # to govern EVERY operand's own extension here -- not each
        # operand's own individual declared type. This used to special-
        # case `/`/`%` alone for this, reasoning that `+`/`-`/`*` are
        # "residue-safe" (their modular arithmetic gives the same answer
        # regardless of HOW each operand was individually extended, as
        # long as each operand's bit pattern is "correct" at the target
        # width) -- but that reasoning is simply wrong: sign- vs zero-
        # extending a signed operand produces a DIFFERENT integer value
        # (e.g. a 1-bit signed `1` means -1 sign-extended but +1 zero-
        # extended), and `(a - b) mod N` genuinely differs depending on
        # WHICH of those two different values `a` is taken to be --
        # "residue-safe" only holds once each operand's value is ALREADY
        # fixed, it says nothing about which extension choice fixes it
        # correctly in the first place. Comparisons are a separate
        # question (IEEE 1364-2005 §5.5.2: "operands ... affect each other
        # as if they were context-determined ... with a result type ...
        # determined from them", i.e. the same combining rule, not each
        # operand's own individual type, but the comparison RESULT's own
        # typing is independent of the surrounding expression -- see the
        # dedicated `elif` below). Extending each operand by its own
        # individual signedness is wrong for all five arithmetic
        # operators: a signed operand paired with an unsigned one must
        # have BOTH read as unsigned, not the signed one sign-extended and
        # then misread as a huge unsigned magnitude. `combined_override`
        # is threaded into BOTH operands' own emission (propagating into
        # whatever nested operator either one is, exactly like a
        # ternary's combined signedness overrides its branches) so this
        # combined decision -- not each operand's own type -- governs
        # every extension nested within either operand too. Mirrors the
        # identical fix in `sim/evaluator.py`; confirmed wrong (cross-
        # engine, against the reference oracle) for `a4 / ((~^a1[0]) | 1)`
        # (a4 signed and negative, divisor an unsigned reduction-derived
        # expression), `a3 % (a0 | 1)` (a0 a signed 1-bit register nested
        # inside the divisor's own `|`), `(a5[5:2] < a0)` (a5[5:2] an
        # unsigned part-select, a0 a signed 1-bit register), and (cross-
        # engine, against Icarus) `(sa - ub)` with `sa` a signed 1-bit
        # register holding `1` (i.e. -1) and `ub` an unsigned 2-bit `0`:
        # Icarus gives `1` (zero-extending `sa` per the pair's combined-
        # unsigned type), not `-1`/`3` (sign-extending `sa` on its own,
        # what individual-signedness extension computed before this fix).
        if expr.op in ("+", "-", "*", "/", "%"):
            combined_override = (
                signed_override
                if signed_override is not None
                else (self._expr_signed(expr.left) and self._expr_signed(expr.right))
            )
        elif expr.op in ("==", "!=", "===", "!==", "<", "<=", ">", ">="):
            # Unlike division, a comparison's OWN operand typing is
            # independent of the rest of the expression (IEEE 1364-2005
            # §5.5.2: "The type and size of the operand shall be
            # independent of the rest of the expression and vice versa")
            # -- an outer `signed_override` (e.g. this comparison is
            # itself a ternary branch) must NOT override this internal
            # decision, unlike every other use of `signed_override` in
            # this function.
            combined_override = self._expr_signed(expr.left) and self._expr_signed(expr.right)
        elif expr.op in ("&", "|", "^", "~^", "^~"):
            # Bitwise ops' own combined signedness is entirely SELF-
            # CONTAINED (governed solely by its own two operands' types),
            # unlike `<<`/`>>`/`<<<`/`>>>` (the other operators reaching
            # the generic `else` below), which genuinely need an outer
            # decision to reach into their left operand's own extension --
            # see the ">>"-specific comment a few lines down. Forwarding
            # the incoming `signed_override` here (as the generic `else`
            # used to do for every remaining op, bitwise included) would
            # leak an unrelated outer decision (e.g. a `%`'s divisor-
            # widening override) into a NESTED, structurally-unrelated
            # operand's own independent signed/unsigned computation --
            # exactly the bug shape already fixed in `sim/evaluator.py`'s
            # mirror-image branch and `_wide_emitter.py`'s equivalent
            # `combined_override` computation (see either docstring for
            # the concrete Icarus-confirmed repro, `(|a3[45]) %
            # (($signed(a4[23]) - a0) | 1)`). `None` lets each operand
            # widen to `op_width` using its own self-determined
            # signedness (the same fallback `_emit_expr` already applies
            # whenever no override is passed) instead of the outer
            # decision; the RESULT-level extension further below (`if
            # width and width > op_width`) still correctly consults the
            # ORIGINAL `signed_override` parameter (not `combined_override`)
            # for how this bitwise op's own already-computed result should
            # be read by whatever outer context requested it.
            combined_override = None
        else:
            combined_override = signed_override
        # `>>` forcing unsigned is a property of how the shift reads its
        # ALREADY-COMPUTED left operand's bit pattern (no sign-bit
        # replication into vacated positions) -- it does NOT mean every
        # leaf reached within that operand must be individually re-typed
        # as unsigned. When `expr.left` is itself a natural-width
        # combining op (`&`/`|`/`^`/`~^`/`^~`), that operand's OWN
        # internal computation is a self-contained Verilog sub-expression
        # whose value is fixed by each of ITS OWN operands' declared
        # types, entirely independent of whatever operator (`>>` or
        # otherwise) later consumes the result -- forcing
        # `left_signed_override=False` here would incorrectly leak into
        # that nested op's OWN per-operand extension (see `combined_override`
        # forwarding a few lines below `else: combined_override =
        # signed_override`), corrupting its computed VALUE, not just how
        # it's read afterward. Only apply the force when `expr.left`
        # actually needs WIDENING for this shift's own context (its own
        # natural width is narrower than `op_width`) -- that's the one
        # case an outer unsigned-widening decision genuinely must reach
        # this deep (matches the already-confirmed `a2 >> ...` fix this
        # override exists for, where `a2` is a plain signed Identifier).
        # Confirmed against Icarus (cross-engine) for `(a4 & a0) >> a7`
        # with `a0` a signed 1-bit register: `a0` must sign-extend to -1
        # (matching Verilog's `a4 & a0`, independent of the `>>`), but the
        # unconditional force made `a0` zero-extend to +1 instead, giving
        # `a4 & 1` rather than `a4 & (-1) == a4`.
        if expr.op == ">>" and self._expr_width(expr.left) >= op_width:
            left_signed_override = None
        else:
            left_signed_override = False if expr.op == ">>" else combined_override
        left = self._emit_expr(expr.left, op_width, left_signed_override)
        # The shift COUNT is self-determined (IEEE 1364-2005 Table 5-22 /
        # SS5.6): it must be evaluated at its OWN natural width, not
        # `op_width` (the enclosing context) -- requesting a wider context
        # here previously let a context-determined operator WITHIN the
        # amount expression (e.g. `~` in `~(cond ? a : b)`) wrongly widen
        # its own operand to that context before complementing, corrupting
        # the amount (`~0` at 32 bits = 0xFFFFFFFF, not the correct 1-bit
        # `~0 = 1`). Also always interpreted as an unsigned magnitude
        # regardless of its own declared signedness -- `signed_override=
        # False` forces zero- rather than sign-extension for any
        # Identifier reached within it. Confirmed against Icarus for
        # `$unsigned(a1) << a0` (signedness) and `$signed((!a6[52])) <<
        # (~(cond ? a4[13] : (~^a1[2])))` (width); mirrors the identical
        # fix in `_wide_emitter.py`.
        # `**`'s exponent, like a shift COUNT, is ALWAYS self-determined
        # (IEEE 1364-2005 SS5.1.5: "In all cases, the second operand of
        # the power operator shall be treated as self-determined") --
        # verified directly against the primary spec text; NOT `op_width`
        # (the base's own context), which every other operator reaching
        # this branch propagates into its right operand.
        if expr.op in ("<<", ">>", "<<<", ">>>"):
            right = self._emit_expr(expr.right, self._shift_amount_width(expr.right), False)
        elif expr.op == "**":
            right = self._emit_expr(expr.right, self._expr_width(expr.right), signed_override)
        else:
            right = self._emit_expr(expr.right, op_width, combined_override)

        # Sign-extend signed operands when context width exceeds operand width
        # (IEEE 1364-2005 §5.5.2).  Skip comparisons (handled separately) and
        # shifts (left operand handled by the >>_ARITH path). `**` is also
        # skipped here -- its own value-emission branch below sign-extends
        # BOTH operands to their OWN respective widths unconditionally
        # (base to `lw`, exponent to its self-determined width), since
        # `_verilog_ipow` needs a properly full-64-bit-sign-extended
        # `long long` to correctly detect a negative exponent regardless
        # of whether `op_width > lw` happens to hold -- that comparison is
        # about the BASE's context, meaningless for the EXPONENT's
        # self-determined width.
        # For division/modulus: always sign-extend signed operands from their
        # own width, since C's / and % treat operands as signed only when the
        # value is at its native signed width. `signed_override`, when set
        # (this BinaryOp is itself a ternary branch), forces the same
        # sign/zero-extension decision for both operands, overriding each
        # operand's own individual signedness -- matching how a signed
        # ternary branch is handled everywhere else (IEEE 1364-2005 §5.5.1).
        # Restricted to `+ - * / %`: this block re-sign-extends `left`/
        # `right` using `_expr_width`'s STATIC, AST-shape-based self-width
        # estimate (`lw`/`rw`) -- necessary for `+`/`-`/`*`/`/`/`%` because
        # C's raw `+ - * / %` on a native register needs the operand's
        # SIGN genuinely replicated all the way up the register (masking
        # to a width alone isn't enough: a carry/borrow chain or signed
        # division reads bits above the "logical" width too). But `lw`
        # only correctly describes the width `left`'s STRING VALUE is
        # actually sign-filled to when `expr.left` is a LEAF (Identifier/
        # Literal) or another SELF-DETERMINED node, whose own internal
        # emission naturally stops at its self-width. For a NESTED
        # context-determined operator (e.g. another `+`/`-`/`*`/bitwise
        # BinaryOp), the recursive `_emit_expr(expr.left, op_width, ...)`
        # call above already computed and masked that operand's value
        # DIRECTLY at the wider `op_width` (not at its own self-width and
        # then extended afterward -- see the arithmetic branch's own
        # `target = max(width, ...)` design a few dozen lines up), so
        # `_expr_width(expr.left)` underestimates where the value's real
        # "content" ends, and `_sign_ext(left, lw)` wrongly reinterprets
        # an arbitrary INTERIOR bit of that already-wide value as the
        # sign bit, corrupting it. Bitwise ops (&,|,^,~^,^~) don't need
        # this pre-extension at all: their `core` gets masked to
        # `op_width` again immediately below regardless (`& wmask
        # (op_width)`), so any extra un-sign-filled bits above `op_width`
        # in the raw C value are irrelevant to the bitwise combination
        # itself -- running this block for them only exposed the `lw`-
        # underestimation bug above without buying anything. Confirmed
        # against Icarus (cross-engine) for `(|a3[45]) % (($signed(a4[23])
        # - a0) | 1)`: the `-` node nested inside the `|` was already
        # correctly computed at the `|`'s 32-bit `op_width`, but this
        # block's `lw=1` (the `-` node's own static self-width) then
        # `_sign_ext`'d that already-32-bit value as if bit 0 were its
        # sign bit, corrupting `1` into a huge all-ones magnitude.
        if expr.op in ("+", "-", "*", "/", "%"):
            left_signed = combined_override if combined_override is not None else self._expr_signed(expr.left)
            right_signed = combined_override if combined_override is not None else self._expr_signed(expr.right)
            if left_signed:
                lw = self._expr_width(expr.left)
                if op_width > lw or expr.op in ("/", "%"):
                    left = f"_sign_ext({left}, {lw})"
            if right_signed:
                rw = self._expr_width(expr.right)
                if op_width > rw or expr.op in ("/", "%"):
                    right = f"_sign_ext({right}, {rw})"

        if expr.op in {"+", "-"}:
            left_mask = self._emit_mask_expr(expr.left, op_width)
            right_mask = self._emit_mask_expr(expr.right, op_width)
            # Hoist the left sub-expression to named temps when inside a temp
            # context and the left operand is itself a +/- chain.  This converts
            # O(k²) inline string growth for k-term addition chains into O(k).
            if self._et_pending is not None and isinstance(expr.left, BinaryOp) and expr.left.op in {"+", "-"}:
                n = self._et_count
                self._et_count += 1
                self._et_pending.append(f"cdef long long _et{n}_v = {left}")
                self._et_pending.append(f"cdef long long _et{n}_m = {left_mask}")
                self._et_node_masks[id(expr.left)] = f"_et{n}_m"
                self._et_node_vals[id(expr.left)] = f"_et{n}_v"
                left = f"_et{n}_v"
                left_mask = f"_et{n}_m"
            core = f"(({left}) {c_op} ({right}))"
            return f"(0 if (({left_mask}) | ({right_mask})) else (({core}) & wmask({width})))"

        # XNOR: XOR then invert. XOR/XNOR have no absorbing bit value (unlike
        # `&`/`|`, where a known-0/known-1 bit forces a determinate result
        # regardless of the other operand's x/z -- and where the raw C value
        # computed from an x-bit's conventional "stored as 0" representation
        # happens to already satisfy "value reads 0 wherever the true result
        # is ambiguous" purely because 0 is absorbing for `&` and neutral-ish
        # for `|` once a known-1 is ruled out). Without an absorbing element,
        # an x bit's 0-value can combine with the OTHER operand's known-1
        # bit to produce a spurious raw value=1 at a position that's
        # actually ambiguous (mask=1) -- explicitly force those bits to 0.
        # Confirmed against Icarus (cross-engine) for `((cmp) ^ (ternary
        # selecting a fully-x operand))` read directly as an `if` condition
        # (see `_emit_if`'s "value already 0 when ambiguous" convention,
        # documented in `_emit_condition_lines_and_expr`): the comparison
        # side was a known 1, XORed against an x bit stored as 0, giving a
        # spurious raw value=1 and wrongly taking the true branch.
        if expr.op in ("~^", "^~"):
            lm = self._emit_mask_expr(expr.left, op_width, combined_override)
            rm = self._emit_mask_expr(expr.right, op_width, combined_override)
            core = f"(~(({left}) ^ ({right})) & ~(({lm}) | ({rm})))"
            return f"({core}) & wmask({width})"

        # Power: pure-integer via `_verilog_ipow` (IEEE 1364-2005 Table
        # 5-6), NOT `pow()`/`double` -- floating point is imprecise for
        # large integers and casting an infinite/negative double back to
        # an unsigned integer type is undefined behavior in C, both real
        # risks with the previous `pow(<double>...)` implementation.
        # Mirrors `sim/value.py`'s `_verilog_pow` / `sim/evaluator.py`'s
        # `_eval_signed_pow` -- both operands must be signed (the same
        # all-or-nothing gate used everywhere else in this codebase for
        # signed comparison/div/mod) before treating either as
        # potentially negative; `_verilog_ipow` needs each operand
        # properly sign-extended to the full 64-bit `long long` to
        # correctly detect a negative exponent (a raw N<64-bit
        # two's-complement pattern doesn't look negative to C without
        # this). Confirmed against Icarus.
        if expr.op == "**":
            both_signed = (
                signed_override
                if signed_override is not None
                else (self._expr_signed(expr.left) and self._expr_signed(expr.right))
            )
            if both_signed:
                pow_base = f"_sign_ext({left}, {self._expr_width(expr.left)})"
                pow_exp = f"_sign_ext({right}, {self._expr_width(expr.right)})"
            else:
                pow_base, pow_exp = left, right
            return f"(_verilog_ipow({pow_base}, {pow_exp})) & wmask({width})"

        # Arithmetic right shift: preserve the signed operand width before
        # truncating to the surrounding assignment width. A shift amount
        # >= 64 must saturate to the sign bit replicated across all bits
        # (0 if non-negative, -1/all-ones if negative) rather than wrap —
        # same native-shift-instruction footgun as the `>>`/`<<` guard above.
        if c_op == ">>_ARITH":
            signed_width = self._expr_width(expr.left) if self._expr_signed(expr.left) else width
            sx = f"_sign_ext({left}, {signed_width})"
            return f"((0 if ({sx}) >= 0 else -1) if ({right}) >= 64 else (({sx}) >> ({right}))) & wmask({width})"

        if expr.op in _COMPARISON_OPS:
            # `left`/`right` were already extended to `op_width` above using
            # `combined_override`, but ONLY when `op_width` exceeds the
            # operand's own self-width -- `_emit_expr`'s Identifier case
            # skips its `_sign_ext` call entirely when `op_width ==
            # sig_width` (nothing needs widening from Verilog's own
            # semantic perspective). That leaves the raw C value's UPPER
            # native-register bits (beyond `op_width`) at whatever
            # arbitrary state they were in -- typically 0 by this
            # codebase's "masked/unsigned values keep upper bits zero"
            # storage invariant -- so a signed comparison's negative
            # values (e.g. 8-bit -1, stored as the small POSITIVE value
            # 255 in the low 8 bits with zero above) get read by C's
            # native signed `<` as a huge positive number instead of -1.
            # An explicit `_sign_ext(..., op_width)` here is required
            # regardless of whether the earlier per-operand step already
            # ran, to fill the native register correctly before the
            # comparison instruction executes. Confirmed wrong (cross-
            # engine, against Icarus) for `reg signed [7:0] a, b; a=-1;
            # b=1; (a < b)`, which must be true.
            if expr.op in ("<", "<=", ">", ">=") and combined_override:
                core = f"(1 if (_sign_ext({left}, {op_width}) {c_op} _sign_ext({right}, {op_width})) else 0)"
            # Unsigned relational: cast to unsigned long long so 64-bit values with
            # MSB=1 (stored as negative long long) compare correctly. Mask
            # each side to `op_width` first -- an operand isn't guaranteed
            # to already read 0 above `op_width` in NATIVE REGISTER terms;
            # a `$signed(...)`-cast operand in particular always emits
            # `_sign_ext(...)`, which (correctly, per `$signed`'s own
            # unconditional cast semantics) fills the ENTIRE native
            # register with the sign bit regardless of `op_width`, not
            # just the bits up to it. Without masking here, comparing that
            # raw sign-filled register against a plain small operand
            # reads as wildly unequal even when both represent the exact
            # same value within `op_width` bits. Confirmed against Icarus
            # (cross-engine) for `(a2[3] != $signed(a7))` with `a2[3]`
            # and `a7` both 1 bit and both holding the same bit value:
            # `$signed(a7)`'s C representation was a full 64-bit -1
            # while `a2[3]`'s was a plain 1, comparing unequal even
            # though both are the same value at their shared 1-bit width.
            elif expr.op in ("<", "<=", ">", ">="):
                core = (
                    f"(1 if (<unsigned long long>(({left}) & wmask({op_width})) {c_op}"
                    f" <unsigned long long>(({right}) & wmask({op_width}))) else 0)"
                )
            # Equality/logical -> sign-neutral, no cast needed. Masked to
            # `op_width` for the same reason as the unsigned-relational
            # branch immediately above.
            else:
                core = f"(1 if ((({left}) & wmask({op_width})) {c_op} (({right}) & wmask({op_width}))) else 0)"
            # This codebase's callers fall into two camps: most (e.g. a
            # plain assignment RHS) write the result through a final
            # `value & ~mask` step that zeroes ambiguous bits regardless
            # of what `core` computed for them, so `core` alone getting
            # this wrong for x/z operands would never surface. But a
            # handful of callers (`_emit_if`/`_emit_for`/`_emit_while`'s
            # `_emit_condition_lines_and_expr`, see its docstring) read
            # this VALUE directly as a Cython boolean WITHOUT ever
            # combining it with the mask, relying on the codebase-wide
            # "value already reads 0 wherever the true result is
            # ambiguous" convention that e.g. the `+`/`-` branch above
            # upholds explicitly (`0 if (left_mask|right_mask) else
            # ...`) but this comparison branch never did -- `core` is a
            # raw C comparison of the value fields alone, where an x/z
            # bit's value is conventionally stored as 0, so e.g. `x <=
            # 29` reads as `0 <= 29` = true instead of the required x.
            # Reuse `_emit_mask_expr`'s own (already-correct, including
            # its `==`/`!=` known-differing-bit short-circuit) mask
            # formula for this exact node rather than re-deriving it.
            # Confirmed against Icarus (cross-engine, against the other
            # 3 engines) for `if (($unsigned(a2[5]) <= a1[6:1]) ^ ...)`
            # and `if ((a2 == a2))` with `a2` fully x -- both wrongly
            # took the true branch before this fix.
            cmp_mask = self._emit_mask_expr(expr, width)
            value_str = f"(0 if ({cmp_mask}) else ({core}))"
            # Hoist both value and mask into named temps (mirrors the
            # identical TernaryOp hoist block above, and its "preventing
            # 2^k recursion" comment applies verbatim here): `cmp_mask`'s
            # own `==`/`!=` known-differing-bit formula re-queries this
            # comparison's OWN operands via `_emit_expr`/`_emit_mask_expr`
            # independently of the `left`/`right` already computed above
            # -- for a nested chain of comparisons (each one's operand
            # itself a comparison), an unhoisted node gets its full
            # sub-expression string re-expanded on this second query,
            # compounding into 2^depth blowup for deep real-world
            # comparison chains (a synthetic fuzzer's shallow trees never
            # reach the depth where this becomes catastrophic). Hoisting
            # here means a later `_emit_expr`/`_emit_mask_expr` query for
            # this SAME node (whether from an outer comparison's own
            # `cmp_mask` derivation or anywhere else) hits the existing
            # `_et_node_vals`/`_et_node_masks` cache checks (at the top of
            # `_emit_expr`'s BinaryOp dispatch and `_emit_mask_expr`'s
            # comparison branch) instead of re-expanding. Confirmed via
            # the real ibex_cs_registers design (whose CSR comparison
            # chains nest far deeper than the differential fuzzer's bounded
            # trees): compiling it went from completing in seconds to
            # exhausting tens of GB of RAM before this hoist was added.
            if self._et_pending is not None:
                n = self._et_count
                self._et_count += 1
                self._et_pending.append(f"cdef long long _et{n}_v = {value_str}")
                self._et_pending.append(f"cdef long long _et{n}_m = {cmp_mask}")
                self._et_node_vals[id(expr)] = f"_et{n}_v"
                self._et_node_masks[id(expr)] = f"_et{n}_m"
                return f"_et{n}_v"
            return value_str

        # Logical right shift: Verilog >> is unsigned (zero-fill).  Cython's >>
        # on long long is arithmetic (sign-extends MSB), so cast to unsigned.
        # A shift amount >= 64 must yield 0 (Verilog semantics), but the
        # native C shift instruction only consults the low 6 bits of the
        # count, so `x >> 64` / `x << 64` silently behave like `x >> 0` /
        # `x << 0` on a 64-bit word — guard it explicitly. The guard's own
        # comparison must be UNSIGNED: Verilog shift amounts are always an
        # unsigned magnitude, but `right` here is a `long long`, so a
        # shift-amount expression computed via negation of a large value
        # (e.g. `x << (-a4)` with `a4` a large positive 64-bit signed
        # value) produces a bit pattern that's a huge magnitude when read
        # as unsigned but a large NEGATIVE `long long` when compared as
        # signed -- `(-huge) >= 64` is then false, letting a genuinely
        # out-of-range shift amount slip past the guard into an actual
        # negative-count shift (undefined behavior in C, not just a
        # wrong-answer bug). Confirmed against Icarus (cross-engine) for
        # `$signed((a1 * a1) << (-a4))` used as a ternary's own condition
        # embedded in a wider assignment.
        if expr.op == ">>":
            # `left` may be the FULL native-64-bit sign-extension of a
            # narrower signed operand (`_sign_ext`'s own contract: it
            # fills the whole `long long` register, unbounded by
            # `op_width`, since most other consumers either further widen
            # from there or mask the FINAL result afterward). A logical
            # shift must NOT let those extra high "padding" bits
            # participate -- shifting a genuinely narrow value right must
            # zero-fill from the top based on the value's OWN natural
            # extent, not the incidental width of its C representation.
            # Confirmed against Icarus (cross-engine) for `($signed(a0) >>
            # a0)` with `a0` a signed 1-bit register holding `1` (i.e. -1)
            # used as an `if` condition: `a0`'s own width is 1, so `-1 >>
            # 1` (logical) must give `0` (the single bit shifts out,
            # nothing left), but shifting the UNMASKED full-64-bit
            # sign-extension of -1 instead gave `0x7FFFFFFFFFFFFFFF` -- an
            # odd, definitely-nonzero value, flipping the condition's
            # truth value.
            #
            # The mask width must be `max(op_width, self._expr_width
            # (expr.left))`, NOT `op_width` alone (the first attempt at
            # this fix): `op_width` here is just the OUTER caller's
            # requested `width` (`>>` isn't in `_NATURAL_WIDTH_OPS`, so it
            # never gets widened to the operand's own natural width the
            # way bitwise ops do) -- for a `>>` result immediately
            # narrowed by an outer cast (e.g. `sel_t'(addr >> SelOffset)`
            # requesting a 1-bit result), masking `left` (the full address)
            # down to that same 1 bit BEFORE shifting discards real
            # address bits the shift still needs to correctly extract the
            # upper ones, corrupting the shift's OWN result even though
            # `left`'s true value was never actually out of range for its
            # OWN width. `self._expr_width(expr.left)` is a STATIC self-
            # width estimate (like every other use of `_expr_width` in
            # this file) that can UNDER-estimate a nested context-
            # determined operand's true computed width, hence `max(...)`
            # with `op_width` rather than using `_expr_width` alone --
            # only ever widening the mask, never narrowing it back below
            # what `op_width` already guaranteed. Confirmed against Icarus
            # (cross-engine) for `sel_t'((cond ? addr_a : addr_b) >>
            # SelOffset)` from the PULP AXI-Lite upsizer's write-select
            # logic (`examples/pulp/axi/axi_lite_dw_converter`): masking
            # the 32-bit address down to `sel_t`'s own 1-bit result width
            # before shifting zeroed the address's bit 1 before it could
            # ever reach the output, wrongly giving `wr_sel_q = 0` instead
            # of `1` for address `0x2`.
            mask_w = max(op_width, self._expr_width(expr.left))
            core = (
                f"(0 if (<unsigned long long>({right})) >= 64 else"
                f" (<long long>((<unsigned long long>({left}) & wmask({mask_w}))"
                f" >> <unsigned long long>({right}))))"
            )
        # For left-shift, promote left operand to long long to avoid
        # C int overflow when small literal << large shift (e.g. 4095 << 20).
        elif expr.op in ("<<", "<<<"):
            core = f"(0 if (<unsigned long long>({right})) >= 64 else ((<long long>({left})) {c_op} ({right})))"
        elif expr.op in ("/", "%") and not combined_override:
            # Unsigned division/modulus: cast both sides to avoid signed C behavior
            # on 64-bit values with MSB=1 stored as negative long long.
            core = f"(<long long>(<unsigned long long>({left}) {c_op} <unsigned long long>({right})))"
        else:
            core = f"(({left}) {c_op} ({right}))"
        if expr.op == "^":
            # Unlike `&`/`|` (see the XNOR comment above for why those are
            # self-safe), plain XOR has no absorbing bit value: an x bit's
            # conventional "stored as 0" value can combine with the OTHER
            # operand's known-1 bit to produce a spurious raw value=1 at a
            # position that's actually ambiguous. Force those bits to 0
            # explicitly so `core` upholds the "value reads 0 wherever the
            # true result is ambiguous" convention -- see the identical fix
            # (and its Icarus-confirmed repro) on the `~^`/`^~` branch above.
            xor_lm = self._emit_mask_expr(expr.left, op_width, combined_override)
            xor_rm = self._emit_mask_expr(expr.right, op_width, combined_override)
            core = f"(({core}) & ~(({xor_lm}) | ({xor_rm})))"
        if expr.op in ("&", "|", "^"):
            # `needs_mask` is False for these -- their natural-width
            # combination is presumed already bounded by an outer
            # assignment/context mask. That presumption breaks when this
            # BinaryOp is embedded as a SUB-expression of another operator
            # rather than a direct assignment RHS (e.g. the divisor of
            # `%`): no outer mask ever runs, and an individual operand's
            # own sign-extension (`_sign_ext`, which fills the full native
            # C register, not bounded to `op_width`) leaks straight through
            # as garbage bits above `op_width`. Mask to `op_width` first
            # (clearing that garbage), THEN -- only if the caller asked for
            # a wider `width` than this operator's own natural op_width --
            # extend using the WHOLE EXPRESSION's own combined signedness
            # (IEEE 1364-2005 §5.5.1), mirroring `sim/evaluator.py`'s
            # bitwise-op branch. Confirmed wrong (cross-engine, against the
            # reference oracle) for `a3 % (a0 | 1)` where `a0` is a signed
            # 1-bit register: `a0`'s own sign-extension leaked past the
            # `|`'s natural 32-bit width into the divisor once nested
            # inside the modulus's wider context.
            if op_width < _WORD_BITS:
                core = f"(({core}) & wmask({op_width}))"
            if width and width > op_width:
                eff_signed = signed_override if signed_override is not None else self._expr_signed(expr)
                if eff_signed:
                    return f"(_sign_ext({core}, {op_width})) & wmask({width})"
                return f"({core}) & wmask({width})"
            return core
        if needs_mask:
            return f"({core}) & wmask({width})"
        return core

    def _emit_unary(self, expr: UnaryOp, width: int, signed_override: bool | None = None) -> str:
        ow = self._expr_width(expr.operand)

        # Reduction operators → 1-bit result (self-determined)
        if expr.op in _REDUCTION_OPS or expr.op == "!":
            # A reduction's OWN result always fits in 1 bit regardless of
            # its operand's width, but `_emit_reduction`/the `!` formula
            # below both compute via native `long long` operand/mask
            # strings -- correct only up to 64 bits. Beyond that (a
            # concat/replication whose own self-determined width exceeds
            # the native register), those strings silently lose the
            # operand's upper bits entirely (`wmask(ow)`/`_cy_hex((1 <<
            # ow) - 1)` both overflow a 64-bit C literal for `ow > 64`,
            # collapsing to an effectively-64-bit mask no matter how wide
            # `ow` really is). Route through the wide emitter instead,
            # which already has correct multi-word reduction primitives
            # (`wide_reduce_and`/`_or`/`_xor`) -- see
            # `_emit_wide_reduction_to_value`'s own docstring. This is a
            # genuine, PRE-EXISTING gap unrelated to any particular
            # operator nested inside the reduction -- confirmed against
            # Icarus (cross-engine) for `((!{a0, a7, a4}) & 64'hFF...FF)`
            # with `a0` 1 bit x, `a7` 1 bit, `a4` 64 bits (66 bits total):
            # embedding the reduction as an AND operand (reached through
            # `_emit_mask_expr`'s generic dispatch, unlike a bare top-
            # level assignment RHS, which apparently has its own
            # unaffected fast path) spuriously read the ambiguous top bit
            # as definite.
            if ow > _WORD_BITS:
                wide = self._emit_wide_reduction_to_value(expr)
                if wide is not None:
                    return wide[0]
            if expr.op == "!":
                operand = self._emit_expr(expr.operand, ow)
                # Must independently return 0 (not just "whatever the raw,
                # x-positions-already-zeroed value happens to compute")
                # whenever the true result is ambiguous -- see the
                # docstring on `_emit_reduction` below for why this
                # matters even though a PAIRED mask expression already
                # gets this right elsewhere. A known-1 bit anywhere in the
                # operand forces `!` definitely false regardless of x
                # elsewhere; only when there is NO known-1 bit AND some
                # bit is x is the result genuinely ambiguous (value must
                # read 0 there, matching the mask).
                operand_mask = self._emit_mask_expr(expr.operand, ow)
                wm = f"wmask({ow})"
                known_one = f"(({operand}) & (~({operand_mask})) & {wm})"
                any_x = f"(({operand_mask}) & {wm})"
                return f"(0 if {known_one} else (0 if {any_x} else (1 if (({operand}) & {wm}) == 0 else 0)))"
            operand = self._emit_expr(expr.operand, ow)
            operand_mask = self._emit_mask_expr(expr.operand, ow)
            return self._emit_reduction(expr.op, operand, operand_mask, ow)

        prefix = _UNARY_PREFIX.get(expr.op)
        if prefix is None:
            return "0"

        # ~/+/- are context-determined (IEEE 1364-2005 Table 5-22): evaluate
        # the operand at the surrounding context width, sign-extending it
        # from its own width first if signed. `~` used to be treated as
        # self-determined here (result width = operand width, sign-extend
        # the RESULT afterward) -- that's wrong for unsigned operands, since
        # zero-extension doesn't commute with bitwise complement (only
        # sign-extension does), confirmed against Icarus/Verilator (see
        # notes/known_issues.md).
        if expr.op in ("~", "+", "-"):
            # This "evaluate at the operand's own fixed width, THEN extend
            # the RESULT" special case applies to `~` ONLY, not unary `-`
            # (despite both being grouped together above as "context-
            # determined") -- the two behave differently under width-
            # extension precisely BECAUSE `~` is a bitwise, per-bit-
            # independent operation while `-` is a genuine two's-
            # complement ARITHMETIC negation: zero-extending a 1-bit value
            # and THEN complementing flips all the newly-added padding
            # bits too (wrong -- `~` must run at the fixed width first,
            # confirmed against Icarus for `$signed(~({a0, a6, a0} &&
            # a7))`), but zero-extending a value and THEN negating gives
            # exactly the modular two's-complement wraparound
            # representation of "minus that value" at the wider width --
            # which is what real hardware (and Icarus) actually computes,
            # confirmed wrong the other way (evaluate-at-1-bit-then-
            # extend-result gives `1`, not Icarus's `all-ones`/-1) for
            # `-(~&{2{(a5[5:2] < a0)}})` widened into a 96-bit
            # destination. So unary `-` (like `+`, a no-op either way)
            # always falls through to the normal context-determined path
            # below -- it must NEVER take this fixed-width shortcut, even
            # when its operand is itself a comparison/reduction/&&/||/!
            # result. Mirrors the identical fix in `sim/evaluator.py`/
            # `sim/vm/compiler.py`.
            if expr.op == "~" and _is_fixed_self_determined(expr.operand):
                operand = self._emit_expr(expr.operand, ow)
                if expr.op == "-":
                    core = f"((-({operand})) & wmask({ow}))"
                elif expr.op == "~":
                    # `~` must independently force 0 wherever the operand is
                    # ambiguous, not just complement whatever raw value bits
                    # it received -- an ambiguous bit's value is
                    # conventionally stored as 0 (see `_emit_reduction`'s
                    # docstring for the general "value is 0 wherever the
                    # true result is x" convention this whole codebase
                    # relies on), so a plain `~` flips it to a SPURIOUS
                    # known-1, violating the very invariant callers like
                    # `&&`'s mask formula depend on ("a nonzero raw value
                    # implies a genuine known-1 bit"). Confirmed against
                    # Icarus (cross-engine) for `((0 < 4) && (~{2{(!(!a7))}}))`
                    # with `a7` fully x: `!(!a7)` correctly reads VALUE=0
                    # (ambiguous), but `~` on the replicated result flipped
                    # those ambiguous-stored-as-0 bits to 1, making `&&`'s
                    # own "both operands look truthy" check wrongly
                    # conclude the whole condition was definitely true.
                    operand_mask = self._emit_mask_expr(expr.operand, ow)
                    core = f"((~({operand})) & (~({operand_mask})) & wmask({ow}))"
                else:
                    core = f"({operand})"
                eval_width = max(ow, width) if width else ow
                if eval_width <= ow:
                    return core
                eff_signed = signed_override if signed_override is not None else self._expr_signed(expr)
                if eff_signed:
                    return f"(_sign_ext({core}, {ow}) & wmask({eval_width}))"
                return f"(({core}) & wmask({eval_width}))"
            eval_width = max(ow, width) if width else ow
            operand = self._emit_expr(expr.operand, eval_width, signed_override)
            eff_signed = signed_override if signed_override is not None else self._expr_signed(expr.operand)
            if eff_signed and eval_width > ow:
                operand = f"_sign_ext({operand}, {ow})"

            if expr.op == "-":
                return f"((-({operand})) & wmask({eval_width}))"
            if expr.op == "~":
                # Same "force 0 wherever ambiguous" fix as the fixed-width
                # branch above, extended (via the same sign/zero rule
                # already applied to `operand` itself just above) to match
                # `operand`'s own width here.
                operand_mask = self._emit_mask_expr(expr.operand, ow)
                if eff_signed and eval_width > ow:
                    operand_mask = f"_sign_ext({operand_mask}, {ow})"
                return f"((~({operand})) & (~({operand_mask})) & wmask({eval_width}))"
            return f"({operand})"

        # Any other unary op reaching here (none currently do -- `!` and
        # every reduction op are handled above, `~`/`+`/`-` above that):
        # fall back to a plain passthrough.
        operand = self._emit_expr(expr.operand, ow)
        return f"({operand})"

    def _emit_wide_reduction_to_value(self, expr: UnaryOp) -> tuple[str, str] | None:
        """Compute a reduction/`!` `UnaryOp` whose operand exceeds 64 bits
        via the wide emitter, returning `(value_expr, mask_expr)` strings
        usable as plain `long long` sub-expressions.

        `_wide_emitter.py`'s `_emit_wide_expr_to_scratch` already has
        correct multi-word reduction support (`wide_reduce_and`/`_or`/
        `_xor`, dispatched for every reduction op and `!` -- see its own
        `_REDUCE_PRIMS` table and the `!`-specific branch right below it)
        -- it's just never been reachable from THIS (narrow) emitter's
        own `_emit_unary`, which always computed reductions via native
        `long long` operand/mask strings, silently losing any operand
        bits beyond the register's own 64. Calling straight into it here
        and hoisting its (necessarily multi-statement, scratch-array-
        based) computation into `_et` temps reuses that already-correct
        logic instead of reimplementing it. The reduction's OWN result is
        always exactly 1 bit, so it always fits back into a native
        register afterward even though computing it correctly requires
        scanning the operand's full width.

        Returns None if hoisting isn't available in this context (no
        `_et_pending` list to append to) or the wide emitter doesn't
        recognize this node shape -- caller falls back to the narrow
        formula, which remains correct up to 64 bits either way.
        """
        if self._et_pending is None:
            return None
        cached_v = self._et_node_vals.get(id(expr))
        cached_m = self._et_node_masks.get(id(expr))
        if cached_v is not None and cached_m is not None:
            return cached_v, cached_m
        slot = self._alloc_scratch()
        lines = self._emit_wide_expr_to_scratch(expr, slot, 1, 1, 0)
        if lines is None:
            self._free_scratch(slot)
            return None
        # `_emit_wide_lhs_write_new` (a WIDE-destination assignment's own
        # entry point into this same recursive scratch emitter) always
        # sets this flag once it decides to use wide scratch space at
        # all -- it's what controls whether the module's wide-primitive
        # helper functions (`wide_reduce_or`, `wide_or`, `wide_shl`, ...)
        # get emitted into the generated .pyx AT ALL (see
        # `_module_has_wide_state`). This call site is a NARROW-
        # destination caller reaching the same scratch machinery for the
        # first time, and needs the identical flag -- without it, a
        # module whose ONLY wide computation is a reduction like this one
        # (no signal/memory in the module is itself >64 bits, and no
        # OTHER assignment goes through the wide-destination path) never
        # emits the wide-primitive definitions its own generated call
        # here still references, which Cython's own type checker catches
        # as "Converting to Python object not allowed without gil"
        # (an undefined-identifier error, not a logic bug) rather than a
        # silent runtime failure.
        self._needs_wide_helpers = True
        n = self._et_count
        self._et_count += 1
        self._et_pending.extend(lines)
        self._et_pending.append(f"cdef long long _et{n}_v = <long long>_sc{slot}_v[0]")
        self._et_pending.append(f"cdef long long _et{n}_m = <long long>_sc{slot}_m[0]")
        self._free_scratch(slot)
        self._et_node_vals[id(expr)] = f"_et{n}_v"
        self._et_node_masks[id(expr)] = f"_et{n}_m"
        return f"_et{n}_v", f"_et{n}_m"

    def _emit_wide_truthy_to_value(self, condition: Expression) -> tuple[str, str] | None:
        """Compute a TernaryOp condition's truthiness via the wide emitter
        when the condition's own self-determined width exceeds 64 bits or
        it internally computes wider than that, returning `(value_expr,
        mask_expr)` strings usable as plain `long long` sub-expressions.

        Mirrors `_stmt_emitters.py`'s `_emit_condition_lines_and_expr`
        (same wide-detection check, same `wide_logical_truth` primitive)
        -- that helper already fixed this exact bug class for `if`/
        `while`/`for` statement conditions, but discards the mask (its
        own callers don't need it, relying on the "value reads 0
        wherever ambiguous" convention). `_emit_ternary_value_mask_exprs`
        needs the REAL mask too, since its "cond_known1"/"cond_mask_zero"
        branch selection consumes `cond`/`cond_mask` as a pair -- so this
        hoists both halves via the `_et_pending` temp mechanism instead
        of returning raw lines (an expression emitter can't return
        multiple statements the way a statement emitter can).

        `wide_logical_truth`'s (value, mask) pair is semantically
        equivalent to a reduce-OR: `dv[0]=1` if any known-1 bit exists
        anywhere, `dm[0]=0` if the result is unambiguous (either a
        known-1 exists, or every bit is fully known and none are 1).
        This is exactly the lens `cond`/`cond_mask` are consumed through
        downstream, so substituting this pair in for the condition's raw
        (truncated, narrow) value/mask is transparent to the rest of the
        ternary's merge formula.

        Returns None if hoisting isn't available in this context, the
        condition isn't actually wide, or the wide emitter doesn't
        recognize this node shape -- caller falls back to the narrow
        formula, which remains correct up to 64 bits either way.
        """
        if self._et_pending is None:
            return None
        w = self._expr_width(condition)
        wide = (
            w > _WORD_BITS
            or self._expr_uses_wide_signal(condition)
            or self._expr_max_internal_width(condition) > _WORD_BITS
        )
        if not wide:
            return None
        cached_v = self._et_node_vals.get(id(condition))
        cached_m = self._et_node_masks.get(id(condition))
        if cached_v is not None and cached_m is not None:
            return cached_v, cached_m
        cond_n = max(1, (w + 63) // 64)
        cond_n = max(cond_n, (self._expr_max_internal_width(condition) + 63) // 64)
        cond_n = max(cond_n, self._module_max_wide_words())
        self._dynamic_max_wide_words = max(self._dynamic_max_wide_words, cond_n)
        cond_slot = self._alloc_scratch()
        cond_lines = self._emit_wide_expr_to_scratch(condition, cond_slot, cond_n, w, 0)
        if cond_lines is None:
            self._free_scratch(cond_slot)
            return None
        self._needs_wide_helpers = True
        truth_slot = self._alloc_scratch()
        cond_lines.append(
            f"wide_logical_truth(_sc{truth_slot}_v, _sc{truth_slot}_m, _sc{cond_slot}_v, _sc{cond_slot}_m, {cond_n})"
        )
        self._free_scratch(cond_slot, truth_slot)
        n = self._et_count
        self._et_count += 1
        self._et_pending.extend(cond_lines)
        self._et_pending.append(f"cdef long long _et{n}_v = <long long>_sc{truth_slot}_v[0]")
        self._et_pending.append(f"cdef long long _et{n}_m = <long long>_sc{truth_slot}_m[0]")
        self._et_node_vals[id(condition)] = f"_et{n}_v"
        self._et_node_masks[id(condition)] = f"_et{n}_m"
        return f"_et{n}_v", f"_et{n}_m"

    def _emit_reduction(self, op: str, operand: str, operand_mask: str, width: int) -> str:  # noqa: PLR0911
        """Emit a reduction operator (result is 1 bit).

        Must independently return 0 whenever the true result is
        genuinely ambiguous (a known-0 bit forces &/~& definitely
        non-x, a known-1 bit forces |/~|/! definitely non-x -- mirrors
        `Value.reduce_and`/`reduce_or` in `sim/value.py`, and the
        already-correct mask formula in `_emit_mask_expr`'s sibling
        UnaryOp branch below) -- NOT just compute from the operand's raw
        (x-positions-already-zeroed) value, which can look spuriously
        "definite" purely because unknown bits happen to read as 0. Most
        callers pair this with that mask expression and separately do
        `value & ~mask` before using the result, which would paper over
        this -- but `_emit_if`/`_emit_for` (unlike assignment contexts)
        use this VALUE directly for truthiness with no mask check at
        all, relying on the "value is 0 wherever the true result is x"
        convention that every value formula must uphold on its own.
        Confirmed against Icarus for `if ((-(~&a4[41:23])))` with `a4`
        fully x -- the old `~&` formula returned 1 (a spurious "definite
        true") instead of 0, picking the wrong `if` branch.
        """
        mask = _cy_hex((1 << width) - 1)
        known_zero = f"((~({operand})) & (~({operand_mask})) & {mask})"
        known_one = f"(({operand}) & (~({operand_mask})) & {mask})"
        any_x = f"(({operand_mask}) & {mask})"
        if op == "&":
            return f"(0 if {known_zero} else (0 if {any_x} else (1 if (({operand}) & {mask}) == {mask} else 0)))"
        if op == "~&":
            return f"(1 if {known_zero} else (0 if {any_x} else (0 if (({operand}) & {mask}) == {mask} else 1)))"
        if op == "|":
            return f"(1 if {known_one} else (0 if {any_x} else (1 if (({operand}) & {mask}) != 0 else 0)))"
        if op == "~|":
            return f"(0 if {known_one} else (0 if {any_x} else (0 if (({operand}) & {mask}) != 0 else 1)))"
        if op == "^":
            # XOR reduction has no absorbing bit -- any x bit anywhere
            # makes the parity genuinely ambiguous, unlike &/| which can
            # still resolve definitely via a single known bit.
            return f"(0 if {any_x} else _xor_reduce({operand}, {width}))"
        if op in ("~^", "^~"):
            return f"(0 if {any_x} else (1 if _xor_reduce({operand}, {width}) == 0 else 0))"
        return "0"

    def _emit_concat(self, expr: Concatenation, width: int | None = None) -> str:
        """Emit concatenation: {a, b, c} ΓåÆ (a << (wb+wc)) | (b << wc) | c."""
        parts = expr.parts
        widths = self._concat_eval_widths(parts, width)
        # Total shift for each part (parts are MSB-first in Verilog)
        result_parts: list[str] = []
        shift = sum(widths)
        for i, part in enumerate(parts):
            shift -= widths[i]
            # `_emit_expr`'s raw C `long long` result is only meaningful
            # within its own `widths[i]` bits -- e.g. `$signed(1'b1)`
            # emits `_sign_ext(1, 1)`, which is -1 (ALL 64 bits set) as a
            # raw C value, matching the natural C representation of
            # "signed -1" but NOT scoped to `widths[i]`. Without masking
            # here first, those spurious high bits bleed into the NEXT
            # part's own bit range once shifted/OR'd together, corrupting
            # neighboring concat members. Confirmed against Icarus (via
            # the identical bug in `_emit_replication` below) for
            # `{2{$signed((a4 <= a6))}}`.
            val = f"(({self._emit_expr(part, widths[i])}) & wmask({widths[i]}))"
            if shift >= 64:
                continue  # would overflow long long, truncated
            elif shift > 0:
                result_parts.append(f"(<long long>({val}) << {shift})")
            else:
                result_parts.append(f"({val})")
        return "(" + " | ".join(result_parts) + ")" if result_parts else "0"

    def _emit_replication(self, expr: Replication) -> str:
        """Emit replication: {N{a}} ΓåÆ repeated OR-shift."""
        if isinstance(expr.count, Literal):
            count = int(expr.count.value)
        else:
            resolved = _const_int(expr.count, self._param_env)
            if resolved is not None:
                count = resolved
            else:
                return "0"  # non-constant replication count not supported in codegen
        val_w = self._expr_width(expr.value)
        # `_emit_expr`'s raw C `long long` result is only meaningful within
        # its own `val_w` bits -- e.g. `$signed(1'b1)` emits `_sign_ext(1,
        # 1)`, which is -1 (ALL 64 bits set) as a raw C value, matching the
        # natural C representation of "signed -1" but NOT scoped to
        # `val_w`. Without masking here first, those spurious high bits
        # survive the shift+OR tiling below and corrupt every tile but the
        # last, since each tile is ORed in at its own bit offset assuming
        # only the low `val_w` bits are meaningful. Confirmed against
        # Icarus for `a2 << {2{$signed((a4 <= a6))}}`.
        val = f"(({self._emit_expr(expr.value, val_w)}) & wmask({val_w}))"
        if count <= 1:
            return f"({val})"
        # Build: (val << (val_w*(count-1))) | ... | (val << val_w) | val
        parts = []
        for i in range(count):
            s = val_w * (count - 1 - i)
            if s >= 64:
                continue  # would overflow long long, truncated
            elif s > 0:
                parts.append(f"(<long long>({val}) << {s})")
            else:
                parts.append(f"({val})")
        return "(" + " | ".join(parts) + ")" if parts else "0"

    def _emit_assignment_pattern(self, expr: AssignmentPattern, width: int) -> str:
        """Emit a SystemVerilog assignment pattern as a bit-packing expression."""
        if expr.default_value is not None:
            val = self._emit_expr(expr.default_value, width)
            if width > 0:
                mask = _cy_hex((1 << width) - 1)
                return f"(({val}) & {mask})"
            return val

        if expr.named_pairs:
            layout = self._resolve_assignment_pattern_layout(expr)
            if layout is not None:
                parts = []
                for name, val_expr in expr.named_pairs:
                    field_info = layout.fields.get(name)
                    if field_info is None:
                        continue
                    offset, fw = field_info
                    val = self._emit_expr(val_expr, fw)
                    fmask = _cy_hex((1 << fw) - 1)
                    if offset > 0:
                        parts.append(f"((<long long>(({val}) & {fmask})) << {offset})")
                    else:
                        parts.append(f"(({val}) & {fmask})")
                return "(" + " | ".join(parts) + ")" if parts else "0"

        if expr.positional:
            # Pack positional values MSB-first (first element in highest bits)
            parts = []
            shift = 0
            for val_expr in reversed(expr.positional):
                vw = self._expr_width(val_expr)
                val = self._emit_expr(val_expr, vw)
                if shift > 0:
                    parts.append(f"((<long long>({val})) << {shift})")
                else:
                    parts.append(f"({val})")
                shift += vw
            return "(" + " | ".join(parts) + ")" if parts else "0"

        return "0"

    def _emit_py_assignment_pattern(self, expr: AssignmentPattern, width: int) -> str | None:
        """Emit a SystemVerilog assignment pattern as a Python bigint expression."""
        if expr.default_value is not None:
            val = self._emit_py_expr(expr.default_value, width)
            if val is None:
                return None
            return f"(({val}) & {self._emit_py_width_mask(width)})"

        if expr.named_pairs:
            layout = self._resolve_assignment_pattern_layout(expr)
            if layout is not None:
                parts = []
                for name, val_expr in expr.named_pairs:
                    field_info = layout.fields.get(name)
                    if field_info is None:
                        continue
                    offset, field_width = field_info
                    val = self._emit_py_expr(val_expr, field_width)
                    if val is None:
                        return None
                    packed = f"(({val}) & {self._emit_py_width_mask(field_width)})"
                    if offset > 0:
                        parts.append(f"(({packed}) << {offset})")
                    else:
                        parts.append(packed)
                return "(" + " | ".join(parts) + ")" if parts else "0"

        if expr.positional:
            parts = []
            shift = 0
            for val_expr in reversed(expr.positional):
                value_width = self._expr_width(val_expr)
                val = self._emit_py_expr(val_expr, value_width)
                if val is None:
                    return None
                packed = f"(({val}) & {self._emit_py_width_mask(value_width)})"
                if shift > 0:
                    parts.append(f"(({packed}) << {shift})")
                else:
                    parts.append(packed)
                shift += value_width
            return "(" + " | ".join(parts) + ")" if parts else "0"

        return "0"

    def _resolve_assignment_pattern_layout(self, expr: AssignmentPattern):
        """Resolve the packed struct layout for a named assignment pattern."""
        from ..elaborate import match_assignment_pattern_layout  # noqa: PLC0415

        layout = match_assignment_pattern_layout(expr, self._struct_type_map)
        if layout is not None or not expr.named_pairs:
            return layout

        field_names = ", ".join(sorted({name for name, _ in expr.named_pairs}))
        raise NotImplementedError(
            "Compiled engine cannot lower named assignment pattern without a matching "
            f"packed struct layout for fields {{{field_names}}}."
        )

    @staticmethod
    def _remap_local_identifiers(root, local_names: set[str], prefix: str) -> None:
        """Prefix local function/task identifiers, including struct-field bases."""
        for node in root.walk():
            if not isinstance(node, Identifier):
                continue
            if node.name in local_names:
                node.name = f"{prefix}.{node.name}"
            if node.hierarchy and node.hierarchy[0] in local_names:
                node.hierarchy[0] = f"{prefix}.{node.hierarchy[0]}"

    def _emit_assignment_pattern_mask(self, expr: AssignmentPattern, width: int) -> str:
        """Emit the x/z mask expression for a packed assignment pattern."""
        if expr.named_pairs:
            layout = self._resolve_assignment_pattern_layout(expr)
            if layout is not None:
                ordered_fields = sorted(layout.fields.items(), key=lambda item: item[1][0], reverse=True)
                named_values = {name: value_expr for name, value_expr in expr.named_pairs}
                result_parts = []
                offset = layout.total_width
                for field_name, (_field_offset, field_width) in ordered_fields:
                    offset -= field_width
                    field_expr = named_values.get(field_name, expr.default_value)
                    field_mask = "0" if field_expr is None else self._emit_mask_expr(field_expr, field_width)
                    if offset >= 64:
                        continue
                    if offset > 0:
                        result_parts.append(f"(<long long>({field_mask}) << {offset})")
                    else:
                        result_parts.append(f"({field_mask})")
                return "(" + " | ".join(result_parts) + ")" if result_parts else "0"

        if expr.positional:
            part_widths = [self._expr_width(part) for part in expr.positional]
            result_parts = []
            offset = sum(part_widths)
            for part, part_width in zip(expr.positional, part_widths):
                offset -= part_width
                part_mask = self._emit_mask_expr(part, part_width)
                if offset >= 64:
                    continue
                if offset > 0:
                    result_parts.append(f"(<long long>({part_mask}) << {offset})")
                else:
                    result_parts.append(f"({part_mask})")
            return "(" + " | ".join(result_parts) + ")" if result_parts else "0"

        if expr.default_value is not None:
            default_width = width or self._expr_width(expr.default_value)
            return self._emit_mask_expr(expr.default_value, default_width)

        return "0"

    def _emit_py_assignment_pattern_mask(self, expr: AssignmentPattern, width: int) -> str | None:
        """Emit the x/z mask of a packed assignment pattern as a Python bigint expression."""
        if expr.named_pairs:
            layout = self._resolve_assignment_pattern_layout(expr)
            if layout is not None:
                ordered_fields = sorted(layout.fields.items(), key=lambda item: item[1][0], reverse=True)
                named_values = {name: value_expr for name, value_expr in expr.named_pairs}
                result_parts = []
                offset = layout.total_width
                for field_name, (_field_offset, field_width) in ordered_fields:
                    offset -= field_width
                    field_expr = named_values.get(field_name, expr.default_value)
                    field_mask = "0" if field_expr is None else self._emit_py_mask_expr(field_expr, field_width)
                    if field_mask is None:
                        return None
                    packed = f"(({field_mask}) & {self._emit_py_width_mask(field_width)})"
                    if offset > 0:
                        result_parts.append(f"(({packed}) << {offset})")
                    else:
                        result_parts.append(packed)
                return "(" + " | ".join(result_parts) + ")" if result_parts else "0"

        if expr.positional:
            parts = []
            shift = 0
            for part in reversed(expr.positional):
                part_width = self._expr_width(part)
                part_mask = self._emit_py_mask_expr(part, part_width)
                if part_mask is None:
                    return None
                packed = f"(({part_mask}) & {self._emit_py_width_mask(part_width)})"
                if shift > 0:
                    parts.append(f"(({packed}) << {shift})")
                else:
                    parts.append(packed)
                shift += part_width
            return "(" + " | ".join(parts) + ")" if parts else "0"

        if expr.default_value is not None:
            mask = self._emit_py_mask_expr(expr.default_value, width)
            if mask is None:
                return None
            return f"(({mask}) & {self._emit_py_width_mask(width)})"

        return "0"

    def _emit_func_call(self, call: FunctionCall, width: int) -> str:  # noqa: PLR0911
        name = call.name.lower()
        if name == "$unsigned":
            if call.arguments:
                arg_w = self._expr_width(call.arguments[0])
                return self._emit_expr(call.arguments[0], arg_w)
            return "0"
        if name == "$signed":
            if call.arguments:
                arg_w = self._expr_width(call.arguments[0])
                arg = self._emit_expr(call.arguments[0], arg_w)
                return f"_sign_ext({arg}, {arg_w})"
            return "0"
        if name == "$clog2":
            if call.arguments:
                arg = self._emit_expr(call.arguments[0], 32)
                return f"_clog2({arg})"
            return "0"
        if name == "$bits":
            if call.arguments:
                arg0 = call.arguments[0]
                # Check for typedef name: $bits(typename)
                if isinstance(arg0, Identifier):
                    bits_key = f"$bits:{arg0.name}"
                    if bits_key in self._param_env:
                        return str(int(self._param_env[bits_key]))
                w = self._expr_width(arg0)
                return str(w)
            return "0"
        # User-defined function
        func = self._function_map.get(call.name)
        if func is not None:
            # Each argument must be emitted AT its own PORT's declared
            # width, not a hardcoded `32` -- the generated
            # `_user_func_XXX` helper does mask the incoming raw value
            # down to the port's width on its own (`c.val[sid] = arg_i &
            # wmask(w)`, see `_gen_sections.py`'s `_gen_user_functions`),
            # which happens to correctly recover the right VALUE for most
            # expression shapes regardless of what width they were
            # emitted at (a plain Identifier/BitSelect's own C
            # representation already holds its full correct value,
            # unaffected by a narrower REQUESTED width). But some node
            # shapes -- notably a `TernaryOp` with an ambiguous (x/z)
            # condition, whose "bitwise-merge both branches" fallback
            # masks its OWN result to the REQUESTED width as part of
            # computing the merge itself, not just as an afterthought --
            # genuinely lose real high-order bits when asked for a width
            # narrower than the argument's own true content. Confirmed
            # against Icarus (cross-engine, `vm`/`reference`/`vm-fast`
            # all already agreed) for `fn_xor64s(((cond) ? a6 : (-a3)),
            # a6[35])` with `fn_xor64s(input signed [63:0] a, ...)`: the
            # old hardcoded `32` truncated the ternary's own ambiguous-
            # condition merge computation for the 63/64-bit-wide argument
            # down to 32 bits, corrupting high-order bits of the value
            # actually stored into port `a`.
            return self._emit_user_func_call_expr(func, call)
        # Unsupported function ΓåÆ 0
        return "0"

    def _emit_user_func_call_expr(self, func, call: FunctionCall) -> str:
        """Build a `_user_func_XXX(c, v0, m0, v1, m1, ...)` call expression.

        Shared by `_emit_func_call` (VALUE side) and `_emit_mask_expr`'s
        FunctionCall case (MASK side, which re-emits this SAME call
        expression to force the function to run before reading
        `c.mask[ret_sid]` -- see that call site's own docstring). Each
        argument is emitted AT its own PORT's declared width (not a
        hardcoded, possibly-too-narrow width -- see `_emit_func_call`'s
        docstring above for the concrete Icarus-confirmed repro this
        avoids), as a (value, mask) PAIR: `_user_func_XXX`'s own
        generated signature (`_gen_sections.py`'s `_gen_user_functions`)
        takes both per argument, since a single `long long` has no room
        for a value AND its x/z mask -- passing only the value would
        silently discard any x/z-ness in every argument before it ever
        reached the function body.
        """
        port_widths = [_range_width(port.width, self._param_env) for port in func.ports]
        args: list[str] = []
        for a, w in zip(call.arguments, port_widths, strict=False):
            args.append(self._emit_expr(a, w))
            args.append(self._emit_mask_expr(a, w))
        safe_name = _safe_ident(call.name)
        return f"_user_func_{safe_name}(c, {', '.join(args)})" if args else f"_user_func_{safe_name}(c)"

    # ΓöÇΓöÇ Expression width computation ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    def _expr_width(self, expr: Expression) -> int:  # noqa: PLR0911, PLR0912
        """Compute the compile-time bit-width of an expression."""
        etype = type(expr)
        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            if sid is not None:
                return self._signal_widths[sid]
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is not None:
                return struct_info[4]
            return 1
        if etype is Literal:
            return expr.width or 32
        if etype is BitSelect:
            mem_access = self._resolve_memory_element_expr(expr)
            if mem_access is not None:
                mid, _name, _indices = mem_access
                return self._mem_info[mid][0]
            return 1
        if etype is RangeSelect:
            if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                return int(expr.msb.value) - int(expr.lsb.value) + 1
            msb_val = _const_int(expr.msb, self._param_env)
            lsb_val = _const_int(expr.lsb, self._param_env)
            if msb_val is not None and lsb_val is not None:
                return msb_val - lsb_val + 1
            return 1
        if etype is PartSelect:
            if isinstance(expr.width, Literal):
                return int(expr.width.value)
            w_val = _const_int(expr.width, self._param_env)
            if w_val is not None:
                return w_val
            return 1
        if etype is Concatenation:
            return sum(self._expr_width(p) for p in expr.parts)
        if etype is Replication:
            if isinstance(expr.count, Literal):
                return int(expr.count.value) * self._expr_width(expr.value)
            resolved = _const_int(expr.count, self._param_env)
            if resolved is not None:
                return resolved * self._expr_width(expr.value)
            return self._expr_width(expr.value)
        if etype is AssignmentPattern:
            from ..elaborate import match_assignment_pattern_layout  # noqa: PLC0415

            if expr.named_pairs:
                layout = match_assignment_pattern_layout(expr, self._struct_type_map)
                if layout is not None:
                    return layout.total_width
            if expr.positional:
                return sum(self._expr_width(part) for part in expr.positional)
            if expr.default_value is not None:
                return self._expr_width(expr.default_value)
            return 1
        if etype is BinaryOp:
            if expr.op in _COMPARISON_OPS:
                return 1
            if expr.op in ("<<", "<<<"):
                # Left-shift result needs left_width + shift bits, not just max().
                # Without this, pend_is_level_last << 32 in a 32-bit context gets
                # masked to 0 by wmask(32), corrupting packed tuser fields.
                lw = self._expr_width(expr.left)
                if isinstance(expr.right, Literal) and not (expr.right.is_x or expr.right.is_z):
                    return lw + int(expr.right.value)
                return max(lw, self._expr_width(expr.right))
            if expr.op in (">>", ">>>", "**"):
                # A shift's self-determined width is its LEFT operand's width
                # only (IEEE 1364-2005 Table 5-22) -- the shift amount (right
                # operand) never contributes bits to the result and must not
                # be folded into a max() here. Without this, a shift amount
                # that happens to be a wide `integer` (32 bits by Verilog
                # default) corrupts this into an inflated width estimate,
                # which then wrongly widens a `~`/`+`/`-` unary operand
                # nested in the shift's left operand before the operator
                # runs (see notes/known_issues.md, item 2.6's regression).
                # `**` (power) shares this SAME row in Table 5-22 (`>> <<
                # ** >>> <<<` -> `L(i)`, exponent always self-determined) --
                # verified directly against the primary spec text; mirrors
                # the identical fix in `sim/evaluator.py`/`sim/vm/compiler.py`.
                return self._expr_width(expr.left)
            return max(self._expr_width(expr.left), self._expr_width(expr.right))
        if etype is UnaryOp:
            if expr.op in _REDUCTION_OPS or expr.op == "!":
                return 1
            return self._expr_width(expr.operand)
        if etype is TernaryOp:
            return max(self._expr_width(expr.true_expr), self._expr_width(expr.false_expr))
        if etype is StringLiteral:
            return len(expr.value) * 8
        if etype is FunctionCall:
            name = expr.name.lower()
            if name in {"$signed", "$unsigned"} and expr.arguments:
                return self._expr_width(expr.arguments[0])
            func = self._function_map.get(expr.name)
            if func is not None:
                ret_sid = self._signal_map.get(f"__func_{func.name}.{func.name}")
                if ret_sid is not None:
                    return self._signal_widths[ret_sid]
            return 32
        return 32

    def _expr_signed(self, expr: Expression, cache: dict[int, bool] | None = None) -> bool:
        """Return True if *expr* is fully signed per IEEE 1364-2005 §5.5.

        Uses ``self._signal_signed`` for signal signedness lookups.
        When *cache* is provided, intermediate results are memoised.
        """
        if cache is not None:
            key = id(expr)
            cached = cache.get(key)
            if cached is not None:
                return cached

        etype = type(expr)

        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            result = sid is not None and sid < len(self._signal_signed) and self._signal_signed[sid]

        elif etype is Literal:
            result = expr.signed

        elif etype in (BitSelect, RangeSelect, PartSelect):
            # Always unsigned (§5.5.1), regardless of the target's declared
            # signedness.
            result = False

        elif etype is UnaryOp:
            # `!` and all reduction ops always produce an unsigned 1-bit
            # result regardless of the operand's own signedness (IEEE
            # 1364-2005 §5.5.1) -- only the context-determined
            # pass-through ops (~, +, -) inherit the operand's signedness.
            if expr.op in ("!", "&", "|", "^", "~&", "~|", "~^", "^~"):
                result = False
            else:
                result = self._expr_signed(expr.operand, cache)

        elif etype is BinaryOp:
            # Comparisons and logical ops always produce an unsigned 1-bit
            # result regardless of operand signedness (IEEE 1364-2005
            # §5.5.1, Table 5-22).
            if expr.op in ("<<", ">>", "<<<", ">>>"):
                result = self._expr_signed(expr.left, cache)
            elif expr.op in ("==", "!=", "===", "!==", "<", "<=", ">", ">=", "&&", "||"):
                result = False
            else:
                result = self._expr_signed(expr.left, cache) and self._expr_signed(expr.right, cache)

        elif etype is TernaryOp:
            result = self._expr_signed(expr.true_expr, cache) and self._expr_signed(expr.false_expr, cache)

        elif etype in (Concatenation, Replication):
            result = False

        elif etype is FunctionCall:
            result = expr.name.lower() == "$signed"

        else:
            result = False

        if cache is not None:
            cache[key] = result
        return result

    def _emit_index_expr(self, expr: Expression) -> str:
        """Emit an index expression using its natural width for wrap semantics."""
        idx_width = self._expr_width(expr)
        if idx_width < 1:
            idx_width = 1
        if idx_width > 32:
            idx_width = 32
        idx_expr = self._emit_expr(expr, idx_width)
        return f"(({idx_expr}) & wmask({idx_width}))"

    # ΓöÇΓöÇ Mask expression generation ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    def _emit_mask_expr(self, expr: Expression, width: int, signed_override: bool | None = None) -> str:  # noqa: PLR0911, PLR0912
        """Return a Cython mask expression string for *expr*.

        Mirrors _emit_expr() but tracks x/z mask propagation.  For ternary
        operators the mask follows the selected branch instead of OR-ing all
        branches (the key difference from the naive all-signals OR).

        *signed_override*, when not ``None``, mirrors `_emit_expr`'s
        parameter of the same name -- most call sites don't pass it (the
        overwhelming majority of existing callers never needed sign-aware
        mask extension), but a handful of BinaryOp branches below that
        already compute a combined-signedness decision for the VALUE side
        (arithmetic `+ - * / %`, comparisons) thread it through here too,
        so a signed Identifier operand's mask gets `_sign_ext`'d the same
        way its value does -- see the Identifier case immediately below
        for why this matters.
        """
        etype = type(expr)

        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            if sid is not None:
                sig_width = self._signal_widths[sid]
                mask = f"c.mask[{sid}]"
                # Mirrors `_emit_expr`'s IDENTICAL Identifier logic for the
                # VALUE -- this was previously MISSING entirely here: the
                # raw mask register was returned completely unaware of
                # `width`/signedness, so a signed Identifier whose VALUE
                # gets `_sign_ext`'d to a wider context (e.g. by a
                # comparison's combined-signedness decision) had its mask
                # silently NOT sign-extended to match -- an x (masked)
                # sign bit's ambiguity never propagated into the newly-
                # filled upper bits, which then read back as spuriously
                # DEFINED zero instead of x, corrupting any downstream
                # "known bit differs" precision check that relied on
                # those bits' mask being accurate (e.g. `==`'s own
                # `known_diff` short-circuit below). Confirmed wrong
                # (cross-engine, against the reference oracle) for
                # `(a0 == a6)` with `a0` a signed 1-bit x-valued register
                # and `a6` a large defined 80-bit value: `a0`'s
                # un-sign-extended mask left bits 1-79 looking
                # "definitely 0", so XOR-ing against `a6`'s own nonzero
                # bits in that range falsely triggered `known_diff`,
                # resolving the comparison as definitely-not-equal instead
                # of correctly ambiguous.
                eff_signed = (
                    signed_override
                    if signed_override is not None
                    else (sid < len(self._signal_signed) and self._signal_signed[sid])
                )
                if width > sig_width and eff_signed:
                    mask = f"_sign_ext({mask}, {sig_width})"
                return mask
            struct_info = self._resolve_struct_access(name)
            if struct_info is not None:
                base_sid, offset, field_width = struct_info
                if offset >= _WORD_BITS or offset + field_width > _WORD_BITS:
                    return self._emit_signal_slice_expr(base_sid, str(offset), field_width, mask=True)
                wmask_val = _cy_lit((1 << field_width) - 1)
                return f"((c.mask[{base_sid}] >> {offset}) & {wmask_val})"
            return "0"

        if etype is Literal:
            if (hasattr(expr, "is_x") and expr.is_x) or (hasattr(expr, "is_z") and expr.is_z):
                return f"wmask({width})"
            # Check for x/z via Value.from_verilog for string literals
            if isinstance(expr.value, str):
                try:
                    v = Value.from_verilog(expr.original_text or expr.value)
                    if v.mask:
                        return _cy_lit(v.mask & ((1 << width) - 1))
                except (ValueError, TypeError):
                    pass
            return "0"

        if etype is BinaryOp:
            if expr.op in _COMPARISON_OPS:
                op_width = max(self._expr_width(expr.left), self._expr_width(expr.right))
            else:
                op_width = width
            # If this node's mask was already hoisted to a named temp (by
            # _emit_binary for +/- or by _emit_mask_expr below for |/&),
            # return it directly to keep the mask path O(k).
            cached_m = self._et_node_masks.get(id(expr))
            if cached_m is not None:
                return cached_m
            # Mirrors `_emit_binary`'s `combined_override`/comparison
            # `signed_override` computation on the VALUE side: `+ - * / %`
            # and comparison/equality operands ALL must be extended (mask
            # included, not just value) using this COMBINED decision, not
            # each operand's own individual signedness -- see
            # `_emit_binary`'s docstring for the full rationale (including
            # why the previous `+ - *`-only-uses-individual-signedness
            # design, based on a "residue-safe" argument, was wrong).
            if expr.op in ("+", "-", "*", "/", "%"):
                mask_override = (
                    signed_override
                    if signed_override is not None
                    else (self._expr_signed(expr.left) and self._expr_signed(expr.right))
                )
            elif expr.op in ("==", "!=", "===", "!==", "<", "<=", ">", ">="):
                mask_override = self._expr_signed(expr.left) and self._expr_signed(expr.right)
            else:
                mask_override = signed_override
            lm = self._emit_mask_expr(expr.left, op_width, mask_override)
            # The shift COUNT is self-determined -- see the identical note
            # in `_emit_binary`. Most node types' mask handling already
            # ignores an over-wide requested width internally (e.g. `~`
            # recomputes its own self-width), but request the amount's own
            # width here too for consistency/safety across all operand
            # shapes rather than relying on that per-node-type accident.
            if expr.op in ("<<", ">>", "<<<", ">>>"):
                rm = self._emit_mask_expr(expr.right, self._shift_amount_width(expr.right))
            elif expr.op == "**":
                # `**`'s exponent is always self-determined -- see the
                # identical note in `_emit_binary`.
                rm = self._emit_mask_expr(expr.right, self._expr_width(expr.right))
            else:
                rm = self._emit_mask_expr(expr.right, op_width, mask_override)
            if expr.op in ("==", "!="):
                # A KNOWN bit that differs resolves the comparison to a
                # definite result regardless of x/z bits elsewhere (mirrors
                # Value._cmp's "=="/"!=" short-circuit in sim/value.py) --
                # only fall back to x when no known bit disagrees and at
                # least one operand still has uncertainty. Unlike
                # </<=/>/>=  (handled by the generic `lm | rm` fallback
                # below, which IS correct there per Value._cmp's non-eq
                # branch), plain (in)equality does NOT go straight to x
                # just because some bit is unknown.
                lv = self._emit_expr(expr.left, op_width)
                rv = self._emit_expr(expr.right, op_width)
                known_diff = f"((({lv}) ^ ({rv})) & ~({lm}) & ~({rm}) & wmask({op_width}))"
                return f"(0 if {known_diff} else (({lm}) | ({rm})))"
            if expr.op in ("&&", "||"):
                # A known-nonzero (truthy) operand forces || definitely
                # true, and a known-EXACTLY-zero operand forces &&
                # definitely false, regardless of unrelated x/z bits in
                # the OTHER operand (mirrors Value.logical_and/logical_or's
                # precision note in sim/value.py) -- relies on this
                # codebase's invariant that a value's bits at masked (x/z)
                # positions are always 0, so a nonzero raw value implies a
                # genuine known-1 bit.
                lv = self._emit_expr(expr.left, op_width)
                rv = self._emit_expr(expr.right, op_width)
                if expr.op == "||":
                    return f"(0 if (({lv}) or ({rv})) else (({lm}) | ({rm})))"
                l_def_zero = f"(({lm}) == 0 and ({lv}) == 0)"
                r_def_zero = f"(({rm}) == 0 and ({rv}) == 0)"
                both_truthy = f"(({lv}) and ({rv}))"
                return (
                    f"(0 if ({l_def_zero}) else"
                    f" (0 if ({r_def_zero}) else"
                    f" (0 if ({both_truthy}) else (({lm}) | ({rm})))))"
                )
            if expr.op in {"+", "-", "*", "/", "%"}:
                # Arithmetic ops need the conservative "ANY x/z bit
                # ANYWHERE in either operand -> the ENTIRE result is x"
                # rule (already correctly applied to `+`/`-` here, but
                # `*`/`/`/`%` were missing it entirely, silently falling
                # through to the generic per-bit-position `lm | rm`
                # fallback at the end of this function -- appropriate for
                # a bitwise combining op, wrong for arithmetic, whose
                # carry/borrow/product/quotient chain can't be computed
                # with partial unknowns). Confirmed against Icarus
                # (cross-engine, against the reference oracle) for `((0 -
                # a3) * (a0 && a0))` with `a0` fully x: `a0 && a0`'s own
                # 1-bit x result, zero-extended into the wider
                # multiplication, has a mask with only its own low bit
                # set (the zero-extension padding bits are definitely 0)
                # -- the old `lm | rm` fallback let that single x bit's
                # position alone determine which RESULT bits read as x,
                # instead of correctly tainting the entire product.
                # Confirmed the identical shape for `/`/`%` too via a
                # divisor whose own x bit survives an `| 1` (used
                # elsewhere to dodge zero-divisor UB) at a different bit
                # position.
                return f"(wmask({width}) if (({lm}) | ({rm})) else 0)"
            if (
                expr.op == ">>>"
                and isinstance(expr.left, FunctionCall)
                and expr.left.name.lower() == "$signed"
                and len(expr.left.arguments) == 1
                and self._expr_width(expr.left.arguments[0]) > _WORD_BITS
            ):
                return f"(wmask({width}) if (({lm}) | ({rm})) else 0)"
            if expr.op in ("<<", ">>", "<<<", ">>>"):
                # An x/z shift COUNT makes the entire shift result x/z --
                # there's no way to know how many positions to shift, so
                # the result mask doesn't depend on WHICH bit positions of
                # the amount are unknown (unlike the generic `lm | rm`
                # fallback below), only on whether ANY of them are.
                # Confirmed against Icarus for `a2 >> ((^(a1 ? a5[4:3] :
                # a5)) + a3[25:16])` with a3 fully x.
                #
                # When the shift COUNT itself is known, the result's mask
                # is the left operand's mask SHIFTED BY THAT SAME AMOUNT --
                # not the left operand's raw, unshifted mask (`lm` alone,
                # returned below before this fix). `<<`/`>>` shift in
                # KNOWN-zero bits (never x/z) at the vacated end, so those
                # newly-vacated positions must read as known-0 in the mask
                # regardless of what `lm` said about that same bit position
                # BEFORE the shift -- reusing `lm` unshifted made a
                # shift-in-zero position look ambiguous whenever the
                # ORIGINAL (pre-shift) bit at that position happened to be
                # x/z. `>>>`'s sign-filled vacated bits need the sign
                # bit's own mask replicated there instead of a plain
                # shift, which `_sign_ext`-based handling elsewhere in
                # this codebase already does for other contexts but isn't
                # threaded through here -- left as the pre-existing
                # (unshifted, separately gapped) behavior for `>>>` only,
                # not addressed by this fix. Confirmed against Icarus
                # (cross-engine) for `~&((a2[6] ? a0 : a3) << a4[29:26])`
                # with `a3` fully x: the shift legitimately produces
                # known-0 low bits (forcing the NAND-reduction definitely
                # 1), but the unshifted mask hid that from the reduction,
                # making it read as fully ambiguous instead.
                if expr.op in ("<<", "<<<", ">>"):
                    rv = self._emit_expr(expr.right, self._shift_amount_width(expr.right))
                    # `>= 64` must compare `rv` as UNSIGNED -- see the
                    # identical fix (and its Icarus-confirmed rationale)
                    # on the VALUE-side shift core a few hundred lines
                    # above: a shift-amount expression computed via
                    # negation of a large value produces a bit pattern
                    # that's a huge magnitude read as unsigned but a
                    # large negative `long long` read as signed, letting
                    # an out-of-range amount slip past a signed `>= 64`
                    # guard into an actual negative-count shift.
                    if expr.op in ("<<", "<<<"):
                        lm_shifted = f"(0 if (<unsigned long long>({rv})) >= 64 else ((<long long>({lm})) << ({rv})))"
                    else:
                        lm_shifted = (
                            f"(0 if (<unsigned long long>({rv})) >= 64 else"
                            f" (<long long>(<unsigned long long>({lm}) >> <unsigned long long>({rv}))))"
                        )
                    return f"(wmask({width}) if ({rm}) else (({lm_shifted}) & wmask({width})))"
                return f"(wmask({width}) if ({rm}) else ({lm}))"
            if expr.op == "**":
                # `base == 0 and exponent < 0` (IEEE 1364-2005 Table 5-6)
                # is genuinely undefined ('bx) even when BOTH operands are
                # fully defined -- independent of the generic `lm | rm`
                # x-propagation below, which only covers actual x/z bits.
                # Mirrors `_emit_expr`'s identical signed-gating for
                # `_verilog_ipow`; unsigned exponents can never be
                # negative, so this condition never triggers there.
                both_signed = self._expr_signed(expr.left) and self._expr_signed(expr.right)
                if both_signed:
                    lv = self._emit_expr(expr.left, op_width)
                    rv = self._emit_expr(expr.right, self._expr_width(expr.right))
                    base_sx = f"_sign_ext({lv}, {self._expr_width(expr.left)})"
                    exp_sx = f"_sign_ext({rv}, {self._expr_width(expr.right)})"
                    undefined = f" or (({base_sx}) == 0 and ({exp_sx}) < 0)"
                else:
                    undefined = ""
                return f"(wmask({width}) if ((({lm}) | ({rm})){undefined}) else 0)"
            # For bitwise OR: known-1 in either input forces result to known-1
            # For bitwise AND: known-0 in either input forces result to known-0
            # Hoist the left sub-expression's value+mask to named temps when in
            # a temp context and the left operand is itself a |/& chain.  This
            # prevents O(k²) inline string growth (both lm and lv would otherwise
            # re-expand the same left subtree at each level of the chain).
            if expr.op == "|":
                lv = self._emit_expr(expr.left, op_width)
                rv = self._emit_expr(expr.right, op_width)
                if self._et_pending is not None and isinstance(expr.left, BinaryOp) and expr.left.op in {"|", "&"}:
                    n = self._et_count
                    self._et_count += 1
                    self._et_pending.append(f"cdef long long _et{n}_v = {lv}")
                    self._et_pending.append(f"cdef long long _et{n}_m = {lm}")
                    self._et_node_masks[id(expr.left)] = f"_et{n}_m"
                    self._et_node_vals[id(expr.left)] = f"_et{n}_v"
                    lv = f"_et{n}_v"
                    lm = f"_et{n}_m"
                return f"((({lm}) | ({rm})) & ~(({lv}) & ~({lm})) & ~(({rv}) & ~({rm})))"
            if expr.op == "&":
                lv = self._emit_expr(expr.left, op_width)
                rv = self._emit_expr(expr.right, op_width)
                if self._et_pending is not None and isinstance(expr.left, BinaryOp) and expr.left.op in {"|", "&"}:
                    n = self._et_count
                    self._et_count += 1
                    self._et_pending.append(f"cdef long long _et{n}_v = {lv}")
                    self._et_pending.append(f"cdef long long _et{n}_m = {lm}")
                    self._et_node_masks[id(expr.left)] = f"_et{n}_m"
                    self._et_node_vals[id(expr.left)] = f"_et{n}_v"
                    lv = f"_et{n}_v"
                    lm = f"_et{n}_m"
                return f"((({lm}) | ({rm})) & ~(~({lv}) & ~({lm})) & ~(~({rv}) & ~({rm})))"
            return f"({lm} | {rm})"

        if etype is UnaryOp:
            ow = self._expr_width(expr.operand)
            if expr.op in _REDUCTION_OPS or expr.op == "!":
                # Same "native long long can't represent a >64-bit operand"
                # gap as `_emit_unary`'s mirror-image VALUE-side branch --
                # see `_emit_wide_reduction_to_value`'s own docstring for
                # the full rationale and the concrete Icarus-confirmed
                # repro. `_emit_wide_reduction_to_value` caches by
                # `id(expr)`, so if the VALUE side already hoisted this
                # SAME node (the common case -- most callers want both),
                # this returns the identical already-computed `_et{n}_m`
                # instead of re-emitting the whole wide computation.
                if ow > _WORD_BITS:
                    wide = self._emit_wide_reduction_to_value(expr)
                    if wide is not None:
                        return wide[1]
                # A reduction's mask isn't just "pass through the operand's
                # mask" -- a known-0 bit forces &/~& definitely non-x, and a
                # known-1 bit forces |/~|/! definitely non-x, even when
                # other bits are x/z (mirrors the fix to `Value.reduce_and`/
                # `reduce_or` in sim/value.py). ^/~^/^~ have no absorbing
                # bit value, so any x bit does force x there.
                opv = self._emit_expr(expr.operand, ow)
                opm = self._emit_mask_expr(expr.operand, ow)
                wm = f"wmask({ow})"
                if expr.op in {"&", "~&"}:
                    return f"(0 if ((~({opv})) & (~({opm})) & {wm}) else (1 if (({opm}) & {wm}) else 0))"
                if expr.op in {"|", "~|", "!"}:
                    return f"(0 if (({opv}) & (~({opm})) & {wm}) else (1 if (({opm}) & {wm}) else 0))"
                return f"(1 if (({opm}) & {wm}) else 0)"
            if expr.op == "-":
                # Arithmetic negation propagates x the same way the `+`/`-`
                # BinaryOp mask handling above does: ANY unknown bit
                # anywhere in the operand makes the WHOLE result unknown
                # (2's-complement negation's borrow chain can't be
                # computed with partial unknowns) -- unlike `~` (bitwise
                # complement) below, which doesn't need this and can just
                # pass the operand's mask through bit-for-bit unchanged.
                # Confirmed against Icarus for `-$signed({a0, a1, a7})`
                # with a1 fully x.
                opm = self._emit_mask_expr(expr.operand, ow)
                return f"(wmask({width}) if ({opm}) else 0)"
            return self._emit_mask_expr(expr.operand, ow)

        if etype is TernaryOp:
            # Check if this node's mask was already hoisted (see symmetric block
            # in _emit_expr — the first caller caches both value+mask).
            cached_m = self._et_node_masks.get(id(expr))
            if cached_m is not None:
                return cached_m
            ternary_exprs = self._emit_ternary_value_mask_exprs(expr, width, py=False, signed_override=signed_override)
            assert ternary_exprs is not None
            value_str, mask_str = ternary_exprs
            if self._et_pending is not None:
                n = self._et_count
                self._et_count += 1
                self._et_pending.append(f"cdef long long _et{n}_v = {value_str}")
                self._et_pending.append(f"cdef long long _et{n}_m = {mask_str}")
                self._et_node_vals[id(expr)] = f"_et{n}_v"
                self._et_node_masks[id(expr)] = f"_et{n}_m"
                return f"_et{n}_m"
            return mask_str

        if etype is Concatenation:
            parts = expr.parts
            part_widths = self._concat_eval_widths(parts, width)
            total_w = sum(part_widths)
            result_parts = []
            offset = total_w
            for p, pw in zip(parts, part_widths):
                offset -= pw
                # Same masking-before-tiling reasoning as `_emit_concat`'s
                # value side -- a mask expression isn't guaranteed scoped
                # to `pw` bits either.
                mask = f"(({self._emit_mask_expr(p, pw)}) & wmask({pw}))"
                if offset >= 64:
                    continue
                elif offset > 0:
                    result_parts.append(f"(<long long>({mask}) << {offset})")
                else:
                    result_parts.append(f"({mask})")
            return "(" + " | ".join(result_parts) + ")" if result_parts else "0"

        if etype is Replication:
            if isinstance(expr.count, Literal):
                count = int(expr.count.value)
            else:
                resolved = _const_int(expr.count, self._param_env)
                if resolved is not None:
                    count = resolved
                else:
                    return f"wmask({width})"
            val_w = self._expr_width(expr.value)
            # Same masking-before-tiling reasoning as `_emit_replication`'s
            # value side.
            vm = f"(({self._emit_mask_expr(expr.value, val_w)}) & wmask({val_w}))"
            if count <= 1:
                return f"({vm})"
            parts = []
            for i in range(count):
                s = val_w * (count - 1 - i)
                if s >= 64:
                    continue
                elif s > 0:
                    parts.append(f"(<long long>({vm}) << {s})")
                else:
                    parts.append(f"({vm})")
            return "(" + " | ".join(parts) + ")" if parts else "0"

        if etype is AssignmentPattern:
            return self._emit_assignment_pattern_mask(expr, width)

        if etype is BitSelect:
            mem_access = self._resolve_memory_element_access(expr)
            if mem_access is not None:
                mid, idx, _name, _indices = mem_access
                return f"c.mem_{mid}_mask[({idx})]"
            if isinstance(expr.target, Identifier):
                tname = self._identifier_name(expr.target)
                sid = self._signal_map.get(tname)
            else:
                tname = None
                sid = None
            if sid is not None:
                index = self._emit_index_expr(expr.index)
                base = self._signal_bases.get(tname, 0)
                if base != 0:
                    index = f"(({index}) - {base})"
                return self._emit_signal_slice_expr(sid, index, 1, mask=True)
            if isinstance(expr.target, Identifier):
                struct_info = self._resolve_struct_access(tname)
                if struct_info is not None:
                    base_sid, offset, _field_width = struct_info
                    index = self._emit_index_expr(expr.index)
                    return self._emit_signal_slice_expr(base_sid, f"{offset} + ({index})", 1, mask=True)
                storage_info = self._resolve_struct_storage_access(tname)
                if storage_info is not None and storage_info[0] == "memory":
                    index_expr = self._emit_struct_storage_index_expr(storage_info[2])
                    if index_expr is None:
                        return "0"
                    index = self._emit_index_expr(expr.index)
                    return self._emit_mem_slice_expr(
                        storage_info[1],
                        index_expr,
                        f"{storage_info[3]} + ({index})",
                        1,
                        mask=True,
                        elem_width=self._mem_info[storage_info[1]][0],
                    )
            plain_mem_target = self._resolve_memory_element_access(expr.target)
            if plain_mem_target is not None:
                mid, idx, name, _indices = plain_mem_target
                elem_width = self._mem_info[mid][0]
                if elem_width > _WORD_BITS:
                    index = self._emit_index_expr(expr.index)
                    bit_base = self._memory_bases.get(name, 0)
                    if bit_base != 0:
                        index = f"(({index}) - {bit_base})"
                    return f"(_wmem{mid}_extract_mask(c, ({idx}), {index}) & 1)"
            target_m = self._emit_mask_expr(expr.target, self._expr_width(expr.target))
            index = self._emit_index_expr(expr.index)
            # Adjust for non-zero base offset (scalar signal or memory-element packed base)
            base = self._select_base(expr.target)
            if base != 0:
                index = f"(({index}) - {base})"
            return f"(({target_m}) >> ({index})) & 1"

        if etype is RangeSelect:
            if isinstance(expr.target, Identifier):
                tname = self._identifier_name(expr.target)
                sid = self._signal_map.get(tname)
                sig_base = self._signal_bases.get(tname, 0)
                if sid is not None:
                    if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                        msb_val = int(expr.msb.value) - sig_base
                        lsb_val = int(expr.lsb.value) - sig_base
                        sel_w = msb_val - lsb_val + 1
                        return self._emit_signal_slice_expr(sid, lsb_val, sel_w, mask=True)
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    if sig_base != 0:
                        msb = f"(({msb}) - {sig_base})"
                        lsb = f"(({lsb}) - {sig_base})"
                    return self._emit_signal_slice_expr(sid, f"({lsb})", f"(({msb}) - ({lsb}) + 1)", mask=True)
                struct_info = self._resolve_struct_access(tname)
                if struct_info is not None:
                    base_sid, offset, _field_width = struct_info
                    if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                        msb_val = int(expr.msb.value)
                        lsb_val = int(expr.lsb.value)
                        sel_w = msb_val - lsb_val + 1
                        return self._emit_signal_slice_expr(base_sid, f"{offset} + {lsb_val}", sel_w, mask=True)
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    return self._emit_signal_slice_expr(
                        base_sid, f"{offset} + ({lsb})", f"(({msb}) - ({lsb}) + 1)", mask=True
                    )
                storage_info = self._resolve_struct_storage_access(tname)
                if storage_info is not None and storage_info[0] == "memory":
                    index_expr = self._emit_struct_storage_index_expr(storage_info[2])
                    if index_expr is None:
                        return "0"
                    mid = storage_info[1]
                    offset = storage_info[3]
                    if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                        msb_val = int(expr.msb.value)
                        lsb_val = int(expr.lsb.value)
                        sel_w = msb_val - lsb_val + 1
                        return self._emit_mem_slice_expr(
                            mid,
                            index_expr,
                            offset + lsb_val,
                            sel_w,
                            mask=True,
                            elem_width=self._mem_info[mid][0],
                        )
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    sel_w = f"(({msb}) - ({lsb}) + 1)"
                    if width > _WORD_BITS:
                        return self._emit_wide_mem_dynamic_slice_expr(
                            mid,
                            index_expr,
                            f"{offset} + ({lsb})",
                            sel_w,
                            mask=True,
                        )
                    return f"(_wmem{mid}_extract_mask(c, ({index_expr}), {offset} + ({lsb})) & _word_mask64({sel_w}))"
            plain_mem_target = self._resolve_memory_element_access(expr.target)
            if plain_mem_target is not None:
                mid, idx, name, _indices = plain_mem_target
                elem_width = self._mem_info[mid][0]
                if elem_width > _WORD_BITS:
                    bit_base = self._memory_bases.get(name, 0)
                    if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                        msb_val = int(expr.msb.value) - bit_base
                        lsb_val = int(expr.lsb.value) - bit_base
                        sel_w = msb_val - lsb_val + 1
                        return self._emit_mem_slice_expr(mid, idx, lsb_val, sel_w, mask=True, elem_width=elem_width)
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    if bit_base != 0:
                        msb = f"(({msb}) - {bit_base})"
                        lsb = f"(({lsb}) - {bit_base})"
                    sel_w = f"(({msb}) - ({lsb}) + 1)"
                    if width > _WORD_BITS:
                        return self._emit_wide_mem_dynamic_slice_expr(mid, idx, f"({lsb})", sel_w, mask=True)
                    return f"(_wmem{mid}_extract_mask(c, ({idx}), ({lsb})) & _word_mask64({sel_w}))"
            target_m = self._emit_mask_expr(expr.target, self._expr_width(expr.target))
            # Determine base offset (scalar signal or memory-element packed base)
            sig_base = self._select_base(expr.target)
            if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                msb_val = int(expr.msb.value) - sig_base
                lsb_val = int(expr.lsb.value) - sig_base
                sel_w = msb_val - lsb_val + 1
                mask_hex = _cy_hex((1 << sel_w) - 1)
                return f"(({target_m}) >> {lsb_val}) & {mask_hex}"
            msb = self._emit_expr(expr.msb, 32)
            lsb = self._emit_expr(expr.lsb, 32)
            if sig_base != 0:
                msb = f"(({msb}) - {sig_base})"
                lsb = f"(({lsb}) - {sig_base})"
            return f"(({target_m}) >> ({lsb})) & wmask(({msb}) - ({lsb}) + 1)"

        if etype is PartSelect:
            if isinstance(expr.target, Identifier):
                tname = self._identifier_name(expr.target)
                sid = self._signal_map.get(tname)
                sig_base = self._signal_bases.get(tname, 0)
                if sid is not None:
                    base = self._emit_expr(expr.base, 32)
                    if isinstance(expr.width, Literal):
                        width_expr = str(int(expr.width.value))
                    else:
                        width_expr = self._emit_expr(expr.width, 32)
                    if sig_base != 0:
                        base = f"(({base}) - {sig_base})"
                    if expr.direction == "+:":
                        lsb_expr = base
                    else:
                        lsb_expr = f"({base}) - ({width_expr}) + 1"
                    return self._emit_signal_slice_expr(sid, lsb_expr, width_expr, mask=True)
                struct_info = self._resolve_struct_access(tname)
                if struct_info is not None:
                    base_sid, offset, _field_width = struct_info
                    base = self._emit_expr(expr.base, 32)
                    if isinstance(expr.width, Literal):
                        width_expr = str(int(expr.width.value))
                    else:
                        width_expr = self._emit_expr(expr.width, 32)
                    if expr.direction == "+:":
                        lsb_expr = f"{offset} + ({base})"
                    else:
                        lsb_expr = f"{offset} + ({base}) - ({width_expr}) + 1"
                    return self._emit_signal_slice_expr(base_sid, lsb_expr, width_expr, mask=True)
                storage_info = self._resolve_struct_storage_access(tname)
                if storage_info is not None and storage_info[0] == "memory":
                    index_expr = self._emit_struct_storage_index_expr(storage_info[2])
                    if index_expr is None:
                        return "0"
                    mid = storage_info[1]
                    offset = storage_info[3]
                    base = self._emit_expr(expr.base, 32)
                    if isinstance(expr.width, Literal):
                        width_expr = str(int(expr.width.value))
                    else:
                        width_expr = self._emit_expr(expr.width, 32)
                    if expr.direction == "+:":
                        lsb_expr = f"{offset} + ({base})"
                    else:
                        lsb_expr = f"{offset} + ({base}) - ({width_expr}) + 1"
                    width_arg: int | str = int(expr.width.value) if isinstance(expr.width, Literal) else width_expr
                    if width > _WORD_BITS:
                        return self._emit_wide_mem_dynamic_slice_expr(mid, index_expr, lsb_expr, width_arg, mask=True)
                    return f"(_wmem{mid}_extract_mask(c, ({index_expr}), {lsb_expr}) & _word_mask64({width_expr}))"
            plain_mem_target = self._resolve_memory_element_access(expr.target)
            if plain_mem_target is not None:
                mid, idx, name, _indices = plain_mem_target
                elem_width = self._mem_info[mid][0]
                if elem_width > _WORD_BITS:
                    base = self._emit_expr(expr.base, 32)
                    bit_base = self._memory_bases.get(name, 0)
                    if bit_base != 0:
                        base = f"(({base}) - {bit_base})"
                    if isinstance(expr.width, Literal):
                        width_expr = str(int(expr.width.value))
                    else:
                        width_expr = self._emit_expr(expr.width, 32)
                    if expr.direction == "+:":
                        lsb_expr = base
                    else:
                        lsb_expr = f"({base}) - ({width_expr}) + 1"
                    width_arg: int | str = int(expr.width.value) if isinstance(expr.width, Literal) else width_expr
                    if width > _WORD_BITS:
                        return self._emit_wide_mem_dynamic_slice_expr(mid, idx, lsb_expr, width_arg, mask=True)
                    return f"(_wmem{mid}_extract_mask(c, ({idx}), {lsb_expr}) & _word_mask64({width_expr}))"
            target_m = self._emit_mask_expr(expr.target, self._expr_width(expr.target))
            base = self._emit_expr(expr.base, 32)
            # Adjust for non-zero base offset (scalar signal or memory-element packed base)
            sig_base = self._select_base(expr.target)
            if sig_base != 0:
                base = f"(({base}) - {sig_base})"
            if isinstance(expr.width, Literal):
                pw = int(expr.width.value)
                mask_hex = _cy_hex((1 << pw) - 1)
            else:
                mask_hex = f"wmask({self._emit_expr(expr.width, 32)})"
            if expr.direction == "+:":
                return f"(({target_m}) >> ({base})) & {mask_hex}"
            return f"(({target_m}) >> (({base}) - ({self._emit_expr(expr.width, 32)}) + 1)) & {mask_hex}"

        if etype is FunctionCall:
            fname = expr.name.lower()
            if fname in {"$signed", "$unsigned"} and expr.arguments:
                # Mirrors `_emit_func_call`'s VALUE-side handling: `$signed`
                # sign-extends its argument's raw value from the argument's
                # OWN self-width up to the native register (via `_sign_ext`,
                # which fills every bit above that width with the source's
                # top bit) -- the MASK must be extended the identical way,
                # or an unknown (masked) sign bit's ambiguity silently gets
                # dropped from the newly-filled upper bits, which then read
                # back as spuriously DEFINED zeros instead of x. `$unsigned`
                # never sign_ext's the value (relies on the "unsigned values
                # keep upper native-register bits zero" invariant), so its
                # mask likewise passes through unextended. Confirmed wrong
                # (cross-engine, against the reference oracle) for
                # `(|($signed(a2[6:1]) ^ a1))` with `a2` fully x: `$signed`'s
                # 6-bit-wide all-x argument was sign-extended to a
                # DEFINED-looking `2'b11` in the top 2 bits of the 8-bit
                # XOR, instead of staying x -- corrupting the reduction's
                # truthiness (and, downstream, an `if` statement's branch
                # selection) from ambiguous to spuriously true.
                arg_w = self._expr_width(expr.arguments[0])
                arg_mask = self._emit_mask_expr(expr.arguments[0], arg_w)
                if fname == "$signed":
                    return f"_sign_ext({arg_mask}, {arg_w})"
                return arg_mask
            # User-defined function: `_user_func_XXX` returns only a
            # `long long` VALUE -- there is no mask channel in its call
            # signature at all, so its own internally-computed x/z bits
            # (`c.mask[ret_sid]`, set correctly by the generated function
            # body) never reach the caller through the return value. The
            # OLD fallback here approximated this by ORing together the
            # ARGUMENT masks (a crude "did any input look ambiguous"
            # guess), which is wrong in both directions: it can UNDER-
            # report (a function whose body happens to mask an ambiguous
            # argument away, e.g. `f = a & 0;`, would still show x here)
            # and, the bug actually hit, OVER-report -- the caller then
            # extends this approximate mask to the OUTER destination's
            # FULL width rather than the function's own narrower return
            # width, corrupting an otherwise-correctly-bounded x result
            # into an all-x one. Confirmed against Icarus (cross-engine,
            # `vm`/`vm-fast`/`reference` all already agreed) for
            # `fn_sub16s(a5, a5[35])` with `a5` fully x and `fn_sub16s`
            # declared `function signed [15:0] fn_sub16s(...)`: the
            # correct result is x only in fn_sub16s's own low 16 bits
            # (zero-extended into the wider destination), not all 64.
            #
            # `ret_sid` is a FIXED, per-FUNCTION (not per-call-site)
            # signal id (`_gen_user_functions` in `_gen_sections.py`
            # names it `__func_{name}.{name}`, no call-site suffix), so
            # `c.mask[ret_sid]` genuinely reflects whichever call most
            # recently ran -- calling the function a SECOND time here
            # (with the identical arguments already re-emitted for the
            # value side) is safe (Verilog functions are pure, input-only
            # ports by spec, so a repeat call has no side effects to
            # duplicate) and populates `c.mask[ret_sid]` correctly before
            # reading it. The `(... if (CALL or 1) else 0)` shape forces
            # the call to always run as part of evaluating the condition
            # (its own return value is discarded) while still producing a
            # single composable expression, matching this codebase's
            # existing ternary-as-expression-sequencing convention used
            # throughout this file.
            func = self._function_map.get(expr.name)
            if func is not None:
                ret_sid = self._signal_map.get(f"__func_{func.name}.{func.name}")
                if ret_sid is not None:
                    call_expr = self._emit_user_func_call_expr(func, expr)
                    return f"(c.mask[{ret_sid}] if ({call_expr} or 1) else 0)"
            # Fallback (function not found -- shouldn't happen in practice):
            # OR all argument masks, same crude approximation as before.
            if expr.arguments:
                parts = [self._emit_mask_expr(a, 32) for a in expr.arguments]
                return "(" + " | ".join(parts) + ")"
            return "0"

        if etype is StringLiteral:
            return "0"

        if etype is Mintypmax:
            return self._emit_mask_expr(expr.typ_val, width)

        return "0"

    # ΓöÇΓöÇ Sensitivity collection ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    def _walk_signals(self, expr: Expression, sigs: set[int]) -> None:  # noqa: PLR0911, PLR0912
        etype = type(expr)
        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            if name.startswith("__vt_local_for_"):
                return
            mid = self._mem_map.get(name)
            if mid is not None:
                sigs.add(self._mem_marker_sigs[mid])
                return
            sid = self._signal_map.get(name)
            if sid is not None:
                sigs.add(sid)
                return
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is not None:
                if struct_info[0] == "signal":
                    sigs.add(struct_info[1])
                else:
                    sigs.add(self._mem_marker_sigs[struct_info[1]])
                    if isinstance(struct_info[2], str):
                        sid = self._signal_map.get(struct_info[2])
                        if sid is not None:
                            sigs.add(sid)
            return
        if etype is Literal or etype is StringLiteral:
            return
        if etype is BinaryOp:
            self._walk_signals(expr.left, sigs)
            self._walk_signals(expr.right, sigs)
            return
        if etype is UnaryOp:
            self._walk_signals(expr.operand, sigs)
            return
        if etype is TernaryOp:
            self._walk_signals(expr.condition, sigs)
            self._walk_signals(expr.true_expr, sigs)
            self._walk_signals(expr.false_expr, sigs)
            return
        if etype is Concatenation:
            for p in expr.parts:
                self._walk_signals(p, sigs)
            return
        if etype is Replication:
            self._walk_signals(expr.count, sigs)
            self._walk_signals(expr.value, sigs)
            return
        if etype is AssignmentPattern:
            for _name, value_expr in expr.named_pairs:
                self._walk_signals(value_expr, sigs)
            if expr.positional:
                for value_expr in expr.positional:
                    self._walk_signals(value_expr, sigs)
            if expr.default_value is not None:
                self._walk_signals(expr.default_value, sigs)
            return
        if etype is BitSelect:
            mem_access = self._resolve_memory_element_access(expr)
            if mem_access is not None:
                mid, _idx, _name, indices = mem_access
                sigs.add(self._mem_marker_sigs[mid])
                for index_expr in indices:
                    self._walk_signals(index_expr, sigs)
                return
            self._walk_signals(expr.target, sigs)
            self._walk_signals(expr.index, sigs)
            return
        if etype is RangeSelect:
            self._walk_signals(expr.target, sigs)
            self._walk_signals(expr.msb, sigs)
            self._walk_signals(expr.lsb, sigs)
            return
        if etype is PartSelect:
            self._walk_signals(expr.target, sigs)
            self._walk_signals(expr.base, sigs)
            self._walk_signals(expr.width, sigs)
            return
        if etype is FunctionCall:
            for arg in expr.arguments:
                self._walk_signals(arg, sigs)
            return
        if etype is Mintypmax:
            self._walk_signals(expr.typ_val, sigs)
            return
