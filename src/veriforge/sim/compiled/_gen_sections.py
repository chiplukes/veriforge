"""Code-generation section methods for CythonCodegen (extracted mixin).

All _gen_* methods that build the .pyx source string sections live here.
CythonCodegen inherits from _GenSectionsMixin.

The narrow helper code (_gen_wmask content) is split across:
  _gen_narrow_accessors.py  -- wmask, _sig_word_val, etc.
  _gen_narrow_stage.py      -- _whole_stage_* helpers
  _gen_narrow_assign.py     -- _whole_assign_* helpers
  _gen_narrow_tail.py       -- slice, sign-ext, display helpers
Wide-signal section methods live in _gen_wide_section.py.
"""

from __future__ import annotations

import re

from veriforge.sim.compiled._codegen_utils import (
    _WORD_BITS,
    _PROCESS_LOOP_LIMIT,
    _safe_const_name,
    _safe_ident,
    _cy_u64_hex,
    _const_int,
)
from veriforge.sim.compiled._gen_narrow_accessors import _gen_narrow_accessor_code
from veriforge.sim.compiled._gen_narrow_stage import _gen_narrow_stage_code
from veriforge.sim.compiled._gen_narrow_assign import _gen_narrow_assign_code
from veriforge.sim.compiled._gen_narrow_tail import _gen_narrow_tail_code
from veriforge.sim.compiled._gen_wide_section import _GenWideSectionsMixin


_BLOCKING_WRITE_RE = re.compile(r"^\s*c\.val\[(\d+)\]\s*=(?!=)")
_NARROW_LHS_RE = re.compile(r"^\s*_set_(?:val|mask)_word\s*\(\s*c\s*,\s*(\d+)\s*,")

# Maximum number of trigger[] terms to inline on a single sensitivity check line.
# Longer sensitivity sets are split across multiple shorter lines using parenthesised
# continuation so no individual line grows beyond ~120 characters.
_MAX_INLINE_SENS = 6


def _emit_sens_check_lines(sorted_sids: list[int], indent: str) -> list[str]:
    """Return one or more Cython if-condition lines for a sensitivity check.

    For small sensitivity sets emits a single inline ``if`` line.  For large
    sets (> ``_MAX_INLINE_SENS`` signals) spreads the condition across multiple
    short lines using parenthesised continuation so that no single generated
    line exceeds roughly 120 characters.
    """
    if len(sorted_sids) <= _MAX_INLINE_SENS:
        cond = " or ".join(f"trigger[{s}]" for s in sorted_sids)
        return [f"{indent}if {cond}:"]
    cont = indent + "        "
    chunks = [sorted_sids[i : i + _MAX_INLINE_SENS] for i in range(0, len(sorted_sids), _MAX_INLINE_SENS)]
    lines: list[str] = []
    for ci, chunk in enumerate(chunks):
        terms = " or ".join(f"trigger[{s}]" for s in chunk)
        is_last = ci == len(chunks) - 1
        if ci == 0 and is_last:
            lines.append(f"{indent}if {terms}:")
        elif ci == 0:
            lines.append(f"{indent}if ({terms}")
        elif is_last:
            lines.append(f"{cont}or {terms}):")
        else:
            lines.append(f"{cont}or {terms}")
    return lines


def _seq_body_to_sv_reads(body_lines: list[str], async_sids: set[int] | None = None) -> list[str]:
    """Rewrite a seq proc body so that signal reads use sv[]/sm[] (pre-posedge snapshot).

    All sequential process bodies should sample inputs from the pre-clock-edge state,
    regardless of the delta iteration in which their clock posedge is detected.  This
    ensures correctness even when the DUT's clock arrives via multiple cont-assign hops
    (e.g. bench_clk → u_dut.clk → u_dut.u_fifo.s_clk), which delays the DUT's seq
    proc to a later delta iteration after the bench cont assigns have already updated
    combinatorial signals such as tvalid.

    **Exception** — signals that are *blocking-written* (``c.val[X] = ...``) inside the
    process must NOT have their reads substituted, because Verilog blocking-assignment
    semantics require subsequent reads in the same process to observe the freshly
    written value.  This matters for temp regs used inside ``always @(posedge clk)``
    blocks (e.g. ``rd_ptr_temp = rd_ptr_reg + 1; rd_ptr_reg <= rd_ptr_temp;``).
    Without this guard, subsequent reads would see the pre-edge stale value, causing
    pointer corruption in CDC FIFOs and similar logic.

    **Exception** — signals listed in *async_sids* (negedge sensitivity signals such as
    async reset inputs) must also use c.val[] not sv[], because when the body fires on
    the negedge of that signal, the snapshot still holds the pre-transition (high) value.
    Reading sv[rst_n] inside ``if (!rst_n)`` would evaluate to 1 (not reset), causing
    the else branch to execute instead of the reset path.

    Substitutions performed (safe because NBA writes use c.nba_val/c.nba_mask/c.nba_dirty):
      c.val[N]                      → sv[N]    (only if N not blocking-written or async here)
      c.mask[N]                     → sm[N]    (only if N not blocking-written or async here)
      _sig_extract_word_val(c, …)   → _sig_extract_word_val_sv(sv, sm, c, …)
      _sig_extract_word_mask(c, …)  → _sig_extract_word_mask_sv(sm, c, …)
    """
    # First pass: collect signal IDs that are blocking-written in this process.
    # Also seed tainted with async sensitivity signals (negedge signals).
    tainted: set[int] = set(async_sids) if async_sids else set()
    for line in body_lines:
        m = _BLOCKING_WRITE_RE.match(line)
        if m:
            tainted.add(int(m.group(1)))
        m = _NARROW_LHS_RE.match(line)
        if m:
            tainted.add(int(m.group(1)))

    # Second pass: substitute, skipping tainted signal IDs.
    def _sub_val(match: re.Match) -> str:
        sid = int(match.group(1))
        return match.group(0) if sid in tainted else f"sv[{sid}]"

    def _sub_mask(match: re.Match) -> str:
        sid = int(match.group(1))
        return match.group(0) if sid in tainted else f"sm[{sid}]"

    val_re = re.compile(r"c\.val\[(\d+)\]")
    mask_re = re.compile(r"c\.mask\[(\d+)\]")
    wide_val_re = re.compile(r"_sig_extract_word_val\(c,\s*(\d+),")
    wide_mask_re = re.compile(r"_sig_extract_word_mask\(c,\s*(\d+),")

    def _sub_wide_val(m: re.Match) -> str:
        sid = int(m.group(1))
        if sid in tainted:
            return m.group(0)
        return f"_sig_extract_word_val_sv(sv, sm, c, {m.group(1)},"

    def _sub_wide_mask(m: re.Match) -> str:
        sid = int(m.group(1))
        if sid in tainted:
            return m.group(0)
        return f"_sig_extract_word_mask_sv(sm, c, {m.group(1)},"

    result = []
    for line in body_lines:
        line = wide_val_re.sub(_sub_wide_val, line)
        line = wide_mask_re.sub(_sub_wide_mask, line)
        # For lines that are themselves a blocking-write LHS, only substitute the RHS.
        m = _BLOCKING_WRITE_RE.match(line)
        if m:
            lhs_end = line.index("=") + 1
            lhs, rhs = line[:lhs_end], line[lhs_end:]
            rhs = val_re.sub(_sub_val, rhs)
            rhs = mask_re.sub(_sub_mask, rhs)
            line = lhs + rhs
        else:
            line = val_re.sub(_sub_val, line)
            line = mask_re.sub(_sub_mask, line)
        result.append(line)
    return result


_CDEF_INIT_RE = re.compile(r"^(\s*)cdef\s+((?:unsigned\s+)?(?:long\s+long|int))\s+(\w+)\s*=\s*(.+)$")
_CDEF_BARE_RE = re.compile(r"^(\s*)cdef\s+((?:unsigned\s+)?(?:long\s+long|int))\s+(\w+)\s*$")


