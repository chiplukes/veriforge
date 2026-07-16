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
        return "0"

    def _emit_expr(self, expr: Expression, width: int) -> str:  # noqa: PLR0911, PLR0912
        """Return a Cython value expression string for *expr*.

        *width* is the context width used for masking arithmetic results.
        """
        etype = type(expr)

        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            if sid is not None:
                return f"c.val[{sid}]"
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
            return self._emit_binary(expr, width)

        if etype is UnaryOp:
            return self._emit_unary(expr, width)

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
            ternary_exprs = self._emit_ternary_value_mask_exprs(expr, width, py=False)
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
                        return self._emit_signal_slice_expr(sid, lsb_val, sel_w)
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    if sig_base != 0:
                        msb = f"(({msb}) - {sig_base})"
                        lsb = f"(({lsb}) - {sig_base})"
                    return self._emit_signal_slice_expr(sid, f"({lsb})", f"(({msb}) - ({lsb}) + 1)")
                struct_info = self._resolve_struct_access(tname)
                if struct_info is not None:
                    base_sid, offset, _field_width = struct_info
                    if isinstance(expr.msb, Literal) and isinstance(expr.lsb, Literal):
                        msb_val = int(expr.msb.value)
                        lsb_val = int(expr.lsb.value)
                        sel_w = msb_val - lsb_val + 1
                        return self._emit_signal_slice_expr(base_sid, f"{offset} + {lsb_val}", sel_w)
                    msb = self._emit_expr(expr.msb, 32)
                    lsb = self._emit_expr(expr.lsb, 32)
                    return self._emit_signal_slice_expr(base_sid, f"{offset} + ({lsb})", f"(({msb}) - ({lsb}) + 1)")
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
                return f"(({target}) >> {lsb_val}) & {mask_hex}"
            msb = self._emit_expr(expr.msb, 32)
            lsb = self._emit_expr(expr.lsb, 32)
            if sig_base != 0:
                msb = f"(({msb}) - {sig_base})"
                lsb = f"(({lsb}) - {sig_base})"
            return f"(({target}) >> ({lsb})) & wmask(({msb}) - ({lsb}) + 1)"

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
            else:
                mask_hex = f"wmask({self._emit_expr(expr.width, 32)})"
            if expr.direction == "+:":
                return f"(({target}) >> ({base})) & {mask_hex}"
            # "-:" direction
            return f"(({target}) >> (({base}) - ({self._emit_expr(expr.width, 32)}) + 1)) & {mask_hex}"

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

    def _emit_ternary_value_mask_exprs(self, expr: TernaryOp, width: int, *, py: bool) -> tuple[str, str] | None:
        cond = self._emit_expr(expr.condition, 1)
        if py:
            true_expr = self._emit_py_expr(expr.true_expr, width)
            false_expr = self._emit_py_expr(expr.false_expr, width)
            cond_mask = self._emit_py_mask_expr(expr.condition, 1)
            true_mask = self._emit_py_mask_expr(expr.true_expr, width)
            false_mask = self._emit_py_mask_expr(expr.false_expr, width)
            width_mask = self._emit_py_width_mask(width)

            # Sign-extend signed branches when context width is larger
            if self._expr_signed(expr.true_expr):
                tw = self._expr_width(expr.true_expr)
                if width > tw and true_expr is not None:
                    true_expr = f"_sign_ext({true_expr}, {tw})"
            if self._expr_signed(expr.false_expr):
                fw = self._expr_width(expr.false_expr)
                if width > fw and false_expr is not None:
                    false_expr = f"_sign_ext({false_expr}, {fw})"
        else:
            true_expr = self._emit_expr(expr.true_expr, width)
            false_expr = self._emit_expr(expr.false_expr, width)
            cond_mask = self._emit_mask_expr(expr.condition, 1)
            true_mask = self._emit_mask_expr(expr.true_expr, width)
            false_mask = self._emit_mask_expr(expr.false_expr, width)
            width_mask = f"wmask({width})"

            # Sign-extend signed branches when context width is larger
            if self._expr_signed(expr.true_expr):
                tw = self._expr_width(expr.true_expr)
                if width > tw:
                    true_expr = f"_sign_ext({true_expr}, {tw})"
            if self._expr_signed(expr.false_expr):
                fw = self._expr_width(expr.false_expr)
                if width > fw:
                    false_expr = f"_sign_ext({false_expr}, {fw})"
        if true_expr is None or false_expr is None or cond_mask is None or true_mask is None or false_mask is None:
            return None
        known_mask = f"((~((({true_expr}) ^ ({false_expr})) | ({true_mask}) | ({false_mask}))) & {width_mask})"
        merged_value = f"(({true_expr}) & ({known_mask}))"
        merged_mask = f"(({width_mask}) ^ ({known_mask}))"
        value_expr = f"(({merged_value}) if ({cond_mask}) else (({true_expr}) if ({cond}) else ({false_expr})))"
        mask_expr = f"(({merged_mask}) if ({cond_mask}) else (({true_mask}) if ({cond}) else ({false_mask})))"
        return value_expr, mask_expr

    def _emit_py_expr(self, expr: Expression, width: int) -> str | None:  # noqa: PLR0911
        etype = type(expr)

        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            if sid is not None:
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

    def _emit_binary(self, expr: BinaryOp, width: int) -> str:  # noqa: PLR0911
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

        left = self._emit_expr(expr.left, op_width)
        right = self._emit_expr(expr.right, op_width)

        # Sign-extend signed operands when context width exceeds operand width
        # (IEEE 1364-2005 §5.5.2).  Skip comparisons (handled separately) and
        # shifts (left operand handled by the >>_ARITH path).
        # For division/modulus: always sign-extend signed operands from their
        # own width, since C's / and % treat operands as signed only when the
        # value is at its native signed width.
        if expr.op not in _COMPARISON_OPS and expr.op not in ("<<", ">>", "<<<", ">>>"):
            if self._expr_signed(expr.left):
                lw = self._expr_width(expr.left)
                if op_width > lw or expr.op in ("/", "%"):
                    left = f"_sign_ext({left}, {lw})"
            if self._expr_signed(expr.right):
                rw = self._expr_width(expr.right)
                if op_width > rw or expr.op in ("/", "%"):
                    right = f"_sign_ext({right}, {rw})"

        if expr.op in {"+", "-"}:
            left_mask = self._emit_mask_expr(expr.left, op_width)
            right_mask = self._emit_mask_expr(expr.right, op_width)
            # Hoist the left sub-expression to named temps when inside a temp
            # context and the left operand is itself a +/- chain.  This converts
            # O(k²) inline string growth for k-term addition chains into O(k).
            if (
                self._et_pending is not None
                and isinstance(expr.left, BinaryOp)
                and expr.left.op in {"+", "-"}
            ):
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

        # XNOR: XOR then invert
        if expr.op in ("~^", "^~"):
            core = f"(~(({left}) ^ ({right})))"
            return f"({core}) & wmask({width})"

        # Power: use int(pow(...)) since Cython has no ** for C integers
        if expr.op == "**":
            return f"(<unsigned long long>pow(<double>({left}), <double>({right}))) & wmask({width})"

        # Arithmetic right shift: preserve the signed operand width before
        # truncating to the surrounding assignment width.
        if c_op == ">>_ARITH":
            if self._expr_signed(expr.left):
                signed_width = self._expr_width(expr.left)
                return f"(_sign_ext({left}, {signed_width}) >> ({right})) & wmask({width})"
            return f"(_sign_ext({left}, {width}) >> ({right})) & wmask({width})"

        if expr.op in _COMPARISON_OPS:
            # Signed relational comparison: both operands must be sign-extended
            # from their own widths so C long-long comparison uses 2's-complement.
            if expr.op in ("<", "<=", ">", ">=") and self._expr_signed(expr.left) and self._expr_signed(expr.right):
                lw = self._expr_width(expr.left)
                rw = self._expr_width(expr.right)
                return f"(1 if (_sign_ext({left}, {lw}) {c_op} _sign_ext({right}, {rw})) else 0)"
            # Unsigned relational: cast to unsigned long long so 64-bit values with
            # MSB=1 (stored as negative long long) compare correctly.
            if expr.op in ("<", "<=", ">", ">="):
                return f"(1 if (<unsigned long long>({left}) {c_op} <unsigned long long>({right})) else 0)"
            # Equality/logical -> sign-neutral, no cast needed
            return f"(1 if (({left}) {c_op} ({right})) else 0)"

        # Logical right shift: Verilog >> is unsigned (zero-fill).  Cython's >>
        # on long long is arithmetic (sign-extends MSB), so cast to unsigned.
        if expr.op == ">>":
            core = f"(<long long>(<unsigned long long>({left}) >> <unsigned long long>({right})))"
        # For left-shift, promote left operand to long long to avoid
        # C int overflow when small literal << large shift (e.g. 4095 << 20).
        elif expr.op in ("<<", "<<<"):
            core = f"((<long long>({left})) {c_op} ({right}))"
        elif expr.op in ("/", "%") and not self._expr_signed(expr.left) and not self._expr_signed(expr.right):
            # Unsigned division/modulus: cast both sides to avoid signed C behavior
            # on 64-bit values with MSB=1 stored as negative long long.
            core = f"(<long long>(<unsigned long long>({left}) {c_op} <unsigned long long>({right})))"
        else:
            core = f"(({left}) {c_op} ({right}))"
        if needs_mask:
            return f"({core}) & wmask({width})"
        return core

    def _emit_unary(self, expr: UnaryOp, width: int) -> str:
        ow = self._expr_width(expr.operand)

        # Reduction operators → 1-bit result (self-determined)
        if expr.op in _REDUCTION_OPS:
            operand = self._emit_expr(expr.operand, ow)
            return self._emit_reduction(expr.op, operand, ow)

        prefix = _UNARY_PREFIX.get(expr.op)
        if prefix is None:
            return "0"

        # Bitwise NOT (~) is self-determined: result width = operand width (ow),
        # regardless of the surrounding context (IEEE 1364-2005 §5.5 Table 5-22).
        # For signed operands in a wider context, sign-extend the result afterward.
        if expr.op == "~":
            operand = self._emit_expr(expr.operand, ow)
            result = f"((~({operand})) & wmask({ow}))"
            if self._expr_signed(expr.operand) and width and width > ow:
                return f"_sign_ext({result}, {ow})"
            return result

        # Unary +/- are context-determined for signed values: sign-extend the
        # signed operand from its own width into the evaluation context.
        if expr.op in ("+", "-"):
            eval_width = max(ow, width) if width else ow
            operand = self._emit_expr(expr.operand, eval_width)
            if self._expr_signed(expr.operand) and eval_width > ow:
                operand = f"_sign_ext({operand}, {ow})"

            if expr.op == "-":
                return f"((-({operand})) & wmask({eval_width}))"
            return f"({operand})"

        # Logical NOT (!) — self-determined 1-bit
        operand = self._emit_expr(expr.operand, ow)
        if expr.op == "!":
            return f"(1 if (({operand}) == 0) else 0)"
        return f"({operand})"

    def _emit_reduction(self, op: str, operand: str, width: int) -> str:  # noqa: PLR0911
        """Emit a reduction operator (result is 1 bit)."""
        # Use a helper approach: check all bits
        mask = _cy_hex((1 << width) - 1)
        if op == "&":
            return f"(1 if (({operand}) & {mask}) == {mask} else 0)"
        if op == "|":
            return f"(1 if (({operand}) & {mask}) != 0 else 0)"
        if op == "^":
            # XOR reduction: count set bits, result is parity
            return f"_xor_reduce({operand}, {width})"
        if op == "~&":
            return f"(0 if (({operand}) & {mask}) == {mask} else 1)"
        if op == "~|":
            return f"(0 if (({operand}) & {mask}) != 0 else 1)"
        if op in ("~^", "^~"):
            return f"(1 if _xor_reduce({operand}, {width}) == 0 else 0)"
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
            val = self._emit_expr(part, widths[i])
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
        val = self._emit_expr(expr.value, val_w)
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
            args = [self._emit_expr(a, 32) for a in call.arguments] if call.arguments else []
            return (
                f"_user_func_{_safe_ident(call.name)}(c, {', '.join(args)})"
                if args
                else f"_user_func_{_safe_ident(call.name)}(c)"
            )
        # Unsupported function ΓåÆ 0
        return "0"

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
            result = self._expr_signed(expr.target, cache)

        elif etype is UnaryOp:
            if expr.op == "!":
                result = False
            else:
                result = self._expr_signed(expr.operand, cache)

        elif etype is BinaryOp:
            if expr.op in ("<<", ">>", "<<<", ">>>"):
                result = self._expr_signed(expr.left, cache)
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

    def _emit_mask_expr(self, expr: Expression, width: int) -> str:  # noqa: PLR0911, PLR0912
        """Return a Cython mask expression string for *expr*.

        Mirrors _emit_expr() but tracks x/z mask propagation.  For ternary
        operators the mask follows the selected branch instead of OR-ing all
        branches (the key difference from the naive all-signals OR).
        """
        etype = type(expr)

        if etype is Identifier:
            name = expr.name
            if expr.hierarchy:
                name = ".".join(expr.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            if sid is not None:
                return f"c.mask[{sid}]"
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
            lm = self._emit_mask_expr(expr.left, op_width)
            rm = self._emit_mask_expr(expr.right, op_width)
            if expr.op in {"+", "-"}:
                return f"(wmask({width}) if (({lm}) | ({rm})) else 0)"
            if (
                expr.op == ">>>"
                and isinstance(expr.left, FunctionCall)
                and expr.left.name.lower() == "$signed"
                and len(expr.left.arguments) == 1
                and self._expr_width(expr.left.arguments[0]) > _WORD_BITS
            ):
                return f"(wmask({width}) if (({lm}) | ({rm})) else 0)"
            # For bitwise OR: known-1 in either input forces result to known-1
            # For bitwise AND: known-0 in either input forces result to known-0
            # Hoist the left sub-expression's value+mask to named temps when in
            # a temp context and the left operand is itself a |/& chain.  This
            # prevents O(k²) inline string growth (both lm and lv would otherwise
            # re-expand the same left subtree at each level of the chain).
            if expr.op == "|":
                lv = self._emit_expr(expr.left, op_width)
                rv = self._emit_expr(expr.right, op_width)
                if (
                    self._et_pending is not None
                    and isinstance(expr.left, BinaryOp)
                    and expr.left.op in {"|", "&"}
                ):
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
                if (
                    self._et_pending is not None
                    and isinstance(expr.left, BinaryOp)
                    and expr.left.op in {"|", "&"}
                ):
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
            return self._emit_mask_expr(expr.operand, ow)

        if etype is TernaryOp:
            # Check if this node's mask was already hoisted (see symmetric block
            # in _emit_expr — the first caller caches both value+mask).
            cached_m = self._et_node_masks.get(id(expr))
            if cached_m is not None:
                return cached_m
            ternary_exprs = self._emit_ternary_value_mask_exprs(expr, width, py=False)
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
                mask = self._emit_mask_expr(p, pw)
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
            vm = self._emit_mask_expr(expr.value, val_w)
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
            # Adjust for non-zero base offset
            if tname is not None:
                base = self._signal_bases.get(tname, 0)
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
            # Determine base offset
            sig_base = 0
            if isinstance(expr.target, Identifier):
                sig_base = self._signal_bases.get(self._identifier_name(expr.target), 0)
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
            # Adjust for non-zero base offset
            if isinstance(expr.target, Identifier):
                sig_base = self._signal_bases.get(self._identifier_name(expr.target), 0)
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
            # For functions, OR all argument masks
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
