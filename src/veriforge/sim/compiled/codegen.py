"""AST .pyx code generation for the compiled Cython engine.

Walks the model AST and produces a complete .pyx source string containing
design-specific process functions, delta loop, and the CompiledSim class.

Phase 1: continuous assigns only.
Phase 2: always @(posedge/negedge) and always @(*) blocks,
         if/else, case, blocking/non-blocking assigns, NBA semantics.
Phase 3: LHS complexity (bit-select, range-select, concatenation) and
         memory arrays.
"""

from __future__ import annotations

import os
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
from veriforge.model.statements import (
    BlockingAssign,
    CaseStatement,
    DelayControl,
    EventControl,
    ForLoop,
    ForeverLoop,
    IfStatement,
    NonblockingAssign,
    ParBlock,
    RepeatLoop,
    SensitivityEdge,
    SeqBlock,
    SystemTaskCall,
    TaskEnable,
    WaitStatement,
    WhileLoop,
)
from veriforge.sim.compiled._codegen_utils import (
    _WORD_BITS,
    _PROCESS_LOOP_LIMIT,
    _I32_MAX,
    _I32_MIN,
    _safe_const_name,
    _safe_ident,
    _cy_lit,
    _cy_hex,
    _cy_u64_hex,
    _const_int,
)
from veriforge.sim.compiled._expr_emitter import _ExprEmitterMixin
from veriforge.sim.compiled._gen_sections import _GenSectionsMixin
from veriforge.sim.compiled._process_compiler import _ProcessCompilerMixin
from veriforge.sim.compiled._stmt_emitters import _StmtEmittersMixin
from veriforge.sim.compiled._wide_emitter import _WideEmitterMixin
from veriforge.sim.value import Value

if TYPE_CHECKING:
    from veriforge.model.design import Module
    from veriforge.model.expressions import Range
    from veriforge.model.statements import Statement
    from veriforge.model.variables import Variable


def _range_width(r: Range | None, param_env: dict[str, int] | None = None) -> int:
    """Compute the bit-width from a Range (msb:lsb), defaulting to 1."""
    if r is None:
        return 1
    if isinstance(r.msb, Literal) and isinstance(r.lsb, Literal):
        return abs(int(r.msb.value) - int(r.lsb.value)) + 1
    try:
        from ..elaborate import _eval_const_expr

        env = param_env if param_env is not None else {}
        msb = _eval_const_expr(r.msb, env)
        lsb = _eval_const_expr(r.lsb, env)
        return abs(msb - lsb) + 1
    except (ValueError, TypeError):
        pass
    return 1


def _scoped_env(signal_name: str, param_env: dict[str, int]) -> dict[str, int]:
    """Build a param env with unprefixed aliases for a hierarchically-prefixed signal."""
    dot = signal_name.rfind(".")
    if dot < 0:
        return param_env
    prefix = signal_name[: dot + 1]
    local = dict(param_env)
    for key, value in param_env.items():
        if key.startswith(prefix):
            unprefixed = key[len(prefix) :]
            if unprefixed not in local:
                local[unprefixed] = value
    return local


def _var_width(var: Variable, param_env: dict[str, int] | None = None) -> int:
    """Width for a variable, respecting special types."""
    kind_name = var.kind.name if hasattr(var.kind, "name") else str(var.kind)
    if kind_name == "INTEGER":
        return 32
    if kind_name in ("REAL", "TIME", "REALTIME"):
        return 64
    if kind_name == "BYTE":
        return 8
    if kind_name == "SHORTINT":
        return 16
    if kind_name == "INT":
        return 32
    if kind_name == "LONGINT":
        return 64
    return _range_width(var.width, param_env)


def _dim_depth(dim: Range, param_env=None) -> int:
    """Depth of one memory dimension."""
    if isinstance(dim.msb, Literal) and isinstance(dim.lsb, Literal):
        return abs(int(dim.msb.value) - int(dim.lsb.value)) + 1
    try:
        from ..elaborate import _eval_const_expr  # noqa: PLC0415

        env = param_env if param_env is not None else {}
        lo = _eval_const_expr(dim.lsb, env)
        hi = _eval_const_expr(dim.msb, env)
        return abs(hi - lo) + 1
    except (ValueError, TypeError):
        pass
    return 1


def _dim_layout(dim: Range, param_env=None) -> tuple[int, int, int]:
    """Return (start, step, depth) for one unpacked memory dimension."""
    start = _const_int(dim.msb, param_env)
    end = _const_int(dim.lsb, param_env)
    depth = _dim_depth(dim, param_env)
    if start is None and end is None:
        start = 0
        step = 1
    elif start is None:
        start = end - (depth - 1) if end is not None else 0
        step = 1
    elif end is None:
        step = 1
    else:
        step = -1 if start > end else 1
    return start, step, depth


def _dim_strides(dimensions: list[Range], param_env=None) -> list[tuple[int, int, int, int]]:
    """Return (start, step, depth, stride) for each unpacked memory dimension."""
    layout = [_dim_layout(dim, param_env) for dim in dimensions]
    strides: list[tuple[int, int, int, int]] = []
    stride = 1
    for start, step, depth in reversed(layout):
        strides.append((start, step, depth, stride))
        stride *= depth
    strides.reverse()
    return strides


