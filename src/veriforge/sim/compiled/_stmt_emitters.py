"""Statement, LHS, and memory write emitters for CythonCodegen (extracted mixin).

All _emit_stmt*, _emit_lhs_write*, _emit_range_write*, _emit_concat_lhs*,
_emit_mem_write*, _emit_if, _emit_case, _emit_for*, _emit_repeat, _emit_while,
_emit_forever, _emit_system_task*, _emit_format_string, _emit_string_output,
and signal-collection helpers live here.
CythonCodegen inherits from _StmtEmittersMixin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from veriforge.model.expressions import (
    AssignmentPattern,
    BinaryOp,
    BitSelect,
    Concatenation,
    FunctionCall,
    Identifier,
    Literal,
    PartSelect,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from veriforge.model.statements import (
    BlockingAssign,
    CaseStatement,
    DelayControl,
    EventControl,
    ForLoop,
    ForeverLoop,
    IfStatement,
    NonblockingAssign,
    RepeatLoop,
    SeqBlock,
    SystemTaskCall,
    TaskEnable,
    WaitStatement,
    WhileLoop,
)
from veriforge.sim.compiled._codegen_utils import (
    _WORD_BITS,
    _PROCESS_LOOP_LIMIT,
    _cy_lit,
    _cy_hex,
    _const_int,
)

if TYPE_CHECKING:
    from veriforge.model.statements import Statement


class _StmtEmittersMixin:
    """Mixin providing statement, LHS, and memory write emitter methods."""

    __slots__ = ()

    # ΓöÇΓöÇ Statement codegen ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    def _emit_stmt(  # noqa: PLR0911, PLR0912
        self, stmt: Statement, indent: int = 1, *, context: str = "process"
    ) -> list[str]:
        """Emit Cython lines for a statement. Returns indented lines."""
        if stmt is None:
            return []

        pad = "    " * indent
        stype = type(stmt)

        if stype is BlockingAssign:
            return self._emit_lhs_write(stmt.lhs, stmt.rhs, indent, is_nba=False)

        if stype is NonblockingAssign:
            return self._emit_lhs_write(stmt.lhs, stmt.rhs, indent, is_nba=True)

        if stype is SeqBlock:
            lines: list[str] = []
            for s in stmt.statements:
                lines.extend(self._emit_stmt(s, indent, context=context))
            return lines

        if stype is IfStatement:
            return self._emit_if(stmt, indent, context=context)

        if stype is CaseStatement:
            return self._emit_case(stmt, indent, context=context)

        if stype is ForLoop:
            return self._emit_for(stmt, indent, context=context)

        if stype is SystemTaskCall:
            return self._emit_system_task(stmt, indent)

        if stype is WhileLoop:
            return self._emit_while(stmt, indent, context=context)

        if stype is RepeatLoop:
            return self._emit_repeat(stmt, indent, context=context)

        if stype is ForeverLoop:
            return self._emit_forever(stmt, indent, context=context)

        if stype is TaskEnable:
            return self._emit_task_enable(stmt, indent)

        raise NotImplementedError(
            f"Compiled engine does not support statement type {type(stmt).__name__!r}. "
            "Use engine='vm' or engine='reference' for this construct."
        )

    def _emit_task_enable(self, stmt: TaskEnable, indent: int) -> list[str]:
        """Emit a user-defined task call."""
        from veriforge.model.ports import PortDirection

        pad = "    " * indent
        task = self._task_map.get(stmt.task_name)
        if task is None:
            return [f"{pad}pass  # unknown task: {stmt.task_name}"]

        lines: list[str] = []
        prefix = f"__task_{task.name}"
        args = stmt.arguments or []

        # Store input/inout args to local signals
        output_bindings: list[tuple[str, Expression]] = []
        for i, port in enumerate(task.ports):
            local_name = f"{prefix}.{port.name}"
            sid = self._signal_map.get(local_name)
            if sid is None:
                continue
            w = self._signal_widths[sid]
            if port.direction == PortDirection.INPUT:
                if i < len(args):
                    val_expr = self._emit_expr(args[i], w)
                else:
                    val_expr = "0"
                lines.append(f"{pad}c.val[{sid}] = ({val_expr}) & wmask({w})")
                lines.append(f"{pad}c.mask[{sid}] = 0")
            elif port.direction == PortDirection.INOUT:
                if i < len(args):
                    val_expr = self._emit_expr(args[i], w)
                    lines.append(f"{pad}c.val[{sid}] = ({val_expr}) & wmask({w})")
                    lines.append(f"{pad}c.mask[{sid}] = 0")
                    output_bindings.append((local_name, args[i]))
                else:
                    lines.append(f"{pad}c.val[{sid}] = 0")
                    lines.append(f"{pad}c.mask[{sid}] = 0")
            elif port.direction == PortDirection.OUTPUT:
                lines.append(f"{pad}c.val[{sid}] = 0")
                lines.append(f"{pad}c.mask[{sid}] = 0")
                if i < len(args):
                    output_bindings.append((local_name, args[i]))

        # Emit the task body inline
        if task.body:
            import copy

            body_copy = copy.deepcopy(task.body)
            local_names = {port.name for port in task.ports}
            self._remap_local_identifiers(body_copy, local_names, prefix)
            lines.extend(self._emit_stmt(body_copy, indent))

        # Copy outputs back
        for local_name, lhs_expr in output_bindings:
            sid = self._signal_map.get(local_name)
            if sid is None:
                continue
            w = self._signal_widths[sid]
            # Create a synthetic identifier for the local signal to read from
            local_id = Identifier(local_name)
            lines.extend(self._emit_lhs_write(lhs_expr, local_id, indent, is_nba=False))

        return lines

    def _emit_lhs_write(  # noqa: PLR0911, PLR0912
        self,
        lhs: Expression,
        rhs: Expression,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str]:
        """Emit assignment lines for any LHS pattern."""
        pad = "    " * indent
        lhs_type = type(lhs)

        # ΓöÇΓöÇ Simple identifier ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
        if lhs_type is Identifier:
            name = lhs.name
            if lhs.hierarchy:
                name = ".".join(lhs.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            if sid is None:
                lhs_mid = self._mem_map.get(name)
                if lhs_mid is not None and isinstance(rhs, Identifier):
                    rhs_name = rhs.name
                    if rhs.hierarchy:
                        rhs_name = ".".join(rhs.hierarchy) + "." + rhs_name
                    rhs_mid = self._mem_map.get(rhs_name)
                    if rhs_mid is not None:
                        lhs_elem_w, lhs_depth = self._mem_info[lhs_mid]
                        rhs_elem_w, rhs_depth = self._mem_info[rhs_mid]
                        if (
                            lhs_elem_w == rhs_elem_w
                            and lhs_depth == rhs_depth
                            and self._memory_layout_matches(lhs_mid, rhs_mid)
                        ):
                            marker_sid = self._mem_marker_sigs[lhs_mid]
                            return self._emit_whole_mem_copy_lines(
                                lhs_mid, rhs_mid, marker_sid=marker_sid, indent=indent, is_nba=is_nba
                            )
                struct_storage_range = self._resolve_struct_storage_mem_range(name)
                if struct_storage_range is not None:
                    mem_lhs, msb, lsb = struct_storage_range
                    return self._emit_mem_range_write(mem_lhs, msb, lsb, rhs, indent, is_nba=is_nba)
                struct_info = self._resolve_struct_access(name)
                if struct_info is not None:
                    base_sid, offset, field_width = struct_info
                    base_w = self._signal_widths[base_sid]
                    rhs_sid = None
                    if isinstance(rhs, Identifier):
                        rhs_name = rhs.name
                        if rhs.hierarchy:
                            rhs_name = ".".join(rhs.hierarchy) + "." + rhs_name
                        rhs_sid = self._signal_map.get(rhs_name)
                    if rhs_sid is not None and self._signal_widths[rhs_sid] == field_width:
                        helper = "_whole_stage_insert_signal" if is_nba else "_whole_assign_insert_signal"
                        return [f"{pad}{helper}(c, {base_sid}, {offset}, {rhs_sid}, {field_width})"]
                    rhs_source = self._resolve_signal_slice_source(rhs)
                    if field_width > _WORD_BITS and rhs_source is not None:
                        rhs_source_sid, rhs_source_lsb = rhs_source
                        helper = "_whole_stage_insert_signal_slice" if is_nba else "_whole_assign_insert_signal_slice"
                        return [
                            f"{pad}{helper}(c, {base_sid}, {offset}, {rhs_source_sid},"
                            f" <int>({rhs_source_lsb}), {field_width})"
                        ]
                    rhs_mem_source = self._resolve_memory_slice_source(rhs)
                    if field_width > _WORD_BITS and rhs_mem_source is not None:
                        rhs_mid, _rhs_idx, _rhs_lsb = rhs_mem_source
                        if self._mem_info[rhs_mid][0] > _WORD_BITS:
                            return self._emit_signal_mem_rhs_source_lines(
                                base_sid,
                                str(offset),
                                rhs_mem_source,
                                str(field_width),
                                indent,
                                is_nba=is_nba,
                            )
                    rhs_val = self._emit_expr(rhs, field_width)
                    rhs_mask = self._emit_mask_expr(rhs, field_width)
                    if field_width <= _WORD_BITS:
                        helper = "_whole_stage_insert_word" if is_nba else "_whole_assign_insert_word"
                        return [
                            f"{pad}{helper}(c, {base_sid}, {offset}, {field_width}, <unsigned long long>(({rhs_val}) & wmask({field_width})), <unsigned long long>(({rhs_mask}) & wmask({field_width})))",
                        ]
                    fmask = _cy_lit((1 << field_width) - 1)
                    clear_mask = _cy_lit((1 << base_w) - 1 ^ ((1 << field_width) - 1) << offset)
                    if is_nba:
                        return [
                            f"{pad}_sfv = ({rhs_val}) & {fmask}",
                            f"{pad}c.nba_val[{base_sid}] = (c.val[{base_sid}] & {clear_mask}) | (_sfv << {offset})",
                            f"{pad}c.nba_mask[{base_sid}] = 0",
                            f"{pad}c.nba_dirty[{base_sid}] = 1",
                            f"{pad}c.nba_pending = 1",
                        ]
                    return [
                        f"{pad}_sfv = ({rhs_val}) & {fmask}",
                        f"{pad}_cdv = (c.val[{base_sid}] & {clear_mask}) | (_sfv << {offset})",
                        f"{pad}if _cdv != c.val[{base_sid}] or c.mask[{base_sid}]:",
                        f"{pad}    c.val[{base_sid}] = _cdv",
                        f"{pad}    c.mask[{base_sid}] = 0",
                        f"{pad}    c.dirty[{base_sid}] = 1",
                    ]
                # Local loop variable assignment ΓÇö use width 64 so
                # wmask(64) == -1 and masking is effectively a no-op,
                # preserving signed semantics for downward-counting loops.
                lv = self._local_vars.get(name)
                if lv is not None:
                    rhs_val = self._emit_expr(rhs, 64)
                    return [f"{pad}{lv} = ({rhs_val})"]
                raise NotImplementedError(
                    f"Compiled engine cannot resolve LHS identifier {name!r}: "
                    "not a module signal or local loop variable."
                )
            lhs_w = self._signal_widths[sid]
            if isinstance(rhs, Identifier):
                lines = self._emit_wide_signal_copy_lines(sid, rhs, indent=indent, is_nba=is_nba)
                if lines is not None:
                    return lines
                rhs_name = rhs.name
                if rhs.hierarchy:
                    rhs_name = ".".join(rhs.hierarchy) + "." + rhs_name
                struct_info = self._resolve_struct_access(rhs_name)
                if struct_info is not None:
                    base_sid, offset, field_width = struct_info
                    helper = "_whole_stage_slice_width_signal" if is_nba else "_whole_assign_slice_width_signal"
                    return [f"{pad}{helper}(c, {sid}, {base_sid}, {offset}, {field_width})"]
            if isinstance(rhs, RangeSelect) and isinstance(rhs.target, Identifier):
                target_name = rhs.target.name
                if rhs.target.hierarchy:
                    target_name = ".".join(rhs.target.hierarchy) + "." + target_name
                struct_info = self._resolve_struct_access(target_name)
                if struct_info is not None:
                    base_sid, offset, _field_width = struct_info
                    helper = "_whole_stage_slice_width_signal" if is_nba else "_whole_assign_slice_width_signal"
                    if isinstance(rhs.msb, Literal) and isinstance(rhs.lsb, Literal):
                        sel_lsb = int(rhs.lsb.value)
                        sel_w = int(rhs.msb.value) - sel_lsb + 1
                        return [f"{pad}{helper}(c, {sid}, {base_sid}, {offset + sel_lsb}, {sel_w})"]
                    lsb_expr = self._emit_expr(rhs.lsb, 32)
                    msb_expr = self._emit_expr(rhs.msb, 32)
                    return [
                        f"{pad}{helper}(c, {sid}, {base_sid}, {offset} + ({lsb_expr}), (({msb_expr}) - ({lsb_expr}) + 1))"
                    ]
            if isinstance(rhs, PartSelect) and isinstance(rhs.target, Identifier):
                target_name = rhs.target.name
                if rhs.target.hierarchy:
                    target_name = ".".join(rhs.target.hierarchy) + "." + target_name
                rhs_sid = self._signal_map.get(target_name)
                part_w = _const_int(rhs.width, self._param_env)
                if rhs_sid is not None and (
                    lhs_w > _WORD_BITS
                    or self._signal_widths[rhs_sid] > _WORD_BITS
                    or part_w is None
                    or part_w > _WORD_BITS
                ):
                    helper = "_whole_stage_slice_width_signal" if is_nba else "_whole_assign_slice_width_signal"
                    width_expr = (
                        str(part_w) if part_w is not None else self._emit_expr(rhs.width, self._expr_width(rhs.width))
                    )
                    base_expr = self._emit_expr(rhs.base, 32)
                    sig_base = self._signal_bases.get(target_name, 0)
                    if rhs.direction == "+:":
                        lsb_expr = f"(({base_expr}) - {sig_base})"
                    else:
                        lsb_expr = f"(({base_expr}) - ({width_expr}) + 1 - {sig_base})"
                    return [f"{pad}{helper}(c, {sid}, {rhs_sid}, {lsb_expr}, {width_expr})"]
                struct_info = self._resolve_struct_access(target_name)
                if struct_info is not None:
                    base_sid, offset, _field_width = struct_info
                    helper = "_whole_stage_slice_width_signal" if is_nba else "_whole_assign_slice_width_signal"
                    base_expr = self._emit_expr(rhs.base, 32)
                    if isinstance(rhs.width, Literal):
                        width_expr = str(int(rhs.width.value))
                    else:
                        width_expr = self._emit_expr(rhs.width, 32)
                    if rhs.direction == "+:":
                        lsb_expr = f"{offset} + ({base_expr})"
                    else:
                        lsb_expr = f"{offset} + ({base_expr}) - ({width_expr}) + 1"
                    return [f"{pad}{helper}(c, {sid}, {base_sid}, {lsb_expr}, {width_expr})"]
            lines = self._emit_signed_literal_xor_shift_lines(sid, rhs, indent=indent, is_nba=is_nba)
            if lines is not None:
                return lines
            lines = self._emit_signed_identifier_xor_shift_lines(sid, rhs, indent=indent, is_nba=is_nba)
            if lines is not None:
                return lines
            lines = self._emit_signed_binop_shift_lines(sid, rhs, indent=indent, is_nba=is_nba)
            if lines is not None:
                return lines
            lines = self._emit_signed_signal_shift_lines(sid, rhs, indent=indent, is_nba=is_nba)
            if lines is not None:
                return lines
            # Legacy shift-after-multiply matchers come before the recursive emitter so the
            # double-width primitives (_whole_assign_mul_signal_shr, _whole_assign_mul_const_shr)
            # are used for (a*b)>>N and (a*K)>>N, matching the reference engine's double-width
            # x-propagation semantics for multiplication.
            lines = self._emit_wide_signal_binop_shift_lines(sid, rhs, indent=indent, is_nba=is_nba)
            if lines is not None:
                return lines
            lines = self._emit_wide_mul_const_shift_lines(sid, rhs, indent=indent, is_nba=is_nba)
            if lines is not None:
                return lines
            # ── New recursive wide emitter (Phase 1) ──────────────────────
            lines = self._emit_wide_lhs_write_new(sid, rhs, indent, is_nba=is_nba)
            if lines is not None:
                return lines
            # ! (logical NOT) before shift — not handled by recursive emitter
            lines = self._emit_wide_lnot_shift_lines(sid, rhs, indent=indent, is_nba=is_nba)
            if lines is not None:
                return lines
            assign_width = 64 if sid in self._unmasked_signal_ids else self._signal_widths[sid]
            lines = self._emit_wide_py_bits_lines(sid, rhs, eval_width=assign_width, indent=indent, is_nba=is_nba)
            if lines is not None:
                return lines

            old_et = self._et_pending
            self._et_pending = []
            rhs_val = self._emit_expr(rhs, assign_width)
            et_lines = [f"{pad}{t}" for t in self._et_pending]
            self._et_pending = old_et
            assign_rhs = (
                f"({rhs_val})"
                if sid in self._unmasked_signal_ids
                else f"({rhs_val}) & wmask({self._signal_widths[sid]})"
            )
            if is_nba:
                return et_lines + [
                    f"{pad}c.nba_val[{sid}] = {assign_rhs}",
                    f"{pad}c.nba_mask[{sid}] = 0",
                    f"{pad}c.nba_dirty[{sid}] = 1",
                    f"{pad}c.nba_pending = 1",
                ]
            return et_lines + [
                f"{pad}_cdv = {assign_rhs}",
                f"{pad}if _cdv != c.val[{sid}] or c.mask[{sid}]:",
                f"{pad}    c.val[{sid}] = _cdv",
                f"{pad}    c.mask[{sid}] = 0",
                f"{pad}    c.dirty[{sid}] = 1",
            ]

        # ΓöÇΓöÇ BitSelect ΓÇö memory or scalar bit ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
        if lhs_type is BitSelect:
            if self._resolve_memory_element_expr(lhs) is not None:
                return self._emit_mem_write(lhs, rhs, indent, is_nba=is_nba)
            if isinstance(lhs.target, Identifier):
                struct_mem_range = self._resolve_struct_storage_mem_select_range(lhs.target, lhs.index, lhs.index)
                if struct_mem_range is not None:
                    mem_lhs, bit_expr_msb, bit_expr_lsb = struct_mem_range
                    return self._emit_mem_range_write(mem_lhs, bit_expr_msb, bit_expr_lsb, rhs, indent, is_nba=is_nba)
                if self._resolve_struct_access(self._identifier_name(lhs.target)) is not None:
                    return self._emit_range_write(lhs.target, lhs.index, lhs.index, rhs, indent, is_nba=is_nba)
            mem_target = self._resolve_memory_element_access(lhs.target)
            if mem_target is not None:
                mid, idx, name, _indices = mem_target
                marker_sid = self._mem_marker_sigs[mid]
                bit = self._emit_index_expr(lhs.index)
                bit_base = self._memory_bases.get(name, 0)
                if bit_base != 0:
                    bit = f"(({bit}) - {bit_base})"
                rhs_val = self._emit_expr(rhs, 1)
                rhs_mask = self._emit_mask_expr(rhs, 1)
                return self._emit_mem_bit_write_lines(
                    mid,
                    idx,
                    bit,
                    rhs_val,
                    rhs_mask,
                    marker_sid=marker_sid,
                    indent=indent,
                    is_nba=is_nba,
                    track_change=False,
                )
            # Scalar bit-select: read-modify-write
            if isinstance(lhs.target, Identifier):
                sid = self._signal_map.get(self._identifier_name(lhs.target))
                if sid is None:
                    raise NotImplementedError(
                        f"Compiled engine cannot resolve bit-select target "
                        f"{self._identifier_name(lhs.target)!r}: signal not found."
                    )
                lhs_w = self._signal_widths[sid]
                idx = self._emit_index_expr(lhs.index)
                # Adjust for non-zero base offset
                sig_base = self._signal_bases.get(self._identifier_name(lhs.target), 0)
                if sig_base != 0:
                    idx = f"(({idx}) - {sig_base})"
                rhs_val = self._emit_expr(rhs, 1)
                if lhs_w > _WORD_BITS:
                    helper = "_whole_stage_insert_word" if is_nba else "_whole_assign_insert_word"
                    return [
                        f"{pad}{helper}(c, {sid}, <int>({idx}), 1, <unsigned long long>(({rhs_val}) & 1), 0)",
                    ]
                if is_nba:
                    # Read from nba_val if already dirty, else from val
                    return [
                        f"{pad}_bbase = c.nba_val[{sid}] if c.nba_dirty[{sid}] else c.val[{sid}]",
                        f"{pad}c.nba_val[{sid}] = (_bbase & ~(1 << ({idx}))) | ((({rhs_val}) & 1) << ({idx}))",
                        f"{pad}c.nba_mask[{sid}] = 0",
                        f"{pad}c.nba_dirty[{sid}] = 1",
                        f"{pad}c.nba_pending = 1",
                    ]
                return [
                    f"{pad}_cdv = (c.val[{sid}] & ~(1 << ({idx}))) | ((({rhs_val}) & 1) << ({idx}))",
                    f"{pad}if _cdv != c.val[{sid}] or c.mask[{sid}]:",
                    f"{pad}    c.val[{sid}] = _cdv",
                    f"{pad}    c.mask[{sid}] = 0",
                    f"{pad}    c.dirty[{sid}] = 1",
                ]
            raise NotImplementedError(
                f"Compiled engine does not support bit-select write on "
                f"{type(lhs.target).__name__!r} target. "
                "Use engine='vm' for this construct."
            )

        # ΓöÇΓöÇ RangeSelect ΓÇö a[msb:lsb] = val ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
        if lhs_type is RangeSelect:
            if self._resolve_memory_element_expr(lhs.target) is not None:
                return self._emit_mem_range_write(lhs.target, lhs.msb, lhs.lsb, rhs, indent, is_nba=is_nba)
            return self._emit_range_write(lhs.target, lhs.msb, lhs.lsb, rhs, indent, is_nba=is_nba)

        # ΓöÇΓöÇ PartSelect ΓÇö a[base +: width] = val ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
        if lhs_type is PartSelect:
            if self._resolve_memory_element_expr(lhs.target) is not None:
                if isinstance(lhs.width, Literal):
                    pw = int(lhs.width.value)
                else:
                    pw = _const_int(lhs.width, self._param_env)
                    if pw is None:
                        pw = 1
                if lhs.direction == "+:":
                    lsb_expr = lhs.base
                    msb_expr = lhs.base if pw == 1 else BinaryOp("+", lhs.base, Literal(pw - 1, width=32))
                else:
                    msb_expr = lhs.base
                    lsb_expr = lhs.base if pw == 1 else BinaryOp("-", lhs.base, Literal(pw - 1, width=32))
                return self._emit_mem_range_write(lhs.target, msb_expr, lsb_expr, rhs, indent, is_nba=is_nba)
            struct_storage_target = False
            if isinstance(lhs.target, Identifier):
                struct_storage_target = (
                    self._resolve_struct_storage_mem_range(self._identifier_name(lhs.target)) is not None
                )
            # Determine base offset for non-zero base signals
            ps_sig_base = 0
            if isinstance(lhs.target, Identifier):
                ps_sig_base = self._signal_bases.get(lhs.target.name, 0)
            if isinstance(lhs.width, Literal):
                pw = int(lhs.width.value)
                if lhs.direction == "+:" and not struct_storage_target:
                    # msb = base + width - 1, lsb = base
                    if isinstance(lhs.base, Literal):
                        base_v = int(lhs.base.value) - ps_sig_base
                        return self._emit_range_write_const(
                            lhs.target,
                            base_v + pw - 1,
                            base_v,
                            rhs,
                            indent,
                            is_nba=is_nba,
                        )
                elif lhs.direction == "-:" and not struct_storage_target:
                    # "-:" direction: msb = base, lsb = base - width + 1
                    if isinstance(lhs.base, Literal):
                        base_v = int(lhs.base.value) - ps_sig_base
                        return self._emit_range_write_const(
                            lhs.target,
                            base_v,
                            base_v - pw + 1,
                            rhs,
                            indent,
                            is_nba=is_nba,
                        )
            # Dynamic part-select: use expression-based range write
            return self._emit_range_write_dynamic_part(lhs, rhs, indent, is_nba=is_nba)

        # ΓöÇΓöÇ Concatenation ΓÇö {a, b, c} = val ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ
        if lhs_type is Concatenation:
            return self._emit_concat_lhs(lhs, rhs, indent, is_nba=is_nba)

        raise NotImplementedError(
            f"Compiled engine does not support LHS type {lhs_type.__name__!r}. Use engine='vm' for this construct."
        )

    def _emit_range_write(  # noqa: PLR0913
        self,
        target: Expression,
        msb_expr: Expression,
        lsb_expr: Expression,
        rhs: Expression,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str]:
        """Emit range-select LHS write: target[msb:lsb] = rhs."""
        if isinstance(target, Identifier):
            struct_signal_range = self._resolve_struct_signal_select_range(target, msb_expr, lsb_expr)
            if struct_signal_range is not None:
                sid, abs_msb_expr, abs_lsb_expr = struct_signal_range
                return self._emit_range_write_sid(sid, abs_msb_expr, abs_lsb_expr, rhs, indent, is_nba=is_nba)
            struct_mem_range = self._resolve_struct_storage_mem_select_range(target, msb_expr, lsb_expr)
            if struct_mem_range is not None:
                mem_lhs, abs_msb_expr, abs_lsb_expr = struct_mem_range
                return self._emit_mem_range_write(mem_lhs, abs_msb_expr, abs_lsb_expr, rhs, indent, is_nba=is_nba)
            sid = self._signal_map.get(self._identifier_name(target))
            if sid is not None:
                return self._emit_range_write_sid(sid, msb_expr, lsb_expr, rhs, indent, is_nba=is_nba)
            raise NotImplementedError(
                f"Compiled engine cannot resolve range-select target "
                f"{self._identifier_name(target)!r}: signal not found."
            )
        raise NotImplementedError(
            f"Compiled engine does not support range-select write on "
            f"{type(target).__name__!r} target. "
            "Use engine='vm' for this construct."
        )

    def _emit_range_write_sid(  # noqa: PLR0913
        self,
        sid: int,
        msb_expr: Expression,
        lsb_expr: Expression,
        rhs: Expression,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str]:
        """Emit range-select LHS write targeting a resolved signal ID."""
        sig_base = self._signal_bases.get(self._signal_names[sid], 0)
        rhs_source = self._resolve_signal_slice_source(rhs) if self._signal_widths[sid] > _WORD_BITS else None
        rhs_mem_source = self._resolve_memory_slice_source(rhs) if self._signal_widths[sid] > _WORD_BITS else None
        if rhs_source is not None or rhs_mem_source is not None:
            if isinstance(msb_expr, Literal) and isinstance(lsb_expr, Literal):
                lsb_str = str(int(lsb_expr.value) - sig_base)
                width_expr = str(int(msb_expr.value) - int(lsb_expr.value) + 1)
            else:
                msb = self._emit_expr(msb_expr, 32)
                lsb = self._emit_expr(lsb_expr, 32)
                if sig_base != 0:
                    msb = f"(({msb}) - {sig_base})"
                    lsb = f"(({lsb}) - {sig_base})"
                lsb_str = lsb
                width_expr = f"(({msb}) - ({lsb}) + 1)"
            if rhs_source is not None:
                rhs_source_sid, rhs_source_lsb = rhs_source
                helper = "_whole_stage_insert_signal_slice" if is_nba else "_whole_assign_insert_signal_slice"
                pad = "    " * indent
                return [
                    f"{pad}{helper}(c, {sid}, <int>({lsb_str}), {rhs_source_sid}, <int>({rhs_source_lsb}), {width_expr})"
                ]
            if rhs_mem_source is not None:
                rhs_mid, _rhs_idx, _rhs_lsb = rhs_mem_source
                if self._mem_info[rhs_mid][0] > _WORD_BITS:
                    return self._emit_signal_mem_rhs_source_lines(
                        sid,
                        lsb_str,
                        rhs_mem_source,
                        width_expr,
                        indent,
                        is_nba=is_nba,
                    )
        if isinstance(msb_expr, Literal) and isinstance(lsb_expr, Literal):
            return self._emit_range_write_const_sid(
                sid,
                int(msb_expr.value) - sig_base,
                int(lsb_expr.value) - sig_base,
                rhs,
                indent,
                is_nba=is_nba,
            )
        pad = "    " * indent
        lhs_w = self._signal_widths[sid]
        msb = self._emit_expr(msb_expr, 32)
        lsb = self._emit_expr(lsb_expr, 32)
        if sig_base != 0:
            msb = f"(({msb}) - {sig_base})"
            lsb = f"(({lsb}) - {sig_base})"
        rhs_val = self._emit_expr(rhs, lhs_w)
        lines = [
            f"{pad}_rmw_msb = ({msb})",
            f"{pad}_rmw_lsb = ({lsb})",
            f"{pad}_rmw_mask = wmask(_rmw_msb - _rmw_lsb + 1) << _rmw_lsb",
        ]
        store_arr = "nba_val" if is_nba else "val"
        if is_nba:
            base_read = f"(c.nba_val[{sid}] if c.nba_dirty[{sid}] else c.val[{sid}])"
        else:
            base_read = f"c.val[{sid}]"
        new_val_expr = f"({base_read} & ~_rmw_mask) | ((({rhs_val}) << _rmw_lsb) & _rmw_mask)"
        if is_nba:
            lines.append(f"{pad}c.{store_arr}[{sid}] = {new_val_expr}")
            lines.extend(
                [
                    f"{pad}c.nba_mask[{sid}] = 0",
                    f"{pad}c.nba_dirty[{sid}] = 1",
                    f"{pad}c.nba_pending = 1",
                ]
            )
        else:
            lines.append(f"{pad}_cdv = {new_val_expr}")
            lines.extend(
                [
                    f"{pad}if _cdv != c.val[{sid}] or c.mask[{sid}]:",
                    f"{pad}    c.val[{sid}] = _cdv",
                    f"{pad}    c.mask[{sid}] = 0",
                    f"{pad}    c.dirty[{sid}] = 1",
                ]
            )
        return lines

    def _emit_range_write_const(  # noqa: PLR0913
        self,
        target: Expression,
        msb_val: int,
        lsb_val: int,
        rhs: Expression,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str]:
        """Emit range-select LHS write with constant msb/lsb."""
        pad = "    " * indent
        if not isinstance(target, Identifier):
            raise NotImplementedError(
                f"Compiled engine does not support constant range-select write on "
                f"{type(target).__name__!r} target. "
                "Use engine='vm' for this construct."
            )
        struct_signal_range = self._resolve_struct_signal_select_range(
            target, Literal(msb_val, width=32), Literal(lsb_val, width=32)
        )
        if struct_signal_range is not None:
            sid, abs_msb_expr, abs_lsb_expr = struct_signal_range
            assert isinstance(abs_msb_expr, Literal)
            assert isinstance(abs_lsb_expr, Literal)
            return self._emit_range_write_const_sid(
                sid,
                int(abs_msb_expr.value),
                int(abs_lsb_expr.value),
                rhs,
                indent,
                is_nba=is_nba,
            )
        sid = self._signal_map.get(self._identifier_name(target))
        if sid is None:
            raise NotImplementedError(
                f"Compiled engine cannot resolve range-select target "
                f"{self._identifier_name(target)!r}: signal not found."
            )
        sig_base = self._signal_bases.get(self._signal_names[sid], 0)
        return self._emit_range_write_const_sid(sid, msb_val - sig_base, lsb_val - sig_base, rhs, indent, is_nba=is_nba)

    def _emit_range_write_const_sid(  # noqa: PLR0913
        self,
        sid: int,
        msb_val: int,
        lsb_val: int,
        rhs: Expression,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str]:
        """Emit range-select LHS write with constant msb/lsb targeting a resolved signal ID."""
        pad = "    " * indent
        sel_w = msb_val - lsb_val + 1
        if self._signal_widths[sid] > _WORD_BITS:
            rhs_source = self._resolve_signal_slice_source(rhs)
            if rhs_source is not None:
                rhs_source_sid, rhs_source_lsb = rhs_source
                helper = "_whole_stage_insert_signal_slice" if is_nba else "_whole_assign_insert_signal_slice"
                return [f"{pad}{helper}(c, {sid}, {lsb_val}, {rhs_source_sid}, <int>({rhs_source_lsb}), {sel_w})"]
            rhs_mem_source = self._resolve_memory_slice_source(rhs)
            if rhs_mem_source is not None:
                rhs_mid, _rhs_idx, _rhs_lsb = rhs_mem_source
                if self._mem_info[rhs_mid][0] > _WORD_BITS:
                    return self._emit_signal_mem_rhs_source_lines(
                        sid,
                        str(lsb_val),
                        rhs_mem_source,
                        str(sel_w),
                        indent,
                        is_nba=is_nba,
                    )
        rhs_val = self._emit_expr(rhs, sel_w)
        range_mask = _cy_hex(((1 << sel_w) - 1) << lsb_val)
        sel_mask = _cy_hex((1 << sel_w) - 1)
        store_arr = "nba_val" if is_nba else "val"
        if is_nba:
            base_read = f"(c.nba_val[{sid}] if c.nba_dirty[{sid}] else c.val[{sid}])"
        else:
            base_read = f"c.val[{sid}]"
        new_val_expr = f"({base_read} & ~{range_mask}) | ((({rhs_val}) & {sel_mask}) << {lsb_val})"
        if is_nba:
            lines = [f"{pad}c.{store_arr}[{sid}] = {new_val_expr}"]
            lines.extend(
                [
                    f"{pad}c.nba_mask[{sid}] = 0",
                    f"{pad}c.nba_dirty[{sid}] = 1",
                    f"{pad}c.nba_pending = 1",
                ]
            )
        else:
            lines = [
                f"{pad}_cdv = {new_val_expr}",
                f"{pad}if _cdv != c.val[{sid}] or c.mask[{sid}]:",
                f"{pad}    c.val[{sid}] = _cdv",
                f"{pad}    c.mask[{sid}] = 0",
                f"{pad}    c.dirty[{sid}] = 1",
            ]
        return lines

    def _emit_range_write_dynamic_part(
        self,
        lhs: PartSelect,
        rhs: Expression,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str]:
        """Emit part-select LHS write with dynamic base."""
        if isinstance(lhs.width, Literal):
            pw = int(lhs.width.value)
        else:
            pw = _const_int(lhs.width, self._param_env)
            if pw is None:
                pw = 1
        if lhs.direction == "+:":
            lsb_expr = lhs.base
            msb_expr = lhs.base if pw == 1 else BinaryOp("+", lhs.base, Literal(pw - 1, width=32))
        else:
            msb_expr = lhs.base
            lsb_expr = lhs.base if pw == 1 else BinaryOp("-", lhs.base, Literal(pw - 1, width=32))
        return self._emit_range_write(lhs.target, msb_expr, lsb_expr, rhs, indent, is_nba=is_nba)

    def _emit_concat_lhs(
        self,
        lhs: Concatenation,
        rhs: Expression,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str]:
        """Emit concatenation LHS: {a, b, c} = rhs ΓåÆ extract slices to each part."""
        pad = "    " * indent
        direct_copy_lines = self._emit_matching_concat_copy(lhs, rhs, indent, is_nba=is_nba)
        if direct_copy_lines is not None:
            return direct_copy_lines
        part_width_infos = [self._concat_part_width_info(p) for p in lhs.parts]
        wide_rhs_source = None
        wide_signal_rhs_source = None
        source = self._resolve_signal_slice_source(rhs)
        if source is not None:
            sid, base_lsb = source
            if self._signal_widths[sid] > _WORD_BITS:
                wide_signal_rhs_source = (sid, base_lsb)
            if self._signal_widths[sid] > _WORD_BITS and all(
                self._concat_part_max_width(p) <= _WORD_BITS for p in lhs.parts
            ):
                wide_rhs_source = (sid, base_lsb)

        if wide_rhs_source is None:
            rhs_width = self._expr_width(rhs)
            rhs_val = self._emit_expr(rhs, rhs_width)
            rhs_mask = self._emit_mask_expr(rhs, rhs_width)
            if rhs_width <= _WORD_BITS:
                lines = [
                    f"{pad}cdef unsigned long long _concat_rhs_val = ({rhs_val}) & wmask({rhs_width})",
                    f"{pad}cdef unsigned long long _concat_rhs_mask = ({rhs_mask}) & wmask({rhs_width})",
                    f"{pad}cdef unsigned long long _cacc_val",
                    f"{pad}cdef unsigned long long _cacc_mask",
                    f"{pad}cdef unsigned long long _part_mask",
                    f"{pad}cdef unsigned long long _slice_mask",
                ]
                concat_rhs_val = "_concat_rhs_val"
                concat_rhs_mask = "_concat_rhs_mask"
            else:
                lines = [
                    f"{pad}cdef unsigned long long _cacc_val",
                    f"{pad}cdef unsigned long long _cacc_mask",
                    f"{pad}cdef unsigned long long _part_mask",
                    f"{pad}cdef unsigned long long _slice_mask",
                ]
                concat_rhs_val = rhs_val
                concat_rhs_mask = rhs_mask
        else:
            lines = [
                f"{pad}cdef unsigned long long _cacc_val",
                f"{pad}cdef unsigned long long _cacc_mask",
                f"{pad}cdef unsigned long long _part_mask",
                f"{pad}cdef unsigned long long _slice_mask",
            ]
            concat_rhs_val = None
            concat_rhs_mask = None

        # Collect (extract_expr, target_sid, msb, lsb) for each part
        # where msb/lsb refer to bits within the target signal
        part_ops: list[
            tuple[str, str, int, str | None, str | None]
        ] = []  # (extract, mask_extract, sid, msb_str, lsb_str)
        mem_part_lines: list[str] = []
        signal_part_lines: list[str] = []
        offset_infos: list[tuple[int | None, str]] = []
        running_offset: tuple[int | None, str] = (0, "0")
        for width_info in reversed(part_width_infos):
            offset_infos.append(running_offset)
            running_offset = self._concat_width_sum([running_offset, width_info])
        offset_infos.reverse()
        for part, (pw, width_expr), (_offset_width, offset_expr) in zip(
            reversed(lhs.parts), reversed(part_width_infos), reversed(offset_infos), strict=True
        ):
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
                extract = self._emit_concat_rhs_extract(concat_rhs_val, offset_expr, pw, width_expr)
                mask_extract = self._emit_concat_rhs_extract(concat_rhs_mask, offset_expr, pw, width_expr)
            else:
                rhs_sid, rhs_base_lsb = wide_rhs_source
                lsb_expr = offset_expr if rhs_base_lsb == "0" else f"({rhs_base_lsb}) + ({offset_expr})"
                extract = self._emit_signal_slice_expr(rhs_sid, lsb_expr, width_expr)
                mask_extract = self._emit_signal_slice_expr(rhs_sid, lsb_expr, width_expr, mask=True)
            mem_access = self._resolve_memory_element_access(part)
            if mem_access is not None:
                mid, idx, _name, _indices = mem_access
                mem_part_lines.extend(
                    self._emit_const_mem_range_write_lines(
                        mid,
                        idx,
                        0,
                        pw,
                        extract,
                        mask_extract,
                        rhs_source=part_rhs_source,
                        marker_sid=self._mem_marker_sigs[mid],
                        indent=indent,
                        is_nba=is_nba,
                    )
                )
                continue

            if isinstance(part, RangeSelect):
                mem_target = self._resolve_memory_element_access(part.target)
                if mem_target is not None:
                    mid, addr, name, _indices = mem_target
                    bit_base = self._memory_bases.get(name, 0)
                    if isinstance(part.msb, Literal) and isinstance(part.lsb, Literal):
                        mem_part_lines.extend(
                            self._emit_const_mem_range_write_lines(
                                mid,
                                addr,
                                int(part.lsb.value) - bit_base,
                                pw,
                                extract,
                                mask_extract,
                                rhs_source=part_rhs_source,
                                marker_sid=self._mem_marker_sigs[mid],
                                indent=indent,
                                is_nba=is_nba,
                            )
                        )
                    else:
                        msb = self._emit_expr(part.msb, 32)
                        lsb = self._emit_expr(part.lsb, 32)
                        if bit_base != 0:
                            msb = f"(({msb}) - {bit_base})"
                            lsb = f"(({lsb}) - {bit_base})"
                        mem_part_lines.extend(
                            self._emit_dynamic_mem_range_write_lines(
                                mid,
                                addr,
                                msb,
                                lsb,
                                extract,
                                mask_extract,
                                rhs_source=part_rhs_source,
                                marker_sid=self._mem_marker_sigs[mid],
                                indent=indent,
                                is_nba=is_nba,
                            )
                        )
                elif isinstance(part.target, Identifier):
                    sid = self._signal_map.get(part.target.name)
                    if sid is not None:
                        if isinstance(part.msb, Literal) and isinstance(part.lsb, Literal):
                            msb_str = str(int(part.msb.value))
                            lsb_str = str(int(part.lsb.value))
                        else:
                            msb_str = self._emit_expr(part.msb, 32)
                            lsb_str = self._emit_expr(part.lsb, 32)
                        signal_lines = self._emit_concat_signal_part_lines(
                            sid,
                            lsb_str,
                            pw,
                            width_expr,
                            extract,
                            mask_extract,
                            part_rhs_source,
                            indent,
                            is_nba=is_nba,
                        )
                        if signal_lines is not None:
                            signal_part_lines.extend(signal_lines)
                        else:
                            part_ops.append((extract, mask_extract, sid, msb_str, lsb_str))
                    else:
                        struct_storage_range = self._resolve_struct_storage_mem_select_range(
                            part.target, part.msb, part.lsb
                        )
                        if struct_storage_range is not None:
                            mem_lhs, msb, lsb = struct_storage_range
                            access = self._resolve_memory_element_access(mem_lhs)
                            if access is not None:
                                mid, idx, _name, _indices = access
                                if isinstance(msb, Literal) and isinstance(lsb, Literal):
                                    mem_part_lines.extend(
                                        self._emit_const_mem_range_write_lines(
                                            mid,
                                            idx,
                                            int(lsb.value),
                                            pw,
                                            extract,
                                            mask_extract,
                                            rhs_source=part_rhs_source,
                                            marker_sid=self._mem_marker_sigs[mid],
                                            indent=indent,
                                            is_nba=is_nba,
                                        )
                                    )
                                else:
                                    mem_part_lines.extend(
                                        self._emit_dynamic_mem_range_write_lines(
                                            mid,
                                            idx,
                                            self._emit_expr(msb, 32),
                                            self._emit_expr(lsb, 32),
                                            extract,
                                            mask_extract,
                                            rhs_source=part_rhs_source,
                                            marker_sid=self._mem_marker_sigs[mid],
                                            indent=indent,
                                            is_nba=is_nba,
                                        )
                                    )
                        else:
                            struct_signal_range = self._resolve_struct_signal_select_range(
                                part.target, part.msb, part.lsb
                            )
                            if struct_signal_range is not None:
                                sid, _abs_msb_expr, abs_lsb_expr = struct_signal_range
                                sig_base = self._signal_bases.get(self._signal_names[sid], 0)
                                if isinstance(abs_lsb_expr, Literal):
                                    lsb_str = str(int(abs_lsb_expr.value) - sig_base)
                                else:
                                    lsb_str = self._emit_expr(abs_lsb_expr, 32)
                                    if sig_base != 0:
                                        lsb_str = f"(({lsb_str}) - {sig_base})"
                                signal_lines = self._emit_concat_signal_part_lines(
                                    sid,
                                    lsb_str,
                                    pw,
                                    width_expr,
                                    extract,
                                    mask_extract,
                                    part_rhs_source,
                                    indent,
                                    is_nba=is_nba,
                                )
                                if signal_lines is not None:
                                    signal_part_lines.extend(signal_lines)
                                else:
                                    self._append_struct_signal_concat_part_op(
                                        part_ops, part.target, part.msb, part.lsb, extract, mask_extract
                                    )
            elif isinstance(part, PartSelect):
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
                        mem_part_lines.extend(
                            self._emit_const_mem_range_write_lines(
                                mid,
                                addr,
                                int(lsb_src.value) - bit_base,
                                pw,
                                extract,
                                mask_extract,
                                rhs_source=part_rhs_source,
                                marker_sid=self._mem_marker_sigs[mid],
                                indent=indent,
                                is_nba=is_nba,
                            )
                        )
                    else:
                        msb = self._emit_expr(msb_src, 32)
                        lsb = self._emit_expr(lsb_src, 32)
                        if bit_base != 0:
                            msb = f"(({msb}) - {bit_base})"
                            lsb = f"(({lsb}) - {bit_base})"
                        mem_part_lines.extend(
                            self._emit_dynamic_mem_range_write_lines(
                                mid,
                                addr,
                                msb,
                                lsb,
                                extract,
                                mask_extract,
                                rhs_source=part_rhs_source,
                                marker_sid=self._mem_marker_sigs[mid],
                                indent=indent,
                                is_nba=is_nba,
                            )
                        )
                elif isinstance(part.target, Identifier):
                    struct_storage_range = self._resolve_struct_storage_mem_select_range(part.target, msb_src, lsb_src)
                    if struct_storage_range is not None:
                        mem_lhs, msb, lsb = struct_storage_range
                        access = self._resolve_memory_element_access(mem_lhs)
                        if access is not None:
                            mid, idx, _name, _indices = access
                            if isinstance(msb, Literal) and isinstance(lsb, Literal):
                                mem_part_lines.extend(
                                    self._emit_const_mem_range_write_lines(
                                        mid,
                                        idx,
                                        int(lsb.value),
                                        pw,
                                        extract,
                                        mask_extract,
                                        rhs_source=part_rhs_source,
                                        marker_sid=self._mem_marker_sigs[mid],
                                        indent=indent,
                                        is_nba=is_nba,
                                    )
                                )
                            else:
                                mem_part_lines.extend(
                                    self._emit_dynamic_mem_range_write_lines(
                                        mid,
                                        idx,
                                        self._emit_expr(msb, 32),
                                        self._emit_expr(lsb, 32),
                                        extract,
                                        mask_extract,
                                        rhs_source=part_rhs_source,
                                        marker_sid=self._mem_marker_sigs[mid],
                                        indent=indent,
                                        is_nba=is_nba,
                                    )
                                )
                    else:
                        sid = self._signal_map.get(part.target.name)
                        if sid is not None:
                            sig_base = self._signal_bases.get(part.target.name, 0)
                            if isinstance(msb_src, Literal):
                                msb_str = str(int(msb_src.value) - sig_base)
                            else:
                                msb_str = self._emit_expr(msb_src, 32)
                                if sig_base != 0:
                                    msb_str = f"(({msb_str}) - {sig_base})"
                            if isinstance(lsb_src, Literal):
                                lsb_str = str(int(lsb_src.value) - sig_base)
                            else:
                                lsb_str = self._emit_expr(lsb_src, 32)
                                if sig_base != 0:
                                    lsb_str = f"(({lsb_str}) - {sig_base})"
                            signal_lines = self._emit_concat_signal_part_lines(
                                sid,
                                lsb_str,
                                pw,
                                width_expr,
                                extract,
                                mask_extract,
                                part_rhs_source,
                                indent,
                                is_nba=is_nba,
                            )
                            if signal_lines is not None:
                                signal_part_lines.extend(signal_lines)
                            else:
                                part_ops.append((extract, mask_extract, sid, msb_str, lsb_str))
                        else:
                            struct_signal_range = self._resolve_struct_signal_select_range(
                                part.target, msb_src, lsb_src
                            )
                            if struct_signal_range is not None:
                                sid, _abs_msb_expr, abs_lsb_expr = struct_signal_range
                                sig_base = self._signal_bases.get(self._signal_names[sid], 0)
                                if isinstance(abs_lsb_expr, Literal):
                                    lsb_str = str(int(abs_lsb_expr.value) - sig_base)
                                else:
                                    lsb_str = self._emit_expr(abs_lsb_expr, 32)
                                    if sig_base != 0:
                                        lsb_str = f"(({lsb_str}) - {sig_base})"
                                signal_lines = self._emit_concat_signal_part_lines(
                                    sid,
                                    lsb_str,
                                    pw,
                                    width_expr,
                                    extract,
                                    mask_extract,
                                    part_rhs_source,
                                    indent,
                                    is_nba=is_nba,
                                )
                                if signal_lines is not None:
                                    signal_part_lines.extend(signal_lines)
                                else:
                                    self._append_struct_signal_concat_part_op(
                                        part_ops, part.target, msb_src, lsb_src, extract, mask_extract
                                    )
                            else:
                                self._append_struct_signal_concat_part_op(
                                    part_ops, part.target, msb_src, lsb_src, extract, mask_extract
                                )
            elif isinstance(part, BitSelect):
                mem_target = self._resolve_memory_element_access(part.target)
                if mem_target is not None:
                    mid, addr, name, _indices = mem_target
                    bit_expr = self._emit_index_expr(part.index)
                    bit_base = self._memory_bases.get(name, 0)
                    if bit_base != 0:
                        bit_expr = f"(({bit_expr}) - {bit_base})"
                    mem_part_lines.extend(
                        self._emit_mem_bit_write_lines(
                            mid,
                            addr,
                            bit_expr,
                            extract,
                            mask_extract,
                            marker_sid=self._mem_marker_sigs[mid],
                            indent=indent,
                            is_nba=is_nba,
                            track_change=False,
                        )
                    )
                elif isinstance(part.target, Identifier):
                    sid = self._signal_map.get(part.target.name)
                    if sid is not None:
                        if isinstance(part.index, Literal):
                            idx_str = str(int(part.index.value))
                        else:
                            idx_str = self._emit_index_expr(part.index)
                        signal_lines = self._emit_concat_signal_part_lines(
                            sid,
                            idx_str,
                            pw,
                            width_expr,
                            extract,
                            mask_extract,
                            part_rhs_source,
                            indent,
                            is_nba=is_nba,
                        )
                        if signal_lines is not None:
                            signal_part_lines.extend(signal_lines)
                        else:
                            part_ops.append((extract, mask_extract, sid, idx_str, idx_str))
                    else:
                        struct_storage_range = self._resolve_struct_storage_mem_select_range(
                            part.target, part.index, part.index
                        )
                        if struct_storage_range is not None:
                            mem_lhs, msb, lsb = struct_storage_range
                            access = self._resolve_memory_element_access(mem_lhs)
                            if access is not None:
                                mid, idx, _name, _indices = access
                                if isinstance(msb, Literal) and isinstance(lsb, Literal):
                                    mem_part_lines.extend(
                                        self._emit_const_mem_range_write_lines(
                                            mid,
                                            idx,
                                            int(lsb.value),
                                            pw,
                                            extract,
                                            mask_extract,
                                            rhs_source=part_rhs_source,
                                            marker_sid=self._mem_marker_sigs[mid],
                                            indent=indent,
                                            is_nba=is_nba,
                                        )
                                    )
                                else:
                                    mem_part_lines.extend(
                                        self._emit_mem_bit_write_lines(
                                            mid,
                                            idx,
                                            self._emit_expr(lsb, 32),
                                            extract,
                                            mask_extract,
                                            marker_sid=self._mem_marker_sigs[mid],
                                            indent=indent,
                                            is_nba=is_nba,
                                            track_change=False,
                                        )
                                    )
                        else:
                            struct_signal_range = self._resolve_struct_signal_select_range(
                                part.target, part.index, part.index
                            )
                            if struct_signal_range is not None:
                                sid, _abs_msb_expr, abs_lsb_expr = struct_signal_range
                                sig_base = self._signal_bases.get(self._signal_names[sid], 0)
                                if isinstance(abs_lsb_expr, Literal):
                                    lsb_str = str(int(abs_lsb_expr.value) - sig_base)
                                else:
                                    lsb_str = self._emit_expr(abs_lsb_expr, 32)
                                    if sig_base != 0:
                                        lsb_str = f"(({lsb_str}) - {sig_base})"
                                signal_lines = self._emit_concat_signal_part_lines(
                                    sid,
                                    lsb_str,
                                    pw,
                                    width_expr,
                                    extract,
                                    mask_extract,
                                    part_rhs_source,
                                    indent,
                                    is_nba=is_nba,
                                )
                                if signal_lines is not None:
                                    signal_part_lines.extend(signal_lines)
                                else:
                                    self._append_struct_signal_concat_part_op(
                                        part_ops, part.target, part.index, part.index, extract, mask_extract
                                    )
                            else:
                                self._append_struct_signal_concat_part_op(
                                    part_ops, part.target, part.index, part.index, extract, mask_extract
                                )
            else:
                # Fallback: simple identifier ΓÇö full signal write
                if isinstance(part, Identifier):
                    part_name = self._identifier_name(part)
                    sid = self._signal_map.get(part_name)
                    if sid is not None:
                        signal_lines = self._emit_concat_signal_part_lines(
                            sid,
                            None,
                            pw,
                            width_expr,
                            extract,
                            mask_extract,
                            part_rhs_source,
                            indent,
                            is_nba=is_nba,
                        )
                        if signal_lines is not None:
                            signal_part_lines.extend(signal_lines)
                        else:
                            part_ops.append((extract, mask_extract, sid, None, None))
                    else:
                        struct_storage_range = self._resolve_struct_storage_mem_range(part_name)
                        if (
                            struct_storage_range is not None
                            and isinstance(struct_storage_range[1], Literal)
                            and isinstance(struct_storage_range[2], Literal)
                        ):
                            mem_lhs, msb, lsb = struct_storage_range
                            access = self._resolve_memory_element_access(mem_lhs)
                            if access is not None:
                                mid, idx, _name, _indices = access
                                mem_part_lines.extend(
                                    self._emit_const_mem_range_write_lines(
                                        mid,
                                        idx,
                                        int(lsb.value),
                                        int(msb.value) - int(lsb.value) + 1,
                                        extract,
                                        mask_extract,
                                        rhs_source=part_rhs_source,
                                        marker_sid=self._mem_marker_sigs[mid],
                                        indent=indent,
                                        is_nba=is_nba,
                                    )
                                )
                        else:
                            struct_info = self._resolve_struct_access(part_name)
                            if struct_info is not None:
                                base_sid, offset, field_width = struct_info
                                signal_lines = self._emit_concat_signal_part_lines(
                                    base_sid,
                                    str(offset),
                                    field_width,
                                    width_expr,
                                    extract,
                                    mask_extract,
                                    part_rhs_source,
                                    indent,
                                    is_nba=is_nba,
                                )
                                if signal_lines is not None:
                                    signal_part_lines.extend(signal_lines)
                                else:
                                    self._append_struct_signal_concat_part_op(
                                        part_ops,
                                        part,
                                        Literal(field_width - 1, width=32),
                                        Literal(0, width=32),
                                        extract,
                                        mask_extract,
                                    )
        # Group operations by signal ID to accumulate modifications
        from collections import OrderedDict  # noqa: PLC0415

        sig_ops: OrderedDict[int, list[tuple[str, str, str | None, str | None]]] = OrderedDict()
        for extract, mask_extract, sid, msb_str, lsb_str in part_ops:
            sig_ops.setdefault(sid, []).append((extract, mask_extract, msb_str, lsb_str))

        for sid, ops in sig_ops.items():
            sig_w = self._signal_widths[sid]
            wmask_val = f"wmask({sig_w})"
            if is_nba:
                # Read current: use nba_val if already dirty, else val
                lines.append(f"{pad}_cacc_val = c.nba_val[{sid}] if c.nba_dirty[{sid}] else c.val[{sid}]")
                lines.append(f"{pad}_cacc_mask = c.nba_mask[{sid}] if c.nba_dirty[{sid}] else c.mask[{sid}]")
                for extract, mask_extract, msb_str, lsb_str in ops:
                    if msb_str is None:
                        # Full signal write
                        lines.append(f"{pad}_part_mask = ({mask_extract}) & {wmask_val}")
                        lines.append(f"{pad}_cacc_val = (({extract}) & {wmask_val}) & ~_part_mask")
                        lines.append(f"{pad}_cacc_mask = _part_mask")
                    else:
                        # Range/bit write: clear target bits, set new bits
                        lines.append(f"{pad}_slice_mask = wmask({msb_str} - {lsb_str} + 1)")
                        lines.append(f"{pad}_part_mask = ({mask_extract}) & _slice_mask")
                        lines.append(
                            f"{pad}_cacc_val = (_cacc_val & ~(_slice_mask << {lsb_str}))"
                            f" | (((({extract}) & ~_part_mask) & _slice_mask) << {lsb_str})"
                        )
                        lines.append(
                            f"{pad}_cacc_mask = (_cacc_mask & ~(_slice_mask << {lsb_str}))"
                            f" | ((_part_mask & _slice_mask) << {lsb_str})"
                        )
                lines.append(f"{pad}c.nba_val[{sid}] = _cacc_val & {wmask_val}")
                lines.append(f"{pad}c.nba_mask[{sid}] = _cacc_mask & {wmask_val}")
                lines.append(f"{pad}c.nba_dirty[{sid}] = 1")
                lines.append(f"{pad}c.nba_pending = 1")
            else:
                # Blocking: accumulate into val directly
                lines.append(f"{pad}_cacc_val = c.val[{sid}]")
                lines.append(f"{pad}_cacc_mask = c.mask[{sid}]")
                for extract, mask_extract, msb_str, lsb_str in ops:
                    if msb_str is None:
                        lines.append(f"{pad}_part_mask = ({mask_extract}) & {wmask_val}")
                        lines.append(f"{pad}_cacc_val = (({extract}) & {wmask_val}) & ~_part_mask")
                        lines.append(f"{pad}_cacc_mask = _part_mask")
                    else:
                        lines.append(f"{pad}_slice_mask = wmask({msb_str} - {lsb_str} + 1)")
                        lines.append(f"{pad}_part_mask = ({mask_extract}) & _slice_mask")
                        lines.append(
                            f"{pad}_cacc_val = (_cacc_val & ~(_slice_mask << {lsb_str}))"
                            f" | (((({extract}) & ~_part_mask) & _slice_mask) << {lsb_str})"
                        )
                        lines.append(
                            f"{pad}_cacc_mask = (_cacc_mask & ~(_slice_mask << {lsb_str}))"
                            f" | ((_part_mask & _slice_mask) << {lsb_str})"
                        )
                lines.append(f"{pad}_cacc_val = _cacc_val & {wmask_val}")
                lines.append(f"{pad}_cacc_mask = _cacc_mask & {wmask_val}")
                lines.append(f"{pad}if _cacc_val != c.val[{sid}] or _cacc_mask != c.mask[{sid}]:")
                lines.append(f"{pad}    c.val[{sid}] = _cacc_val")
                lines.append(f"{pad}    c.mask[{sid}] = _cacc_mask")
                lines.append(f"{pad}    c.dirty[{sid}] = 1")

        lines.extend(signal_part_lines)
        lines.extend(mem_part_lines)
        return lines

    def _emit_mem_write(
        self,
        lhs: BitSelect,
        rhs: Expression,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str]:
        """Emit memory write: mem[addr] = rhs or mem[addr] <= rhs."""
        pad = "    " * indent
        access = self._resolve_memory_element_access(lhs)
        if access is None:
            raise NotImplementedError(
                "Compiled engine cannot resolve memory element write target: "
                "the LHS bit-select does not map to a known memory array. "
                "Use engine='vm' for this construct."
            )
        mid, idx, _name, _indices = access
        elem_w, _depth = self._mem_info[mid]
        marker_sid = self._mem_marker_sigs[mid]
        if elem_w > _WORD_BITS:
            words = self._mem_words(mid)
            rhs_access = self._resolve_memory_element_access(rhs)
            if rhs_access is not None:
                rhs_mid, rhs_idx, _rhs_name, _rhs_indices = rhs_access
                if self._mem_info[rhs_mid][0] == elem_w and self._memory_layout_matches(mid, rhs_mid):
                    return self._emit_wide_mem_copy_lines(
                        mid,
                        idx,
                        rhs_mid,
                        rhs_idx,
                        marker_sid=marker_sid,
                        indent=indent,
                        is_nba=is_nba,
                        track_change=False,
                    )
            rhs_source = self._resolve_signal_slice_source(rhs)
            if rhs_source is not None:
                return self._emit_const_mem_range_write_lines(
                    mid,
                    idx,
                    0,
                    elem_w,
                    "0",
                    "0",
                    rhs_source=rhs_source,
                    marker_sid=marker_sid,
                    indent=indent,
                    is_nba=is_nba,
                )
            flat_parts = self._flatten_concat_identifier_parts(rhs)
            if flat_parts is not None:
                return self._emit_wide_mem_flat_concat_lines(
                    mid,
                    idx,
                    flat_parts,
                    elem_w,
                    marker_sid=marker_sid,
                    indent=indent,
                    is_nba=is_nba,
                    track_change=False,
                )
            if isinstance(rhs, Concatenation):
                concat_lhs = self._build_whole_mem_concat_lhs(lhs, rhs, elem_w)
                if concat_lhs is not None:
                    return self._emit_concat_lhs(concat_lhs, rhs, indent, is_nba=is_nba)
            if isinstance(rhs, Literal) and not rhs.is_x and not rhs.is_z and int(rhs.value) == 0:
                return self._emit_wide_mem_zero_lines(
                    mid, idx, marker_sid=marker_sid, indent=indent, is_nba=is_nba, track_change=False
                )
        rhs_val = self._emit_expr(rhs, elem_w)
        rhs_mask = self._emit_mask_expr(rhs, elem_w)
        return self._emit_scalar_mem_write_lines(
            mid, idx, rhs_val, rhs_mask, elem_w, marker_sid=marker_sid, indent=indent, is_nba=is_nba, track_change=False
        )

    def _emit_const_mem_range_write_lines(  # noqa: PLR0913
        self,
        mid: int,
        idx: str,
        lsb_v: int,
        sel_w: int,
        rhs_val: str,
        rhs_mask: str,
        *,
        rhs_source: tuple[int, str] | None = None,
        marker_sid: int,
        indent: int,
        is_nba: bool,
    ) -> list[str]:
        """Emit a memory range write for a constant in-element bit range."""
        pad = "    " * indent
        elem_w, _depth = self._mem_info[mid]
        msb_v = lsb_v + sel_w - 1
        if elem_w > _WORD_BITS:
            if rhs_source is not None:
                rhs_sid, rhs_lsb = rhs_source
                helper = f"_wmem{mid}_stage_insert_signal_slice" if is_nba else f"_wmem{mid}_assign_insert_signal_slice"
                if is_nba:
                    return [f"{pad}{helper}(c, ({idx}), {lsb_v}, {rhs_sid}, <int>({rhs_lsb}), {sel_w})"]
                return [f"{pad}{helper}(c, ({idx}), {lsb_v}, {rhs_sid}, <int>({rhs_lsb}), {sel_w}, {marker_sid})"]
            words = self._mem_words(mid)
            lines = [] if is_nba else [f"{pad}_mchg = 0"]
            start_word = lsb_v // _WORD_BITS
            end_word = msb_v // _WORD_BITS
            for word_index in range(start_word, end_word + 1):
                chunk_lsb = max(lsb_v, word_index * _WORD_BITS)
                chunk_msb = min(msb_v, (word_index + 1) * _WORD_BITS - 1)
                chunk_w = chunk_msb - chunk_lsb + 1
                src_shift = chunk_lsb - lsb_v
                word_lsb = chunk_lsb - (word_index * _WORD_BITS)
                chunk_mask = f"_word_mask64({chunk_w})"
                word_mask = f"({chunk_mask} << {word_lsb})"
                chunk_val = (
                    f"((<unsigned long long>((({rhs_val}) >> {src_shift}))) & {chunk_mask})"
                    if src_shift
                    else f"((<unsigned long long>({rhs_val})) & {chunk_mask})"
                )
                chunk_rmask = (
                    f"((<unsigned long long>((({rhs_mask}) >> {src_shift}))) & {chunk_mask})"
                    if src_shift
                    else f"((<unsigned long long>({rhs_mask})) & {chunk_mask})"
                )
                if is_nba:
                    lines.extend(
                        [
                            f"{pad}c.nba_mem_range_mid[c.nba_mem_range_count] = {mid}",
                            f"{pad}c.nba_mem_range_addr[c.nba_mem_range_count] = (({idx}) * {words}) + {word_index}",
                            f"{pad}c.nba_mem_range_msb[c.nba_mem_range_count] = {word_lsb + chunk_w - 1}",
                            f"{pad}c.nba_mem_range_lsb[c.nba_mem_range_count] = {word_lsb}",
                            f"{pad}c.nba_mem_range_val[c.nba_mem_range_count] = <long long>({chunk_val})",
                            f"{pad}c.nba_mem_range_mask[c.nba_mem_range_count] = <long long>({chunk_rmask})",
                            f"{pad}c.nba_mem_range_count += 1",
                        ]
                    )
                else:
                    word_addr = f"((({idx}) * {words}) + {word_index})"
                    new_val = (
                        f"((c.wide_mem_{mid}_val[{word_addr}] & ~{word_mask})"
                        f" | ((({chunk_val}) & ~({chunk_rmask})) << {word_lsb}))"
                    )
                    new_mask = (
                        f"((c.wide_mem_{mid}_mask[{word_addr}] & ~{word_mask})"
                        f" | ((({chunk_rmask}) & {chunk_mask}) << {word_lsb}))"
                    )
                    lines.extend(
                        [
                            f"{pad}_mwvu = {new_val}",
                            f"{pad}_mwmu = {new_mask}",
                            f"{pad}if c.wide_mem_{mid}_val[{word_addr}] != _mwvu or c.wide_mem_{mid}_mask[{word_addr}] != _mwmu:",
                            f"{pad}    c.wide_mem_{mid}_val[{word_addr}] = _mwvu",
                            f"{pad}    c.wide_mem_{mid}_mask[{word_addr}] = _mwmu",
                            f"{pad}    _mchg = 1",
                        ]
                    )
            if is_nba:
                lines.append(f"{pad}c.nba_pending = 1")
            else:
                lines.extend(
                    [
                        f"{pad}if _mchg:",
                        f"{pad}    c.val[{marker_sid}] ^= 1",
                        f"{pad}    c.dirty[{marker_sid}] = 1",
                    ]
                )
            return lines
        range_mask_int = ((1 << sel_w) - 1) << lsb_v
        range_mask = (
            f"(<unsigned long long>0x{range_mask_int:x})" if 0 <= msb_v < _WORD_BITS else _cy_hex(range_mask_int)
        )
        sel_mask = _cy_hex((1 << sel_w) - 1)
        if is_nba:
            return [
                f"{pad}c.nba_mem_range_mid[c.nba_mem_range_count] = {mid}",
                f"{pad}c.nba_mem_range_addr[c.nba_mem_range_count] = ({idx})",
                f"{pad}c.nba_mem_range_msb[c.nba_mem_range_count] = {msb_v}",
                f"{pad}c.nba_mem_range_lsb[c.nba_mem_range_count] = {lsb_v}",
                f"{pad}c.nba_mem_range_val[c.nba_mem_range_count] = ({rhs_val}) & {sel_mask}",
                f"{pad}c.nba_mem_range_mask[c.nba_mem_range_count] = ({rhs_mask}) & {sel_mask}",
                f"{pad}c.nba_mem_range_count += 1",
                f"{pad}c.nba_pending = 1",
            ]
        return [
            f"{pad}_mwi = ({idx})",
            f"{pad}_mwv = (c.mem_{mid}_val[_mwi] & ~{range_mask})"
            f" | ((((<unsigned long long>(({rhs_val}) & ~({rhs_mask}))) << {lsb_v}) & {range_mask}))",
            f"{pad}_mwm = (c.mem_{mid}_mask[_mwi] & ~{range_mask})"
            f" | ((((<unsigned long long>({rhs_mask})) << {lsb_v}) & {range_mask}))",
            f"{pad}if c.mem_{mid}_val[_mwi] != _mwv or c.mem_{mid}_mask[_mwi] != _mwm:",
            f"{pad}    c.mem_{mid}_val[_mwi] = _mwv",
            f"{pad}    c.mem_{mid}_mask[_mwi] = _mwm",
            f"{pad}    c.val[{marker_sid}] ^= 1",
            f"{pad}    c.dirty[{marker_sid}] = 1",
        ]

    def _emit_dynamic_mem_range_write_lines(  # noqa: PLR0913
        self,
        mid: int,
        idx: str,
        msb_expr: str,
        lsb_expr: str,
        rhs_val: str,
        rhs_mask: str,
        *,
        rhs_source: tuple[int, str] | None = None,
        marker_sid: int,
        indent: int,
        is_nba: bool,
    ) -> list[str]:
        """Emit a memory range write for dynamic in-element bounds using raw rhs value/mask expressions."""
        pad = "    " * indent
        elem_w, _depth = self._mem_info[mid]
        if elem_w > _WORD_BITS:
            if rhs_source is not None:
                rhs_sid, rhs_lsb = rhs_source
                sel_w_expr = f"(({msb_expr}) - ({lsb_expr}) + 1)"
                helper = f"_wmem{mid}_stage_insert_signal_slice" if is_nba else f"_wmem{mid}_assign_insert_signal_slice"
                if is_nba:
                    return [f"{pad}{helper}(c, ({idx}), ({lsb_expr}), {rhs_sid}, <int>({rhs_lsb}), {sel_w_expr})"]
                return [
                    f"{pad}{helper}(c, ({idx}), ({lsb_expr}), {rhs_sid}, <int>({rhs_lsb}), {sel_w_expr}, {marker_sid})"
                ]
            return self._emit_wide_mem_dynamic_range_lines(
                mid,
                idx,
                msb_expr,
                lsb_expr,
                rhs_val,
                rhs_mask,
                marker_sid=marker_sid,
                indent=indent,
                is_nba=is_nba,
            )
        if is_nba:
            return [
                f"{pad}c.nba_mem_range_mid[c.nba_mem_range_count] = {mid}",
                f"{pad}c.nba_mem_range_addr[c.nba_mem_range_count] = ({idx})",
                f"{pad}c.nba_mem_range_msb[c.nba_mem_range_count] = ({msb_expr})",
                f"{pad}c.nba_mem_range_lsb[c.nba_mem_range_count] = ({lsb_expr})",
                f"{pad}c.nba_mem_range_val[c.nba_mem_range_count] = ({rhs_val})",
                f"{pad}c.nba_mem_range_mask[c.nba_mem_range_count] = ({rhs_mask})",
                f"{pad}c.nba_mem_range_count += 1",
                f"{pad}c.nba_pending = 1",
            ]
        return [
            f"{pad}_mwi = ({idx})",
            f"{pad}_mwv = (c.mem_{mid}_val[_mwi]"
            f" & ~(wmask(({msb_expr}) - ({lsb_expr}) + 1) << ({lsb_expr})))"
            f" | (((({rhs_val}) & ~({rhs_mask})) << ({lsb_expr})) & (wmask(({msb_expr}) - ({lsb_expr}) + 1) << ({lsb_expr})))",
            f"{pad}_mwm = (c.mem_{mid}_mask[_mwi] & ~(wmask(({msb_expr}) - ({lsb_expr}) + 1) << ({lsb_expr})))"
            f" | ((({rhs_mask}) << ({lsb_expr})) & (wmask(({msb_expr}) - ({lsb_expr}) + 1) << ({lsb_expr})))",
            f"{pad}if c.mem_{mid}_val[_mwi] != _mwv or c.mem_{mid}_mask[_mwi] != _mwm:",
            f"{pad}    c.mem_{mid}_val[_mwi] = _mwv",
            f"{pad}    c.mem_{mid}_mask[_mwi] = _mwm",
            f"{pad}    c.val[{marker_sid}] ^= 1",
            f"{pad}    c.dirty[{marker_sid}] = 1",
        ]

    def _emit_mem_range_write(  # noqa: PLR0913
        self,
        mem_lhs: BitSelect,
        msb_expr: Expression,
        lsb_expr: Expression,
        rhs: Expression,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str]:
        """Emit memory partial-range write: memory[addr][msb:lsb] = rhs."""
        pad = "    " * indent
        access = self._resolve_memory_element_access(mem_lhs)
        if access is None:
            raise NotImplementedError(
                "Compiled engine cannot resolve memory range-select write target: "
                "the LHS does not map to a known memory array. "
                "Use engine='vm' for this construct."
            )
        mid, idx, name, _indices = access
        elem_w, _depth = self._mem_info[mid]
        marker_sid = self._mem_marker_sigs[mid]
        sig_base = self._memory_bases.get(name, 0)
        if isinstance(msb_expr, Literal) and isinstance(lsb_expr, Literal):
            msb_v = int(msb_expr.value) - sig_base
            lsb_v = int(lsb_expr.value) - sig_base
            sel_w = msb_v - lsb_v + 1
            rhs_source = None
            rhs_mem_source = None
            if elem_w > _WORD_BITS and sel_w > _WORD_BITS:
                rhs_source = self._resolve_signal_slice_source(rhs)
                rhs_mem_source = self._resolve_memory_slice_source(rhs)
                if (
                    rhs_mem_source is not None
                    and rhs_mem_source[0] != mid
                    and self._mem_info[rhs_mem_source[0]][0] > _WORD_BITS
                ):
                    rhs_mid, rhs_idx, rhs_lsb = rhs_mem_source
                    return self._emit_wide_mem_insert_mem_slice_lines(
                        mid,
                        idx,
                        str(lsb_v),
                        rhs_mid,
                        rhs_idx,
                        rhs_lsb,
                        str(sel_w),
                        marker_sid=marker_sid,
                        indent=indent,
                        is_nba=is_nba,
                    )
            rhs_val = self._emit_expr(rhs, sel_w)
            rhs_mask = self._emit_mask_expr(rhs, sel_w)
            return self._emit_const_mem_range_write_lines(
                mid,
                idx,
                lsb_v,
                sel_w,
                rhs_val,
                rhs_mask,
                rhs_source=rhs_source,
                marker_sid=marker_sid,
                indent=indent,
                is_nba=is_nba,
            )
        # Dynamic msb/lsb (rare)
        msb = self._emit_expr(msb_expr, 32)
        lsb = self._emit_expr(lsb_expr, 32)
        if sig_base != 0:
            msb = f"(({msb}) - {sig_base})"
            lsb = f"(({lsb}) - {sig_base})"
        if elem_w > _WORD_BITS:
            rhs_source = self._resolve_signal_slice_source(rhs)
            if rhs_source is not None:
                rhs_sid, rhs_lsb = rhs_source
                sel_w_expr = f"(({msb}) - ({lsb}) + 1)"
                helper = f"_wmem{mid}_stage_insert_signal_slice" if is_nba else f"_wmem{mid}_assign_insert_signal_slice"
                if is_nba:
                    return [f"{pad}{helper}(c, ({idx}), ({lsb}), {rhs_sid}, <int>({rhs_lsb}), {sel_w_expr})"]
                return [f"{pad}{helper}(c, ({idx}), ({lsb}), {rhs_sid}, <int>({rhs_lsb}), {sel_w_expr}, {marker_sid})"]
            rhs_mem_source = self._resolve_memory_slice_source(rhs)
            if (
                rhs_mem_source is not None
                and rhs_mem_source[0] != mid
                and self._mem_info[rhs_mem_source[0]][0] > _WORD_BITS
            ):
                rhs_mid, rhs_idx, rhs_lsb = rhs_mem_source
                return self._emit_wide_mem_insert_mem_slice_lines(
                    mid,
                    idx,
                    lsb,
                    rhs_mid,
                    rhs_idx,
                    rhs_lsb,
                    f"(({msb}) - ({lsb}) + 1)",
                    marker_sid=marker_sid,
                    indent=indent,
                    is_nba=is_nba,
                )
        rhs_val = self._emit_expr(rhs, elem_w)
        rhs_mask = self._emit_mask_expr(rhs, elem_w)
        return self._emit_dynamic_mem_range_write_lines(
            mid,
            idx,
            msb,
            lsb,
            rhs_val,
            rhs_mask,
            marker_sid=marker_sid,
            indent=indent,
            is_nba=is_nba,
        )

    def _emit_if(self, stmt: IfStatement, indent: int, *, context: str = "process") -> list[str]:
        """Emit if/else as Cython if/else."""
        pad = "    " * indent
        old_et = self._et_pending
        self._et_pending = []
        cond = self._emit_expr(stmt.condition, 1)
        et_lines = [f"{pad}{t}" for t in self._et_pending]
        self._et_pending = old_et
        lines = et_lines + [f"{pad}if ({cond}):"]

        if stmt.then_body:
            body = self._emit_stmt(stmt.then_body, indent + 1, context=context)
            lines.extend(body if body else [f"{'    ' * (indent + 1)}pass"])
        else:
            lines.append(f"{'    ' * (indent + 1)}pass")

        if stmt.else_body:
            lines.append(f"{pad}else:")
            body = self._emit_stmt(stmt.else_body, indent + 1, context=context)
            lines.extend(body if body else [f"{'    ' * (indent + 1)}pass"])

        return lines

    def _emit_case(self, stmt: CaseStatement, indent: int, *, context: str = "process") -> list[str]:
        """Emit case/casex/casez as if/elif chain."""
        pad = "    " * indent
        sel_w = self._expr_width(stmt.expression)
        is_casex = hasattr(stmt, "case_type") and stmt.case_type in ("casex", "casez")

        sel = self._emit_expr(stmt.expression, sel_w)
        lines: list[str] = []
        default_lines: list[str] = []
        first = True

        # For casex/casez, we need mask expressions for don't-care matching
        if is_casex:
            sel_mask = self._emit_expr_mask(stmt.expression)

        for item in stmt.items:
            if item.is_default:
                default_lines = self._emit_stmt(item.body, indent + 1, context=context) if item.body else []
                continue

            # Build comparison conditions
            conds = []
            for val_expr in item.values:
                val = self._emit_expr(val_expr, sel_w)
                if is_casex:
                    val_mask = self._emit_expr_mask(val_expr)
                    # casex: don't-care bits = x/z in either operand
                    # match when (sel_val ^ item_val) & ~(sel_mask | item_mask) == 0
                    conds.append(f"(({sel}) ^ ({val})) & ~(({sel_mask}) | ({val_mask})) == 0")
                else:
                    conds.append(f"({sel}) == ({val})")
            cond = " or ".join(conds) if conds else "0"

            keyword = "if" if first else "elif"
            lines.append(f"{pad}{keyword} {cond}:")
            first = False

            if item.body:
                body = self._emit_stmt(item.body, indent + 1, context=context)
                lines.extend(body if body else [f"{'    ' * (indent + 1)}pass"])
            else:
                lines.append(f"{'    ' * (indent + 1)}pass")

        if default_lines:
            if first:
                # Only a default item ΓÇö unconditional
                lines.extend(default_lines)
            else:
                lines.append(f"{pad}else:")
                lines.extend(default_lines if default_lines else [f"{'    ' * (indent + 1)}pass"])
        elif not first:
            # No default ΓÇö no else needed
            pass

        # If case had no items at all, emit pass
        if not lines:
            lines.append(f"{pad}pass  # empty case")

        return lines

    def _emit_for(self, stmt: ForLoop, indent: int, *, context: str = "process") -> list[str]:
        """Emit for loop as Cython while loop.

        If the loop variable is a local integer (not a signal), it is tracked
        as a Cython local variable so that the init, condition, and update all
        work correctly.
        """
        # Detect local loop variable: init assigns to an Identifier not in signal_map
        loop_var_name: str | None = None
        if isinstance(stmt.init, BlockingAssign) and isinstance(stmt.init.lhs, Identifier):
            if self._signal_map.get(stmt.init.lhs.name) is None:
                lv_name = stmt.init.lhs.name
                # Don't shadow an existing local
                if lv_name not in self._local_vars:
                    cy_name = f"_lv_{lv_name}"
                    self._local_vars[lv_name] = cy_name
                    loop_var_name = lv_name

        lines: list[str] = []
        # init
        lines.extend(self._emit_stmt(stmt.init, indent, context=context))
        pad = "    " * indent
        lines.append(f"{pad}while True:")
        inner_pad = "    " * (indent + 1)
        if stmt.signed_var and isinstance(stmt.init, BlockingAssign) and isinstance(stmt.init.lhs, Identifier):
            loop_width = self._expr_width(stmt.init.lhs)
            if loop_var_name is not None:
                lines.append(f"{inner_pad}if {self._local_vars[loop_var_name]} < 0:")
                lines.append(f"{inner_pad}    break")
            elif loop_width > 1:
                loop_expr = self._emit_expr(stmt.init.lhs, loop_width)
                lines.append(f"{inner_pad}if ((({loop_expr}) >> {loop_width - 1}) & 1) != 0:")
                lines.append(f"{inner_pad}    break")
        cond = self._emit_expr(stmt.condition, 1)
        lines.append(f"{inner_pad}if not ({cond}):")
        lines.append(f"{inner_pad}    break")
        # body
        if stmt.body:
            lines.extend(self._emit_stmt(stmt.body, indent + 1, context=context))
        # update
        lines.extend(self._emit_stmt(stmt.update, indent + 1, context=context))

        # Remove local var after loop scope
        if loop_var_name is not None:
            del self._local_vars[loop_var_name]

        return lines

    def _emit_repeat(self, stmt: RepeatLoop, indent: int, *, context: str = "process") -> list[str]:
        """Emit repeat loop as a finite Cython for loop.

        The repeat count is evaluated once before the loop body. If the count
        expression contains any X/Z bits, the compiled path matches the
        reference executor by running zero iterations.
        """
        pad = "    " * indent
        inner_pad = "    " * (indent + 1)
        temp_index = self._next_temp_index()
        count_name = f"_lv_repeat_count_{temp_index}"
        iter_name = f"_lv_repeat_i_{temp_index}"
        count_width = max(self._expr_width(stmt.count), 1)
        count_expr = self._emit_expr(stmt.count, count_width)
        count_mask = self._emit_mask_expr(stmt.count, count_width)

        lines = [
            f"{pad}{count_name} = 0",
            f"{pad}if ({count_mask}) == 0:",
            f"{inner_pad}{count_name} = ({count_expr})",
            f"{pad}for {iter_name} in range({count_name}):",
        ]

        if stmt.body:
            body = self._emit_stmt(stmt.body, indent + 1, context=context)
            lines.extend(body if body else [f"{inner_pad}pass"])
        else:
            lines.append(f"{inner_pad}pass")
        if context == "process":
            lines.append(f"{inner_pad}if c.finished:")
            lines.append(f"{inner_pad}    return")

        return lines

    def _emit_while(self, stmt: WhileLoop, indent: int, *, context: str = "process") -> list[str]:
        """Emit while loop with the same bounded-iteration guard as the interpreter."""
        pad = "    " * indent
        inner_pad = "    " * (indent + 1)
        temp_index = self._next_temp_index()
        iter_name = f"_lv_while_iters_{temp_index}"
        cond = self._emit_expr(stmt.condition, 1)
        cond_mask = self._emit_mask_expr(stmt.condition, 1)

        lines = [
            f"{pad}{iter_name} = 0",
            f"{pad}while True:",
            f"{inner_pad}if ({cond_mask}) != 0 or not ({cond}):",
            f"{inner_pad}    break",
        ]

        if stmt.body:
            body = self._emit_stmt(stmt.body, indent + 1, context=context)
            lines.extend(body if body else [f"{inner_pad}pass"])
        else:
            lines.append(f"{inner_pad}pass")
        if context == "process":
            lines.append(f"{inner_pad}if c.finished:")
            lines.append(f"{inner_pad}    return")

        lines.append(f"{inner_pad}{iter_name} += 1")
        lines.append(f"{inner_pad}if {iter_name} > PROCESS_LOOP_LIMIT:")
        if context == "init":
            lines.append(f"{inner_pad}    raise RuntimeError('While loop exceeded {_PROCESS_LOOP_LIMIT} iterations')")
        else:
            lines.append(f"{inner_pad}    c.error_code = ERR_WHILE_LOOP_LIMIT")
            lines.append(f"{inner_pad}    return")
        return lines

    def _emit_forever(self, stmt: ForeverLoop, indent: int, *, context: str = "process") -> list[str]:
        """Emit forever loop with shared bounded-iteration guard."""
        pad = "    " * indent
        inner_pad = "    " * (indent + 1)
        temp_index = self._next_temp_index()
        iter_name = f"_lv_forever_iters_{temp_index}"

        lines = [
            f"{pad}{iter_name} = 0",
            f"{pad}while True:",
        ]

        if stmt.body:
            body = self._emit_stmt(stmt.body, indent + 1, context=context)
            lines.extend(body if body else [f"{inner_pad}pass"])
        else:
            lines.append(f"{inner_pad}pass")
        if context == "process":
            lines.append(f"{inner_pad}if c.finished:")
            lines.append(f"{inner_pad}    return")

        lines.append(f"{inner_pad}{iter_name} += 1")
        lines.append(f"{inner_pad}if {iter_name} > PROCESS_LOOP_LIMIT:")
        if context == "init":
            lines.append(f"{inner_pad}    raise RuntimeError('Forever loop exceeded {_PROCESS_LOOP_LIMIT} iterations')")
        else:
            lines.append(f"{inner_pad}    c.error_code = ERR_FOREVER_LOOP_LIMIT")
            lines.append(f"{inner_pad}    return")
        return lines

    def _next_temp_index(self) -> int:
        """Allocate a unique suffix for generated local temporaries."""
        index = self._temp_var_counter
        self._temp_var_counter += 1
        return index

    # ΓöÇΓöÇ System task codegen ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    def _emit_system_task(self, stmt: SystemTaskCall, indent: int) -> list[str]:
        """Emit code for $write, $display, $fflush, $finish, $stop."""
        pad = "    " * indent
        name = stmt.task_name.lower()

        if name == "$fflush":
            return [f"{pad}pass  # $fflush (no-op in compiled)"]

        if name in ("$finish", "$stop"):
            return [f"{pad}c.finished = 1  # {name}"]

        if name in ("$display", "$write"):
            lines: list[str] = []
            if stmt.arguments:
                first = stmt.arguments[0]
                if isinstance(first, StringLiteral):
                    lines = self._emit_format_string(first.value, stmt.arguments[1:], indent)
                else:
                    for i, arg in enumerate(stmt.arguments):
                        if i > 0:
                            lines.append(f"{pad}_out_char(c, 32)  # space")
                        if isinstance(arg, StringLiteral) or (
                            isinstance(arg, TernaryOp)
                            and (isinstance(arg.true_expr, StringLiteral) or isinstance(arg.false_expr, StringLiteral))
                        ):
                            lines.extend(self._emit_string_output(arg, indent))
                        else:
                            w = self._expr_width(arg)
                            expr_code = self._emit_expr(arg, w)
                            lines.append(f"{pad}_out_int_dec(c, {expr_code})")
            if name == "$display":
                lines.append(f"{pad}_out_newline(c)")
            return lines

        # Unknown system tasks ΓÇö skip silently
        return [f"{pad}pass  # {stmt.task_name} skipped"]

    def _emit_format_string(self, fmt: str, args: list, indent: int) -> list[str]:
        """Parse a Verilog format string and emit _out_* calls."""
        pad = "    " * indent
        lines: list[str] = []
        arg_idx = 0
        i = 0

        while i < len(fmt):
            ch = fmt[i]
            if ch == "\\" and i + 1 < len(fmt):
                # Escape sequences
                nch = fmt[i + 1]
                if nch == "n":
                    lines.append(f"{pad}_out_newline(c)")
                    i += 2
                elif nch == "t":
                    lines.append(f"{pad}_out_char(c, 9)  # tab")
                    i += 2
                elif nch == "\\":
                    lines.append(f"{pad}_out_char(c, 92)  # backslash")
                    i += 2
                else:
                    lines.append(f"{pad}_out_char(c, {ord(nch)})")
                    i += 2
            elif ch == "%" and i + 1 < len(fmt):
                i += 1
                # Parse optional '0' (zero-pad flag) and width
                zero_pad = False
                if i < len(fmt) and fmt[i] == "0":
                    zero_pad = True
                    i += 1
                width = 0
                while i < len(fmt) and fmt[i].isdigit():
                    width = width * 10 + int(fmt[i])
                    i += 1
                if i >= len(fmt):
                    break
                spec = fmt[i].lower()
                i += 1
                if spec == "%":
                    lines.append(f"{pad}_out_char(c, 37)  # '%'")
                elif spec == "c" and arg_idx < len(args):
                    w = self._expr_width(args[arg_idx])
                    expr_code = self._emit_expr(args[arg_idx], w)
                    lines.append(f"{pad}_out_char(c, <char>({expr_code} & 0xff))")
                    arg_idx += 1
                elif spec == "d" and arg_idx < len(args):
                    w = self._expr_width(args[arg_idx])
                    expr_code = self._emit_expr(args[arg_idx], w)
                    if width:
                        lines.append(f"{pad}_out_int_dec_w(c, {expr_code}, {width}, {1 if zero_pad else 0})")
                    else:
                        lines.append(f"{pad}_out_int_dec(c, {expr_code})")
                    arg_idx += 1
                elif spec in ("h", "x") and arg_idx < len(args):
                    w = self._expr_width(args[arg_idx])
                    expr_code = self._emit_expr(args[arg_idx], w)
                    if width:
                        lines.append(f"{pad}_out_int_hex_w(c, {expr_code}, {width}, {1 if zero_pad else 0})")
                    else:
                        lines.append(f"{pad}_out_int_hex(c, {expr_code})")
                    arg_idx += 1
                elif spec == "o" and arg_idx < len(args):
                    w = self._expr_width(args[arg_idx])
                    expr_code = self._emit_expr(args[arg_idx], w)
                    if width:
                        lines.append(f"{pad}_out_int_oct_w(c, {expr_code}, {width}, {1 if zero_pad else 0})")
                    else:
                        lines.append(f"{pad}_out_int_oct(c, {expr_code})")
                    arg_idx += 1
                elif spec == "b" and arg_idx < len(args):
                    w = self._expr_width(args[arg_idx])
                    expr_code = self._emit_expr(args[arg_idx], w)
                    lines.append(f"{pad}_out_int_bin(c, {expr_code}, {w})")
                    arg_idx += 1
                elif spec == "s" and arg_idx < len(args):
                    lines.extend(self._emit_string_output(args[arg_idx], indent))
                    arg_idx += 1
                elif spec == "t":
                    # %t ΓÇö simulation time
                    lines.append(f"{pad}_out_int_dec(c, c.sim_time)")
                else:
                    # Unknown spec ΓÇö output the '%' and the spec char
                    lines.append(f"{pad}_out_char(c, 37)  # '%'")
                    lines.append(f"{pad}_out_char(c, {ord(fmt[i - 1])})")
            else:
                lines.append(f"{pad}_out_char(c, {ord(ch)})  # '{ch}'")
                i += 1

        return lines

    def _emit_string_output(self, arg: Expression, indent: int) -> list[str]:
        """Emit code to output an expression as a string (%s).

        Handles StringLiteral directly (char-by-char) and TernaryOp
        with StringLiteral branches via if/else.  Falls back to decimal
        integer output for other expression types.
        """
        pad = "    " * indent
        lines: list[str] = []

        if isinstance(arg, StringLiteral):
            for ch in arg.value:
                lines.append(f"{pad}_out_char(c, {ord(ch)})")
            return lines

        if isinstance(arg, TernaryOp) and (
            isinstance(arg.true_expr, StringLiteral) or isinstance(arg.false_expr, StringLiteral)
        ):
            cond_w = self._expr_width(arg.condition)
            cond_code = self._emit_expr(arg.condition, cond_w)
            lines.append(f"{pad}if {cond_code}:")
            true_lines = self._emit_string_output(arg.true_expr, indent + 1)
            lines.extend(true_lines or [f"{'    ' * (indent + 1)}pass"])
            lines.append(f"{pad}else:")
            false_lines = self._emit_string_output(arg.false_expr, indent + 1)
            lines.extend(false_lines or [f"{'    ' * (indent + 1)}pass"])
            return lines

        # Fallback: emit as decimal integer
        w = self._expr_width(arg)
        expr_code = self._emit_expr(arg, w)
        lines.append(f"{pad}_out_int_dec(c, {expr_code})")
        return lines

    # ΓöÇΓöÇ Sensitivity collection from statements ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    def _collect_stmt_signals(self, stmt: Statement, sigs: set[int]) -> None:  # noqa: PLR0911
        """Collect all signal IDs read in a statement's expressions."""
        if stmt is None:
            return

        stype = type(stmt)

        if stype is BlockingAssign or stype is NonblockingAssign:
            self._collect_lhs_read_signals(stmt.lhs, sigs)
            self._walk_signals(stmt.rhs, sigs)
            return

        if stype is IfStatement:
            self._walk_signals(stmt.condition, sigs)
            self._collect_stmt_signals(stmt.then_body, sigs)
            self._collect_stmt_signals(stmt.else_body, sigs)
            return

        if stype is CaseStatement:
            self._walk_signals(stmt.expression, sigs)
            for item in stmt.items:
                for val in item.values:
                    self._walk_signals(val, sigs)
                self._collect_stmt_signals(item.body, sigs)
            return

        if stype is SeqBlock:
            for s in stmt.statements:
                self._collect_stmt_signals(s, sigs)
            return

        if stype is ForLoop:
            self._collect_stmt_signals(stmt.init, sigs)
            self._walk_signals(stmt.condition, sigs)
            self._collect_stmt_signals(stmt.update, sigs)
            self._collect_stmt_signals(stmt.body, sigs)
            return

        if stype is SystemTaskCall:
            for arg in stmt.arguments:
                self._walk_signals(arg, sigs)
            return

    def _collect_lhs_read_signals(self, lhs: Expression, sigs: set[int]) -> None:
        """Collect signal IDs read while evaluating an LHS selector expression."""
        etype = type(lhs)

        if etype is Identifier:
            name = lhs.name
            if lhs.hierarchy:
                name = ".".join(lhs.hierarchy) + "." + name
            self._collect_struct_storage_index_sensitivity(name, sigs)
            return

        if etype is BitSelect:
            self._collect_lhs_read_signals(lhs.target, sigs)
            self._walk_signals(lhs.index, sigs)
            return

        if etype is RangeSelect:
            self._collect_lhs_read_signals(lhs.target, sigs)
            self._walk_signals(lhs.msb, sigs)
            self._walk_signals(lhs.lsb, sigs)
            return

        if etype is PartSelect:
            self._collect_lhs_read_signals(lhs.target, sigs)
            self._walk_signals(lhs.base, sigs)
            self._walk_signals(lhs.width, sigs)
            return

        if etype is Concatenation:
            for part in lhs.parts:
                self._collect_lhs_read_signals(part, sigs)

    def _collect_stmt_writes_targets(self, stmt: Statement, sigs: set[int]) -> None:  # noqa: PLR0911
        """Remove written LHS targets from inferred combinational sensitivity."""
        if stmt is None:
            return

        stype = type(stmt)

        if stype is BlockingAssign or stype is NonblockingAssign:
            self._remove_lhs_targets(stmt.lhs, sigs)
            return

        if stype is IfStatement:
            self._collect_stmt_writes_targets(stmt.then_body, sigs)
            self._collect_stmt_writes_targets(stmt.else_body, sigs)
            return

        if stype is CaseStatement:
            for item in stmt.items:
                self._collect_stmt_writes_targets(item.body, sigs)
            return

        if stype is SeqBlock:
            for s in stmt.statements:
                self._collect_stmt_writes_targets(s, sigs)
            return

        if stype is ForLoop:
            self._collect_stmt_writes_targets(stmt.init, sigs)
            self._collect_stmt_writes_targets(stmt.update, sigs)
            self._collect_stmt_writes_targets(stmt.body, sigs)
            return

        if stype is WhileLoop or stype is ForeverLoop or stype is RepeatLoop:
            self._collect_stmt_writes_targets(stmt.body, sigs)
            return

        if stype is DelayControl or stype is EventControl or stype is WaitStatement:
            self._collect_stmt_writes_targets(stmt.body, sigs)
            return

    def _remove_lhs_targets(self, lhs: Expression, sigs: set[int]) -> None:
        """Remove base LHS targets from an inferred sensitivity set."""
        etype = type(lhs)
        if etype is Identifier:
            name = lhs.name
            if lhs.hierarchy:
                name = ".".join(lhs.hierarchy) + "." + name
            sid = self._signal_map.get(name)
            if sid is not None:
                sigs.discard(sid)
            struct_info = self._resolve_struct_storage_access(name)
            if struct_info is not None:
                if struct_info[0] == "signal":
                    sigs.discard(struct_info[1])
                else:
                    sigs.discard(self._mem_marker_sigs[struct_info[1]])
            return
        if etype is BitSelect or etype is RangeSelect or etype is PartSelect:
            self._remove_lhs_targets(lhs.target, sigs)
            return
        if etype is Concatenation:
            for part in lhs.parts:
                self._remove_lhs_targets(part, sigs)