def _hoist_inline_cdefs(body_lines: list[str]) -> tuple[list[str], list[str]]:
    """Hoist inline ``cdef TYPE name [= expr]`` declarations to function level.

    Cython forbids ``cdef`` inside ``if``/``elif``/``for`` blocks.  When the
    emitter places temporaries inside a conditional chain they trigger a Cython
    compile error.  This function:

    1. Scans *body_lines* for both ``cdef TYPE name = expr`` (with initializer)
       and bare ``cdef TYPE name`` (no initializer) at any indent.
    2. Collects unique ``cdef TYPE name`` declarations for the function top level.
    3. Rewrites initializer forms to plain ``name = expr`` assignments; removes
       bare declaration lines (the declaration is now at function level).
    """
    seen: dict[str, str] = {}  # name → ctype (first occurrence wins)
    new_body: list[str] = []
    for line in body_lines:
        m = _CDEF_INIT_RE.match(line)
        if m:
            pad, ctype, name, expr = m.group(1), m.group(2), m.group(3), m.group(4)
            if name not in seen:
                seen[name] = ctype
            new_body.append(f"{pad}{name} = {expr}")
            continue
        m = _CDEF_BARE_RE.match(line)
        if m:
            _, ctype, name = m.group(1), m.group(2), m.group(3)
            if name not in seen:
                seen[name] = ctype
            # Drop the bare declaration — it is now at function level.
            continue
        new_body.append(line)
    hoisted = [f"    cdef {ctype} {name}" for name, ctype in seen.items()]
    return hoisted, new_body