class CythonCodegen(
    _GenSectionsMixin, _StmtEmittersMixin, _WideEmitterMixin, _ExprEmitterMixin, _ProcessCompilerMixin
):  # cm:b1c5e8
    """Generate a complete .pyx source from a module AST."""

    __slots__ = (
        "_combo_processes",
        "_delta_limit",
        "_function_map",
        "_initial_lines",
        "_local_vars",
        "_mem_info",
        "_mem_layouts",
        "_mem_map",
        "_mem_marker_sigs",
        "_memory_bases",
        "_module",
        "_n_mems",
        "_n_sigs",
        "_param_env",
        "_param_init",
        "_processes",
        "_scratch_peak",
        "_scratch_slot_count",
        "_seq_processes",
        "_signal_bases",
        "_signal_map",
        "_signal_names",
        "_signal_signed",
        "_signal_widths",
        "_struct_signal_types",
        "_struct_type_map",
        "_task_map",
        "_temp_var_counter",
        "_timing_diagnostics",
        "_unmasked_signal_ids",
        "_var_init",
    )

    def __init__(self) -> None:
        self._signal_map: dict[str, int] = {}
        self._signal_names: list[str] = []
        self._signal_widths: list[int] = []
        self._signal_signed: list[bool] = []
        self._n_sigs: int = 0
        self._unmasked_signal_ids: set[int] = set()
        self._processes: list[tuple[set[int], list[str]]] = []
        self._combo_processes: list[tuple[set[int], list[str]]] = []
        self._seq_processes: list[tuple[dict[int, str], set[int], list[str]]] = []
        self._module: Module | None = None
        self._initial_lines: list[str] = []
        self._mem_map: dict[str, int] = {}
        self._mem_info: list[tuple[int, int]] = []
        self._mem_layouts: list[list[tuple[int, int, int, int]]] = []
        self._mem_marker_sigs: list[int] = []
        self._n_mems: int = 0
        self._function_map: dict[str, object] = {}
        self._task_map: dict[str, object] = {}
        self._param_init: dict[int, int] = {}
        self._param_env: dict[str, int] = {}
        self._var_init: dict[int, tuple[int, int]] = {}
        self._struct_signal_types: dict[str, object] = {}
        self._struct_type_map: dict[str, object] = {}
        self._memory_bases: dict[str, int] = {}
        self._signal_bases: dict[str, int] = {}
        self._local_vars: dict[str, str] = {}
        self._temp_var_counter: int = 0
        self._scratch_slot_count: int = 0
        self._scratch_peak: int = 0
        self._timing_diagnostics: list[str] = []
        self._delta_limit: int = 10_000

    # ── Wide scratch allocator ────────────────────────────────────────────
    # Used by _emit_wide_expr_to_scratch (Phase 1) to manage temporary
    # wide-value slots within a single process function.  Each slot is a pair
    # of stack arrays (_sc{n}_v[N_WIDE_WORDS] and _sc{n}_m[N_WIDE_WORDS]).
    # The allocator is reset at the start of each top-level statement emit.

    def _alloc_scratch(self) -> int:
        """Claim the next scratch slot and return its index."""
        slot = self._scratch_slot_count
        self._scratch_slot_count += 1
        if self._scratch_slot_count > self._scratch_peak:
            self._scratch_peak = self._scratch_slot_count
        return slot

    def _free_scratch(self, *slots: int) -> None:
        """Release scratch slots (LIFO — caller must release in reverse order)."""
        self._scratch_slot_count -= len(slots)
        if self._scratch_slot_count < 0:
            self._scratch_slot_count = 0

    def _reset_scratch(self) -> None:
        """Reset allocator between top-level statements."""
        self._scratch_slot_count = 0

    # ── Struct resolution ─────────────────────────────────────────────────

    def _resolve_struct_access(self, name: str) -> tuple[int, int, int] | None:
        """Resolve nested struct access to (base_sid, offset, width)."""
        from ..elaborate import resolve_struct_access  # noqa: PLC0415

        info = resolve_struct_access(name, self._struct_signal_types, self._signal_map)
        if info is None:
            return None
        base_name, offset, width = info
        base_sid = self._signal_map.get(base_name)
        if base_sid is None:
            return None
        return base_sid, offset, width

    def _resolve_struct_storage_access(self, name: str) -> tuple[str, int, int | str | None, int, int] | None:
        """Resolve nested struct access to either a signal base or a memory element."""
        from ..elaborate import resolve_struct_storage_access  # noqa: PLC0415

        info = resolve_struct_storage_access(name, self._struct_signal_types, self._signal_map, set(self._mem_map))
        if info is None:
            return None
        storage_name, storage_index, offset, width = info
        if storage_index is None:
            sid = self._signal_map.get(storage_name)
            if sid is None:
                return None
            return "signal", sid, None, offset, width
        mid = self._mem_map.get(storage_name)
        if mid is None:
            return None
        return "memory", mid, storage_index, offset, width

    def _resolve_struct_storage_mem_range(self, name: str) -> tuple[BitSelect, Literal, Literal] | None:
        """Resolve a struct field stored inside a memory element to a memory range-select target."""
        struct_storage_info = self._resolve_struct_storage_access(name)
        if struct_storage_info is None or struct_storage_info[0] != "memory":
            return None
        index_spec = struct_storage_info[2]
        if isinstance(index_spec, int):
            index_expr = Literal(index_spec)
        elif isinstance(index_spec, str):
            index_expr = Identifier(index_spec)
        else:
            return None
        mem_name = next(
            mem_signal_name for mem_signal_name, mem_id in self._mem_map.items() if mem_id == struct_storage_info[1]
        )
        mem_lhs = BitSelect(Identifier(mem_name), index_expr)
        msb = Literal(struct_storage_info[3] + struct_storage_info[4] - 1)
        lsb = Literal(struct_storage_info[3])
        return mem_lhs, msb, lsb

    def _emit_struct_storage_index_expr(self, index_spec: int | str | None) -> str | None:
        """Emit a Cython expression for a struct-storage memory index."""
        if index_spec is None:
            return None
        if isinstance(index_spec, int):
            return str(index_spec)
        sid = self._signal_map.get(index_spec)
        if sid is not None:
            return f"(c.val[{sid}] & wmask({self._signal_widths[sid]}))"
        local_var = self._local_vars.get(index_spec)
        if local_var is not None:
            return local_var
        return None

    def _collect_struct_storage_index_sensitivity(self, name: str, sigs: set[int]) -> None:
        """Add dynamic memory-index dependencies for flattened struct-storage accesses."""
        struct_info = self._resolve_struct_storage_access(name)
        if struct_info is None or struct_info[0] != "memory":
            return
        index_spec = struct_info[2]
        if isinstance(index_spec, str):
            sid = self._signal_map.get(index_spec)
            if sid is not None:
                sigs.add(sid)

    @staticmethod
    def _offset_expr(expr: Expression, offset: int) -> Expression:
        """Return expr + offset, folding literal offsets when possible."""
        if offset == 0:
            return expr
        if isinstance(expr, Literal):
            width = expr.width if expr.width else 32
            return Literal(int(expr.value) + offset, width=width)
        return BinaryOp("+", Literal(offset, width=32), expr)

    def _resolve_struct_storage_mem_select_range(
        self, target: Identifier, msb_expr: Expression, lsb_expr: Expression
    ) -> tuple[BitSelect, Expression, Expression] | None:
        """Resolve a struct-backed memory field select into an absolute memory element range."""
        struct_storage_range = self._resolve_struct_storage_mem_range(self._identifier_name(target))
        if struct_storage_range is None:
            return None
        mem_lhs, _field_msb, field_lsb = struct_storage_range
        if not isinstance(field_lsb, Literal):
            return None
        offset = int(field_lsb.value)
        return mem_lhs, self._offset_expr(msb_expr, offset), self._offset_expr(lsb_expr, offset)

    def _resolve_struct_signal_select_range(
        self, target: Identifier, msb_expr: Expression, lsb_expr: Expression
    ) -> tuple[int, Expression, Expression] | None:
        """Resolve a packed-struct signal field select into parent-signal bit bounds."""
        struct_info = self._resolve_struct_access(self._identifier_name(target))
        if struct_info is None:
            return None
        base_sid, offset, _field_width = struct_info
        return base_sid, self._offset_expr(msb_expr, offset), self._offset_expr(lsb_expr, offset)

    def _append_struct_signal_concat_part_op(
        self,
        part_ops: list[tuple[str, str, int, str | None, str | None]],
        target: Identifier,
        msb_expr: Expression,
        lsb_expr: Expression,
        extract: str,
        mask_extract: str,
    ) -> bool:
        """Append a concat-part write that targets a packed-struct signal field."""
        struct_signal_range = self._resolve_struct_signal_select_range(target, msb_expr, lsb_expr)
        if struct_signal_range is None:
            return False
        sid, abs_msb_expr, abs_lsb_expr = struct_signal_range
        sig_base = self._signal_bases.get(self._signal_names[sid], 0)
        if isinstance(abs_msb_expr, Literal):
            msb_str = str(int(abs_msb_expr.value) - sig_base)
        else:
            msb_str = self._emit_expr(abs_msb_expr, 32)
            if sig_base != 0:
                msb_str = f"(({msb_str}) - {sig_base})"
        if isinstance(abs_lsb_expr, Literal):
            lsb_str = str(int(abs_lsb_expr.value) - sig_base)
        else:
            lsb_str = self._emit_expr(abs_lsb_expr, 32)
            if sig_base != 0:
                lsb_str = f"(({lsb_str}) - {sig_base})"
        part_ops.append((extract, mask_extract, sid, msb_str, lsb_str))
        return True

    @staticmethod
    def _emit_concat_signal_rhs_source_lines(
        sid: int, lsb_expr: str, rhs_source: tuple[int, str], width_expr: str, indent: int, *, is_nba: bool
    ) -> list[str]:
        """Emit concat-part lines that copy a wide RHS signal slice into a signal slice."""
        pad = "    " * indent
        rhs_sid, rhs_lsb = rhs_source
        helper = "_whole_stage_insert_signal_slice" if is_nba else "_whole_assign_insert_signal_slice"
        return [f"{pad}{helper}(c, {sid}, <int>({lsb_expr}), {rhs_sid}, <int>({rhs_lsb}), {width_expr})"]

    def _emit_signal_mem_rhs_source_lines(
        self, sid: int, lsb_expr: str, rhs_source: tuple[int, str, str], width_expr: str, indent: int, *, is_nba: bool
    ) -> list[str]:
        """Emit lines that copy a wide RHS memory slice into a signal slice."""
        rhs_mid, rhs_idx, rhs_lsb = rhs_source
        temp_index = self._next_temp_index()
        idx_var = f"_memsrc_idx_{temp_index}"
        dst_lsb_var = f"_memdst_lsb_{temp_index}"
        src_lsb_var = f"_memsrc_lsb_{temp_index}"
        width_var = f"_memsrc_width_{temp_index}"
        bit_var = f"_memsrc_bit_{temp_index}"
        chunk_var = f"_memsrc_chunk_{temp_index}"
        word_val_var = f"_memsrc_word_v_{temp_index}"
        word_mask_var = f"_memsrc_word_m_{temp_index}"
        helper = "_whole_stage_insert_word" if is_nba else "_whole_assign_insert_word"
        pad = "    " * indent
        return [
            f"{pad}cdef int {idx_var} = ({rhs_idx})",
            f"{pad}cdef int {dst_lsb_var} = <int>({lsb_expr})",
            f"{pad}cdef int {src_lsb_var} = <int>({rhs_lsb})",
            f"{pad}cdef int {width_var} = <int>({width_expr})",
            f"{pad}cdef int {bit_var} = 0",
            f"{pad}cdef int {chunk_var}",
            f"{pad}cdef unsigned long long {word_val_var}",
            f"{pad}cdef unsigned long long {word_mask_var}",
            f"{pad}while {bit_var} < {width_var}:",
            f"{pad}    {chunk_var} = {width_var} - {bit_var}",
            f"{pad}    if {chunk_var} > 64:",
            f"{pad}        {chunk_var} = 64",
            f"{pad}    {word_val_var} = _wmem{rhs_mid}_extract_val(c, {idx_var}, {src_lsb_var} + {bit_var}) & _word_mask64({chunk_var})",
            f"{pad}    {word_mask_var} = _wmem{rhs_mid}_extract_mask(c, {idx_var}, {src_lsb_var} + {bit_var}) & _word_mask64({chunk_var})",
            f"{pad}    {helper}(c, {sid}, {dst_lsb_var} + {bit_var}, {chunk_var}, {word_val_var}, {word_mask_var})",
            f"{pad}    {bit_var} += {chunk_var}",
        ]

    def _emit_concat_signal_part_lines(
        self,
        sid: int,
        lsb_expr: str | None,
        width: int | None,
        width_expr: str,
        extract: str,
        mask_extract: str,
        rhs_source: tuple[int, str] | None,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str] | None:
        """Emit wide-signal concat writes without routing through one-word accumulators."""
        if self._signal_widths[sid] <= _WORD_BITS:
            return None
        if lsb_expr is None:
            if rhs_source is None:
                return None
            return self._emit_concat_signal_rhs_source_lines(sid, "0", rhs_source, width_expr, indent, is_nba=is_nba)

        if rhs_source is not None:
            return self._emit_concat_signal_rhs_source_lines(
                sid, lsb_expr, rhs_source, width_expr, indent, is_nba=is_nba
            )

        if width is not None and width > _WORD_BITS:
            return None

        pad = "    " * indent
        helper = "_whole_stage_insert_word" if is_nba else "_whole_assign_insert_word"
        slice_mask = self._concat_slice_mask_expr(width, width_expr)
        return [
            f"{pad}{helper}(c, {sid}, <int>({lsb_expr}), <int>({width_expr}), "
            f"<unsigned long long>(({extract}) & {slice_mask}), <unsigned long long>(({mask_extract}) & {slice_mask}))"
        ]

    def _select_base(self, target: Expression) -> int:
        """Return the packed-range LSB base for scalar or memory-element selects."""
        if isinstance(target, Identifier):
            return self._signal_bases.get(self._identifier_name(target), 0)
        mem_access = self._resolve_memory_element_expr(target)
        if mem_access is not None:
            _mid, tname, _indices = mem_access
            return self._memory_bases.get(tname, 0)
        return 0

    def _resolve_memory_element_expr(self, expr: Expression) -> tuple[int, str, list[Expression]] | None:
        """Return metadata for a full unpacked-memory element access."""
        indices: list[Expression] = []
        target = expr
        while isinstance(target, BitSelect):
            indices.append(target.index)
            target = target.target
        if not isinstance(target, Identifier):
            return None
        name = self._identifier_name(target)
        mid = self._mem_map.get(name)
        if mid is None:
            return None
        ordered_indices = list(reversed(indices))
        if len(ordered_indices) != len(self._mem_layouts[mid]):
            return None
        return mid, name, ordered_indices

    def _emit_memory_flat_index(self, mid: int, indices: list[Expression]) -> str:
        """Emit the flattened storage index for a full unpacked-memory element access."""
        terms: list[str] = []
        for index_expr, (start, step, depth, stride) in zip(indices, self._mem_layouts[mid], strict=True):
            raw_index = self._emit_index_expr(index_expr)
            # Normalize Verilog index → zero-based C-array position.
            # For descending [HIGH:LOW] the minimum valid index is LOW = start - (depth-1),
            # so the C position is (i - LOW).  Using (start - i) instead would reverse
            # the mapping (MEM[0] ↔ MEM[depth-1]), which is wrong for Verilog semantics.
            low = start - (depth - 1) if step < 0 else start
            pos = f"(({raw_index}) - {low})" if low != 0 else f"({raw_index})"
            if stride != 1:
                pos = f"(({pos}) * {stride})"
            terms.append(pos)
        flat_index = " + ".join(terms) if terms else "0"
        return f"({flat_index})"

    def _resolve_memory_element_access(self, expr: Expression) -> tuple[int, str, str, list[Expression]] | None:
        """Return (mid, flat_index_expr, name, unpacked_indices) for a full memory element access."""
        access = self._resolve_memory_element_expr(expr)
        if access is None:
            return None
        mid, name, indices = access
        return mid, self._emit_memory_flat_index(mid, indices), name, indices

    def _memory_layout_matches(self, lhs_mid: int, rhs_mid: int) -> bool:
        """Return whether two memories have the same element layout."""
        return (
            self._mem_info[lhs_mid] == self._mem_info[rhs_mid]
            and self._mem_layouts[lhs_mid] == self._mem_layouts[rhs_mid]
        )

    @staticmethod
    def _identifier_name(expr: Identifier) -> str:
        """Return a fully qualified identifier name."""
        if expr.hierarchy:
            return ".".join(expr.hierarchy) + "." + expr.name
        return expr.name

    @staticmethod
    def _emit_signal_slice_expr(sid: int, lsb_expr: str, width_expr: str | int, *, mask: bool = False) -> str:
        helper = "_sig_extract_word_mask" if mask else "_sig_extract_word_val"
        width_str = str(width_expr)
        return f"<long long>({helper}(c, {sid}, <int>({lsb_expr})) & _word_mask64(<int>({width_str})))"

    def _concat_part_width_info(self, part: Expression) -> tuple[int | None, str]:
        """Return (static_width, width_expr) for a concat LHS part."""
        if isinstance(part, RangeSelect):
            if isinstance(part.msb, Literal) and isinstance(part.lsb, Literal):
                width = int(part.msb.value) - int(part.lsb.value) + 1
                return width, str(width)
            msb_val = _const_int(part.msb, self._param_env)
            lsb_val = _const_int(part.lsb, self._param_env)
            if msb_val is not None and lsb_val is not None:
                width = msb_val - lsb_val + 1
                return width, str(width)
            msb_expr = self._emit_expr(part.msb, 32)
            lsb_expr = self._emit_expr(part.lsb, 32)
            return None, f"(({msb_expr}) - ({lsb_expr}) + 1)"
        width = self._expr_width(part)
        return width, str(width)

    def _concat_part_max_width(self, part: Expression) -> int:
        """Return the maximum compile-time width a concat part can occupy."""
        static_width, _width_expr = self._concat_part_width_info(part)
        if static_width is not None:
            return static_width
        if isinstance(part, RangeSelect):
            return self._expr_width(part.target)
        return self._expr_width(part)

    @staticmethod
    def _concat_width_sum(width_infos: list[tuple[int | None, str]]) -> tuple[int | None, str]:
        if all(width is not None for width, _expr in width_infos):
            total = sum(width for width, _expr in width_infos if width is not None)
            return total, str(total)
        terms: list[str] = []
        const_total = 0
        for width, expr in width_infos:
            if width is not None:
                const_total += width
            else:
                terms.append(f"({expr})")
        if const_total:
            terms.append(str(const_total))
        return None, " + ".join(terms) if terms else "0"

    def _concat_part_wide_rhs_source(
        self, part: Expression, offset_expr: str, wide_signal_rhs_source: tuple[int, str] | None
    ) -> tuple[int, str] | None:
        if wide_signal_rhs_source is None or self._concat_part_max_width(part) <= _WORD_BITS:
            return None

        if self._resolve_memory_element_access(part) is not None:
            rhs_sid, rhs_base_lsb = wide_signal_rhs_source
            return rhs_sid, offset_expr if rhs_base_lsb == "0" else f"({rhs_base_lsb}) + ({offset_expr})"

        target: Expression | None = None
        if isinstance(part, (RangeSelect, PartSelect, BitSelect)):
            target = part.target
        elif isinstance(part, Identifier):
            target = part

        if target is None:
            return None
        if self._resolve_memory_element_access(target) is None:
            if not isinstance(target, Identifier):
                return None
            target_name = self._identifier_name(target)
            if (
                self._signal_map.get(target_name) is None
                and self._resolve_struct_access(target_name) is None
                and self._resolve_struct_storage_mem_range(target_name) is None
            ):
                return None

        rhs_sid, rhs_base_lsb = wide_signal_rhs_source
        return rhs_sid, offset_expr if rhs_base_lsb == "0" else f"({rhs_base_lsb}) + ({offset_expr})"

    def _concat_eval_widths(self, parts: list[Expression], total_width: int | None = None) -> list[int]:
        width_infos = [self._concat_part_width_info(part) for part in parts]
        widths = [width for width, _width_expr in width_infos]
        unknown_indices = [index for index, width in enumerate(widths) if width is None]
        if total_width is not None and len(unknown_indices) == 1:
            known_total = sum(width for width in widths if width is not None)
            inferred_width = total_width - known_total
            if inferred_width >= 0:
                widths[unknown_indices[0]] = inferred_width
        return [width if width is not None else self._expr_width(parts[index]) for index, width in enumerate(widths)]

    def _concat_parts_match_width(self, lhs_part: Expression, rhs_part: Expression) -> bool:
        lhs_width, _lhs_width_expr = self._concat_part_width_info(lhs_part)
        rhs_width, _rhs_width_expr = self._concat_part_width_info(rhs_part)
        if lhs_width is not None and rhs_width is not None:
            return lhs_width == rhs_width
        if isinstance(lhs_part, BitSelect) and isinstance(rhs_part, BitSelect):
            return True
        if isinstance(lhs_part, RangeSelect) and isinstance(rhs_part, RangeSelect):
            return repr(lhs_part.msb) == repr(rhs_part.msb) and repr(lhs_part.lsb) == repr(rhs_part.lsb)
        if isinstance(lhs_part, PartSelect) and isinstance(rhs_part, PartSelect):
            return repr(lhs_part.width) == repr(rhs_part.width)
        return False

    def _lhs_storage_signals(self, lhs: Expression) -> set[int]:  # noqa: PLR0911, PLR0912
        if isinstance(lhs, Concatenation):
            sigs: set[int] = set()
            for part in lhs.parts:
                sigs.update(self._lhs_storage_signals(part))
            return sigs

        if isinstance(lhs, Identifier):
            name = self._identifier_name(lhs)
            sid = self._signal_map.get(name)
            if sid is not None:
                return {sid}
            mid = self._mem_map.get(name)
            if mid is not None:
                return {self._mem_marker_sigs[mid]}
            struct_info = self._resolve_struct_access(name)
            if struct_info is not None:
                return {struct_info[0]}
            struct_storage_range = self._resolve_struct_storage_mem_range(name)
            if struct_storage_range is not None:
                access = self._resolve_memory_element_access(struct_storage_range[0])
                if access is not None:
                    return {self._mem_marker_sigs[access[0]]}
            return set()

        target: Expression | None = None
        if isinstance(lhs, BitSelect):
            mem_access = self._resolve_memory_element_access(lhs)
            if mem_access is not None:
                return {self._mem_marker_sigs[mem_access[0]]}
            target = lhs.target
        elif isinstance(lhs, (RangeSelect, PartSelect)):
            mem_access = self._resolve_memory_element_access(lhs.target)
            if mem_access is not None:
                return {self._mem_marker_sigs[mem_access[0]]}
            target = lhs.target

        if isinstance(target, Identifier):
            name = self._identifier_name(target)
            sid = self._signal_map.get(name)
            if sid is not None:
                return {sid}
            struct_info = self._resolve_struct_access(name)
            if struct_info is not None:
                return {struct_info[0]}
            struct_storage_range = self._resolve_struct_storage_mem_range(name)
            if struct_storage_range is not None:
                access = self._resolve_memory_element_access(struct_storage_range[0])
                if access is not None:
                    return {self._mem_marker_sigs[access[0]]}
        return set()

    def _emit_matching_concat_copy(
        self,
        lhs: Concatenation,
        rhs: Expression,
        indent: int,
        *,
        is_nba: bool,
    ) -> list[str] | None:
        if not isinstance(rhs, Concatenation) or len(lhs.parts) != len(rhs.parts):
            return None
        if not all(
            self._concat_parts_match_width(lhs_part, rhs_part)
            for lhs_part, rhs_part in zip(lhs.parts, rhs.parts, strict=True)
        ):
            return None

        rhs_signals: set[int] = set()
        self._walk_signals(rhs, rhs_signals)
        lhs_signals = self._lhs_storage_signals(lhs)
        if not lhs_signals or lhs_signals & rhs_signals:
            return None

        lines: list[str] = []
        for lhs_part, rhs_part in zip(lhs.parts, rhs.parts, strict=True):
            lines.extend(self._emit_lhs_write(lhs_part, rhs_part, indent, is_nba=is_nba))
        return lines

    def _build_whole_mem_concat_lhs(self, lhs: BitSelect, rhs: Concatenation, total_width: int) -> Concatenation | None:
        """Build a concat LHS that partitions a whole wide memory element to match an RHS concat."""
        widths = self._concat_eval_widths(rhs.parts, total_width=total_width)
        if any(width <= 0 for width in widths):
            return None
        if sum(widths) != total_width:
            return None

        parts: list[Expression] = []
        msb = total_width - 1
        for width in widths:
            lsb = msb - width + 1
            if width == 1:
                parts.append(BitSelect(lhs, Literal(lsb, width=32)))
            else:
                parts.append(RangeSelect(lhs, Literal(msb, width=32), Literal(lsb, width=32)))
            msb = lsb - 1
        return Concatenation(parts)

    @staticmethod
    def _concat_slice_mask_expr(width: int | None, width_expr: str) -> str:
        if width is not None:
            return _cy_hex((1 << width) - 1)
        return f"wmask({width_expr})"

    def _emit_concat_rhs_extract(
        self,
        rhs_expr: str,
        offset_expr: str,
        width: int | None,
        width_expr: str,
    ) -> str:
        mask_expr = self._concat_slice_mask_expr(width, width_expr)
        if offset_expr == "0":
            return f"({rhs_expr}) & {mask_expr}"
        return f"(({rhs_expr}) >> ({offset_expr})) & {mask_expr}"

    @staticmethod
    def _emit_mem_slice_expr(
        mid: int, idx_expr: str, offset: int, width: int, *, mask: bool = False, elem_width: int = 64
    ) -> str:
        array_name = "mask" if mask else "val"
        if elem_width > _WORD_BITS:
            helper = f"_wmem{mid}_extract_mask" if mask else f"_wmem{mid}_extract_val"
            wmask = _cy_lit((1 << width) - 1)
            return f"({helper}(c, ({idx_expr}), {offset}) & {wmask})"
        wmask = _cy_lit((1 << width) - 1)
        return f"((c.mem_{mid}_{array_name}[({idx_expr})] >> {offset}) & {wmask})"

    @staticmethod
    def _emit_wide_mem_dynamic_slice_expr(
        mid: int, idx_expr: str, lsb_expr: str, width_expr: int | str, *, mask: bool = False
    ) -> str:
        if isinstance(width_expr, int) and width_expr <= _WORD_BITS:
            helper = f"_wmem{mid}_extract_mask" if mask else f"_wmem{mid}_extract_val"
            wmask = _cy_lit((1 << width_expr) - 1)
            return f"({helper}(c, ({idx_expr}), {lsb_expr}) & {wmask})"
        helper = f"_wmem{mid}_py_extract_mask" if mask else f"_wmem{mid}_py_extract_val"
        return f"{helper}(c, ({idx_expr}), {lsb_expr}, {width_expr})"

    def _resolve_signal_slice_source(self, expr: Expression) -> tuple[int, str] | None:
        if isinstance(expr, Identifier):
            name = self._identifier_name(expr)
            sid = self._signal_map.get(name)
            if sid is not None:
                return sid, "0"
            struct_info = self._resolve_struct_access(name)
            if struct_info is not None:
                base_sid, offset, field_width = struct_info
                if offset >= _WORD_BITS or offset + field_width > _WORD_BITS:
                    return base_sid, str(offset)
            return None

        if isinstance(expr, RangeSelect):
            source = self._resolve_signal_slice_source(expr.target)
            if source is None:
                return None
            sid, base_lsb = source
            lsb_expr = self._emit_expr(expr.lsb, 32)
            if base_lsb == "0":
                return sid, lsb_expr
            return sid, f"({base_lsb}) + ({lsb_expr})"

        if isinstance(expr, BitSelect):
            source = self._resolve_signal_slice_source(expr.target)
            if source is None:
                return None
            sid, base_lsb = source
            index_expr = self._emit_expr(expr.index, 32)
            if base_lsb == "0":
                return sid, index_expr
            return sid, f"({base_lsb}) + ({index_expr})"

        if isinstance(expr, PartSelect):
            source = self._resolve_signal_slice_source(expr.target)
            if source is None:
                return None
            sid, base_lsb = source
            base_expr = self._emit_expr(expr.base, 32)
            if expr.direction == "+:":
                lsb_expr = f"({base_expr})"
            else:
                if isinstance(expr.width, Literal):
                    part_width = int(expr.width.value)
                    lsb_expr = f"(({base_expr}) - {part_width - 1})"
                else:
                    width_expr = self._emit_expr(expr.width, 32)
                    lsb_expr = f"(({base_expr}) - ({width_expr}) + 1)"
            if base_lsb == "0":
                return sid, lsb_expr
            return sid, f"({base_lsb}) + ({lsb_expr})"

        if isinstance(expr, Concatenation):
            widths = self._concat_eval_widths(expr.parts)
            base_sid: int | None = None
            base_lsb: int | None = None
            for index, part in enumerate(expr.parts):
                span = self._resolve_static_signal_slice_span(part)
                if span is None:
                    return None
                sid, part_lsb, part_width = span
                if part_width != widths[index]:
                    return None
                offset = sum(widths[index + 1 :])
                part_base = part_lsb - offset
                if part_base < 0:
                    return None
                if base_sid is None:
                    base_sid = sid
                    base_lsb = part_base
                elif sid != base_sid or part_base != base_lsb:
                    return None
            if base_sid is not None and base_lsb is not None:
                return base_sid, str(base_lsb)

        return None

    def _resolve_memory_slice_source(self, expr: Expression) -> tuple[int, str, str] | None:
        access = self._resolve_memory_element_access(expr)
        if access is not None:
            mid, idx, _name, _indices = access
            return mid, idx, "0"

        if isinstance(expr, Identifier):
            name = self._identifier_name(expr)
            struct_storage_info = self._resolve_struct_storage_access(name)
            if struct_storage_info is None or struct_storage_info[0] != "memory":
                return None
            _storage_kind, mid, index_spec, offset, _field_width = struct_storage_info
            idx_expr = self._emit_struct_storage_index_expr(index_spec)
            if idx_expr is None:
                return None
            return mid, idx_expr, str(offset)

        if isinstance(expr, RangeSelect):
            mem_target = self._resolve_memory_element_access(expr.target)
            if mem_target is not None:
                mid, idx, name, _indices = mem_target
                lsb_expr = self._emit_expr(expr.lsb, 32)
                bit_base = self._memory_bases.get(name, 0)
                if bit_base != 0:
                    lsb_expr = f"(({lsb_expr}) - {bit_base})"
                return mid, idx, lsb_expr
            source = self._resolve_memory_slice_source(expr.target)
            if source is None:
                return None
            mid, idx, base_lsb = source
            lsb_expr = self._emit_expr(expr.lsb, 32)
            if base_lsb == "0":
                return mid, idx, lsb_expr
            return mid, idx, f"({base_lsb}) + ({lsb_expr})"

        if isinstance(expr, BitSelect):
            mem_target = self._resolve_memory_element_access(expr.target)
            if mem_target is not None:
                mid, idx, name, _indices = mem_target
                index_expr = self._emit_expr(expr.index, 32)
                bit_base = self._memory_bases.get(name, 0)
                if bit_base != 0:
                    index_expr = f"(({index_expr}) - {bit_base})"
                return mid, idx, index_expr
            source = self._resolve_memory_slice_source(expr.target)
            if source is None:
                return None
            mid, idx, base_lsb = source
            index_expr = self._emit_expr(expr.index, 32)
            if base_lsb == "0":
                return mid, idx, index_expr
            return mid, idx, f"({base_lsb}) + ({index_expr})"

        if isinstance(expr, PartSelect):
            mem_target = self._resolve_memory_element_access(expr.target)
            if mem_target is not None:
                mid, idx, name, _indices = mem_target
                base_expr = self._emit_expr(expr.base, 32)
                if expr.direction == "+:":
                    lsb_expr = base_expr
                else:
                    if isinstance(expr.width, Literal):
                        part_width = int(expr.width.value)
                        lsb_expr = f"(({base_expr}) - {part_width - 1})"
                    else:
                        width_expr = self._emit_expr(expr.width, 32)
                        lsb_expr = f"(({base_expr}) - ({width_expr}) + 1)"
                bit_base = self._memory_bases.get(name, 0)
                if bit_base != 0:
                    lsb_expr = f"(({lsb_expr}) - {bit_base})"
                return mid, idx, lsb_expr
            source = self._resolve_memory_slice_source(expr.target)
            if source is None:
                return None
            mid, idx, base_lsb = source
            base_expr = self._emit_expr(expr.base, 32)
            if expr.direction == "+:":
                lsb_expr = base_expr
            else:
                if isinstance(expr.width, Literal):
                    part_width = int(expr.width.value)
                    lsb_expr = f"(({base_expr}) - {part_width - 1})"
                else:
                    width_expr = self._emit_expr(expr.width, 32)
                    lsb_expr = f"(({base_expr}) - ({width_expr}) + 1)"
            if base_lsb == "0":
                return mid, idx, lsb_expr
            return mid, idx, f"({base_lsb}) + ({lsb_expr})"

        return None

    def _resolve_static_signal_slice_span(self, expr: Expression) -> tuple[int, int, int] | None:
        if isinstance(expr, Identifier):
            name = self._identifier_name(expr)
            sid = self._signal_map.get(name)
            if sid is not None:
                return sid, 0, self._signal_widths[sid]
            struct_info = self._resolve_struct_access(name)
            if struct_info is not None:
                base_sid, offset, field_width = struct_info
                return base_sid, offset, field_width
            return None

        if isinstance(expr, BitSelect):
            span = self._resolve_static_signal_slice_span(expr.target)
            if span is None:
                return None
            sid, base_lsb, _source_width = span
            index = _const_int(expr.index, self._param_env)
            if index is None:
                return None
            return sid, base_lsb + index, 1

        if isinstance(expr, RangeSelect):
            span = self._resolve_static_signal_slice_span(expr.target)
            if span is None:
                return None
            sid, base_lsb, _source_width = span
            msb = _const_int(expr.msb, self._param_env)
            lsb = _const_int(expr.lsb, self._param_env)
            if msb is None or lsb is None or msb < lsb:
                return None
            return sid, base_lsb + lsb, msb - lsb + 1

        if isinstance(expr, PartSelect):
            span = self._resolve_static_signal_slice_span(expr.target)
            if span is None:
                return None
            sid, base_lsb, _source_width = span
            base = _const_int(expr.base, self._param_env)
            width = _const_int(expr.width, self._param_env)
            if base is None or width is None or width <= 0:
                return None
            lsb = base if expr.direction == "+:" else base - width + 1
            if lsb < 0:
                return None
            return sid, base_lsb + lsb, width

        return None

    @staticmethod
    def _normalize_replication_value(expr: Expression) -> Expression:
        if isinstance(expr, Concatenation) and len(expr.parts) == 1:
            return expr.parts[0]
        return expr

    @staticmethod
    def _literal_low_word(expr: Literal) -> tuple[int, int] | None:
        if expr.original_text:
            try:
                value = Value.from_verilog(expr.original_text)
            except ValueError:
                return None
            if value.val >> _WORD_BITS or value.mask >> _WORD_BITS:
                return None
            return value.val & ((1 << _WORD_BITS) - 1), value.mask & ((1 << _WORD_BITS) - 1)
        if (hasattr(expr, "is_x") and expr.is_x) or (hasattr(expr, "is_z") and expr.is_z):
            literal_width = expr.width or 32
            if literal_width > _WORD_BITS:
                return None
            return 0, (1 << literal_width) - 1
        lit_val = 0
        if isinstance(expr.value, (int, float)):
            lit_val = int(expr.value)
        elif isinstance(expr.value, str) and expr.value.strip():
            try:
                lit_val = int(expr.value.strip(), 0)
            except (ValueError, TypeError):
                return None
        if lit_val >> _WORD_BITS:
            return None
        return lit_val & ((1 << _WORD_BITS) - 1), 0

    @property
    def signal_map(self) -> dict[str, int]:
        """Name to signal-ID mapping (available after generate())."""
        return self._signal_map

    @property
    def signal_widths(self) -> list[int]:
        """Signal widths indexed by signal ID (available after generate())."""
        return self._signal_widths

    @property
    def signal_signed(self) -> list[bool]:
        """Signal signedness indexed by signal ID (available after generate())."""
        return self._signal_signed

    @property
    def n_sigs(self) -> int:
        return self._n_sigs

    @property
    def mem_map(self) -> dict[str, int]:
        """Name ΓåÆ memory-ID mapping (available after generate())."""
        return self._mem_map

    @property
    def mem_info(self) -> list[tuple[int, int]]:
        """(elem_width, depth) per memory ID."""
        return self._mem_info

    @property
    def n_mems(self) -> int:
        return self._n_mems

    def _wide_layout(self) -> tuple[list[int], list[int], int]:
        offsets: list[int] = []
        words_per_sig: list[int] = []
        total_words = 0
        for width in self._signal_widths:
            if width > _WORD_BITS:
                words = (width + (_WORD_BITS - 1)) // _WORD_BITS
                offsets.append(total_words)
                words_per_sig.append(words)
                total_words += words
            else:
                offsets.append(0)
                words_per_sig.append(0)
        return offsets, words_per_sig, total_words

    def _mem_words(self, mid: int) -> int:
        elem_width, _depth = self._mem_info[mid]
        return (elem_width + (_WORD_BITS - 1)) // _WORD_BITS

    def _module_has_wide_state(self) -> bool:
        return any(width > _WORD_BITS for width in self._signal_widths) or any(
            elem_width > _WORD_BITS for elem_width, _depth in self._mem_info
        )

    def _module_max_wide_words(self) -> int:
        """Return the maximum number of 64-bit words needed by any wide signal or memory."""
        max_w = 0
        for width in self._signal_widths:
            if width > _WORD_BITS:
                max_w = max(max_w, (width + _WORD_BITS - 1) // _WORD_BITS)
        for elem_width, _depth in self._mem_info:
            if elem_width > _WORD_BITS:
                max_w = max(max_w, (elem_width + _WORD_BITS - 1) // _WORD_BITS)
        return max_w if max_w > 0 else 1

    def _expr_summary(self, expr: Expression) -> str:
        etype = type(expr)
        if etype is Identifier:
            return f"identifier '{self._identifier_name(expr)}'"
        if etype is Literal:
            return "literal"
        if etype is BinaryOp:
            return f"operator '{expr.op}'"
        if etype is UnaryOp:
            return f"operator '{expr.op}'"
        if etype is TernaryOp:
            return "ternary expression"
        if etype is Concatenation:
            return "concatenation"
        if etype is Replication:
            return "replication"
        if etype is RangeSelect:
            return "range select"
        if etype is PartSelect:
            return "part select"
        if etype is BitSelect:
            return "bit select"
        if etype is AssignmentPattern:
            return "assignment pattern"
        if etype is FunctionCall:
            return f"function call '{expr.name}'"
        return etype.__name__

    def _raise_wide_transport_error(self, context: str, detail: str, expr: Expression | None = None) -> None:
        loc = ""
        if expr is not None and getattr(expr, "loc", None) is not None and expr.loc.line:
            loc = f" at line {expr.loc.line}"
            if expr.loc.file:
                loc += f" in {expr.loc.file}"
        raise NotImplementedError(
            "Compiled engine wide transport-only support does not allow "
            f"{detail} in {context}{loc}. Use engine='vm' for wide non-transport behavior."
        )

    def _identifier_has_wide_transport(self, expr: Identifier) -> bool:
        name = self._identifier_name(expr)
        sid = self._signal_map.get(name)
        if sid is not None:
            return self._signal_widths[sid] > _WORD_BITS
        struct_info = self._resolve_struct_storage_access(name)
        if struct_info is not None:
            return struct_info[4] > _WORD_BITS
        mid = self._mem_map.get(name)
        if mid is not None:
            return self._mem_info[mid][0] > _WORD_BITS
        return False

    def _identifier_has_wide_struct_memory_transport(self, expr: Identifier) -> bool:
        struct_info = self._resolve_struct_storage_access(self._identifier_name(expr))
        return struct_info is not None and struct_info[0] == "memory" and struct_info[4] > _WORD_BITS

    def _validate_wide_transport_expr(self, expr: Expression, context: str) -> bool:  # noqa: PLR0911, PLR0912
        """Validate that *expr* does not use wide values in non-transport ways.

        Returns True when the expression result itself is wider than one machine
        word and is therefore a wide transport value that parents must handle
        carefully.
        """
        etype = type(expr)

        if etype is Identifier:
            return self._identifier_has_wide_transport(expr)

        if etype in {Literal, StringLiteral}:
            return self._expr_width(expr) > _WORD_BITS

        if etype is BitSelect:
            self._validate_wide_transport_expr(expr.target, f"{context} bit-select target")
            if self._validate_wide_transport_expr(expr.index, f"{context} bit-select index"):
                self._raise_wide_transport_error(context, "wide bit-select index", expr.index)
            return False

        if etype is RangeSelect:
            self._validate_wide_transport_expr(expr.target, f"{context} range-select target")
            if self._validate_wide_transport_expr(expr.msb, f"{context} range-select msb"):
                self._raise_wide_transport_error(context, "wide range-select msb", expr.msb)
            if self._validate_wide_transport_expr(expr.lsb, f"{context} range-select lsb"):
                self._raise_wide_transport_error(context, "wide range-select lsb", expr.lsb)
            return self._expr_width(expr) > _WORD_BITS

        if etype is PartSelect:
            self._validate_wide_transport_expr(expr.target, f"{context} part-select target")
            if self._validate_wide_transport_expr(expr.base, f"{context} part-select base"):
                self._raise_wide_transport_error(context, "wide part-select base", expr.base)
            if self._validate_wide_transport_expr(expr.width, f"{context} part-select width"):
                self._raise_wide_transport_error(context, "wide part-select width", expr.width)
            return self._expr_width(expr) > _WORD_BITS

        if etype is Concatenation:
            for index, part in enumerate(expr.parts):
                self._validate_wide_transport_expr(part, f"{context} concatenation part {index}")
            return self._expr_width(expr) > _WORD_BITS

        if etype is Replication:
            if self._validate_wide_transport_expr(expr.count, f"{context} replication count"):
                self._raise_wide_transport_error(context, "wide replication count", expr.count)
            self._validate_wide_transport_expr(expr.value, f"{context} replication value")
            return self._expr_width(expr) > _WORD_BITS

        if etype is AssignmentPattern:
            for name, value_expr in expr.named_pairs:
                self._validate_wide_transport_expr(value_expr, f"{context} assignment-pattern field '{name}'")
            if expr.positional:
                for index, value_expr in enumerate(expr.positional):
                    self._validate_wide_transport_expr(value_expr, f"{context} assignment-pattern item {index}")
            if expr.default_value is not None:
                self._validate_wide_transport_expr(expr.default_value, f"{context} assignment-pattern default")
            return self._expr_width(expr) > _WORD_BITS

        if etype is FunctionCall:
            name = expr.name.lower()
            if name == "$bits":
                return False
            arg_has_wide = False
            for index, arg in enumerate(expr.arguments):
                arg_has_wide |= self._validate_wide_transport_expr(arg, f"{context} argument {index}")
            if arg_has_wide or self._expr_width(expr) > _WORD_BITS:
                self._raise_wide_transport_error(context, self._expr_summary(expr), expr)
            return False

        if etype is UnaryOp:
            operand_has_wide = self._validate_wide_transport_expr(expr.operand, f"{context} operand")
            if operand_has_wide or self._expr_width(expr) > _WORD_BITS:
                self._raise_wide_transport_error(context, self._expr_summary(expr), expr)
            return False

        if etype is BinaryOp:
            left_has_wide = self._validate_wide_transport_expr(expr.left, f"{context} left operand")
            right_has_wide = self._validate_wide_transport_expr(expr.right, f"{context} right operand")
            if left_has_wide or right_has_wide or self._expr_width(expr) > _WORD_BITS:
                self._raise_wide_transport_error(context, self._expr_summary(expr), expr)
            return False

        if etype is TernaryOp:
            cond_has_wide = self._validate_wide_transport_expr(expr.condition, f"{context} condition")
            true_has_wide = self._validate_wide_transport_expr(expr.true_expr, f"{context} true branch")
            false_has_wide = self._validate_wide_transport_expr(expr.false_expr, f"{context} false branch")
            if cond_has_wide:
                self._raise_wide_transport_error(context, "wide ternary condition", expr.condition)
            return true_has_wide or false_has_wide or self._expr_width(expr) > _WORD_BITS

        return self._expr_width(expr) > _WORD_BITS

    def _validate_wide_transport_lhs(self, lhs: Expression, context: str) -> bool:
        lhs_type = type(lhs)
        if lhs_type is Identifier:
            return self._identifier_has_wide_transport(lhs)

        if lhs_type is Concatenation:
            for index, part in enumerate(lhs.parts):
                self._validate_wide_transport_lhs(part, f"{context} concatenation part {index}")
            return self._expr_width(lhs) > _WORD_BITS

        if lhs_type is BitSelect:
            self._validate_wide_transport_lhs(lhs.target, f"{context} bit-select target")
            if self._validate_wide_transport_expr(lhs.index, f"{context} bit-select index"):
                self._raise_wide_transport_error(context, "wide bit-select index", lhs.index)
            return False

        if lhs_type is RangeSelect:
            self._validate_wide_transport_lhs(lhs.target, f"{context} range-select target")
            if self._validate_wide_transport_expr(lhs.msb, f"{context} range-select msb"):
                self._raise_wide_transport_error(context, "wide range-select msb", lhs.msb)
            if self._validate_wide_transport_expr(lhs.lsb, f"{context} range-select lsb"):
                self._raise_wide_transport_error(context, "wide range-select lsb", lhs.lsb)
            return self._expr_width(lhs) > _WORD_BITS

        if lhs_type is PartSelect:
            self._validate_wide_transport_lhs(lhs.target, f"{context} part-select target")
            if self._validate_wide_transport_expr(lhs.base, f"{context} part-select base"):
                self._raise_wide_transport_error(context, "wide part-select base", lhs.base)
            if self._validate_wide_transport_expr(lhs.width, f"{context} part-select width"):
                self._raise_wide_transport_error(context, "wide part-select width", lhs.width)
            return self._expr_width(lhs) > _WORD_BITS

        self._raise_wide_transport_error(context, f"unsupported assignment target '{lhs_type.__name__}'", lhs)
        return False

    def _validate_wide_transport_assignment(self, lhs: Expression, rhs: Expression, context: str) -> None:
        self._validate_wide_transport_lhs(lhs, f"{context} lhs")
        self._validate_wide_transport_expr(rhs, f"{context} rhs")

    def _validate_wide_transport_stmt(self, stmt: Statement | None, context: str) -> None:  # noqa: PLR0911, PLR0912
        if stmt is None:
            return

        stype = type(stmt)
        if stype in {BlockingAssign, NonblockingAssign}:
            self._validate_wide_transport_assignment(stmt.lhs, stmt.rhs, context)
            return

        if stype in {SeqBlock, ParBlock}:
            for index, child in enumerate(stmt.statements):
                self._validate_wide_transport_stmt(child, f"{context} statement {index}")
            return

        if stype is IfStatement:
            if self._validate_wide_transport_expr(stmt.condition, f"{context} if-condition"):
                self._raise_wide_transport_error(context, "wide if-condition", stmt.condition)
            self._validate_wide_transport_stmt(stmt.then_body, f"{context} then-branch")
            self._validate_wide_transport_stmt(stmt.else_body, f"{context} else-branch")
            return

        if stype is CaseStatement:
            if self._validate_wide_transport_expr(stmt.expression, f"{context} case expression"):
                self._raise_wide_transport_error(context, "wide case expression", stmt.expression)
            for item_index, item in enumerate(stmt.items):
                for value_index, value_expr in enumerate(item.values):
                    if self._validate_wide_transport_expr(
                        value_expr, f"{context} case item {item_index} value {value_index}"
                    ):
                        self._raise_wide_transport_error(context, "wide case item value", value_expr)
                self._validate_wide_transport_stmt(item.body, f"{context} case item {item_index}")
            return

        if stype is ForLoop:
            self._validate_wide_transport_stmt(stmt.init, f"{context} for-init")
            if self._validate_wide_transport_expr(stmt.condition, f"{context} for-condition"):
                self._raise_wide_transport_error(context, "wide for-condition", stmt.condition)
            self._validate_wide_transport_stmt(stmt.update, f"{context} for-update")
            self._validate_wide_transport_stmt(stmt.body, f"{context} for-body")
            return

        if stype is WhileLoop:
            if self._validate_wide_transport_expr(stmt.condition, f"{context} while-condition"):
                self._raise_wide_transport_error(context, "wide while-condition", stmt.condition)
            self._validate_wide_transport_stmt(stmt.body, f"{context} while-body")
            return

        if stype is RepeatLoop:
            if self._validate_wide_transport_expr(stmt.count, f"{context} repeat-count"):
                self._raise_wide_transport_error(context, "wide repeat-count", stmt.count)
            self._validate_wide_transport_stmt(stmt.body, f"{context} repeat-body")
            return

        if stype is ForeverLoop:
            self._validate_wide_transport_stmt(stmt.body, f"{context} forever-body")
            return

        if stype is WaitStatement:
            if self._validate_wide_transport_expr(stmt.condition, f"{context} wait-condition"):
                self._raise_wide_transport_error(context, "wide wait-condition", stmt.condition)
            self._validate_wide_transport_stmt(stmt.body, f"{context} wait-body")
            return

        if stype is DelayControl:
            if self._validate_wide_transport_expr(stmt.delay, f"{context} delay"):
                self._raise_wide_transport_error(context, "wide delay expression", stmt.delay)
            self._validate_wide_transport_stmt(stmt.body, f"{context} delayed-body")
            return

        if stype is EventControl:
            for event_index, event in enumerate(stmt.events):
                if self._validate_wide_transport_expr(event.signal, f"{context} event {event_index} signal"):
                    self._raise_wide_transport_error(context, "wide event-control signal", event.signal)
            self._validate_wide_transport_stmt(stmt.body, f"{context} event-body")
            return

        if stype in {TaskEnable, SystemTaskCall}:
            for index, arg in enumerate(stmt.arguments):
                self._validate_wide_transport_expr(arg, f"{context} argument {index}")
            return

    def _validate_wide_transport_only(self, module: Module) -> None:
        # Only enforce strict transport-only restrictions when explicitly requested.
        # The default path lets the recursive wide emitter handle any expression it
        # supports; only unsupported patterns will cause a runtime error during emit.
        if os.environ.get("VERILOG_TOOLS_COMPILED_WIDE_TRANSPORT_ONLY", "") != "1":
            return
        if not self._module_has_wide_state():
            return

        for index, assign in enumerate(module.continuous_assigns):
            self._validate_wide_transport_assignment(assign.lhs, assign.rhs, f"continuous assign {index}")

        for index, block in enumerate(module.always_blocks):
            for edge_index, edge in enumerate(block.sensitivity_list):
                if isinstance(edge, SensitivityEdge) and self._validate_wide_transport_expr(
                    edge.signal, f"always block {index} sensitivity {edge_index}"
                ):
                    self._raise_wide_transport_error(
                        f"always block {index}", "wide edge/level sensitivity signal", edge.signal
                    )
            self._validate_wide_transport_stmt(block.body, f"always block {index}")

        for index, block in enumerate(module.initial_blocks):
            self._validate_wide_transport_stmt(block.body, f"initial block {index}")

        for func in module.functions:
            self._validate_wide_transport_stmt(func.body, f"function '{func.name}'")

        for task in module.tasks:
            self._validate_wide_transport_stmt(task.body, f"task '{task.name}'")

    def generate(self, module: Module, *, delta_limit: int = 10_000) -> str:
        """Generate a complete .pyx source string for *module*."""
        self._module = module
        self._delta_limit = delta_limit

        # Check for unsupported constructs before codegen
        if module.instances:
            raise NotImplementedError(
                "Compiled engine does not support module instantiation / hierarchy. Use engine='vm' instead."
            )

        if module.generate_blocks:
            raise NotImplementedError(
                "Generate constructs must be elaborated before compilation. "
                "Call flatten_module() or elaborate_generates() first."
            )

        self._register_signals(module)
        # Build function/task maps
        self._function_map = {f.name: f for f in module.functions}
        self._task_map = {t.name: t for t in module.tasks}
        self._register_func_task_signals(module)
        self._validate_wide_transport_only(module)
        self._compile_continuous_assigns(module)
        self._compile_always_blocks(module)
        self._compile_initial_blocks(module)
        self._timing_diagnostics = self._collect_timing_diagnostics(module)

        sections = [
            self._gen_header(),
            self._gen_constants(),
            self._gen_struct(),
            self._gen_wmask(),
            self._gen_wide_primitives(),
            self._gen_wide_adapters(),
            self._gen_wide_mem_helpers(),
            self._gen_user_functions(),
            self._gen_process_functions(),
            self._gen_delta_loop(),
            self._gen_compiled_sim(),
        ]
        return "\n\n".join(sections) + "\n"

    def generate_to_file(self, module: Module, path: str, *, delta_limit: int = 10_000) -> str:
        """Generate a complete .pyx source for *module* and write it to *path*.

        Unlike :meth:`generate`, this method never holds the entire ``.pyx``
        string in memory simultaneously.  Each section is written to *path*
        incrementally as it is generated, and the process-function section is
        streamed one function at a time so that peak memory is bounded by the
        largest single process function rather than the total output size.

        Returns the SHA-256 hex digest of the generated source bytes.  Pass
        this to :meth:`~.compiler.CythonCompiler.compile_pyx_file` as the
        ``source_sha256_hex`` argument.
        """
        import hashlib

        self._module = module
        self._delta_limit = delta_limit

        if module.instances:
            raise NotImplementedError(
                "Compiled engine does not support module instantiation / hierarchy. Use engine='vm' instead."
            )
        if module.generate_blocks:
            raise NotImplementedError(
                "Generate constructs must be elaborated before compilation. "
                "Call flatten_module() or elaborate_generates() first."
            )

        self._register_signals(module)
        self._function_map = {f.name: f for f in module.functions}
        self._task_map = {t.name: t for t in module.tasks}
        self._register_func_task_signals(module)
        self._validate_wide_transport_only(module)
        self._compile_continuous_assigns(module)
        self._compile_always_blocks(module)
        self._compile_initial_blocks(module)
        self._timing_diagnostics = self._collect_timing_diagnostics(module)

        hasher = hashlib.sha256()

        with open(path, "wb") as fh:
            def _write(text: str) -> None:
                encoded = text.encode("utf-8")
                fh.write(encoded)
                hasher.update(encoded)

            small_sections = [
                self._gen_header,
                self._gen_constants,
                self._gen_struct,
                self._gen_wmask,
                self._gen_wide_primitives,
                self._gen_wide_adapters,
                self._gen_wide_mem_helpers,
                self._gen_user_functions,
            ]
            for gen_fn in small_sections:
                _write(gen_fn())
                _write("\n\n")

            # Process functions streamed one function at a time to cap peak memory.
            self._gen_process_functions_to(_write)

            for gen_fn in (self._gen_delta_loop, self._gen_compiled_sim):
                _write("\n\n")
                _write(gen_fn())

            _write("\n")

        return hasher.hexdigest()

    # ΓöÇΓöÇ Signal registration ΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇΓöÇ

    def _register_signal(self, name: str, width: int, signed: bool = False) -> int:
        if name in self._signal_map:
            return self._signal_map[name]
        sid = self._n_sigs
        self._n_sigs += 1
        self._signal_map[name] = sid
        self._signal_names.append(name)
        self._signal_widths.append(width)
        self._signal_signed.append(signed)
        return sid

    def _register_memory(
        self, name: str, elem_width: int, depth: int, dims: list[tuple[int, int, int, int]] | None = None
    ) -> int:
        """Register a memory array, returning its memory id."""
        mid = self._n_mems
        self._n_mems += 1
        self._mem_map[name] = mid
        self._mem_info.append((elem_width, depth))
        self._mem_layouts.append(dims or [(0, 1, depth, 1)])
        # Synthetic 1-bit marker signal for dirty tracking
        marker_sid = self._register_signal(f"__mem_{mid}_wr", 1)
        self._mem_marker_sigs.append(marker_sid)
        return mid

    def _eval_initial_value(self, expr, width: int) -> tuple[int, int] | None:
        """Statically evaluate an initial_value expression. Returns (val, mask) or None."""
        if expr is None:
            return None
        try:
            wmask = (1 << width) - 1
            if isinstance(expr, Literal):
                if expr.is_x or expr.is_z:
                    return (0, wmask)
                if isinstance(expr.value, (int, float)):
                    return (int(expr.value) & wmask, 0)
                if isinstance(expr.value, str):
                    try:
                        text = expr.original_text or expr.value
                        v = Value.from_verilog(text)
                        return (v.val & wmask, v.mask & wmask)
                    except (ValueError, TypeError):
                        return (0, wmask)
            if isinstance(expr, UnaryOp):
                # Unary ~ and - have subtle width semantics: the operand is
                # self-determined to its own width (IEEE 1364-2005 §5.4.1).
                # For UNSIZED integer literals (width=None), Verilog treats
                # them as 32-bit, so -1 → 0xFFFFFFFF, then truncated to the
                # target width. We can evaluate that statically.
                # For SIZED literals (width != None), the self-determined width
                # differs from the context (e.g. -3'b001 = 3'b111 = 7, not
                # 0xFF in an 8-bit context), so we defer to runtime.
                if (
                    expr.op in ("-", "~")
                    and isinstance(expr.operand, Literal)
                    and not expr.operand.is_x
                    and not expr.operand.is_z
                    and expr.operand.width is None
                    and isinstance(expr.operand.value, (int, float))
                ):
                    inner = int(expr.operand.value) & 0xFFFFFFFF
                    if expr.op == "-":
                        result = (-inner) & 0xFFFFFFFF
                    else:
                        result = (~inner) & 0xFFFFFFFF
                    return (result & wmask, 0)
                return None
            if isinstance(expr, BinaryOp):
                left = self._eval_initial_value(expr.left, width)
                right = self._eval_initial_value(expr.right, width)
                if left is not None and left[1] == 0 and right is not None and right[1] == 0:
                    if expr.op == "+":
                        return ((left[0] + right[0]) & wmask, 0)
                    if expr.op == "-":
                        return ((left[0] - right[0]) & wmask, 0)
        except Exception:
            pass
        return None

    def _register_signals(self, module: Module) -> None:
        """Register all signals from the module (same order as VM compiler)."""
        from ..elaborate import _build_param_env  # noqa: PLC0415

        param_env = _build_param_env(module)

        for net in module.nets:
            senv = _scoped_env(net.name, param_env)
            w = _range_width(net.width, senv)
            lsb = 0
            if net.width is not None:
                lsb_val = _const_int(net.width.lsb, senv)
                if lsb_val is not None:
                    lsb = lsb_val
            if net.dimensions:
                if lsb != 0:
                    self._memory_bases[net.name] = lsb
                dims = _dim_strides(net.dimensions, senv)
                depth = 1
                for _start, _step, dim_depth, _stride in dims:
                    depth *= dim_depth
                self._register_memory(net.name, w, depth, dims)
            else:
                if lsb != 0:
                    self._signal_bases[net.name] = lsb
                sid = self._register_signal(net.name, w, signed=net.signed)
                if hasattr(net, "initial_value") and net.initial_value is not None:
                    iv = self._eval_initial_value(net.initial_value, w)
                    if iv is not None:
                        self._var_init[sid] = iv

        for var in module.variables:
            senv = _scoped_env(var.name, param_env)
            w = _var_width(var, senv)
            lsb = 0
            if var.width is not None:
                lsb_val = _const_int(var.width.lsb, senv)
                if lsb_val is not None:
                    lsb = lsb_val
            if var.dimensions:
                if lsb != 0:
                    self._memory_bases[var.name] = lsb
                dims = _dim_strides(var.dimensions, senv)
                depth = 1
                for _start, _step, dim_depth, _stride in dims:
                    depth *= dim_depth
                self._register_memory(var.name, w, depth, dims)
            else:
                if lsb != 0:
                    self._signal_bases[var.name] = lsb
                sid = self._register_signal(var.name, w, signed=var.signed)
                kind_name = var.kind.name if hasattr(var.kind, "name") else str(var.kind)
                if kind_name == "INTEGER":
                    self._unmasked_signal_ids.add(sid)
                if var.initial_value is not None:
                    iv = self._eval_initial_value(var.initial_value, w)
                    if iv is not None:
                        self._var_init[sid] = iv

        for port in module.ports:
            senv = _scoped_env(port.name, param_env)
            w = _range_width(port.width, senv)
            lsb = 0
            if port.width is not None:
                lsb_val = _const_int(port.width.lsb, senv)
                if lsb_val is not None:
                    lsb = lsb_val
            if port.name not in self._signal_map:
                if getattr(port, "dimensions", None):
                    if lsb != 0:
                        self._memory_bases[port.name] = lsb
                    dims = _dim_strides(port.dimensions, senv)
                    depth = 1
                    for _start, _step, dim_depth, _stride in dims:
                        depth *= dim_depth
                    self._register_memory(port.name, w, depth, dims)
                else:
                    if lsb != 0:
                        self._signal_bases[port.name] = lsb
                    self._register_signal(port.name, w, signed=port.signed)

        # Register parameters as constant-valued signals
        self._register_parameters(module)

        # Register enum member constants from typedefs
        self._register_enum_constants(module)

        # Register struct type information for field access
        self._register_struct_types(module)

    def _register_parameters(self, module: Module) -> None:
        """Register parameters as signals initialized to their constant values."""
        from veriforge.sim.elaborate import _build_param_env, parameter_signal_width  # noqa: PLC0415

        param_env = _build_param_env(module)
        self._param_env = param_env
        for p in module.parameters:
            if p.name in param_env and p.name not in self._signal_map:
                val = param_env[p.name]
                if isinstance(val, str):
                    # Byte-pack string parameters (e.g. RESET_STRATEGY="MINI")
                    int_val = 0
                    for ch in val:
                        int_val = (int_val << 8) | ord(ch)
                    width = parameter_signal_width(p, param_env, val)
                    sid = self._register_signal(p.name, width, signed=p.signed)
                    self._param_init[sid] = int_val & ((1 << width) - 1)
                elif isinstance(val, int):
                    width = parameter_signal_width(p, param_env, val)
                    sid = self._register_signal(p.name, width, signed=p.signed)
                    self._param_init[sid] = val & ((1 << width) - 1)

    def _register_enum_constants(self, module: Module) -> None:
        """Register enum member constants from typedefs as signals."""
        from veriforge.sim.elaborate import _build_enum_env  # noqa: PLC0415

        enum_env = _build_enum_env(module)
        for name, (val, width) in enum_env.items():
            if name not in self._signal_map:
                sid = self._register_signal(name, width)
                mask = (1 << width) - 1
                self._param_init[sid] = val & mask

    def _register_struct_types(self, module: Module) -> None:
        """Register struct type information for field access resolution."""
        from veriforge.sim.elaborate import _build_struct_env  # noqa: PLC0415

        _type_map, struct_signal_map = _build_struct_env(module)
        self._struct_signal_types.update(struct_signal_map)
        self._struct_type_map.update(_type_map)

    def _register_func_task_signals(self, module: Module) -> None:
        """Register local signals for user-defined function/task ports."""
        from veriforge.model.functions import FunctionDecl, TaskDecl

        for func in module.functions:
            func: FunctionDecl
            prefix = f"__func_{func.name}"
            senv = _scoped_env(prefix, self._param_env)
            # Return width
            ret_width = 32
            if func.return_range is not None:
                rw = _range_width(func.return_range, senv)
                if rw > 0:
                    ret_width = rw
            elif func.return_kind == "integer":
                ret_width = 32
            self._register_signal(f"{prefix}.{func.name}", ret_width)
            for port in func.ports:
                local_name = f"{prefix}.{port.name}"
                self._register_signal(local_name, _range_width(port.width, senv))
                type_name = getattr(port, "data_type", None)
                if type_name:
                    bare = type_name.rsplit("::", 1)[-1] if "::" in type_name else type_name
                    layout = self._struct_type_map.get(bare)
                    if layout is not None:
                        self._struct_signal_types[local_name] = layout
            for local_var in func.locals:
                local_name = f"{prefix}.{local_var.name}"
                self._register_signal(local_name, _var_width(local_var, senv))
                type_name = getattr(local_var, "type_name", None)
                if type_name:
                    bare = type_name.rsplit("::", 1)[-1] if "::" in type_name else type_name
                    layout = self._struct_type_map.get(bare)
                    if layout is not None:
                        self._struct_signal_types[local_name] = layout

        for task in module.tasks:
            task: TaskDecl
            prefix = f"__task_{task.name}"
            senv = _scoped_env(prefix, self._param_env)
            for port in task.ports:
                self._register_signal(f"{prefix}.{port.name}", _range_width(port.width, senv))
