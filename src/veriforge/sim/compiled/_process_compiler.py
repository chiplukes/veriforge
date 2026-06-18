"""Process-block and continuous-assign compilation mixin for CythonCodegen.

Contains _compile_continuous_assigns and all its helpers, plus
_compile_initial_blocks and _compile_always_blocks.
CythonCodegen inherits from _ProcessCompilerMixin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    Identifier,
    Literal,
    PartSelect,
    RangeSelect,
    TernaryOp,
)
from veriforge.model.statements import (
    CaseStatement,
    DelayControl,
    EventControl,
    ForeverLoop,
    ForLoop,
    IfStatement,
    RepeatLoop,
    SensitivityEdge,
    SeqBlock,
    SystemTaskCall,
    WaitStatement,
    WhileLoop,
)
from veriforge.sim.compiled._codegen_utils import (
    _WORD_BITS,
    _cy_lit,
    _const_int,
)

if TYPE_CHECKING:
    from veriforge.model.design import Module


class _ProcessCompilerMixin:
    """Mixin providing continuous-assign and process-block compilation for CythonCodegen."""

    __slots__ = ()

    def _compile_continuous_assigns(self, module: Module) -> None:
        for assign in module.continuous_assigns:
            sensitivity: set[int] = set()
            self._walk_signals(assign.rhs, sensitivity)

            # Concatenation LHS: assign {hi, lo} = x ΓåÆ decompose into multiple simple assigns
            if isinstance(assign.lhs, Concatenation):
                self._compile_concat_cont_assign(assign, sensitivity)
                continue

            # BitSelect LHS: assign out[idx] = rhs ΓåÆ read-modify-write one bit
            if isinstance(assign.lhs, BitSelect) and (
                isinstance(assign.lhs.target, Identifier)
                or (isinstance(assign.lhs.target, BitSelect) and isinstance(assign.lhs.target.target, Identifier))
            ):
                self._compile_bitselect_cont_assign(assign, sensitivity)
                continue

            # RangeSelect LHS: assign out[msb:lsb] = rhs ΓåÆ read-modify-write bit range
            if isinstance(assign.lhs, RangeSelect) and (
                isinstance(assign.lhs.target, Identifier)
                or (isinstance(assign.lhs.target, BitSelect) and isinstance(assign.lhs.target.target, Identifier))
            ):
                self._compile_rangeselect_cont_assign(assign, sensitivity)
                continue

            # PartSelect LHS: assign out[base +: width] = rhs → reuse range-write lowering
            if isinstance(assign.lhs, PartSelect) and (
                isinstance(assign.lhs.target, Identifier)
                or (isinstance(assign.lhs.target, BitSelect) and isinstance(assign.lhs.target.target, Identifier))
            ):
                self._compile_partselect_cont_assign(assign, sensitivity)
                continue

            if self._compile_struct_field_cont_assign(assign, sensitivity):
                continue

            lhs_sid = self._signal_map.get(assign.lhs.name) if isinstance(assign.lhs, Identifier) else None
            if lhs_sid is None:
                continue  # skip unsupported LHS
            lhs_w = self._signal_widths[lhs_sid]

            if isinstance(assign.lhs, Identifier) and isinstance(assign.rhs, Identifier):
                rhs_name = assign.rhs.name
                if assign.rhs.hierarchy:
                    rhs_name = ".".join(assign.rhs.hierarchy) + "." + rhs_name
                struct_info = self._resolve_struct_access(rhs_name)
                if struct_info is not None:
                    base_sid, offset, field_width = struct_info
                    lines = [f"    _whole_assign_slice_width_signal(c, {lhs_sid}, {base_sid}, {offset}, {field_width})"]
                    self._processes.append((sensitivity, lines))
                    continue

            if isinstance(assign.lhs, Identifier) and isinstance(assign.rhs, BitSelect):
                target = assign.rhs.target
                if isinstance(target, Identifier):
                    rhs_name = self._identifier_name(target)
                    rhs_mid = self._mem_map.get(rhs_name)
                    if rhs_mid is not None:
                        rhs_elem_w, _depth = self._mem_info[rhs_mid]
                        if lhs_w > _WORD_BITS or rhs_elem_w > _WORD_BITS:
                            idx = self._emit_index_expr(assign.rhs.index)
                            lines = [f"    _whole_assign_mem_elem_{rhs_mid}(c, {lhs_sid}, ({idx}))"]
                            self._processes.append((sensitivity, lines))
                            continue

            if isinstance(assign.lhs, Identifier):
                lines = self._emit_signed_literal_xor_shift_lines(lhs_sid, assign.rhs, indent=1, is_nba=False)
                if lines is not None:
                    self._processes.append((sensitivity, lines))
                    continue
                lines = self._emit_signed_identifier_xor_shift_lines(lhs_sid, assign.rhs, indent=1, is_nba=False)
                if lines is not None:
                    self._processes.append((sensitivity, lines))
                    continue
                lines = self._emit_signed_binop_shift_lines(lhs_sid, assign.rhs, indent=1, is_nba=False)
                if lines is not None:
                    self._processes.append((sensitivity, lines))
                    continue
                lines = self._emit_signed_signal_shift_lines(lhs_sid, assign.rhs, indent=1, is_nba=False)
                if lines is not None:
                    self._processes.append((sensitivity, lines))
                    continue
                lines = self._emit_wide_const_signal_lines(lhs_sid, assign.rhs, indent=1, is_nba=False)
                if lines is not None:
                    self._processes.append((sensitivity, lines))
                    continue

            if isinstance(assign.lhs, Identifier) and isinstance(assign.rhs, RangeSelect):
                target = assign.rhs.target
                if (
                    isinstance(target, Identifier)
                    and isinstance(assign.rhs.msb, Literal)
                    and isinstance(assign.rhs.lsb, Literal)
                ):
                    rhs_sid = self._signal_map.get(target.name)
                    if rhs_sid is not None:
                        sig_base = self._signal_bases.get(target.name, 0)
                        rhs_msb = int(assign.rhs.msb.value) - sig_base
                        rhs_lsb = int(assign.rhs.lsb.value) - sig_base
                        if rhs_lsb >= 0 and (
                            lhs_w > _WORD_BITS or self._signal_widths[rhs_sid] > _WORD_BITS or rhs_msb >= _WORD_BITS
                        ):
                            lines = [f"    _whole_assign_slice_const_signal(c, {lhs_sid}, {rhs_sid}, {rhs_lsb})"]
                            self._processes.append((sensitivity, lines))
                            continue
                    target_name = target.name
                    if target.hierarchy:
                        target_name = ".".join(target.hierarchy) + "." + target_name
                    struct_info = self._resolve_struct_access(target_name)
                    if struct_info is not None:
                        base_sid, offset, _field_width = struct_info
                        sig_base = self._signal_bases.get(target_name, 0)
                        rhs_msb = int(assign.rhs.msb.value) - sig_base
                        rhs_lsb = int(assign.rhs.lsb.value) - sig_base
                        if rhs_lsb >= 0:
                            sel_w = rhs_msb - rhs_lsb + 1
                            lines = [
                                f"    _whole_assign_slice_width_signal(c, {lhs_sid}, {base_sid}, {offset + rhs_lsb}, {sel_w})"
                            ]
                            self._processes.append((sensitivity, lines))
                            continue

            if isinstance(assign.lhs, Identifier) and isinstance(assign.rhs, PartSelect):
                target = assign.rhs.target
                if isinstance(target, Identifier):
                    rhs_sid = self._signal_map.get(target.name)
                    part_w = _const_int(assign.rhs.width, self._param_env)
                    if (
                        rhs_sid is not None
                        and part_w is not None
                        and (lhs_w > _WORD_BITS or self._signal_widths[rhs_sid] > _WORD_BITS or part_w > _WORD_BITS)
                    ):
                        sig_base = self._signal_bases.get(target.name, 0)
                        base_expr = self._emit_expr(assign.rhs.base, self._expr_width(assign.rhs.base))
                        if assign.rhs.direction == "+:":
                            lsb_expr = f"(({base_expr}) - {sig_base})"
                        elif assign.rhs.direction == "-:":
                            lsb_expr = f"(({base_expr}) - {part_w - 1} - {sig_base})"
                        else:
                            lsb_expr = None
                        if lsb_expr is not None:
                            lines = [f"    _whole_assign_slice_const_signal(c, {lhs_sid}, {rhs_sid}, {lsb_expr})"]
                            self._processes.append((sensitivity, lines))
                            continue
                    target_name = target.name
                    if target.hierarchy:
                        target_name = ".".join(target.hierarchy) + "." + target_name
                    struct_info = self._resolve_struct_access(target_name)
                    if struct_info is not None:
                        base_sid, offset, _field_width = struct_info
                        part_w_expr = (
                            str(part_w)
                            if part_w is not None
                            else self._emit_expr(assign.rhs.width, self._expr_width(assign.rhs.width))
                        )
                        base_expr = self._emit_expr(assign.rhs.base, self._expr_width(assign.rhs.base))
                        if assign.rhs.direction == "+:":
                            lsb_expr = f"{offset} + ({base_expr})"
                        elif part_w is not None:
                            lsb_expr = f"{offset} + ({base_expr}) - {part_w - 1}"
                        else:
                            lsb_expr = f"{offset} + ({base_expr}) - ({part_w_expr}) + 1"
                        lines = [
                            f"    _whole_assign_slice_width_signal(c, {lhs_sid}, {base_sid}, {lsb_expr}, {part_w_expr})"
                        ]
                        self._processes.append((sensitivity, lines))
                        continue

            if isinstance(assign.lhs, Identifier) and isinstance(assign.rhs, Concatenation):
                flat_parts = self._flatten_concat_identifier_parts(assign.rhs)
                if flat_parts is not None:
                    total_width = sum(width for _, width, _, _ in flat_parts)
                    if total_width > _WORD_BITS or lhs_w > _WORD_BITS:
                        self._processes.append((sensitivity, self._emit_flat_concat_whole_assign(lhs_sid, flat_parts)))
                        continue

            if (
                isinstance(assign.lhs, Identifier)
                and isinstance(assign.rhs, BinaryOp)
                and isinstance(assign.rhs.left, Concatenation)
                and isinstance(assign.rhs.right, Literal)
                and assign.rhs.op in {"<<", ">>"}
            ):
                lines = self._emit_wide_flat_concat_shift_lines(lhs_sid, assign.rhs, indent=1, is_nba=False)
                if lines is not None:
                    self._processes.append((sensitivity, lines))
                    continue

            if (
                isinstance(assign.lhs, Identifier)
                and isinstance(assign.rhs, BinaryOp)
                and isinstance(assign.rhs.left, TernaryOp)
                and isinstance(assign.rhs.left.condition, Identifier)
                and isinstance(assign.rhs.left.true_expr, Identifier)
                and isinstance(assign.rhs.left.false_expr, Identifier)
                and isinstance(assign.rhs.right, Literal)
                and assign.rhs.op in {"<<", ">>"}
            ):
                lines = self._emit_wide_ternary_shift_lines(lhs_sid, assign.rhs, indent=1, is_nba=False)
                if lines is not None:
                    self._processes.append((sensitivity, lines))
                    continue

            # Fast path: signal-to-signal copy (no scratch needed)
            if isinstance(assign.lhs, Identifier):
                lines = self._emit_wide_signal_copy_lines(lhs_sid, assign.rhs, indent=1, is_nba=False)
                if lines is not None:
                    self._processes.append((sensitivity, lines))
                    continue

            # Multiply-shift matchers must precede the recursive emitter (double-width semantics)
            if isinstance(assign.lhs, Identifier) and isinstance(assign.rhs, BinaryOp):
                lines = self._emit_wide_signal_binop_shift_lines(lhs_sid, assign.rhs, indent=1, is_nba=False)
                if lines is not None:
                    self._processes.append((sensitivity, lines))
                    continue
                lines = self._emit_wide_mul_const_shift_lines(lhs_sid, assign.rhs, indent=1, is_nba=False)
                if lines is not None:
                    self._processes.append((sensitivity, lines))
                    continue
                # ! (logical NOT) before shift — not handled by recursive emitter
                lines = self._emit_wide_lnot_shift_lines(lhs_sid, assign.rhs, indent=1, is_nba=False)
                if lines is not None:
                    self._processes.append((sensitivity, lines))
                    continue

            lines = self._emit_wide_lhs_write_new(lhs_sid, assign.rhs, indent=1, is_nba=False)
            if lines is not None:
                self._processes.append((sensitivity, lines))
                continue

            lines = self._emit_wide_py_bits_lines(lhs_sid, assign.rhs, eval_width=lhs_w, indent=1, is_nba=False)
            if lines is not None:
                self._processes.append((sensitivity, lines))
                continue

            rhs_val = self._emit_expr(assign.rhs, lhs_w)

            # Build per-expression mask that tracks x/z through ternaries correctly
            mask_expr = self._emit_mask_expr(assign.rhs, lhs_w)

            rhs_w = self._expr_width(assign.rhs)
            if isinstance(assign.rhs, Identifier) and self._expr_signed(assign.rhs) and lhs_w > rhs_w:
                v_line = f"    cdef long long v = _sign_ext({rhs_val}, {rhs_w}) & wmask({lhs_w})"
            else:
                v_line = f"    cdef long long v = ({rhs_val}) & wmask({lhs_w})"

            lines = [
                v_line,
                f"    cdef long long m = {mask_expr}",
                "    v = v & ~m",
                f"    if v != c.val[{lhs_sid}] or m != c.mask[{lhs_sid}]:",
                f"        c.val[{lhs_sid}] = v",
                f"        c.mask[{lhs_sid}] = m",
                f"        c.dirty[{lhs_sid}] = 1",
            ]
            self._processes.append((sensitivity, lines))

    def _compile_struct_field_cont_assign(self, assign, sensitivity: set[int]) -> bool:
        """Compile continuous assign to a packed struct field.

        Handles flattened assigns such as ``arb.data = rhs`` by updating the
        packed base signal value and mask for only the targeted field.
        """
        lhs = assign.lhs
        if not isinstance(lhs, Identifier):
            return False

        name = lhs.name
        if lhs.hierarchy:
            name = ".".join(lhs.hierarchy) + "." + name

        if name in self._signal_map:
            return False
        struct_storage_range = self._resolve_struct_storage_mem_range(name)
        if struct_storage_range is not None:
            self._collect_struct_storage_index_sensitivity(name, sensitivity)
            mem_lhs, msb, lsb = struct_storage_range
            lines = self._emit_mem_range_write(mem_lhs, msb, lsb, assign.rhs, indent=1, is_nba=False)
            self._processes.append((sensitivity, lines))
            return True
        struct_info = self._resolve_struct_access(name)
        if struct_info is None:
            return False

        base_sid, offset, field_width = struct_info
        base_width = self._signal_widths[base_sid]
        rhs_sid = None
        if isinstance(assign.rhs, Identifier):
            rhs_name = assign.rhs.name
            if assign.rhs.hierarchy:
                rhs_name = ".".join(assign.rhs.hierarchy) + "." + rhs_name
            rhs_sid = self._signal_map.get(rhs_name)
        if base_width > _WORD_BITS and rhs_sid is not None and self._signal_widths[rhs_sid] == field_width:
            lines = [f"    _whole_assign_insert_signal(c, {base_sid}, {offset}, {rhs_sid}, {field_width})"]
            self._processes.append((sensitivity, lines))
            return True
        rhs_source = self._resolve_signal_slice_source(assign.rhs)
        if base_width > _WORD_BITS and field_width > _WORD_BITS and rhs_source is not None:
            rhs_source_sid, rhs_source_lsb = rhs_source
            lines = [
                f"    _whole_assign_insert_signal_slice(c, {base_sid}, {offset}, {rhs_source_sid},"
                f" <int>({rhs_source_lsb}), {field_width})"
            ]
            self._processes.append((sensitivity, lines))
            return True
        rhs_mem_source = self._resolve_memory_slice_source(assign.rhs)
        if base_width > _WORD_BITS and field_width > _WORD_BITS and rhs_mem_source is not None:
            rhs_mid, _rhs_idx, _rhs_lsb = rhs_mem_source
            if self._mem_info[rhs_mid][0] > _WORD_BITS:
                lines = self._emit_signal_mem_rhs_source_lines(
                    base_sid,
                    str(offset),
                    rhs_mem_source,
                    str(field_width),
                    1,
                    is_nba=False,
                )
                self._processes.append((sensitivity, lines))
                return True
        if base_width > _WORD_BITS and field_width <= _WORD_BITS:
            rhs_val = self._emit_expr(assign.rhs, field_width)
            rhs_mask = self._emit_mask_expr(assign.rhs, field_width)
            lines = [
                f"    _whole_assign_insert_word(c, {base_sid}, {offset}, {field_width}, <unsigned long long>(({rhs_val}) & wmask({field_width})), <unsigned long long>(({rhs_mask}) & wmask({field_width})))",
            ]
            self._processes.append((sensitivity, lines))
            return True
        field_mask = _cy_lit((1 << field_width) - 1)
        clear_mask = _cy_lit(((1 << base_width) - 1) ^ (((1 << field_width) - 1) << offset))
        rhs_val = self._emit_expr(assign.rhs, field_width)
        rhs_mask = self._emit_mask_expr(assign.rhs, field_width)

        lines = [
            f"    cdef long long rval = ({rhs_val}) & {field_mask}",
            f"    cdef long long rmask = ({rhs_mask}) & {field_mask}",
            f"    cdef long long new_val = (c.val[{base_sid}] & {clear_mask}) | ((rval & ~rmask) << {offset})",
            f"    cdef long long new_mask = (c.mask[{base_sid}] & {clear_mask}) | (rmask << {offset})",
            f"    if new_val != c.val[{base_sid}] or new_mask != c.mask[{base_sid}]:",
            f"        c.val[{base_sid}] = new_val",
            f"        c.mask[{base_sid}] = new_mask",
            f"        c.dirty[{base_sid}] = 1",
        ]
        self._processes.append((sensitivity, lines))
        return True

    def _compile_concat_cont_assign(self, assign, sensitivity: set[int]) -> None:
        """Compile continuous assign with concatenation LHS.

        assign {a, b, c} = rhs ΓåÆ generate one process per part,
        extracting the appropriate bit slice from the RHS.
        """
        parts = assign.lhs.parts
        part_width_infos = [self._concat_part_width_info(p) for p in parts]
        for part in parts:
            self._collect_lhs_read_signals(part, sensitivity)

        direct_copy_lines = self._emit_matching_concat_copy(assign.lhs, assign.rhs, 1, is_nba=False)
        if direct_copy_lines is not None:
            self._processes.append((sensitivity, direct_copy_lines))
            return

        # Build per-expression mask that tracks x/z through ternaries correctly
        wide_rhs_source = None
        wide_signal_rhs_source = None
        source = self._resolve_signal_slice_source(assign.rhs)
        if source is not None:
            sid, base_lsb = source
            if self._signal_widths[sid] > _WORD_BITS:
                wide_signal_rhs_source = (sid, base_lsb)
                if all(self._concat_part_max_width(p) <= _WORD_BITS for p in parts):
                    wide_rhs_source = (sid, base_lsb)

        if wide_rhs_source is None:
            rhs_width = self._expr_width(assign.rhs)
            rhs_val = self._emit_expr(assign.rhs, rhs_width)
            mask_expr = self._emit_mask_expr(assign.rhs, rhs_width)
        else:
            rhs_val = None
            mask_expr = None

        # Process each part (MSB-first in parts list)
        for i, part in enumerate(parts):
            pw, width_expr = part_width_infos[i]
            slice_mask = self._concat_slice_mask_expr(pw, width_expr)
            _offset_width, offset_expr = self._concat_width_sum(part_width_infos[i + 1 :])
            part_rhs_source = self._concat_part_wide_rhs_source(part, offset_expr, wide_signal_rhs_source)

            if part_rhs_source is not None:
                extract = "0"
                mask_extract = "0"
            elif wide_signal_rhs_source is not None and self._concat_part_max_width(part) <= _WORD_BITS:
                rhs_sid, rhs_base_lsb = wide_signal_rhs_source
                lsb_expr = offset_expr if rhs_base_lsb == "0" else f"({rhs_base_lsb}) + ({offset_expr})"
                extract = self._emit_signal_slice_expr(rhs_sid, lsb_expr, width_expr)
                mask_extract = self._emit_signal_slice_expr(rhs_sid, lsb_expr, width_expr, mask=True)
            elif wide_rhs_source is None:
                extract = self._emit_concat_rhs_extract(rhs_val, offset_expr, pw, width_expr)
                mask_extract = self._emit_concat_rhs_extract(mask_expr, offset_expr, pw, width_expr)
            else:
                rhs_sid, rhs_base_lsb = wide_rhs_source
                lsb_expr = offset_expr if rhs_base_lsb == "0" else f"({rhs_base_lsb}) + ({offset_expr})"
                extract = self._emit_signal_slice_expr(rhs_sid, lsb_expr, width_expr)
                mask_extract = self._emit_signal_slice_expr(rhs_sid, lsb_expr, width_expr, mask=True)
            mem_access = self._resolve_memory_element_access(part)
            if mem_access is not None:
                mid, idx, _name, _indices = mem_access
                lines = self._emit_const_mem_range_write_lines(
                    mid,
                    idx,
                    0,
                    pw,
                    extract,
                    mask_extract,
                    rhs_source=part_rhs_source,
                    marker_sid=self._mem_marker_sigs[mid],
                    indent=1,
                    is_nba=False,
                )
                self._processes.append((sensitivity, lines))
                continue
            if isinstance(part, PartSelect):
                if part.direction == "+:":
                    if isinstance(part.base, Literal):
                        base_val = int(part.base.value)
                        lsb_src = Literal(base_val, width=part.base.width if part.base.width else 32)
                        msb_src = Literal(base_val + pw - 1, width=part.base.width if part.base.width else 32)
                    else:
                        lsb_src = part.base
                        msb_src = part.base if pw == 1 else BinaryOp("+", part.base, Literal(pw - 1, width=32))
                else:
                    if isinstance(part.base, Literal):
                        base_val = int(part.base.value)
                        msb_src = Literal(base_val, width=part.base.width if part.base.width else 32)
                        lsb_src = Literal(base_val - pw + 1, width=part.base.width if part.base.width else 32)
                    else:
                        msb_src = part.base
                        lsb_src = part.base if pw == 1 else BinaryOp("-", part.base, Literal(pw - 1, width=32))
                mem_target = self._resolve_memory_element_access(part.target)
                if mem_target is not None:
                    mid, addr, name, _indices = mem_target
                    bit_base = self._memory_bases.get(name, 0)
                    if isinstance(lsb_src, Literal):
                        lines = self._emit_const_mem_range_write_lines(
                            mid,
                            addr,
                            int(lsb_src.value) - bit_base,
                            pw,
                            extract,
                            mask_extract,
                            rhs_source=part_rhs_source,
                            marker_sid=self._mem_marker_sigs[mid],
                            indent=1,
                            is_nba=False,
                        )
                    else:
                        msb = self._emit_expr(msb_src, 32)
                        lsb = self._emit_expr(lsb_src, 32)
                        if bit_base != 0:
                            msb = f"(({msb}) - {bit_base})"
                            lsb = f"(({lsb}) - {bit_base})"
                        lines = self._emit_dynamic_mem_range_write_lines(
                            mid,
                            addr,
                            msb,
                            lsb,
                            extract,
                            mask_extract,
                            rhs_source=part_rhs_source,
                            marker_sid=self._mem_marker_sigs[mid],
                            indent=1,
                            is_nba=False,
                        )
                    self._processes.append((sensitivity, lines))
                    continue
                if isinstance(part.target, Identifier):
                    struct_storage_range = self._resolve_struct_storage_mem_select_range(part.target, msb_src, lsb_src)
                    if struct_storage_range is not None:
                        mem_lhs, msb, lsb = struct_storage_range
                        access = self._resolve_memory_element_access(mem_lhs)
                        if access is not None:
                            mid, idx, _name, _indices = access
                            if isinstance(msb, Literal) and isinstance(lsb, Literal):
                                lines = self._emit_const_mem_range_write_lines(
                                    mid,
                                    idx,
                                    int(lsb.value),
                                    pw,
                                    extract,
                                    mask_extract,
                                    rhs_source=part_rhs_source,
                                    marker_sid=self._mem_marker_sigs[mid],
                                    indent=1,
                                    is_nba=False,
                                )
                            else:
                                lines = self._emit_dynamic_mem_range_write_lines(
                                    mid,
                                    idx,
                                    self._emit_expr(msb, 32),
                                    self._emit_expr(lsb, 32),
                                    extract,
                                    mask_extract,
                                    rhs_source=part_rhs_source,
                                    marker_sid=self._mem_marker_sigs[mid],
                                    indent=1,
                                    is_nba=False,
                                )
                            self._processes.append((sensitivity, lines))
                            continue
                    struct_signal_range = self._resolve_struct_signal_select_range(part.target, msb_src, lsb_src)
                    if struct_signal_range is not None:
                        sid, _abs_msb_expr, abs_lsb_expr = struct_signal_range
                        sig_base = self._signal_bases.get(self._signal_names[sid], 0)
                        if isinstance(abs_lsb_expr, Literal):
                            lsb = str(int(abs_lsb_expr.value) - sig_base)
                        else:
                            lsb = self._emit_expr(abs_lsb_expr, 32)
                            if sig_base != 0:
                                lsb = f"(({lsb}) - {sig_base})"
                        if part_rhs_source is not None and self._signal_widths[sid] > _WORD_BITS:
                            lines = self._emit_concat_signal_rhs_source_lines(
                                sid, lsb, part_rhs_source, width_expr, 1, is_nba=False
                            )
                        elif self._signal_widths[sid] > _WORD_BITS:
                            lines = [
                                f"    _whole_assign_insert_word(c, {sid}, <int>({lsb}), <int>({width_expr}), <unsigned long long>(({extract}) & {slice_mask}), <unsigned long long>(({mask_extract}) & {slice_mask}))",
                            ]
                        else:
                            range_mask = f"({slice_mask}) << ({lsb})"
                            lines = [
                                f"    cdef long long new_val = (c.val[{sid}] & ~({range_mask}))"
                                f" | (((({extract}) & ~({mask_extract})) & {slice_mask}) << ({lsb}))",
                                f"    cdef long long new_mask = (c.mask[{sid}] & ~({range_mask}))"
                                f" | (((({mask_extract}) & {slice_mask})) << ({lsb}))",
                                f"    if new_val != c.val[{sid}] or new_mask != c.mask[{sid}]:",
                                f"        c.val[{sid}] = new_val",
                                f"        c.mask[{sid}] = new_mask",
                                f"        c.dirty[{sid}] = 1",
                            ]
                        self._processes.append((sensitivity, lines))
                        continue
                    sid = self._signal_map.get(self._identifier_name(part.target))
                    if sid is not None:
                        sig_base = self._signal_bases.get(self._identifier_name(part.target), 0)
                        if isinstance(lsb_src, Literal):
                            lsb = str(int(lsb_src.value) - sig_base)
                        else:
                            lsb = self._emit_expr(lsb_src, 32)
                            if sig_base != 0:
                                lsb = f"(({lsb}) - {sig_base})"
                        if self._signal_widths[sid] > _WORD_BITS:
                            lines = [
                                f"    _whole_assign_insert_word(c, {sid}, <int>({lsb}), <int>({width_expr}), <unsigned long long>(({extract}) & {slice_mask}), <unsigned long long>(({mask_extract}) & {slice_mask}))",
                            ]
                        else:
                            range_mask = f"({slice_mask}) << ({lsb})"
                            lines = [
                                f"    cdef long long new_val = (c.val[{sid}] & ~({range_mask}))"
                                f" | (((({extract}) & ~({mask_extract})) & {slice_mask}) << ({lsb}))",
                                f"    cdef long long new_mask = (c.mask[{sid}] & ~({range_mask}))"
                                f" | (((({mask_extract}) & {slice_mask})) << ({lsb}))",
                                f"    if new_val != c.val[{sid}] or new_mask != c.mask[{sid}]:",
                                f"        c.val[{sid}] = new_val",
                                f"        c.mask[{sid}] = new_mask",
                                f"        c.dirty[{sid}] = 1",
                            ]
                        self._processes.append((sensitivity, lines))
                        continue
            if isinstance(part, RangeSelect):
                mem_target = self._resolve_memory_element_access(part.target)
                if mem_target is not None:
                    mid, addr, name, _indices = mem_target
                    bit_base = self._memory_bases.get(name, 0)
                    if isinstance(part.msb, Literal) and isinstance(part.lsb, Literal):
                        lines = self._emit_const_mem_range_write_lines(
                            mid,
                            addr,
                            int(part.lsb.value) - bit_base,
                            pw,
                            extract,
                            mask_extract,
                            rhs_source=part_rhs_source,
                            marker_sid=self._mem_marker_sigs[mid],
                            indent=1,
                            is_nba=False,
                        )
                    else:
                        msb = self._emit_expr(part.msb, 32)
                        lsb = self._emit_expr(part.lsb, 32)
                        if bit_base != 0:
                            msb = f"(({msb}) - {bit_base})"
                            lsb = f"(({lsb}) - {bit_base})"
                        lines = self._emit_dynamic_mem_range_write_lines(
                            mid,
                            addr,
                            msb,
                            lsb,
                            extract,
                            mask_extract,
                            rhs_source=part_rhs_source,
                            marker_sid=self._mem_marker_sigs[mid],
                            indent=1,
                            is_nba=False,
                        )
                    self._processes.append((sensitivity, lines))
                    continue
                if isinstance(part.target, Identifier):
                    struct_storage_range = self._resolve_struct_storage_mem_select_range(
                        part.target, part.msb, part.lsb
                    )
                    if struct_storage_range is not None:
                        mem_lhs, msb, lsb = struct_storage_range
                        access = self._resolve_memory_element_access(mem_lhs)
                        if access is not None:
                            mid, idx, _name, _indices = access
                            if isinstance(msb, Literal) and isinstance(lsb, Literal):
                                lines = self._emit_const_mem_range_write_lines(
                                    mid,
                                    idx,
                                    int(lsb.value),
                                    pw,
                                    extract,
                                    mask_extract,
                                    rhs_source=part_rhs_source,
                                    marker_sid=self._mem_marker_sigs[mid],
                                    indent=1,
                                    is_nba=False,
                                )
                            else:
                                lines = self._emit_dynamic_mem_range_write_lines(
                                    mid,
                                    idx,
                                    self._emit_expr(msb, 32),
                                    self._emit_expr(lsb, 32),
                                    extract,
                                    mask_extract,
                                    rhs_source=part_rhs_source,
                                    marker_sid=self._mem_marker_sigs[mid],
                                    indent=1,
                                    is_nba=False,
                                )
                            self._processes.append((sensitivity, lines))
                            continue
                    struct_signal_range = self._resolve_struct_signal_select_range(part.target, part.msb, part.lsb)
                    if struct_signal_range is not None:
                        sid, _abs_msb_expr, abs_lsb_expr = struct_signal_range
                        sig_base = self._signal_bases.get(self._signal_names[sid], 0)
                        if isinstance(abs_lsb_expr, Literal):
                            lsb = str(int(abs_lsb_expr.value) - sig_base)
                        else:
                            lsb = self._emit_expr(abs_lsb_expr, 32)
                            if sig_base != 0:
                                lsb = f"(({lsb}) - {sig_base})"
                        if part_rhs_source is not None and self._signal_widths[sid] > _WORD_BITS:
                            lines = self._emit_concat_signal_rhs_source_lines(
                                sid, lsb, part_rhs_source, width_expr, 1, is_nba=False
                            )
                        elif self._signal_widths[sid] > _WORD_BITS:
                            lines = [
                                f"    _whole_assign_insert_word(c, {sid}, <int>({lsb}), <int>({width_expr}), <unsigned long long>(({extract}) & {slice_mask}), <unsigned long long>(({mask_extract}) & {slice_mask}))",
                            ]
                        else:
                            range_mask = f"({slice_mask}) << ({lsb})"
                            lines = [
                                f"    cdef long long new_val = (c.val[{sid}] & ~({range_mask}))"
                                f" | (((({extract}) & ~({mask_extract})) & {slice_mask}) << ({lsb}))",
                                f"    cdef long long new_mask = (c.mask[{sid}] & ~({range_mask}))"
                                f" | (((({mask_extract}) & {slice_mask})) << ({lsb}))",
                                f"    if new_val != c.val[{sid}] or new_mask != c.mask[{sid}]:",
                                f"        c.val[{sid}] = new_val",
                                f"        c.mask[{sid}] = new_mask",
                                f"        c.dirty[{sid}] = 1",
                            ]
                        self._processes.append((sensitivity, lines))
                        continue
                    sid = self._signal_map.get(self._identifier_name(part.target))
                    if sid is not None:
                        sig_base = self._signal_bases.get(self._identifier_name(part.target), 0)
                        if isinstance(part.lsb, Literal):
                            lsb = str(int(part.lsb.value) - sig_base)
                        else:
                            lsb = self._emit_expr(part.lsb, 32)
                            if sig_base != 0:
                                lsb = f"(({lsb}) - {sig_base})"
                        if self._signal_widths[sid] > _WORD_BITS:
                            lines = [
                                f"    _whole_assign_insert_word(c, {sid}, <int>({lsb}), <int>({width_expr}), <unsigned long long>(({extract}) & {slice_mask}), <unsigned long long>(({mask_extract}) & {slice_mask}))",
                            ]
                        else:
                            range_mask = f"({slice_mask}) << ({lsb})"
                            lines = [
                                f"    cdef long long new_val = (c.val[{sid}] & ~({range_mask}))"
                                f" | (((({extract}) & ~({mask_extract})) & {slice_mask}) << ({lsb}))",
                                f"    cdef long long new_mask = (c.mask[{sid}] & ~({range_mask}))"
                                f" | (((({mask_extract}) & {slice_mask})) << ({lsb}))",
                                f"    if new_val != c.val[{sid}] or new_mask != c.mask[{sid}]:",
                                f"        c.val[{sid}] = new_val",
                                f"        c.mask[{sid}] = new_mask",
                                f"        c.dirty[{sid}] = 1",
                            ]
                        self._processes.append((sensitivity, lines))
                        continue
            if isinstance(part, BitSelect):
                mem_target = self._resolve_memory_element_access(part.target)
                if mem_target is not None:
                    mid, addr, name, _indices = mem_target
                    bit = self._emit_index_expr(part.index)
                    bit_base = self._memory_bases.get(name, 0)
                    if bit_base != 0:
                        bit = f"(({bit}) - {bit_base})"
                    lines = self._emit_mem_bit_write_lines(
                        mid,
                        addr,
                        bit,
                        extract,
                        mask_extract,
                        marker_sid=self._mem_marker_sigs[mid],
                        indent=1,
                        is_nba=False,
                        track_change=False,
                    )
                    self._processes.append((sensitivity, lines))
                    continue
                if isinstance(part.target, Identifier):
                    struct_storage_range = self._resolve_struct_storage_mem_select_range(
                        part.target, part.index, part.index
                    )
                    if struct_storage_range is not None:
                        mem_lhs, msb, lsb = struct_storage_range
                        access = self._resolve_memory_element_access(mem_lhs)
                        if access is not None:
                            mid, idx, _name, _indices = access
                            if isinstance(msb, Literal) and isinstance(lsb, Literal):
                                lines = self._emit_const_mem_range_write_lines(
                                    mid,
                                    idx,
                                    int(lsb.value),
                                    pw,
                                    extract,
                                    mask_extract,
                                    rhs_source=part_rhs_source,
                                    marker_sid=self._mem_marker_sigs[mid],
                                    indent=1,
                                    is_nba=False,
                                )
                            else:
                                lines = self._emit_mem_bit_write_lines(
                                    mid,
                                    idx,
                                    self._emit_expr(lsb, 32),
                                    extract,
                                    mask_extract,
                                    marker_sid=self._mem_marker_sigs[mid],
                                    indent=1,
                                    is_nba=False,
                                    track_change=False,
                                )
                            self._processes.append((sensitivity, lines))
                            continue
                    struct_signal_range = self._resolve_struct_signal_select_range(part.target, part.index, part.index)
                    if struct_signal_range is not None:
                        sid, _abs_msb_expr, abs_lsb_expr = struct_signal_range
                        idx = self._emit_expr(abs_lsb_expr, 32)
                        sig_base = self._signal_bases.get(self._signal_names[sid], 0)
                        if sig_base != 0:
                            idx = f"(({idx}) - {sig_base})"
                        if self._signal_widths[sid] > _WORD_BITS:
                            lines = [
                                f"    _whole_assign_insert_word(c, {sid}, <int>({idx}), 1, <unsigned long long>(({extract}) & 1), <unsigned long long>(({mask_extract}) & 1))",
                            ]
                        else:
                            lines = [
                                f"    cdef long long idx = ({idx}) & 0x3f",
                                f"    cdef long long rval = ({extract}) & 1",
                                f"    cdef long long m = ({mask_extract}) & 1",
                                f"    cdef long long new_val = (c.val[{sid}] & ~(1LL << idx)) | (rval << idx)",
                                f"    cdef long long new_mask = c.mask[{sid}] & ~(1LL << idx)",
                                "    if m:",
                                f"        new_val = c.val[{sid}]",
                                f"        new_mask = c.mask[{sid}] | (1LL << idx)",
                                f"    if new_val != c.val[{sid}] or new_mask != c.mask[{sid}]:",
                                f"        c.val[{sid}] = new_val",
                                f"        c.mask[{sid}] = new_mask",
                                f"        c.dirty[{sid}] = 1",
                            ]
                        self._processes.append((sensitivity, lines))
                        continue
                    sid = self._signal_map.get(self._identifier_name(part.target))
                    if sid is not None:
                        idx = self._emit_index_expr(part.index)
                        sig_base = self._signal_bases.get(self._identifier_name(part.target), 0)
                        if sig_base != 0:
                            idx = f"(({idx}) - {sig_base})"
                        if self._signal_widths[sid] > _WORD_BITS:
                            lines = [
                                f"    _whole_assign_insert_word(c, {sid}, <int>({idx}), 1, <unsigned long long>(({extract}) & 1), <unsigned long long>(({mask_extract}) & 1))",
                            ]
                        else:
                            lines = [
                                f"    cdef long long idx = ({idx}) & 0x3f",
                                f"    cdef long long rval = ({extract}) & 1",
                                f"    cdef long long m = ({mask_extract}) & 1",
                                f"    cdef long long new_val = (c.val[{sid}] & ~(1LL << idx)) | (rval << idx)",
                                f"    cdef long long new_mask = c.mask[{sid}] & ~(1LL << idx)",
                                "    if m:",
                                f"        new_val = c.val[{sid}]",
                                f"        new_mask = c.mask[{sid}] | (1LL << idx)",
                                f"    if new_val != c.val[{sid}] or new_mask != c.mask[{sid}]:",
                                f"        c.val[{sid}] = new_val",
                                f"        c.mask[{sid}] = new_mask",
                                f"        c.dirty[{sid}] = 1",
                            ]
                        self._processes.append((sensitivity, lines))
                        continue
            if not isinstance(part, Identifier):
                continue
            part_name = self._identifier_name(part)
            sid = self._signal_map.get(part_name)
            if sid is None:
                struct_storage_range = self._resolve_struct_storage_mem_range(part_name)
                if struct_storage_range is not None:
                    mem_lhs, msb, lsb = struct_storage_range
                    if not isinstance(msb, Literal) or not isinstance(lsb, Literal):
                        continue
                    access = self._resolve_memory_element_access(mem_lhs)
                    if access is None:
                        continue
                    mid, idx, _name, _indices = access
                    lines = self._emit_const_mem_range_write_lines(
                        mid,
                        idx,
                        int(lsb.value),
                        int(msb.value) - int(lsb.value) + 1,
                        extract,
                        mask_extract,
                        rhs_source=part_rhs_source,
                        marker_sid=self._mem_marker_sigs[mid],
                        indent=1,
                        is_nba=False,
                    )
                    self._processes.append((sensitivity, lines))
                    continue
                struct_info = self._resolve_struct_access(part_name)
                if struct_info is None:
                    continue
                base_sid, offset, _ = struct_info
                if part_rhs_source is not None and self._signal_widths[base_sid] > _WORD_BITS:
                    lines = self._emit_concat_signal_rhs_source_lines(
                        base_sid, str(offset), part_rhs_source, width_expr, 1, is_nba=False
                    )
                elif self._signal_widths[base_sid] > _WORD_BITS:
                    lines = [
                        f"    _whole_assign_insert_word(c, {base_sid}, {offset}, <int>({width_expr}), <unsigned long long>(({extract}) & {slice_mask}), <unsigned long long>(({mask_extract}) & {slice_mask}))",
                    ]
                else:
                    range_mask = f"({slice_mask}) << {offset}"
                    lines = [
                        f"    cdef long long new_val = (c.val[{base_sid}] & ~({range_mask}))"
                        f" | (((({extract}) & ~({mask_extract})) & {slice_mask}) << {offset})",
                        f"    cdef long long new_mask = (c.mask[{base_sid}] & ~({range_mask}))"
                        f" | (((({mask_extract}) & {slice_mask})) << {offset})",
                        f"    if new_val != c.val[{base_sid}] or new_mask != c.mask[{base_sid}]:",
                        f"        c.val[{base_sid}] = new_val",
                        f"        c.mask[{base_sid}] = new_mask",
                        f"        c.dirty[{base_sid}] = 1",
                    ]
                self._processes.append((sensitivity, lines))
                continue
            lines = [
                f"    cdef long long v = ({extract}) & wmask({pw})",
                f"    cdef long long m = ({mask_extract}) & wmask({pw})",
                "    v = v & ~m",
                f"    if v != c.val[{sid}] or m != c.mask[{sid}]:",
                f"        c.val[{sid}] = v",
                f"        c.mask[{sid}] = m",
                f"        c.dirty[{sid}] = 1",
            ]
            self._processes.append((sensitivity, lines))

    def _compile_bitselect_cont_assign(self, assign, sensitivity: set[int]) -> None:
        """Compile continuous assign with BitSelect LHS: assign out[idx] = rhs."""
        lhs = assign.lhs

        mem_access = self._resolve_memory_element_access(lhs)
        if mem_access is not None:
            mid, idx, _name, indices = mem_access
            elem_w, _depth = self._mem_info[mid]
            marker_sid = self._mem_marker_sigs[mid]
            index_sensitivity: set[int] = set()
            for index_expr in indices:
                self._walk_signals(index_expr, index_sensitivity)
            sensitivity |= index_sensitivity
            if elem_w > _WORD_BITS:
                words = self._mem_words(mid)
                rhs_access = self._resolve_memory_element_access(assign.rhs)
                if rhs_access is not None:
                    rhs_mid, rhs_idx, _rhs_name, _rhs_indices = rhs_access
                    rhs_elem_w, _rhs_depth = self._mem_info[rhs_mid]
                    if rhs_elem_w == elem_w and self._memory_layout_matches(mid, rhs_mid):
                        lines = self._emit_wide_mem_copy_lines(
                            mid, idx, rhs_mid, rhs_idx, marker_sid=marker_sid, indent=1, is_nba=False, track_change=True
                        )
                        self._processes.append((sensitivity, lines))
                        return
                rhs_source = self._resolve_signal_slice_source(assign.rhs)
                if rhs_source is not None:
                    lines = self._emit_const_mem_range_write_lines(
                        mid,
                        idx,
                        0,
                        elem_w,
                        "0",
                        "0",
                        rhs_source=rhs_source,
                        marker_sid=marker_sid,
                        indent=1,
                        is_nba=False,
                    )
                    self._processes.append((sensitivity, lines))
                    return
                flat_parts = self._flatten_concat_identifier_parts(assign.rhs)
                if flat_parts is not None:
                    lines = self._emit_wide_mem_flat_concat_lines(
                        mid,
                        idx,
                        flat_parts,
                        elem_w,
                        marker_sid=marker_sid,
                        indent=1,
                        is_nba=False,
                        track_change=True,
                    )
                    self._processes.append((sensitivity, lines))
                    return
                if isinstance(assign.rhs, Concatenation):
                    concat_lhs = self._build_whole_mem_concat_lhs(lhs, assign.rhs, elem_w)
                    if concat_lhs is not None:
                        lines = self._emit_concat_lhs(concat_lhs, assign.rhs, 1, is_nba=False)
                        self._processes.append((sensitivity, lines))
                        return
                if (
                    isinstance(assign.rhs, Literal)
                    and not assign.rhs.is_x
                    and not assign.rhs.is_z
                    and int(assign.rhs.value) == 0
                ):
                    lines = self._emit_wide_mem_zero_lines(
                        mid, idx, marker_sid=marker_sid, indent=1, is_nba=False, track_change=True
                    )
                    self._processes.append((sensitivity, lines))
                    return

            rhs_val = self._emit_expr(assign.rhs, elem_w)
            rhs_mask = self._emit_mask_expr(assign.rhs, elem_w)
            lines = self._emit_scalar_mem_write_lines(
                mid, idx, rhs_val, rhs_mask, elem_w, marker_sid=marker_sid, indent=1, is_nba=False, track_change=True
            )
            self._processes.append((sensitivity, lines))
            return

        # Memory element bit write: assign MEM[addr][bit] = rhs
        mem_target = self._resolve_memory_element_access(lhs.target)
        if mem_target is not None:
            mid, addr, name, indices = mem_target
            marker_sid = self._mem_marker_sigs[mid]
            range_sensitivity: set[int] = set()
            for index_expr in indices:
                self._walk_signals(index_expr, range_sensitivity)
            self._walk_signals(lhs.index, range_sensitivity)
            sensitivity |= range_sensitivity
            bit = self._emit_index_expr(lhs.index)
            bit_base = self._memory_bases.get(name, 0)
            if bit_base != 0:
                bit = f"(({bit}) - {bit_base})"
            rhs_val = self._emit_expr(assign.rhs, 1)
            rhs_mask = self._emit_mask_expr(assign.rhs, 1)
            lines = self._emit_mem_bit_write_lines(
                mid, addr, bit, rhs_val, rhs_mask, marker_sid=marker_sid, indent=1, is_nba=False, track_change=True
            )
            self._processes.append((sensitivity, lines))
            return

        if isinstance(lhs.target, Identifier):
            struct_mem_range = self._resolve_struct_storage_mem_select_range(lhs.target, lhs.index, lhs.index)
            if struct_mem_range is not None:
                range_sensitivity: set[int] = set()
                self._collect_lhs_read_signals(lhs.target, range_sensitivity)
                self._walk_signals(lhs.index, range_sensitivity)
                sensitivity |= range_sensitivity
                mem_lhs, bit_expr_msb, bit_expr_lsb = struct_mem_range
                lines = self._emit_mem_range_write(mem_lhs, bit_expr_msb, bit_expr_lsb, assign.rhs, 1, is_nba=False)
                self._processes.append((sensitivity, lines))
                return
            if self._resolve_struct_access(self._identifier_name(lhs.target)) is not None:
                index_sensitivity: set[int] = set()
                self._collect_lhs_read_signals(lhs.target, index_sensitivity)
                self._walk_signals(lhs.index, index_sensitivity)
                sensitivity |= index_sensitivity
                lines = self._emit_range_write(lhs.target, lhs.index, lhs.index, assign.rhs, 1, is_nba=False)
                self._processes.append((sensitivity, lines))
                return

        # Packed vector case: assign VEC[idx] = rhs
        sid = self._signal_map.get(self._identifier_name(lhs.target))
        if sid is None:
            return
        # Also add sensitivity from the index expression
        index_sensitivity: set[int] = set()
        self._walk_signals(lhs.index, index_sensitivity)
        sensitivity |= index_sensitivity
        idx = self._emit_index_expr(lhs.index)
        # Adjust for non-zero base offset
        sig_base = self._signal_bases.get(self._identifier_name(lhs.target), 0)
        if sig_base != 0:
            idx = f"(({idx}) - {sig_base})"
        rhs_val = self._emit_expr(assign.rhs, 1)

        # Build per-expression mask that tracks x/z through ternaries correctly
        mask_expr = self._emit_mask_expr(assign.rhs, 1)

        if self._signal_widths[sid] > _WORD_BITS:
            lines = [
                f"    _whole_assign_insert_word(c, {sid}, <int>({idx}), 1, <unsigned long long>(({rhs_val}) & 1), <unsigned long long>(({mask_expr}) & 1))",
            ]
            self._processes.append((sensitivity, lines))
            return

        lines = [
            f"    cdef long long idx = ({idx}) & 0x3f",
            f"    cdef long long rval = ({rhs_val}) & 1",
            f"    cdef long long m = {mask_expr}",
            f"    cdef long long new_val = (c.val[{sid}] & ~(1LL << idx)) | (rval << idx)",
            f"    cdef long long new_mask = c.mask[{sid}] & ~(1LL << idx)",
            "    if m:",
            f"        new_val = c.val[{sid}]",
            f"        new_mask = c.mask[{sid}] | (1LL << idx)",
            f"    if new_val != c.val[{sid}] or new_mask != c.mask[{sid}]:",
            f"        c.val[{sid}] = new_val",
            f"        c.mask[{sid}] = new_mask",
            f"        c.dirty[{sid}] = 1",
        ]
        self._processes.append((sensitivity, lines))

    def _compile_rangeselect_cont_assign(self, assign, sensitivity: set[int]) -> None:
        """Compile continuous assign with RangeSelect LHS: assign out[msb:lsb] = rhs."""
        lhs = assign.lhs
        range_sensitivity: set[int] = set()
        self._collect_lhs_read_signals(lhs.target, range_sensitivity)
        self._walk_signals(lhs.msb, range_sensitivity)
        self._walk_signals(lhs.lsb, range_sensitivity)
        sensitivity |= range_sensitivity
        lines = self._emit_lhs_write(lhs, assign.rhs, 1, is_nba=False)
        self._processes.append((sensitivity, lines))

    def _compile_partselect_cont_assign(self, assign, sensitivity: set[int]) -> None:
        """Compile continuous assign with PartSelect LHS: assign out[base +: width] = rhs."""
        lhs = assign.lhs
        part_sensitivity: set[int] = set()
        self._collect_lhs_read_signals(lhs.target, part_sensitivity)
        self._walk_signals(lhs.base, part_sensitivity)
        self._walk_signals(lhs.width, part_sensitivity)
        sensitivity |= part_sensitivity
        lines = self._emit_lhs_write(lhs, assign.rhs, 1, is_nba=False)
        self._processes.append((sensitivity, lines))

    def _flatten_concat_identifier_parts(self, expr: Expression) -> list[tuple[str, int, str, str]] | None:
        parts: list[tuple[str, int, str, str]] = []

        def walk(node: Expression) -> bool:
            if isinstance(node, Identifier):
                name = node.name
                if node.hierarchy:
                    name = ".".join(node.hierarchy) + "." + name
                sid = self._signal_map.get(name)
                if sid is None:
                    return False
                parts.append(("sig", self._signal_widths[sid], str(sid), "0"))
                return True
            if isinstance(node, RangeSelect) and isinstance(node.target, Identifier):
                if not isinstance(node.msb, Literal) or not isinstance(node.lsb, Literal):
                    return False
                name = node.target.name
                if node.target.hierarchy:
                    name = ".".join(node.target.hierarchy) + "." + name
                sid = self._signal_map.get(name)
                if sid is None:
                    return False
                sig_base = self._signal_bases.get(name, 0)
                msb = int(node.msb.value) - sig_base
                lsb = int(node.lsb.value) - sig_base
                if msb < lsb or lsb < 0:
                    return False
                parts.append(("sig", msb - lsb + 1, str(sid), str(lsb)))
                return True
            if isinstance(node, PartSelect) and isinstance(node.target, Identifier):
                width_val = _const_int(node.width, self._param_env)
                if width_val is None or width_val <= 0:
                    return False
                name = node.target.name
                if node.target.hierarchy:
                    name = ".".join(node.target.hierarchy) + "." + name
                sid = self._signal_map.get(name)
                if sid is None:
                    return False
                sig_base = self._signal_bases.get(name, 0)
                base_expr = self._emit_expr(node.base, self._expr_width(node.base))
                if node.direction == "+:":
                    src_lsb = f"(({base_expr}) - {sig_base})"
                elif node.direction == "-:":
                    src_lsb = f"(({base_expr}) - {width_val - 1} - {sig_base})"
                else:
                    return False
                parts.append(("sig", width_val, str(sid), src_lsb))
                return True
            if isinstance(node, Concatenation):
                for child in node.parts:
                    if not walk(child):
                        return False
                return True
            if isinstance(node, (RangeSelect, PartSelect)):
                static_width, _width_expr = self._concat_part_width_info(node)
                if static_width is None:
                    return False
            node_width = self._expr_width(node)
            if 0 < node_width <= _WORD_BITS:
                parts.append(
                    ("expr", node_width, self._emit_expr(node, node_width), self._emit_mask_expr(node, node_width))
                )
                return True
            return False

        return parts if walk(expr) else None

    @staticmethod
    def _concat_word_expr(flat_parts: list[tuple[str, int, str, str]], base: int, *, mask: bool) -> str:
        terms: list[str] = []
        offsets: list[tuple[str, int, str, str, int]] = []
        running_lsb = 0
        for kind, width, expr_a, expr_b in reversed(flat_parts):
            offsets.append((kind, width, expr_a, expr_b, running_lsb))
            running_lsb += width

        helper = "_sig_extract_word_mask" if mask else "_sig_extract_word_val"
        for kind, width, expr_a, expr_b, part_lsb in offsets:
            overlap_lo = max(base, part_lsb)
            overlap_hi = min(base + (_WORD_BITS - 1), part_lsb + width - 1)
            if overlap_lo > overlap_hi:
                continue
            overlap_w = overlap_hi - overlap_lo + 1
            src_offset = overlap_lo - part_lsb
            dst_shift = overlap_lo - base
            if kind == "sig":
                src_lsb = f"(({expr_b}) + {src_offset})" if src_offset else f"({expr_b})"
                term = f"({helper}(c, {expr_a}, {src_lsb}) & _word_mask64({overlap_w}))"
            else:
                src_expr = expr_b if mask else expr_a
                shifted = (
                    f"((<unsigned long long>({src_expr})) >> {src_offset})"
                    if src_offset
                    else f"(<unsigned long long>({src_expr}))"
                )
                term = f"({shifted} & _word_mask64({overlap_w}))"
            if dst_shift > 0:
                term = f"({term} << {dst_shift})"
            terms.append(term)

        return " | ".join(terms) if terms else "0"

    def _masked_flat_concat_word_exprs(
        self, flat_parts: list[tuple[str, int, str, str]], base: int, total_width: int
    ) -> tuple[str, str]:
        tail_mask = f"_word_mask64({total_width - base})"
        word_v = f"(({self._concat_word_expr(flat_parts, base, mask=False)}) & {tail_mask})"
        word_m = f"(({self._concat_word_expr(flat_parts, base, mask=True)}) & {tail_mask})"
        return word_v, word_m

    # ΓöÇΓöÇ Timing detection ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    @staticmethod
    def _has_timing(stmt) -> bool:  # noqa: PLR0911
        """Check if a statement tree contains timing controls (#delay, @event, wait)."""
        if stmt is None:
            return False
        stype = type(stmt)
        if stype in (DelayControl, EventControl, WaitStatement):
            return True
        if stype is SeqBlock:
            return any(_ProcessCompilerMixin._has_timing(s) for s in stmt.statements)
        if stype is IfStatement:
            return _ProcessCompilerMixin._has_timing(stmt.then_body) or _ProcessCompilerMixin._has_timing(
                stmt.else_body
            )
        if stype is CaseStatement:
            return any(_ProcessCompilerMixin._has_timing(item.body) for item in stmt.items)
        if stype is ForLoop:
            return (
                _ProcessCompilerMixin._has_timing(stmt.body)
                or _ProcessCompilerMixin._has_timing(stmt.init)
                or _ProcessCompilerMixin._has_timing(stmt.update)
            )
        if stype in (WhileLoop, RepeatLoop, ForeverLoop):
            return _ProcessCompilerMixin._has_timing(stmt.body)
        return False

    # ΓöÇΓöÇ Initial block compilation ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    def _compile_initial_blocks(self, module: Module) -> None:
        """Compile initial blocks without timing or system tasks.

        Blocks containing timing controls or system tasks ($display, etc.)
        are left for the scheduler's reference executor fallback.
        """
        for block in module.initial_blocks:
            if self._has_timing(block.body):
                continue  # handled by reference executor fallback
            if self._has_system_tasks(block.body):
                continue  # needs reference executor for $display etc.
            lines = self._strip_redundant_with_gil_blocks(self._emit_stmt(block.body, indent=2, context="init"))
            self._initial_lines.extend(lines)

    @staticmethod
    def _has_system_tasks(stmt) -> bool:  # noqa: PLR0911
        """Check if a statement tree contains system task calls ($display etc.)."""
        if stmt is None:
            return False
        stype = type(stmt)
        if stype is SystemTaskCall:
            return True
        if stype is SeqBlock:
            return any(_ProcessCompilerMixin._has_system_tasks(s) for s in stmt.statements)
        if stype is IfStatement:
            return _ProcessCompilerMixin._has_system_tasks(stmt.then_body) or _ProcessCompilerMixin._has_system_tasks(
                stmt.else_body
            )
        if stype is CaseStatement:
            return any(_ProcessCompilerMixin._has_system_tasks(item.body) for item in stmt.items)
        if stype is ForLoop:
            return (
                _ProcessCompilerMixin._has_system_tasks(stmt.body)
                or _ProcessCompilerMixin._has_system_tasks(stmt.init)
                or _ProcessCompilerMixin._has_system_tasks(stmt.update)
            )
        if stype in (WhileLoop, RepeatLoop, ForeverLoop):
            return _ProcessCompilerMixin._has_system_tasks(stmt.body)
        return False

    @property
    def has_compiled_initials(self) -> bool:
        """True if any initial blocks were compiled natively."""
        return bool(self._initial_lines)

    @staticmethod
    def _strip_redundant_with_gil_blocks(lines: list[str]) -> list[str]:
        """Initial-block code already runs with the GIL held, so nested wrappers are redundant."""
        out: list[str] = []
        dedent_next = False
        for line in lines:
            if line.lstrip() == "with gil:":
                dedent_next = True
                continue
            if dedent_next:
                dedent_next = False
                out.append(line[4:] if line.startswith("    ") else line)
                continue
            out.append(line)
        return out

    @property
    def has_timing_initials(self) -> bool:
        """True if any initial blocks need the reference executor fallback."""
        if self._module is None:
            return False
        return any(
            _ProcessCompilerMixin._has_timing(block.body) or _ProcessCompilerMixin._has_system_tasks(block.body)
            for block in self._module.initial_blocks
        )

    # -- Timing diagnostics ---------------------------------------------------

    @staticmethod
    def _is_loop_stmt(stmt) -> bool:
        return type(stmt) in (ForeverLoop, WhileLoop, RepeatLoop)

    @staticmethod
    def _detect_clock_gen_pattern(body) -> bool:
        """True if body looks like: (forever|while) #N clk = ~clk."""
        if not _ProcessCompilerMixin._is_loop_stmt(body):
            return False
        inner = body.body
        stmts = inner.statements if type(inner) is SeqBlock else [inner]
        return any(type(s) is DelayControl for s in stmts)

    def _collect_timing_diagnostics(self, module) -> list:
        """Return one human-readable warning per falling-back process.

        Covers:
        - always blocks with timing controls (#delay / @event) → coroutine fallback
        - initial blocks with timing controls → coroutine fallback
        - initial blocks with system tasks ($display, $readmemh, …) → reference executor
        """
        diags = []
        _COST = "expect ~10-100x slower than native compiled for this process"

        for i, block in enumerate(module.always_blocks, start=1):
            if not self._has_timing(block.body):
                continue
            tag = f"always block {i}"
            if self._detect_clock_gen_pattern(block.body):
                diags.append(
                    f"{tag}: #delay clock-generator loop → coroutine fallback; {_COST}. "
                    "Use Simulator.batch_run() with a Python-side clock instead."
                )
            else:
                diags.append(
                    f"{tag}: timing control (#delay or @event) → coroutine fallback; {_COST}. "
                    "Restructure as always @(posedge clk) or move clocking into batch_run()."
                )

        for i, block in enumerate(module.initial_blocks, start=1):
            has_timing = self._has_timing(block.body)
            has_sys = self._has_system_tasks(block.body)
            if not (has_timing or has_sys):
                continue
            tag = f"initial block {i}"
            reasons: list[str] = []
            if has_timing:
                inner = block.body
                has_loop = self._is_loop_stmt(inner) or (
                    type(inner) is SeqBlock and any(self._is_loop_stmt(s) for s in inner.statements)
                )
                reasons.append("#delay loop" if has_loop else "timing controls (#delay)")
            if has_sys:
                reasons.append("system tasks ($display/$readmemh/…)")
            reason_str = " + ".join(reasons)
            diags.append(
                f"{tag}: {reason_str} → reference executor fallback; {_COST}. "
                "Apply stimulus from Python before batch_run() calls."
            )

        return diags

    @property
    def timing_diagnostics(self) -> list:
        """Warnings for slow compiled simulation patterns detected during generate()."""
        return list(self._timing_diagnostics)

    # ΓöÇΓöÇ Always block compilation ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    def _compile_always_blocks(self, module: Module) -> None:
        """Compile always blocks into combo and sequential process lists.

        Blocks containing timing controls (#delay, @event) are skipped ΓÇö
        the scheduler will route them through the reference executor.
        """
        from veriforge.model.behavioral import SensitivityType  # noqa: PLC0415

        for block in module.always_blocks:
            if self._has_timing(block.body):
                continue  # handled by reference executor fallback

            sensitivity: set[int] = set()
            edges: dict[int, str] = {}
            if block.sensitivity_type == SensitivityType.COMBINATIONAL:
                self._collect_stmt_signals(block.body, sensitivity)
                self._collect_stmt_writes_targets(block.body, sensitivity)
            else:
                for edge in block.sensitivity_list:
                    if isinstance(edge, SensitivityEdge) and isinstance(edge.signal, Identifier):
                        sid = self._signal_map.get(edge.signal.name)
                        if sid is not None:
                            sensitivity.add(sid)
                            if edge.edge in ("posedge", "negedge"):
                                if self._signal_widths[sid] > _WORD_BITS:
                                    raise NotImplementedError(
                                        f"{edge.edge} on wide signal '{edge.signal.name}' "
                                        f"({self._signal_widths[sid]} bits) is not supported "
                                        f"in the compiled engine"
                                    )
                                edges[sid] = edge.edge
            body_lines = self._emit_stmt(block.body, indent=1)

            if block.sensitivity_type == SensitivityType.COMBINATIONAL:
                self._combo_processes.append((sensitivity, body_lines))
            else:
                # Sequential ΓÇö @(posedge clk), @(negedge clk), etc.
                self._seq_processes.append((edges, sensitivity, body_lines))