class _GenSectionsMixin(_GenWideSectionsMixin):
    """Mixin providing all _gen_* section-builder methods for CythonCodegen."""

    __slots__ = ()

    def _gen_header(self) -> str:
        return (
            "# cython: language_level=3, boundscheck=False, wraparound=False\n"
            "# cython: cdivision=True, initializedcheck=False, nonecheck=False\n"
            "\n"
            "from libc.string cimport memcpy\n"
            "from libc.math cimport pow\n"
            "from libc.stdio cimport snprintf"
        )

    def _gen_constants(self) -> str:
        wide_offsets, wide_words, total_wide_words = self._wide_layout()
        lines = [f"DEF N_SIGS = {max(self._n_sigs, 1)}"]
        lines.append(f"DEF N_WIDE_WORDS = {max(total_wide_words, 1)}")
        lines.append("DEF OUT_BUF_MAX = 65536")
        lines.append(f"DEF PROCESS_LOOP_LIMIT = {_PROCESS_LOOP_LIMIT}")
        lines.append("DEF ERR_NONE = 0")
        lines.append("DEF ERR_WHILE_LOOP_LIMIT = 1")
        lines.append("DEF ERR_FOREVER_LOOP_LIMIT = 2")
        lines.append("DEF ERR_DELTA_LIMIT = 3")
        lines.append(f"DEF DELTA_LIMIT = {self._delta_limit}")
        # After this many delta iterations, start checking for value-level
        # stability (fixpoint) so designs whose dirty flags never quiet
        # (e.g. combo loops with intermediate writes) still terminate.
        lines.append(f"DEF DELTA_CONV_CHECK_START = {min(16, max(self._delta_limit - 2, 0))}")
        if self._n_mems > 0:
            lines.append("DEF NBA_MEM_MAX = 64")
            lines.append("DEF NBA_MEM_RANGE_MAX = 64")
        # Build unique constant names (sanitised names can collide)
        used: set[str] = set()
        cnames: list[str] = []
        for sid in range(self._n_sigs):
            cname = _safe_const_name(self._signal_names[sid])
            if cname in used:
                suffix = 2
                while f"{cname}_{suffix}" in used:
                    suffix += 1
                cname = f"{cname}_{suffix}"
            used.add(cname)
            cnames.append(cname)
        for sid, cname in enumerate(cnames):
            lines.append(f"DEF SIG_{cname} = {sid}")
        lines.append("")
        for sid, cname in enumerate(cnames):
            lines.append(f"DEF W_{cname} = {self._signal_widths[sid]}")
            lines.append(f"DEF WIDE_WORDS_{cname} = {wide_words[sid]}")
            lines.append(f"DEF WIDE_OFFSET_{cname} = {wide_offsets[sid]}")
        # Memory constants
        for mid in range(self._n_mems):
            ew, depth = self._mem_info[mid]
            lines.append(f"DEF MEM_{mid}_WIDTH = {ew}")
            lines.append(f"DEF MEM_{mid}_DEPTH = {depth}")
            lines.append(f"DEF MEM_{mid}_WORDS = {self._mem_words(mid)}")
        return "\n".join(lines)

    def _gen_struct(self) -> str:
        lines = [
            "cdef struct SimCtx:",
            "    long long val[N_SIGS]",
            "    long long mask[N_SIGS]",
            "    int       width[N_SIGS]",
            "    int       wide_words[N_SIGS]",
            "    int       wide_offset[N_SIGS]",
            "    long long nba_val[N_SIGS]",
            "    long long nba_mask[N_SIGS]",
            "    unsigned long long wide_nba_val[N_WIDE_WORDS]",
            "    unsigned long long wide_nba_mask[N_WIDE_WORDS]",
            "    int       nba_dirty[N_SIGS]",
            "    int       dirty[N_SIGS]",
            "    int       nba_pending",
            "    unsigned long long wide_val[N_WIDE_WORDS]",
            "    unsigned long long wide_mask[N_WIDE_WORDS]",
            "    long long conv_val[N_SIGS]",
            "    long long conv_mask[N_SIGS]",
            "    unsigned long long conv_wide_val[N_WIDE_WORDS]",
            "    unsigned long long conv_wide_mask[N_WIDE_WORDS]",
            "    long long sim_time",
            "    char      out_buf[OUT_BUF_MAX]",
            "    int       out_count",
            "    int       finished",
            "    int       error_code",
        ]
        # Memory arrays
        for mid in range(self._n_mems):
            ew, depth = self._mem_info[mid]
            if ew > _WORD_BITS:
                words = self._mem_words(mid)
                lines.append(f"    unsigned long long wide_mem_{mid}_val[{depth * words}]")
                lines.append(f"    unsigned long long wide_mem_{mid}_mask[{depth * words}]")
            else:
                lines.append(f"    long long mem_{mid}_val[{depth}]")
                lines.append(f"    long long mem_{mid}_mask[{depth}]")
        # NBA memory queue
        if self._n_mems > 0:
            lines.extend(
                [
                    "    int       nba_mem_count",
                    "    int       nba_mem_mid[NBA_MEM_MAX]",
                    "    int       nba_mem_addr[NBA_MEM_MAX]",
                    "    long long nba_mem_val[NBA_MEM_MAX]",
                    "    long long nba_mem_mask[NBA_MEM_MAX]",
                    "    int       nba_mem_range_count",
                    "    int       nba_mem_range_mid[NBA_MEM_RANGE_MAX]",
                    "    int       nba_mem_range_addr[NBA_MEM_RANGE_MAX]",
                    "    int       nba_mem_range_msb[NBA_MEM_RANGE_MAX]",
                    "    int       nba_mem_range_lsb[NBA_MEM_RANGE_MAX]",
                    "    long long nba_mem_range_val[NBA_MEM_RANGE_MAX]",
                    "    long long nba_mem_range_mask[NBA_MEM_RANGE_MAX]",
                ]
            )
        return "\n".join(lines)

    def _gen_wmask(self) -> str:
        lines: list[str] = []
        lines.extend(_gen_narrow_accessor_code())
        lines.extend(_gen_narrow_stage_code())
        lines.extend(_gen_narrow_assign_code())
        for mid in range(self._n_mems):
            elem_width, _depth = self._mem_info[mid]
            if elem_width > _WORD_BITS:
                read_val = f"_wmem{mid}_word_val(c, addr, i)"
                read_mask = f"_wmem{mid}_word_mask(c, addr, i)"
                low_val = f"_wmem{mid}_word_val(c, addr, 0)"
                low_mask = f"_wmem{mid}_word_mask(c, addr, 0)"
            else:
                read_val = f"(<unsigned long long>c.mem_{mid}_val[addr] if i == 0 else 0)"
                read_mask = f"(<unsigned long long>c.mem_{mid}_mask[addr] if i == 0 else 0)"
                low_val = f"<unsigned long long>c.mem_{mid}_val[addr]"
                low_mask = f"<unsigned long long>c.mem_{mid}_mask[addr]"
            lines.extend(
                [
                    f"cdef inline void _whole_assign_mem_elem_{mid}(SimCtx *c, int dst_sid, int addr) noexcept nogil:",
                    "    cdef int dst_words = c.wide_words[dst_sid]",
                    "    cdef int i, remaining_w, src_remaining_w, changed = 0",
                    "    cdef unsigned long long out_v, out_m, tail_mask, src_mask",
                    "    cdef long long new_v, new_m",
                    "    if dst_words > 0:",
                    "        for i in range(dst_words):",
                    f"            src_remaining_w = MEM_{mid}_WIDTH - (i * 64)",
                    "            if src_remaining_w <= 0:",
                    "                out_v = 0",
                    "                out_m = 0",
                    "            else:",
                    f"                out_v = {read_val}",
                    f"                out_m = {read_mask}",
                    "                src_mask = _word_mask64(src_remaining_w)",
                    "                out_v &= src_mask",
                    "                out_m &= src_mask",
                    "            remaining_w = c.width[dst_sid] - (i * 64)",
                    "            tail_mask = _word_mask64(remaining_w)",
                    "            out_v &= tail_mask",
                    "            out_m &= tail_mask",
                    "            if out_v != c.wide_val[c.wide_offset[dst_sid] + i] or out_m != c.wide_mask[c.wide_offset[dst_sid] + i]:",
                    "                c.wide_val[c.wide_offset[dst_sid] + i] = out_v",
                    "                c.wide_mask[c.wide_offset[dst_sid] + i] = out_m",
                    "                changed = 1",
                    "        new_v = <long long>c.wide_val[c.wide_offset[dst_sid]]",
                    "        new_m = <long long>c.wide_mask[c.wide_offset[dst_sid]]",
                    "    else:",
                    f"        out_v = {low_val}",
                    f"        out_m = {low_mask}",
                    "        tail_mask = _word_mask64(c.width[dst_sid])",
                    "        new_v = <long long>(out_v & tail_mask)",
                    "        new_m = <long long>(out_m & tail_mask)",
                    "    if new_v != c.val[dst_sid] or new_m != c.mask[dst_sid]:",
                    "        c.val[dst_sid] = new_v",
                    "        c.mask[dst_sid] = new_m",
                    "        changed = 1",
                    "    if changed:",
                    "        c.dirty[dst_sid] = 1",
                    "",
                ]
            )
        lines.extend(_gen_narrow_tail_code())
        return "\n".join(lines)

    def _gen_user_functions(self) -> str:
        """Generate Cython helpers for user-defined functions and tasks."""
        import copy

        from veriforge.model.functions import FunctionDecl

        parts: list[str] = []

        for func in self._function_map.values():
            func: FunctionDecl
            prefix = f"__func_{func.name}"
            safe_name = _safe_ident(func.name)
            ret_name = f"{prefix}.{func.name}"
            ret_sid = self._signal_map[ret_name]
            ret_w = self._signal_widths[ret_sid]

            # Build parameter list
            params = ", ".join(f"long long arg_{i}" for i in range(len(func.ports)))
            sig = (
                f"cdef inline long long _user_func_{safe_name}(SimCtx *c, {params}) noexcept nogil:"
                if params
                else f"cdef inline long long _user_func_{safe_name}(SimCtx *c) noexcept nogil:"
            )
            parts.append(sig)

            # Initialize return value to 0
            parts.append(f"    c.val[{ret_sid}] = 0")
            parts.append(f"    c.mask[{ret_sid}] = 0")

            # Store args to local port signals
            for i, port in enumerate(func.ports):
                local_name = f"{prefix}.{port.name}"
                sid = self._signal_map[local_name]
                w = self._signal_widths[sid]
                parts.append(f"    c.val[{sid}] = arg_{i} & wmask({w})")
                parts.append(f"    c.mask[{sid}] = 0")

            # Emit function body with remapped identifiers
            if func.body:
                body_copy = copy.deepcopy(func.body)
                local_names = {port.name for port in func.ports}
                local_names.update(local_var.name for local_var in func.locals)
                local_names.add(func.name)
                self._remap_local_identifiers(body_copy, local_names, prefix)
                self._et_count = 0
                self._et_node_masks = {}
                self._et_node_vals = {}
                body_lines = self._emit_stmt(body_copy, indent=1)
                hoisted_et_cdefs, body_lines = _hoist_inline_cdefs(body_lines)
                joined = "\n".join(body_lines)
                parts.extend(hoisted_et_cdefs)
                if any("_cdv" in ln for ln in body_lines):
                    parts.append("    cdef long long _cdv")
                if any("_clhs" in ln for ln in body_lines):
                    parts.append("    cdef long long _clhs")
                if any("_sfv" in ln for ln in body_lines):
                    parts.append("    cdef long long _sfv")
                if any("_mchg" in ln for ln in body_lines):
                    parts.append("    cdef int _mchg")
                if any("_mwi" in ln for ln in body_lines):
                    parts.append("    cdef long long _mwi")
                if any("_mwv" in ln for ln in body_lines):
                    parts.append("    cdef long long _mwv, _mwm")
                if any("_mwvu" in ln for ln in body_lines):
                    parts.append("    cdef unsigned long long _mwvu, _mwmu")
                if "_rmw_msb" in joined:
                    parts.append("    cdef int _rmw_msb, _rmw_lsb")
                    parts.append("    cdef long long _rmw_mask")
                if "_ps_lsb" in joined:
                    parts.append("    cdef int _ps_lsb")
                    parts.append("    cdef long long _ps_mask")
                for m in sorted(set(re.findall(r"\b(_lv_\w+)\b", joined))):
                    parts.append(f"    cdef long long {m}")
                sc_indices = sorted({int(s) for s in re.findall(r"_sc(\d+)_[vm]", joined)})
                if sc_indices:
                    max_words = self._module_max_wide_words()
                    for sc_i in range(sc_indices[-1] + 1):
                        parts.append(f"    cdef unsigned long long _sc{sc_i}_v[{max_words}]")
                        parts.append(f"    cdef unsigned long long _sc{sc_i}_m[{max_words}]")
                parts.extend(body_lines)

            # Return the function return value
            parts.append(f"    return c.val[{ret_sid}] & wmask({ret_w})")
            parts.append("")

        if not parts:
            return "# No user-defined functions"
        return "\n".join(parts)

    def _compile_always_body(self, block_body) -> list[str]:
        """Compile one always-block body Statement to code lines on demand.

        Resets the expression-temporary counters so names are unique per function.
        Called from the process-function generators so the IR for each block is
        discarded as soon as its text has been written to disk.
        """
        self._et_count = 0
        self._et_node_masks = {}
        self._et_node_vals = {}
        return self._emit_stmt(block_body, indent=1)

    def _gen_process_functions(self) -> str:
        parts: list[str] = []

        # Continuous assign functions
        if not self._processes and not self._combo_processes and not self._seq_processes:
            return "# No process functions"

        # Pre-compute per-seq-process negedge sids (async reset signals).
        # These must NOT be rewritten to sv[] in the body because when the negedge
        # fires, sv[] still holds the pre-transition (high) value.
        seq_negedge_sids = [
            {sid for sid, et in edges.items() if et == "negedge"} for edges, _, _ in self._seq_processes
        ]

        process_groups = (
            ("cont", (body_lines for _sens, body_lines in self._processes), False, False),
            ("combo", (self._compile_always_body(body) for _sens, body in self._combo_processes), True, False),
            ("seq", (self._compile_always_body(body) for _edges, _sens, body in self._seq_processes), True, True),
        )
        for prefix, body_groups, emit_pass_when_empty, use_sv in process_groups:
            for i, body_lines in enumerate(body_groups):
                if use_sv:
                    # Seq procs receive the pre-posedge snapshot so that all sequential
                    # processes read the same pre-clock-edge values, regardless of how many
                    # cont-assign hops delayed their clock posedge detection.
                    parts.append(
                        f"cdef inline void {prefix}_{i}(SimCtx *c, long long *sv, long long *sm) noexcept nogil:"
                    )
                else:
                    parts.append(f"cdef inline void {prefix}_{i}(SimCtx *c) noexcept nogil:")
                if body_lines:
                    decls: list[str] = []
                    if use_sv:
                        async_sids = seq_negedge_sids[i] if prefix == "seq" else None
                        body_lines = _seq_body_to_sv_reads(body_lines, async_sids)
                    # Hoist inline cdef-with-initializer declarations to function
                    # level so they are never emitted inside if/elif blocks (Cython
                    # forbids cdef inside conditional blocks).
                    hoisted_cdefs, body_lines = _hoist_inline_cdefs(body_lines)
                    decls.extend(hoisted_cdefs)
                    joined = "\n".join(body_lines)
                    if "_clhs" in joined:
                        decls.append("    cdef long long _clhs")
                    if "_cdv" in joined:
                        decls.append("    cdef long long _cdv")
                    if "_sfv" in joined:
                        decls.append("    cdef long long _sfv")
                    if "_mchg" in joined:
                        decls.append("    cdef int _mchg")
                    if "_mwi" in joined:
                        decls.append("    cdef long long _mwi")
                    if "_mwv" in joined:
                        decls.append("    cdef long long _mwv, _mwm")
                    if "_mwvu" in joined:
                        decls.append("    cdef unsigned long long _mwvu, _mwmu")
                    if "_rmw_msb" in joined:
                        decls.append("    cdef int _rmw_msb, _rmw_lsb")
                        decls.append("    cdef long long _rmw_mask")
                    if "_ps_lsb" in joined:
                        decls.append("    cdef int _ps_lsb")
                        decls.append("    cdef long long _ps_mask")
                    for m in re.findall(r"\b(_lv_\w+)\b", joined):
                        decl = f"    cdef long long {m}"
                        if decl not in decls:
                            decls.append(decl)
                    sc_indices = sorted({int(s) for s in re.findall(r"_sc(\d+)_[vm]", joined)})
                    if sc_indices:
                        max_words = self._module_max_wide_words()
                        for sc_i in range(sc_indices[-1] + 1):
                            decls.append(f"    cdef unsigned long long _sc{sc_i}_v[{max_words}]")
                            decls.append(f"    cdef unsigned long long _sc{sc_i}_m[{max_words}]")
                    parts.extend(decls)
                    parts.extend(body_lines)
                elif emit_pass_when_empty:
                    parts.append("    pass")
                parts.append("")

        return "\n".join(parts)

    def _gen_process_functions_to(self, write_fn) -> None:
        """Stream process functions one at a time via *write_fn*.

        Produces byte-for-byte identical output to ``_gen_process_functions()``
        but never accumulates more than one process function's body lines in
        memory simultaneously.  Suitable for designs where the total process
        function section would otherwise require tens of GB to build as a
        single string.

        *write_fn* is called with successive ``str`` fragments whose
        concatenation equals the full section text.
        """
        if not self._processes and not self._combo_processes and not self._seq_processes:
            write_fn("# No process functions")
            return

        seq_negedge_sids = [
            {sid for sid, et in edges.items() if et == "negedge"} for edges, _, _ in self._seq_processes
        ]

        process_groups = (
            ("cont", (body_lines for _sens, body_lines in self._processes), False, False),
            ("combo", (self._compile_always_body(body) for _sens, body in self._combo_processes), True, False),
            ("seq", (self._compile_always_body(body) for _edges, _sens, body in self._seq_processes), True, True),
        )
        first_func = True
        for prefix, body_groups, emit_pass_when_empty, use_sv in process_groups:
            for i, body_lines in enumerate(body_groups):
                func_parts: list[str] = []
                if use_sv:
                    func_parts.append(
                        f"cdef inline void {prefix}_{i}(SimCtx *c, long long *sv, long long *sm) noexcept nogil:"
                    )
                else:
                    func_parts.append(f"cdef inline void {prefix}_{i}(SimCtx *c) noexcept nogil:")

                if body_lines:
                    decls: list[str] = []
                    if use_sv:
                        async_sids = seq_negedge_sids[i] if prefix == "seq" else None
                        body_lines = _seq_body_to_sv_reads(body_lines, async_sids)
                    hoisted_cdefs, body_lines = _hoist_inline_cdefs(body_lines)
                    decls.extend(hoisted_cdefs)
                    joined = "\n".join(body_lines)
                    if "_clhs" in joined:
                        decls.append("    cdef long long _clhs")
                    if "_cdv" in joined:
                        decls.append("    cdef long long _cdv")
                    if "_sfv" in joined:
                        decls.append("    cdef long long _sfv")
                    if "_mchg" in joined:
                        decls.append("    cdef int _mchg")
                    if "_mwi" in joined:
                        decls.append("    cdef long long _mwi")
                    if "_mwv" in joined:
                        decls.append("    cdef long long _mwv, _mwm")
                    if "_mwvu" in joined:
                        decls.append("    cdef unsigned long long _mwvu, _mwmu")
                    if "_rmw_msb" in joined:
                        decls.append("    cdef int _rmw_msb, _rmw_lsb")
                        decls.append("    cdef long long _rmw_mask")
                    if "_ps_lsb" in joined:
                        decls.append("    cdef int _ps_lsb")
                        decls.append("    cdef long long _ps_mask")
                    for m in re.findall(r"\b(_lv_\w+)\b", joined):
                        decl = f"    cdef long long {m}"
                        if decl not in decls:
                            decls.append(decl)
                    sc_indices = sorted({int(s) for s in re.findall(r"_sc(\d+)_[vm]", joined)})
                    if sc_indices:
                        max_words = self._module_max_wide_words()
                        for sc_i in range(sc_indices[-1] + 1):
                            decls.append(f"    cdef unsigned long long _sc{sc_i}_v[{max_words}]")
                            decls.append(f"    cdef unsigned long long _sc{sc_i}_m[{max_words}]")
                    func_parts.extend(decls)
                    func_parts.extend(body_lines)
                elif emit_pass_when_empty:
                    func_parts.append("    pass")

                func_parts.append("")  # trailing blank line (matches _gen_process_functions)
                chunk = "\n".join(func_parts)
                # Between functions: write a leading \n so that the trailing \n
                # from the previous chunk and this \n together form the blank line
                # separator, matching "\n".join(all_parts) with "" elements.
                if first_func:
                    write_fn(chunk)
                    first_func = False
                else:
                    write_fn("\n" + chunk)

    def _gen_delta_loop(self) -> str:  # noqa: PLR0912, PLR0915
        has_seq = bool(self._seq_processes)
        lines = [
            "cdef int delta_loop(SimCtx *c, long long *sv, long long *sm) noexcept nogil:",
            "    cdef int it, i, changed, _j, _stable",
            "    cdef long long _nbaw",
            f"    cdef int trigger[{max(self._n_sigs, 1)}]",
        ]

        # Declare locals for NBA memory range drain (partial byte-lane writes)
        if self._n_mems > 0:
            lines.append("    cdef int _rmr_msb, _rmr_lsb")
            lines.append("    cdef long long _rmr_mask")

        # Edge detection: compute fire_seq_N flags inside the delta loop
        # so that edges propagated through continuous assigns are detected.
        if has_seq:
            for i, (_edges, _sens, _body) in enumerate(self._seq_processes):
                lines.append(f"    cdef int fire_seq_{i} = 0")
                lines.append(f"    cdef int done_seq_{i} = 0")

        lines.append("")
        lines.append("    for it in range(DELTA_LIMIT):")

        # Copy dirty ΓåÆ trigger, then clear dirty
        lines.append("        changed = 0")
        lines.append("        for i in range(N_SIGS):")
        lines.append("            trigger[i] = c.dirty[i]")
        lines.append("            if trigger[i]:")
        lines.append("                changed = 1")
        lines.append("            c.dirty[i] = 0")
        lines.append("")
        # On the very first iteration, if nothing was externally dirtied
        # we still need to run all assigns once (bootstrap).
        lines.append("        if it == 0 and not changed:")
        lines.append("            for i in range(N_SIGS):")
        lines.append("                trigger[i] = 1")
        lines.append("            changed = 1")
        lines.append("")
        lines.append("        if not changed:")
        lines.append("            break")
        lines.append("")
        # Value-level convergence: once past DELTA_CONV_CHECK_START iterations,
        # snapshot all signal values at the top of the iteration.  If the
        # iteration produces no value change, the state is a fixpoint — the
        # processes are deterministic functions of state, so further
        # iterations cannot change anything even if dirty flags survive
        # (combo loops with intermediate writes keep re-marking dirty).
        lines.append("        if it >= DELTA_CONV_CHECK_START:")
        lines.append("            memcpy(c.conv_val, c.val, N_SIGS * sizeof(long long))")
        lines.append("            memcpy(c.conv_mask, c.mask, N_SIGS * sizeof(long long))")
        lines.append("            memcpy(c.conv_wide_val, c.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))")
        lines.append("            memcpy(c.conv_wide_mask, c.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))")

        # Edge detection inside the delta loop ΓÇö check each iteration
        # so edges propagated through continuous assigns are caught.
        # Each sequential process fires at most once per step.
        if has_seq:
            lines.append("")
            for i, (edges, _sens, _body) in enumerate(self._seq_processes):
                edge_checks = []
                for sid, edge_type in edges.items():
                    if edge_type == "posedge":
                        edge_checks.append(f"((c.val[{sid}] & 1) == 1 and (sv[{sid}] & 1) == 0)")
                    else:  # negedge
                        edge_checks.append(f"((c.val[{sid}] & 1) == 0 and (sv[{sid}] & 1) == 1)")
                if edge_checks:
                    cond = " or ".join(edge_checks)
                    lines.append(f"        if not done_seq_{i} and ({cond}):")
                    lines.append(f"            fire_seq_{i} = 1")

        # Fire sequential processes (once per step, guarded by fire flag)
        if has_seq:
            lines.append("")
            for i in range(len(self._seq_processes)):
                lines.append(f"        if fire_seq_{i}:")
                lines.append(f"            seq_{i}(c, sv, sm)")
                lines.append("            if c.finished:")
                lines.append("                return it")
                lines.append("            if c.error_code != ERR_NONE:")
                lines.append("                return it")
                lines.append(f"            fire_seq_{i} = 0")
                lines.append(f"            done_seq_{i} = 1")
            lines.append("")

            # Apply NBA: copy nba_val ΓåÆ val for signals with nba_dirty set
            lines.append("        if c.nba_pending:")
            lines.append("            for i in range(N_SIGS):")
            lines.append("                if c.nba_dirty[i]:")
            lines.append("                    if c.wide_words[i] > 0:")
            lines.append("                        changed = 0")
            lines.append("                        for _j in range(c.wide_words[i]):")
            lines.append(
                "                            if c.wide_val[c.wide_offset[i] + _j] != c.wide_nba_val[c.wide_offset[i] + _j] or c.wide_mask[c.wide_offset[i] + _j] != c.wide_nba_mask[c.wide_offset[i] + _j]:"
            )
            lines.append(
                "                                c.wide_val[c.wide_offset[i] + _j] = c.wide_nba_val[c.wide_offset[i] + _j]"
            )
            lines.append(
                "                                c.wide_mask[c.wide_offset[i] + _j] = c.wide_nba_mask[c.wide_offset[i] + _j]"
            )
            lines.append("                                changed = 1")
            lines.append("                        if c.nba_val[i] != c.val[i] or c.nba_mask[i] != c.mask[i]:")
            lines.append("                            c.val[i] = c.nba_val[i]")
            lines.append("                            c.mask[i] = c.nba_mask[i]")
            lines.append("                            changed = 1")
            lines.append("                        if changed:")
            lines.append("                            c.dirty[i] = 1")
            lines.append("                    else:")
            lines.append("                        _nbaw = wmask(c.width[i])")
            lines.append(
                "                        if (c.nba_val[i] & _nbaw) != c.val[i] or (c.nba_mask[i] & _nbaw) != c.mask[i]:"
            )
            lines.append("                            c.val[i] = c.nba_val[i] & _nbaw")
            lines.append("                            c.mask[i] = c.nba_mask[i] & _nbaw")
            lines.append("                            c.dirty[i] = 1")
            lines.append("                    c.nba_dirty[i] = 0")
            # Drain NBA memory queue
            if self._n_mems > 0:
                lines.append("            for i in range(c.nba_mem_count):")
                for mid in range(self._n_mems):
                    marker_sid = self._mem_marker_sigs[mid]
                    elem_w, _depth = self._mem_info[mid]
                    cond_kw = "if" if mid == 0 else "elif"
                    lines.append(f"                {cond_kw} c.nba_mem_mid[i] == {mid}:")
                    if elem_w > _WORD_BITS:
                        lines.append(
                            f"                    c.wide_mem_{mid}_val[c.nba_mem_addr[i]] = <unsigned long long>c.nba_mem_val[i]"
                        )
                        lines.append(
                            f"                    c.wide_mem_{mid}_mask[c.nba_mem_addr[i]] = <unsigned long long>c.nba_mem_mask[i]"
                        )
                    else:
                        lines.append(f"                    c.mem_{mid}_val[c.nba_mem_addr[i]] = c.nba_mem_val[i]")
                        lines.append(f"                    c.mem_{mid}_mask[c.nba_mem_addr[i]] = c.nba_mem_mask[i]")
                    lines.append(f"                    c.val[{marker_sid}] ^= 1")
                    lines.append(f"                    c.dirty[{marker_sid}] = 1")
                lines.append("            c.nba_mem_count = 0")
            # Drain NBA memory range queue (partial byte-lane writes)
            if self._n_mems > 0:
                lines.append("            for i in range(c.nba_mem_range_count):")
                lines.append("                _rmr_msb = c.nba_mem_range_msb[i]")
                lines.append("                _rmr_lsb = c.nba_mem_range_lsb[i]")
                lines.append("                _rmr_mask = wmask(_rmr_msb - _rmr_lsb + 1) << _rmr_lsb")
                for mid in range(self._n_mems):
                    marker_sid = self._mem_marker_sigs[mid]
                    elem_w, _depth = self._mem_info[mid]
                    cond_kw = "if" if mid == 0 else "elif"
                    addr_expr = "c.nba_mem_range_addr[i]"
                    lines.append(f"                {cond_kw} c.nba_mem_range_mid[i] == {mid}:")
                    if elem_w > _WORD_BITS:
                        lines.append(
                            f"                    c.wide_mem_{mid}_val[{addr_expr}] ="
                            f" (c.wide_mem_{mid}_val[{addr_expr}] & ~_rmr_mask)"
                            f" | ((((<unsigned long long>c.nba_mem_range_val[i]) & ~(<unsigned long long>c.nba_mem_range_mask[i])) << _rmr_lsb) & _rmr_mask)"
                        )
                        lines.append(
                            f"                    c.wide_mem_{mid}_mask[{addr_expr}] ="
                            f" (c.wide_mem_{mid}_mask[{addr_expr}] & ~_rmr_mask)"
                            f" | ((((<unsigned long long>c.nba_mem_range_mask[i])) << _rmr_lsb) & _rmr_mask)"
                        )
                    else:
                        lines.append(
                            f"                    c.mem_{mid}_val[{addr_expr}] ="
                            f" (c.mem_{mid}_val[{addr_expr}] & ~_rmr_mask)"
                            f" | (((c.nba_mem_range_val[i] & ~c.nba_mem_range_mask[i]) << _rmr_lsb) & _rmr_mask)"
                        )
                        lines.append(
                            f"                    c.mem_{mid}_mask[{addr_expr}] ="
                            f" (c.mem_{mid}_mask[{addr_expr}] & ~_rmr_mask)"
                            f" | ((c.nba_mem_range_mask[i] << _rmr_lsb) & _rmr_mask)"
                        )
                    lines.append(f"                    c.dirty[{marker_sid}] = 1")
                lines.append("            c.nba_mem_range_count = 0")
            lines.append("            c.nba_pending = 0")

        # Invoke each continuous assign guarded by trigger flags
        for i, (sens, _body) in enumerate(self._processes):
            if sens:
                lines.extend(_emit_sens_check_lines(sorted(sens), "        "))
                lines.append(f"            cont_{i}(c)")
                lines.append("            if c.finished:")
                lines.append("                return it")
                lines.append("            if c.error_code != ERR_NONE:")
                lines.append("                return it")
            else:
                lines.append(f"        cont_{i}(c)")
                lines.append("        if c.finished:")
                lines.append("            return it")
                lines.append("        if c.error_code != ERR_NONE:")
                lines.append("            return it")

        # Invoke combinational always blocks guarded by trigger flags
        for i, (sens, _body) in enumerate(self._combo_processes):
            if sens:
                lines.extend(_emit_sens_check_lines(sorted(sens), "        "))
                lines.append(f"            combo_{i}(c)")
                lines.append("            if c.finished:")
                lines.append("                return it")
                lines.append("            if c.error_code != ERR_NONE:")
                lines.append("                return it")
            else:
                lines.append(f"        combo_{i}(c)")
                lines.append("        if c.finished:")
                lines.append("            return it")
                lines.append("        if c.error_code != ERR_NONE:")
                lines.append("            return it")

        # Dirty flags produced by the cont/combo functions in this
        # iteration will be consumed at the TOP of the NEXT iteration
        # (copied into trigger[], then cleared).  The convergence check
        # is there: if no dirty flags survive, the loop breaks.

        # Value-level convergence check (see snapshot above): if this
        # iteration changed no signal value, we are at a fixpoint — stop
        # even though dirty flags may survive.  When the NBA-apply block is
        # emitted (has_seq), skip the check while an NBA is pending — its
        # application next iteration may still change state.  Without seq
        # processes there is no apply block, so nba_pending can never clear
        # and must not gate the check.
        lines.append("")
        if has_seq:
            lines.append("        if it >= DELTA_CONV_CHECK_START and not c.nba_pending:")
        else:
            lines.append("        if it >= DELTA_CONV_CHECK_START:")
        # Memory-marker signals are internal bookkeeping — they flip on every
        # memory write within an iteration, even when the same combo process
        # both clears and rewrites the memory (net data unchanged).  Force them
        # to compare equal so they don't poison the fixpoint criterion.  If
        # memory data really did change, downstream signals that read the
        # memory will reflect it and trip the check normally.
        for marker_sid in self._mem_marker_sigs:
            lines.append(f"            c.conv_val[{marker_sid}] = c.val[{marker_sid}]")
            lines.append(f"            c.conv_mask[{marker_sid}] = c.mask[{marker_sid}]")
        lines.append("            _stable = 1")
        lines.append("            for i in range(N_SIGS):")
        lines.append("                if c.val[i] != c.conv_val[i] or c.mask[i] != c.conv_mask[i]:")
        lines.append("                    _stable = 0")
        lines.append("                    break")
        lines.append("            if _stable:")
        lines.append("                for i in range(N_WIDE_WORDS):")
        lines.append(
            "                    if c.wide_val[i] != c.conv_wide_val[i] or c.wide_mask[i] != c.conv_wide_mask[i]:"
        )
        lines.append("                        _stable = 0")
        lines.append("                        break")
        lines.append("            if _stable:")
        lines.append("                break")

        # If the loop ran to completion without converging, report it.
        # The else-clause fires only when the for loop is NOT exited via break.
        lines.append("    else:")
        lines.append("        c.error_code = ERR_DELTA_LIMIT")

        # After the loop, clear any remaining dirty flags so the caller
        # starts the next time-step with a clean slate.
        lines.append("")
        lines.append("    for i in range(N_SIGS):")
        lines.append("        c.dirty[i] = 0")
        lines.append("    return it")
        return "\n".join(lines)

    def _gen_compiled_sim(self) -> str:
        sn = max(self._n_sigs, 1)
        wide_offsets, wide_words, _total_wide_words = self._wide_layout()
        lines = [
            "cdef class CompiledSim:",
            "    cdef SimCtx ctx",
            f"    cdef long long _snap_v[{sn}]",
            f"    cdef long long _snap_m[{sn}]",
            "",
            "    def __init__(self):",
            "        cdef int i",
            "        for i in range(N_SIGS):",
            "            self.ctx.val[i] = 0",
            "            self.ctx.mask[i] = 0",
            "            self.ctx.width[i] = 0",
            "            self.ctx.wide_words[i] = 0",
            "            self.ctx.wide_offset[i] = 0",
            "            self.ctx.dirty[i] = 0",
            "            self.ctx.nba_val[i] = 0",
            "            self.ctx.nba_mask[i] = 0",
            "            self.ctx.nba_dirty[i] = 0",
            "            self._snap_v[i] = 0",
            "            self._snap_m[i] = 0",
            "        for i in range(N_WIDE_WORDS):",
            "            self.ctx.wide_val[i] = 0",
            "            self.ctx.wide_mask[i] = 0",
            "            self.ctx.wide_nba_val[i] = 0",
            "            self.ctx.wide_nba_mask[i] = 0",
        ]
        # Per-signal width and mask init (outside the loop, constant indices)
        for sid in range(self._n_sigs):
            cname = _safe_const_name(self._signal_names[sid])
            lines.append(f"        self.ctx.width[{sid}] = W_{cname}")
            lines.append(f"        self.ctx.wide_words[{sid}] = WIDE_WORDS_{cname}")
            lines.append(f"        self.ctx.wide_offset[{sid}] = WIDE_OFFSET_{cname}")
            lines.append(f"        self.ctx.mask[{sid}] = wmask(W_{cname})")
            if wide_words[sid] > 0:
                for word_index in range(wide_words[sid]):
                    remaining_width = self._signal_widths[sid] - (word_index * 64)
                    lines.append(
                        f"        self.ctx.wide_mask[{wide_offsets[sid] + word_index}] = _word_mask64({remaining_width})"
                    )
        lines.append("        self.ctx.nba_pending = 0")
        lines.append("        self.ctx.sim_time = 0")
        lines.append("        self.ctx.out_count = 0")
        lines.append("        self.ctx.finished = 0")
        lines.append("        self.ctx.error_code = ERR_NONE")
        # Initialize parameter signals to their constant values
        for sid, val in self._param_init.items():
            self._emit_signal_init_lines(lines, sid, val, 0)
        # Initialize variable/net signals with declared initial values
        for sid, (val, mask) in self._var_init.items():
            self._emit_signal_init_lines(lines, sid, val, mask)
        # Memory initialization
        for mid in range(self._n_mems):
            elem_w, depth = self._mem_info[mid]
            if elem_w > _WORD_BITS:
                words = self._mem_words(mid)
                lines.append(f"        for i in range({depth * words}):")
                lines.append(f"            self.ctx.wide_mem_{mid}_val[i] = 0")
                lines.append(
                    f"            self.ctx.wide_mem_{mid}_mask[i] = _word_mask64(MEM_{mid}_WIDTH - ((i % MEM_{mid}_WORDS) * 64))"
                )
            else:
                lines.append(f"        for i in range({depth}):")
                lines.append(f"            self.ctx.mem_{mid}_val[i] = 0")
                lines.append(f"            self.ctx.mem_{mid}_mask[i] = wmask(MEM_{mid}_WIDTH)")
        if self._n_mems > 0:
            lines.append("        self.ctx.nba_mem_count = 0")
            lines.append("        self.ctx.nba_mem_range_count = 0")

        # Native initial block execution (no timing)
        if self._initial_lines:
            lines.append("        # Initial block values")
            # Use a pointer alias so _emit_stmt's c.val[...] syntax works
            lines.append("        cdef SimCtx *c = &self.ctx")
            if any("_clhs" in ln for ln in self._initial_lines):
                lines.append("        cdef long long _clhs")
            if any("_cdv" in ln for ln in self._initial_lines):
                lines.append("        cdef long long _cdv")
            if any("_sfv" in ln for ln in self._initial_lines):
                lines.append("        cdef long long _sfv")
            if any("_mchg" in ln for ln in self._initial_lines):
                lines.append("        cdef int _mchg")
            if any("_mwi" in ln for ln in self._initial_lines):
                lines.append("        cdef long long _mwi")
            if any("_mwv" in ln for ln in self._initial_lines):
                lines.append("        cdef long long _mwv, _mwm")
            if any("_mwvu" in ln for ln in self._initial_lines):
                lines.append("        cdef unsigned long long _mwvu, _mwmu")
            lines.extend(self._initial_lines)
            lines.append("        self._raise_runtime_error()")

        # drive method
        lines.extend(
            [
                "",
                "    cpdef void drive(self, int sid, long long v, long long m):",
                "        if v != self.ctx.val[sid] or m != self.ctx.mask[sid]:",
                "            self.ctx.val[sid] = v",
                "            self.ctx.mask[sid] = m",
                "            self.ctx.dirty[sid] = 1",
            ]
        )

        lines.extend(
            [
                "",
                "    cpdef void drive_wide(self, int sid, object v, object m):",
                "        cdef int words = self.ctx.wide_words[sid]",
                "        cdef int offset = self.ctx.wide_offset[sid]",
                "        cdef int i, remaining_w, changed = 0",
                "        cdef unsigned long long word_v, word_m, tail_mask",
                "        cdef long long low_v, low_m",
                "        if words == 0:",
                "            self.drive(sid, <long long>v, <long long>m)",
                "            return",
                "        for i in range(words):",
                "            word_v = <unsigned long long>((v >> (i * 64)) & ((1 << 64) - 1))",
                "            word_m = <unsigned long long>((m >> (i * 64)) & ((1 << 64) - 1))",
                "            remaining_w = self.ctx.width[sid] - (i * 64)",
                "            tail_mask = _word_mask64(remaining_w)",
                "            word_v &= tail_mask",
                "            word_m &= tail_mask",
                "            if word_v != self.ctx.wide_val[offset + i] or word_m != self.ctx.wide_mask[offset + i]:",
                "                self.ctx.wide_val[offset + i] = word_v",
                "                self.ctx.wide_mask[offset + i] = word_m",
                "                changed = 1",
                "        low_v = <long long>self.ctx.wide_val[offset]",
                "        low_m = <long long>self.ctx.wide_mask[offset]",
                "        if low_v != self.ctx.val[sid] or low_m != self.ctx.mask[sid]:",
                "            self.ctx.val[sid] = low_v",
                "            self.ctx.mask[sid] = low_m",
                "            changed = 1",
                "        if changed:",
                "            self.ctx.dirty[sid] = 1",
            ]
        )

        # read method
        lines.extend(
            [
                "",
                "    cpdef tuple read(self, int sid):",
                "        return (self.ctx.val[sid], self.ctx.mask[sid])",
            ]
        )

        lines.extend(
            [
                "",
                "    cpdef tuple read_wide(self, int sid):",
                "        cdef int words = self.ctx.wide_words[sid]",
                "        cdef int offset = self.ctx.wide_offset[sid]",
                "        cdef int i",
                "        cdef object value = 0",
                "        cdef object mask = 0",
                "        if words == 0:",
                "            return self.read(sid)",
                "        for i in range(words - 1, -1, -1):",
                "            value = (value << 64) | self.ctx.wide_val[offset + i]",
                "            mask = (mask << 64) | self.ctx.wide_mask[offset + i]",
                "        return (value, mask)",
            ]
        )

        # snapshot method — capture current values for edge detection
        lines.extend(
            [
                "",
                "    cpdef void snapshot(self):",
                f"        memcpy(self._snap_v, self.ctx.val, {sn} * sizeof(long long))",
                f"        memcpy(self._snap_m, self.ctx.mask, {sn} * sizeof(long long))",
            ]
        )

        # refresh_data_snapshot method — called after a coro drives signals mid-timestep.
        # Settles continuous assigns (propagates driven signals through port connections),
        # then refreshes _snap_v/_snap_m so sequential RHS reads see the updated values,
        # while preserving the pre-timestep clock snapshot for correct edge detection.
        if self._seq_processes:
            clock_sids = sorted({sid for edges, _sens, _body in self._seq_processes for sid in edges})
            save_lines = [
                f"        cdef long long _sv_{s} = self._snap_v[{s}], _sm_{s} = self._snap_m[{s}]" for s in clock_sids
            ]
            restore_lines = [f"        self._snap_v[{s}] = _sv_{s}; self._snap_m[{s}] = _sm_{s}" for s in clock_sids]
            # Run one pass of all continuous assigns so that coro-driven signals
            # (e.g. bench STALL_REQ) propagate to port-connected submodule signals
            # (e.g. u_stall.STALL_REQ) before we snapshot.  Without this, the
            # snapshot captures the un-propagated value and sequential processes
            # see stale data at the posedge.
            settle_lines = [f"        cont_{i}(&self.ctx)" for i in range(len(self._processes))]
            lines.extend(
                [
                    "",
                    "    cpdef void refresh_data_snapshot(self):",
                    *save_lines,
                    *settle_lines,
                    f"        memcpy(self._snap_v, self.ctx.val, {sn} * sizeof(long long))",
                    f"        memcpy(self._snap_m, self.ctx.mask, {sn} * sizeof(long long))",
                    *restore_lines,
                ]
            )

        # step method
        lines.extend(
            [
                "",
                "    cdef void _raise_runtime_error(self):",
                "        if self.ctx.error_code == ERR_WHILE_LOOP_LIMIT:",
                f"            raise RuntimeError('While loop exceeded {_PROCESS_LOOP_LIMIT} iterations')",
                "        if self.ctx.error_code == ERR_FOREVER_LOOP_LIMIT:",
                f"            raise RuntimeError('Forever loop exceeded {_PROCESS_LOOP_LIMIT} iterations')",
                "        if self.ctx.error_code == ERR_DELTA_LIMIT:",
                f"            raise RuntimeError('Delta cycle limit ({self._delta_limit}) exceeded')",
                "",
                "    cpdef int step(self):",
                "        cdef int deltas",
                "        self.ctx.error_code = ERR_NONE",
                "        with nogil:",
                "            deltas = delta_loop(&self.ctx, self._snap_v, self._snap_m)",
                "        self._raise_runtime_error()",
                "        return deltas",
            ]
        )

        # set_time method
        lines.extend(
            [
                "",
                "    cpdef void set_time(self, long long t):",
                "        self.ctx.sim_time = t",
            ]
        )

        # Memory access methods
        if self._n_mems > 0:
            # mem_read(mid, addr) ΓåÆ (val, mask)
            lines.extend(
                [
                    "",
                    "    cpdef tuple mem_read(self, int mid, int addr):",
                ]
            )
            for mid in range(self._n_mems):
                elem_w, _depth = self._mem_info[mid]
                kw = "if" if mid == 0 else "elif"
                lines.append(f"        {kw} mid == {mid}:")
                if elem_w > _WORD_BITS:
                    words = self._mem_words(mid)
                    lines.extend(
                        [
                            "            v = 0",
                            "            m = 0",
                            f"            for i in range({words}):",
                            f"                v |= int(self.ctx.wide_mem_{mid}_val[addr * {words} + i]) << (i * 64)",
                            f"                m |= int(self.ctx.wide_mem_{mid}_mask[addr * {words} + i]) << (i * 64)",
                            "            return (v, m)",
                        ]
                    )
                else:
                    lines.append(f"            return (self.ctx.mem_{mid}_val[addr], self.ctx.mem_{mid}_mask[addr])")
            lines.append("        return (0, -1)")
            # mem_write(mid, addr, val, mask)
            lines.extend(
                [
                    "",
                    "    cpdef void mem_write(self, int mid, int addr, long long v, long long m):",
                ]
            )
            for mid in range(self._n_mems):
                marker_sid = self._mem_marker_sigs[mid]
                elem_w, _depth = self._mem_info[mid]
                kw = "if" if mid == 0 else "elif"
                lines.append(f"        {kw} mid == {mid}:")
                if elem_w > _WORD_BITS:
                    words = self._mem_words(mid)
                    lines.extend(
                        [
                            f"            for i in range({words}):",
                            f"                self.ctx.wide_mem_{mid}_val[addr * {words} + i] = <unsigned long long>0",
                            f"                self.ctx.wide_mem_{mid}_mask[addr * {words} + i] = _word_mask64(MEM_{mid}_WIDTH - (i * 64))",
                        ]
                    )
                else:
                    lines.append(f"            self.ctx.mem_{mid}_val[addr] = v")
                    lines.append(f"            self.ctx.mem_{mid}_mask[addr] = m")
                lines.append(f"            self.ctx.val[{marker_sid}] ^= 1")
                lines.append(f"            self.ctx.dirty[{marker_sid}] = 1")
            lines.extend(
                [
                    "",
                    "    cpdef void mem_write_wide(self, int mid, int addr, object v, object m):",
                ]
            )
            for mid in range(self._n_mems):
                marker_sid = self._mem_marker_sigs[mid]
                elem_w, _depth = self._mem_info[mid]
                kw = "if" if mid == 0 else "elif"
                lines.append(f"        {kw} mid == {mid}:")
                if elem_w > _WORD_BITS:
                    words = self._mem_words(mid)
                    lines.extend(
                        [
                            f"            for i in range({words}):",
                            f"                self.ctx.wide_mem_{mid}_val[addr * {words} + i] = <unsigned long long>((v >> (i * 64)) & ((1 << 64) - 1))",
                            f"                self.ctx.wide_mem_{mid}_mask[addr * {words} + i] = <unsigned long long>((m >> (i * 64)) & ((1 << 64) - 1))",
                        ]
                    )
                else:
                    lines.append(f"            self.ctx.mem_{mid}_val[addr] = <long long>v")
                    lines.append(f"            self.ctx.mem_{mid}_mask[addr] = <long long>m")
                lines.append(f"            self.ctx.val[{marker_sid}] ^= 1")
                lines.append(f"            self.ctx.dirty[{marker_sid}] = 1")

        # batch_run method ΓÇö multi-cycle execution entirely in C
        sn = max(self._n_sigs, 1)
        lines.extend(
            [
                "",
                "    cpdef int batch_run(self, int cycles, int clk_sid,",
                "                        int n_events=0, int[::1] ev_cycles=None,",
                "                        int[::1] ev_sids=None, long long[::1] ev_vals=None):",
                "        cdef int i, ev_idx = 0, cycles_run = cycles",
                f"        cdef long long sv[{sn}]",
                f"        cdef long long sm[{sn}]",
                "        self.ctx.error_code = ERR_NONE",
                "        cdef int ev_applied",
                "        with nogil:",
                "            for i in range(cycles):",
                "                # Apply any scheduled events for this cycle",
                "                ev_applied = 0",
                "                while ev_idx < n_events and ev_cycles[ev_idx] == i:",
                "                    self.ctx.val[ev_sids[ev_idx]] = ev_vals[ev_idx]",
                "                    self.ctx.mask[ev_sids[ev_idx]] = 0",
                "                    self.ctx.dirty[ev_sids[ev_idx]] = 1",
                "                    ev_applied = 1",
                "                    ev_idx += 1",
                "                # Settle: propagate event through continuous assigns",
                "                # before snapshotting so port wiring (e.g. DUT rst port",
                "                # driven by bench rst reg) reflects the event in sv[].",
                "                if ev_applied:",
                f"                    memcpy(sv, self.ctx.val, {sn} * sizeof(long long))",
                f"                    memcpy(sm, self.ctx.mask, {sn} * sizeof(long long))",
                "                    delta_loop(&self.ctx, sv, sm)",
                "                    if self.ctx.error_code != ERR_NONE:",
                "                        cycles_run = i + 1",
                "                        break",
                "                # Snapshot before posedge",
                f"                memcpy(sv, self.ctx.val, {sn} * sizeof(long long))",
                f"                memcpy(sm, self.ctx.mask, {sn} * sizeof(long long))",
                "                # Posedge: drive clk high",
                "                self.ctx.val[clk_sid] = 1",
                "                self.ctx.mask[clk_sid] = 0",
                "                self.ctx.dirty[clk_sid] = 1",
                "                delta_loop(&self.ctx, sv, sm)",
                "                if self.ctx.error_code != ERR_NONE:",
                "                    cycles_run = i + 1",
                "                    break",
                "                if self.ctx.finished:",
                "                    cycles_run = i + 1",
                "                    break",
                "                # Snapshot before negedge",
                f"                memcpy(sv, self.ctx.val, {sn} * sizeof(long long))",
                f"                memcpy(sm, self.ctx.mask, {sn} * sizeof(long long))",
                "                # Negedge: drive clk low",
                "                self.ctx.val[clk_sid] = 0",
                "                self.ctx.mask[clk_sid] = 0",
                "                self.ctx.dirty[clk_sid] = 1",
                "                delta_loop(&self.ctx, sv, sm)",
                "                if self.ctx.error_code != ERR_NONE:",
                "                    cycles_run = i + 1",
                "                    break",
                "                if self.ctx.finished:",
                "                    cycles_run = i + 1",
                "                    break",
                "        self._raise_runtime_error()",
                "        return cycles_run",
            ]
        )

        # drain_output method ΓÇö reads the output buffer and returns bytes
        lines.extend(
            [
                "",
                "    cpdef bytes drain_output(self):",
                "        cdef int n = self.ctx.out_count",
                "        if n == 0:",
                "            return b''",
                "        self.ctx.out_count = 0",
                "        return self.ctx.out_buf[:n]",
            ]
        )

        # is_finished method ΓÇö check if $finish was called
        lines.extend(
            [
                "",
                "    cpdef bint is_finished(self):",
                "        return self.ctx.finished != 0",
            ]
        )

        return "\n".join(lines)
