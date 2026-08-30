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
from veriforge.model.expressions import Identifier
from veriforge.model.statements import BlockingAssign
from veriforge.sim.compiled._gen_narrow_accessors import _gen_narrow_accessor_code
from veriforge.sim.compiled._gen_narrow_stage import _gen_narrow_stage_code
from veriforge.sim.compiled._gen_narrow_assign import _gen_narrow_assign_code
from veriforge.sim.compiled._gen_narrow_tail import _gen_narrow_tail_code
from veriforge.sim.compiled._gen_wide_section import _GenWideSectionsMixin


_BLOCKING_WRITE_RE = re.compile(r"^\s*c\.val\[(\d+)\]\s*=(?!=)")
_NARROW_LHS_RE = re.compile(r"^\s*_set_(?:val|mask)_word\s*\(\s*c\s*,\s*(\d+)\s*,")

# Memory-element counterparts of the two patterns above -- see
# _seq_body_to_sv_reads's "Memories" docstring section for why memories need
# their own (coarser, per-mid rather than per-address) taint tracking.
_MEM_BLOCKING_WRITE_RE = re.compile(r"^\s*c\.(?:wide_)?mem_(\d+)_val\[[^\]]*\]\s*=(?!=)")
_MEM_ASSIGN_HELPER_RE = re.compile(r"_wmem(\d+)_assign_insert")
_MEM_VAL_RE = re.compile(r"c\.mem_(\d+)_val\[")
_MEM_MASK_RE = re.compile(r"c\.mem_(\d+)_mask\[")
_WIDE_MEM_VAL_RE = re.compile(r"c\.wide_mem_(\d+)_val\[")
_WIDE_MEM_MASK_RE = re.compile(r"c\.wide_mem_(\d+)_mask\[")
_WMEM_EXTRACT_VAL_RE = re.compile(r"_wmem(\d+)_extract_val\(c,")
_WMEM_EXTRACT_MASK_RE = re.compile(r"_wmem(\d+)_extract_mask\(c,")

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


def _seq_body_to_sv_reads(
    body_lines: list[str], async_sids: set[int] | None = None, func_internal_sids: set[int] | None = None
) -> list[str]:
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

    **Exception** — signals listed in *async_sids* (every one of THIS process's own
    posedge/negedge sensitivity/trigger signals -- the ordinary clock included, not
    just negedge/async-reset ones as the name suggests) must also use c.val[] not
    sv[], because by the time this body actually runs, that signal's edge has
    already genuinely happened -- sv[] still holds its PRE-transition value (that's
    the whole point of taking the snapshot before flipping it, so step()'s own
    separate edge-DETECTION logic can compare old-vs-new), but any read of the
    trigger signal FROM WITHIN the body it triggered must see the value that
    triggered it. Originally implemented only for negedge sensitivity signals
    (async reset inputs: reading sv[rst_n] inside ``if (!rst_n)`` on the negedge
    that fires this body would evaluate to 1 (not reset), causing the else branch
    to execute instead of the reset path) -- confirmed the identical gap also hits
    the ordinary posedge clock itself: ``always @(posedge clk) o <= clk;`` gave
    ``o <= 0`` (sv[]'s stale pre-edge value) instead of the correct ``o <= 1``.

    **Exception** — signals listed in *func_internal_sids* (a user-defined function's own
    port/local/return-variable signals -- see ``_gen_user_functions``) must also use
    c.val[]/c.mask[] not sv[]/sm[], for a different reason than blocking-written locals:
    their true value is written DIRECTLY by a ``_user_func_XXX(...)`` call embedded
    inline in an expression (not a standalone ``c.val[N] = ...`` statement this
    function's own blocking-write detection recognizes), so the first-pass "blocking-
    written" scan never sees them and would otherwise treat them as an ordinary,
    rewritable signal reference. `sv[]`/`sm[]` were never populated for these IDs at
    all (they are not real driven signals participating in this process's own posedge-
    snapshot convention), so rewriting a mask/value READ of one to `sv`/`sm` silently
    reads stale/zero-initialized shadow-array garbage instead of the value the function
    call that same line just computed. Confirmed against Icarus (cross-engine,
    `vm`/`vm-fast`/`reference` all already agreed) for `y <= {2{fn_sel1(a2[4], a5)}}`:
    the mask-side re-invocation's `c.mask[ret_sid]` read got rewritten to `sm[ret_sid]`,
    which was never written by anything, spuriously reading fully-x garbage regardless
    of the function's own correctly-computed (fully defined) result.

    Substitutions performed (safe because NBA writes use c.nba_val/c.nba_mask/c.nba_dirty):
      c.val[N]                      → sv[N]    (only if N not blocking-written or async here)
      c.mask[N]                     → sm[N]    (only if N not blocking-written or async here)
      _sig_extract_word_val(c, …)   → _sig_extract_word_val_sv(sv, sm, c, …)
      _sig_extract_word_mask(c, …)  → _sig_extract_word_mask_sv(sm, c, …)

    **Memories** — a 2-D packed array (e.g. an AXI-Stream `tdata` bus modeled
    per-lane for element addressing) is elaborated as a *memory*, not a
    plain signal, so it never goes through the `c.val[N]`/`_sig_extract_word_val`
    substitutions above at all -- it has its own read paths
    (`c.mem_{mid}_val[addr]`/`c.mem_{mid}_mask[addr]` for narrow elements,
    `_wmem{mid}_extract_val(c, addr, lsb)`/`_wmem{mid}_extract_mask(c, ...)`
    for wide ones) that, before this, had NO pre-edge snapshot concept
    whatsoever -- always live, regardless of process kind. This matters the
    same way it does for wide signals: a memory fed by a continuous assign
    (e.g. a wide port connection propagating a parent module's bits into a
    child's flattened packed-array port) can still be re-derived by further
    delta-loop settling within the same clock edge, so a sequential process
    reading it needs a value frozen at the edge, not the live one (confirmed
    against the real axis_pix_correction2 RTL: `axis_regslice.v`'s skid
    buffer reads its wide input port -- itself modeled as a memory for
    per-lane addressing -- and read the live, still-settling value under
    batch_run()-driven stimulus).

    Substituted the same way, but per memory id (`mid`) rather than per
    signal id, and *coarser*: if `mid` is blocking-written **anywhere** in
    this body (`c.mem_{mid}_val[...] = ...`, `c.wide_mem_{mid}_val[...] = ...`,
    or a call into a `_wmem{mid}_assign_*` blocking-write helper), every read
    of that mid in this body is left untouched (100% live, matching prior
    behavior) rather than tracking taint per-address -- a dynamic address
    expression makes exact per-element taint undecidable at codegen time, and
    this coarse rule is a pure no-op for any mid that already worked
    correctly (nothing to preserve if the mid is never written here):
      c.mem_{mid}_val[…]                    → c.mem_{mid}_snap_val[…]
      c.mem_{mid}_mask[…]                   → c.mem_{mid}_snap_mask[…]
      c.wide_mem_{mid}_val[…]               → c.wide_mem_{mid}_snap_val[…]
      c.wide_mem_{mid}_mask[…]              → c.wide_mem_{mid}_snap_mask[…]
      _wmem{mid}_extract_val(c, …)          → _wmem{mid}_extract_val_snap(c, …)
      _wmem{mid}_extract_mask(c, …)         → _wmem{mid}_extract_mask_snap(c, …)
    """
    # First pass: collect signal IDs that are blocking-written in this process.
    # Also seed tainted with async sensitivity signals (negedge signals) and
    # any user-defined function's own internal signals (see the docstring's
    # func_internal_sids exception above).
    tainted: set[int] = set(async_sids) if async_sids else set()
    if func_internal_sids:
        tainted.update(func_internal_sids)
    tainted_mem: set[int] = set()
    for line in body_lines:
        m = _BLOCKING_WRITE_RE.match(line)
        if m:
            tainted.add(int(m.group(1)))
        m = _NARROW_LHS_RE.match(line)
        if m:
            tainted.add(int(m.group(1)))
        m = _MEM_BLOCKING_WRITE_RE.match(line)
        if m:
            tainted_mem.add(int(m.group(1)))
        for m in _MEM_ASSIGN_HELPER_RE.finditer(line):
            tainted_mem.add(int(m.group(1)))

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

    def _sub_mem_val(m: re.Match) -> str:
        mid = int(m.group(1))
        return m.group(0) if mid in tainted_mem else f"c.mem_{mid}_snap_val["

    def _sub_mem_mask(m: re.Match) -> str:
        mid = int(m.group(1))
        return m.group(0) if mid in tainted_mem else f"c.mem_{mid}_snap_mask["

    def _sub_wide_mem_val(m: re.Match) -> str:
        mid = int(m.group(1))
        return m.group(0) if mid in tainted_mem else f"c.wide_mem_{mid}_snap_val["

    def _sub_wide_mem_mask(m: re.Match) -> str:
        mid = int(m.group(1))
        return m.group(0) if mid in tainted_mem else f"c.wide_mem_{mid}_snap_mask["

    def _sub_wmem_extract_val(m: re.Match) -> str:
        mid = int(m.group(1))
        if mid in tainted_mem:
            return m.group(0)
        return f"_wmem{mid}_extract_val_snap(c,"

    def _sub_wmem_extract_mask(m: re.Match) -> str:
        mid = int(m.group(1))
        if mid in tainted_mem:
            return m.group(0)
        return f"_wmem{mid}_extract_mask_snap(c,"

    result = []
    for line in body_lines:
        line = wide_val_re.sub(_sub_wide_val, line)
        line = wide_mask_re.sub(_sub_wide_mask, line)
        line = _WMEM_EXTRACT_VAL_RE.sub(_sub_wmem_extract_val, line)
        line = _WMEM_EXTRACT_MASK_RE.sub(_sub_wmem_extract_mask, line)
        line = _WIDE_MEM_VAL_RE.sub(_sub_wide_mem_val, line)
        line = _WIDE_MEM_MASK_RE.sub(_sub_wide_mem_mask, line)
        line = _MEM_VAL_RE.sub(_sub_mem_val, line)
        line = _MEM_MASK_RE.sub(_sub_mem_mask, line)
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

    def _mem_snap_memcpy_lines(self, indent: str) -> list[str]:
        """Lines that copy every memory's live val/mask arrays into their
        pre-edge snapshot (`mem_{mid}_snap_val`/`wide_mem_{mid}_snap_val`)
        counterparts. Emitted at every point that already snapshots
        `ctx.val`/`ctx.mask` into `sv`/`sm` (`snapshot()`,
        `refresh_data_snapshot()`, and each of `batch_run()`'s three
        snapshot points) -- see notes/roadmap.md "Wide-signal pre-edge
        snapshot gap" for why a 2-D packed array (elaborated as a `memory`
        for per-element addressing, not a plain signal) needs this too: a
        memory fed by a continuous assign (e.g. a wide port connection) can
        still be re-derived by further delta-loop settling within the same
        clock edge, so a sequential process reading it needs the same
        frozen-at-the-edge value narrow/wide signals get from sv[]/sm[]/
        wide_snap_val/wide_snap_mask.
        """
        lines: list[str] = []
        for mid in range(self._n_mems):
            elem_w, depth = self._mem_info[mid]
            if elem_w > _WORD_BITS:
                words = self._mem_words(mid)
                lines.append(
                    f"{indent}memcpy(self.ctx.wide_mem_{mid}_snap_val, self.ctx.wide_mem_{mid}_val,"
                    f" {depth * words} * sizeof(unsigned long long))"
                )
                lines.append(
                    f"{indent}memcpy(self.ctx.wide_mem_{mid}_snap_mask, self.ctx.wide_mem_{mid}_mask,"
                    f" {depth * words} * sizeof(unsigned long long))"
                )
            else:
                lines.append(
                    f"{indent}memcpy(self.ctx.mem_{mid}_snap_val, self.ctx.mem_{mid}_val, {depth} * sizeof(long long))"
                )
                lines.append(
                    f"{indent}memcpy(self.ctx.mem_{mid}_snap_mask, self.ctx.mem_{mid}_mask, {depth} * sizeof(long long))"
                )
        return lines

    def _nba_mem_queue_bound(self) -> int:
        """Compute a safe capacity for the NBA memory queues (see the call
        site in `_gen_constants` for the full story on why a hardcoded
        constant here silently corrupts unrelated memory for any design
        with a whole-array NBA copy wider than that constant).

        A single non-blocking statement (a whole-array copy, a
        concat-LHS memory member, or an ordinary `mem[addr] <= val;`)
        pushes at most one queue entry per element of the memories it
        targets, so the total number of elements across every memory in
        the design is a safe upper bound for how many entries any ONE
        such statement can push in a single delta-loop iteration. Several
        *different* processes could in principle all target the exact
        same memory in the same edge (legal, if unusual, SystemVerilog --
        "last NBA wins"), so this multiplies in a generous safety margin
        rather than assuming exactly one writer per memory; the memory
        cost of over-provisioning here is a few KB even for a large
        design, utterly negligible next to the cost of a silent
        out-of-bounds write into adjacent simulation state.

        Precise counting (walking the compiled process bodies for the
        exact number of push sites) would be tighter, but `self._processes`
        holds already-generated Cython lines while `self._seq_processes`
        still holds raw AST bodies at the point `_gen_constants` runs
        (`_gen_process_functions`, which lowers them to lines, runs
        later) -- so exact counting isn't available this early without
        reordering the generation pipeline.
        """
        total_mem_elements = sum(depth for _elem_w, depth in self._mem_info)
        return total_mem_elements * 4

    def _gen_header(self) -> str:
        return (
            "# cython: language_level=3, boundscheck=False, wraparound=False\n"
            "# cython: cdivision=True, initializedcheck=False, nonecheck=False\n"
            "\n"
            "from libc.string cimport memcpy\n"
            "from libc.math cimport pow\n"
            "from libc.stdio cimport snprintf"
        )

    def _gen_constants_core(self) -> str:
        """The fixed-count (independent of signal/memory count) `DEF`
        constants, plus per-memory `MEM_{mid}_WIDTH` -- everything actually
        referenced from within a process function body or one of the
        shared helper-function sections (`_gen_wide_mem_helpers` uses
        `MEM_{mid}_WIDTH`; nothing in a process function body references
        any `DEF` constant by name at all -- signal/memory ids are always
        interpolated as plain integer literals, e.g. `c.val[{sid}]` with
        `sid` a Python int, never a symbolic `DEF SIG_x` reference).

        Split out from `_gen_constants_signal_names` (below) specifically
        so `generate_to_files`'s split-compile path can duplicate ONLY
        this (small, O(1) in signal count) piece into every worker file,
        instead of the full `_gen_constants()` output -- which, for a
        design with thousands of signals (`DEF SIG_x`/`W_x`/
        `WIDE_WORDS_x`/`WIDE_OFFSET_x`, 4 lines each, used ONLY by
        `_gen_compiled_sim`'s `__init__`, itself main-file-only), made the
        split-compile path duplicate that O(n_sigs) block into every
        worker for no benefit -- confirmed empirically: a first cut that
        duplicated the *entire* `_gen_constants()` output into every
        worker file made total generated-code volume (and therefore
        overall compile wall time) WORSE than the unsplit baseline for a
        128-instance design, exactly backwards from this feature's whole
        point.
        """
        _wide_offsets, _wide_words, total_wide_words = self._wide_layout()
        lines = [f"DEF N_SIGS = {max(self._n_sigs, 1)}"]
        lines.append(f"DEF N_WIDE_WORDS = {max(total_wide_words, self._dynamic_max_wide_words, 1)}")
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
            # These two queues buffer whole-element (NBA_MEM_MAX) and
            # partial-bit-range (NBA_MEM_RANGE_MAX) non-blocking memory
            # writes queued during ONE delta-loop iteration (every
            # sequential process fires at most once per iteration, and the
            # queues are fully drained -- reset to 0 -- before the next
            # iteration begins, per the "if c.nba_pending: ... count = 0"
            # drain block below). A hardcoded "64 is surely enough" bound
            # silently overflows this FIXED-SIZE C array for any design
            # with a whole-array NBA copy/concat-LHS wider than 64
            # elements (e.g. a 128-lane AXI-Stream bus, `s0_pixels_tdata
            # <= axis_fifo_tdata;` with a 128-element memory) -- with NO
            # bounds check, the overflow silently corrupts whatever
            # memory follows this struct in the class layout (empirically
            # confirmed: it silently clobbered `_snap_v[]`/`_snap_m[]`
            # -- the pre-edge snapshot arrays `always_ff` bodies read via
            # `sv`/`sm` -- corrupting an unrelated COMBINATIONAL signal's
            # snapshotted value mid-iteration and causing a real design's
            # FIFO read pointer to advance one edge early). See
            # `_nba_mem_queue_bound` for how the real capacity is derived.
            nba_mem_bound = max(64, self._nba_mem_queue_bound())
            lines.append(f"DEF NBA_MEM_MAX = {nba_mem_bound}")
            lines.append(f"DEF NBA_MEM_RANGE_MAX = {nba_mem_bound}")
        for mid in range(self._n_mems):
            ew, _depth = self._mem_info[mid]
            lines.append(f"DEF MEM_{mid}_WIDTH = {ew}")
        return "\n".join(lines)

    def _gen_constants_signal_names(self) -> str:
        """The O(n_sigs)/O(n_mems) `DEF` constants -- symbolic per-signal
        names (`SIG_x`/`W_x`/`WIDE_WORDS_x`/`WIDE_OFFSET_x`) and the
        remaining per-memory ones (`MEM_{mid}_DEPTH`/`MEM_{mid}_WORDS`,
        `_WIDTH` itself being in `_gen_constants_core` since a shared
        helper function needs it) -- referenced ONLY from
        `_gen_compiled_sim` (the `CompiledSim` class's `__init__`), so
        needed in the main file only; see `_gen_constants_core`.
        """
        wide_offsets, wide_words, _total_wide_words = self._wide_layout()
        lines: list[str] = []
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
        # Memory constants (MEM_{mid}_WIDTH is in _gen_constants_core)
        for mid in range(self._n_mems):
            lines.append(f"DEF MEM_{mid}_DEPTH = {self._mem_info[mid][1]}")
            lines.append(f"DEF MEM_{mid}_WORDS = {self._mem_words(mid)}")
        return "\n".join(lines)

    def _gen_constants(self) -> str:
        return self._gen_constants_core() + "\n" + self._gen_constants_signal_names()

    def _struct_size_literals(self) -> dict[str, int]:
        """The literal (int) values behind the `DEF`-named array sizes used
        in the ``SimCtx`` field list -- computed exactly as `_gen_constants`
        computes them, factored out so both it and the plain-C-header
        renderer used for the split-compile path (`_gen_struct_extern_c`)
        stay in sync by construction rather than by two independently
        maintained copies of this arithmetic.
        """
        _wide_offsets, _wide_words, total_wide_words = self._wide_layout()
        literals = {
            "N_SIGS": max(self._n_sigs, 1),
            "N_WIDE_WORDS": max(total_wide_words, self._dynamic_max_wide_words, 1),
            "OUT_BUF_MAX": 65536,
        }
        if self._n_mems > 0:
            nba_mem_bound = max(64, self._nba_mem_queue_bound())
            literals["NBA_MEM_MAX"] = nba_mem_bound
            literals["NBA_MEM_RANGE_MAX"] = nba_mem_bound
        return literals

    def _struct_field_lines(self) -> list[str]:
        """The ``SimCtx`` field declarations, indented for use directly under
        a ``cdef struct SimCtx:``/``cdef extern from ...: cdef struct
        SimCtx:`` header line. Array sizes reference the `DEF`-named
        constants (`N_SIGS` etc.) -- valid Cython either way; see
        `_gen_struct`/`_gen_struct_extern`.
        """
        lines = [
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
            "    unsigned long long wide_snap_val[N_WIDE_WORDS]",
            "    unsigned long long wide_snap_mask[N_WIDE_WORDS]",
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
                lines.append(f"    unsigned long long wide_mem_{mid}_snap_val[{depth * words}]")
                lines.append(f"    unsigned long long wide_mem_{mid}_snap_mask[{depth * words}]")
            else:
                lines.append(f"    long long mem_{mid}_val[{depth}]")
                lines.append(f"    long long mem_{mid}_mask[{depth}]")
                lines.append(f"    long long mem_{mid}_snap_val[{depth}]")
                lines.append(f"    long long mem_{mid}_snap_mask[{depth}]")
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
        return lines

    def _gen_struct(self) -> str:
        return "\n".join(["cdef struct SimCtx:", *self._struct_field_lines()])

    def _gen_struct_extern(self, header_name: str) -> str:
        """Same fields as `_gen_struct`, but declared as living in the plain
        C header *header_name* (see `_gen_struct_extern_c`) instead of as a
        Cython-native struct. Used identically by the main file and every
        worker file in the split-compile path (`generate_to_files`) so that
        every file's ``SimCtx`` resolves to the exact same plain C type --
        a Cython-native `cdef struct` declared separately (even from
        byte-for-byte identical text) in two different ``.pyx`` files gets
        two distinct, incompatible mangled C struct tags, which very much
        matters here since worker files receive a ``SimCtx *`` from the
        main file across a real (non-inlined) C function call.
        """
        return "\n".join(
            [
                f'cdef extern from "{header_name}":',
                "    cdef struct SimCtx:",
                *(f"    {line}" for line in self._struct_field_lines()),
            ]
        )

    def _gen_struct_extern_c(self) -> str:
        """Plain C header text defining ``struct SimCtx`` for the
        split-compile path -- see `_gen_struct_extern`. Same field list as
        `_gen_struct`, with the `DEF`-named array sizes (`N_SIGS` etc.)
        substituted for their literal values (a plain ``.h`` file has no
        concept of Cython's `DEF`), via `_struct_size_literals` so the two
        never drift out of sync.
        """
        literals = self._struct_size_literals()
        body_lines = self._struct_field_lines()
        for name, value in literals.items():
            body_lines = [re.sub(rf"\b{name}\b", str(value), line) for line in body_lines]
        return "\n".join(
            [
                "#ifndef VERIFORGE_SIMCTX_H",
                "#define VERIFORGE_SIMCTX_H",
                "struct SimCtx {",
                *(f"    {line.strip()};" for line in body_lines),
                "};",
                "#endif",
            ]
        )

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

            # The generated `_user_func_XXX` call boundary below is
            # hardcoded to a single native `long long` per argument/
            # return -- there is no multi-word representation anywhere
            # in this ABI (the signature, the port-binding writes
            # (`c.val[sid] = arg_i_v & wmask(w)`, a single scalar write
            # regardless of the port's real width), and the return
            # statement (`return c.val[ret_sid] & wmask(ret_w)`) are ALL
            # single-word). A port or return wider than 64 bits is
            # therefore unconditionally unrepresentable through this
            # boundary -- not merely a routing gap the way the other
            # "wide value in narrow context" bugs fixed this wave were
            # (those were cases where CORRECT wide storage/computation
            # already existed elsewhere and just wasn't being reached;
            # here there is no wide storage for a function argument/
            # return to reach AT ALL). Confirmed against Icarus (cross-
            # engine, both a 64-bit and a 128-bit destination) for a
            # function with a 71-bit port: silently wrong on compiled
            # regardless of the calling context's own width, since the
            # port's OWN storage can only ever hold 64 bits. Properly
            # supporting this would mean redesigning the call ABI for
            # multi-word argument/return passing -- deliberately out of
            # scope here; fail loudly at compile time instead of
            # silently corrupting the port/return value.
            for port in func.ports:
                sid = self._signal_map[f"{prefix}.{port.name}"]
                w = self._signal_widths[sid]
                if w > _WORD_BITS:
                    raise NotImplementedError(
                        f"Compiled engine: user-defined function '{func.name}' has a port "
                        f"'{port.name}' ({w} bits) wider than {_WORD_BITS} bits, not yet "
                        f"supported for function arguments. Use engine='vm' or "
                        f"engine='reference' for this design, or narrow the port."
                    )
            if ret_w > _WORD_BITS:
                raise NotImplementedError(
                    f"Compiled engine: user-defined function '{func.name}' has a return "
                    f"width of {ret_w} bits, wider than {_WORD_BITS} bits, not yet "
                    f"supported for function return values. Use engine='vm' or "
                    f"engine='reference' for this design, or narrow the return width."
                )

            # Build parameter list. Each argument passes its VALUE and its
            # x/z MASK as a separate pair (`arg_{i}_v`, `arg_{i}_m}`) --
            # a single `long long` return/argument has no room for both,
            # and this call boundary previously carried ONLY the value,
            # silently discarding any x/z-ness in every argument before
            # it ever reached the function body (every port's mask was
            # hardcoded to 0 below, regardless of what the caller's
            # actual argument expression's mask was). Confirmed against
            # Icarus (cross-engine, `vm`/`vm-fast`/`reference` all
            # already agreed) for `fn_sub16s(a5, a5[35])` with `a5` fully
            # x: the correct result has fn_sub16s's own 16-bit return
            # width worth of x, but with the argument mask always
            # silently zeroed here, the subtraction inside the function
            # body saw two fully-DEFINED (looking) operands and computed
            # a spurious definite value instead.
            params = ", ".join(f"long long arg_{i}_v, long long arg_{i}_m" for i in range(len(func.ports)))
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
                parts.append(f"    c.val[{sid}] = arg_{i}_v & wmask({w})")
                parts.append(f"    c.mask[{sid}] = arg_{i}_m & wmask({w})")

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
                if any("_cdm" in ln for ln in body_lines):
                    parts.append("    cdef long long _cdm")
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

    def _collect_blocking_write_sids(self, block_body) -> set[int]:
        """Every signal id blocking-assigned (``=``, not ``<=``) anywhere in a
        process body, resolved via ``self._signal_map``.

        Computed once per seq body, *before* any statement is compiled, so
        that per-statement emitters needing to choose between a live-reading
        and a pre-edge-snapshot-reading helper for an NBA statement's signal
        RHS source (e.g. ``_wmem{mid}_stage_insert_signal_slice`` vs. its
        ``_sv``-suffixed twin -- see notes/roadmap.md "Wide-signal pre-edge
        snapshot gap") can make that decision at emission time instead of via
        `_seq_body_to_sv_reads`'s later text-level substitution, which can't
        safely parse these particular call sites' other (arbitrary-
        expression) arguments. Mirrors `_BLOCKING_WRITE_RE`'s scope exactly:
        only a plain (possibly hierarchical) identifier LHS resolves to a
        whole signal id here -- bit/range-select and memory-element targets
        don't taint a whole signal's value and are out of scope.
        """
        tainted: set[int] = set()
        for assign in block_body.find(BlockingAssign):
            target = assign.lhs
            if isinstance(target, Identifier):
                name = target.name
                if target.hierarchy:
                    name = ".".join(target.hierarchy) + "." + name
                sid = self._signal_map.get(name)
                if sid is not None:
                    tainted.add(sid)
        return tainted

    def _compile_always_body(self, block_body, *, is_seq: bool = False, edge_sids: set[int] | None = None) -> list[str]:
        """Compile one always-block body Statement to code lines on demand.

        Resets the expression-temporary counters so names are unique per function.
        Called from the process-function generators so the IR for each block is
        discarded as soon as its text has been written to disk.

        *is_seq* pre-computes `self._body_tainted_sids` (see
        `_collect_blocking_write_sids`) for statement emitters that need it;
        left `None` for cont/combo bodies, which have no `sv`/`sm` in scope
        at all and must never attempt the snapshot-reading path.

        *edge_sids* -- this seq process's own posedge/negedge trigger
        signal id(s) (`edges.keys()` from `_process_compiler.py`'s
        `_seq_processes` tuple) -- are unioned into `_body_tainted_sids` too,
        forcing a LIVE read (never the `sv`/`sm` pre-edge snapshot) for any
        read of the signal that triggered this very body. `sv[clk_sid]` is
        deliberately left at its PRE-edge value by `refresh_data_snapshot()`
        (`compiled_scheduler.py`) so `step()`'s own edge-DETECTION logic can
        compare old-vs-new and recognize the transition -- but by the time
        the body created by THIS function actually starts running, that
        transition has already happened for real, so `clk`'s live value is
        already 1 (for a posedge) and any read of `clk` from inside its own
        triggering body must see that, not the stale pre-edge snapshot the
        same array holds for unrelated purposes. Confirmed wrong directly:
        `always @(posedge clk) o <= clk;` gave `o <= 0` (the pre-edge value)
        instead of the correct `o <= 1` -- reference/vm/vm-fast all use a
        separate old-value slot just for edge detection and don't share this
        gap.
        """
        self._et_count = 0
        self._et_node_masks = {}
        self._et_node_vals = {}
        if is_seq:
            self._body_tainted_sids = self._collect_blocking_write_sids(block_body)
            if edge_sids:
                self._body_tainted_sids |= edge_sids
        else:
            self._body_tainted_sids = None
        return self._emit_stmt(block_body, indent=1)

    def _gen_process_functions(self) -> str:
        parts: list[str] = []

        # Continuous assign functions
        if not self._processes and not self._combo_processes and not self._seq_processes:
            return "# No process functions"

        # Pre-compute per-seq-process edge-trigger sids (every posedge AND
        # negedge sensitivity signal, ordinary clocks included). These must
        # NOT be rewritten to sv[] in the body: by the time the body runs
        # the edge has already genuinely happened, but sv[] still holds the
        # PRE-transition value -- see `_seq_body_to_sv_reads`'s docstring.
        seq_negedge_sids = [{sid for sid, _et in edges.items()} for edges, _, _ in self._seq_processes]
        # Every user-defined function's own internal signals (ports, return
        # variable, locals) must also never be rewritten to sv[]/sm[] -- see
        # `_seq_body_to_sv_reads`'s own docstring (func_internal_sids
        # exception) for the concrete Icarus-confirmed repro this avoids.
        func_internal_sids: set[int] = set()
        for func in self._function_map.values():
            prefix = f"__func_{func.name}"
            names = [func.name, *(p.name for p in func.ports), *(v.name for v in func.locals)]
            for name in names:
                sid = self._signal_map.get(f"{prefix}.{name}")
                if sid is not None:
                    func_internal_sids.add(sid)

        process_groups = (
            ("cont", (body_lines for _sens, body_lines in self._processes), False, False),
            ("combo", (self._compile_always_body(body) for _sens, body in self._combo_processes), True, False),
            (
                "seq",
                (
                    self._compile_always_body(body, is_seq=True, edge_sids=set(_edges))
                    for _edges, _sens, body in self._seq_processes
                ),
                True,
                True,
            ),
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
                        body_lines = _seq_body_to_sv_reads(body_lines, async_sids, func_internal_sids)
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
                    if "_cdm" in joined:
                        decls.append("    cdef long long _cdm")
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

    def _gen_process_functions_to(self, write_fn, *, route_fn=None) -> None:
        """Stream process functions one at a time via *write_fn*.

        Produces byte-for-byte identical output to ``_gen_process_functions()``
        but never accumulates more than one process function's body lines in
        memory simultaneously.  Suitable for designs where the total process
        function section would otherwise require tens of GB to build as a
        single string.

        *write_fn* is called with successive ``str`` fragments whose
        concatenation equals the full section text.

        *route_fn*, if given, is called as ``route_fn(prefix, i)`` (``prefix``
        one of ``"cont"``/``"combo"``/``"seq"``, ``i`` the function's index
        within its group) before each function is emitted, and must return
        either ``None`` (emit to *write_fn* as ``cdef inline``, the default
        behavior) or a ``(target_write_fn, is_public)`` pair -- used by
        :meth:`~.codegen.CythonCodegen.generate_to_files` to split a large
        design's process functions across multiple ``.pyx`` files for
        parallel compilation (see that method's docstring for why). A
        function routed to a non-``write_fn`` target is emitted as
        ``cdef public`` instead of ``cdef inline`` -- `inline` and
        cross-translation-unit (`public`) linkage don't mix cleanly, and a
        function living in its own file has no same-file call site to
        benefit from inlining anyway.
        """
        if not self._processes and not self._combo_processes and not self._seq_processes:
            write_fn("# No process functions")
            return

        # Every posedge AND negedge sensitivity/trigger signal per seq
        # process (ordinary clocks included) -- see `_seq_body_to_sv_reads`'s
        # docstring for why these must never be rewritten to sv[]/sm[].
        seq_negedge_sids = [{sid for sid, _et in edges.items()} for edges, _, _ in self._seq_processes]
        # Mirrors `_gen_process_functions`'s identical precomputation -- see
        # `_seq_body_to_sv_reads`'s docstring (func_internal_sids exception)
        # for the rationale; this method must stay byte-for-byte identical
        # to that one.
        func_internal_sids: set[int] = set()
        for func in self._function_map.values():
            prefix = f"__func_{func.name}"
            names = [func.name, *(p.name for p in func.ports), *(v.name for v in func.locals)]
            for name in names:
                sid = self._signal_map.get(f"{prefix}.{name}")
                if sid is not None:
                    func_internal_sids.add(sid)

        process_groups = (
            ("cont", (body_lines for _sens, body_lines in self._processes), False, False),
            ("combo", (self._compile_always_body(body) for _sens, body in self._combo_processes), True, False),
            (
                "seq",
                (
                    self._compile_always_body(body, is_seq=True, edge_sids=set(_edges))
                    for _edges, _sens, body in self._seq_processes
                ),
                True,
                True,
            ),
        )
        first_func_seen: set[int] = set()  # ids of streams that already received their first chunk
        for prefix, body_groups, emit_pass_when_empty, use_sv in process_groups:
            for i, body_lines in enumerate(body_groups):
                routed = route_fn(prefix, i) if route_fn is not None else None
                target_write_fn, is_public = (write_fn, False) if routed is None else routed
                qualifier = "public" if is_public else "inline"

                func_parts: list[str] = []
                if use_sv:
                    func_parts.append(
                        f"cdef {qualifier} void {prefix}_{i}(SimCtx *c, long long *sv, long long *sm) noexcept nogil:"
                    )
                else:
                    func_parts.append(f"cdef {qualifier} void {prefix}_{i}(SimCtx *c) noexcept nogil:")

                if body_lines:
                    decls: list[str] = []
                    if use_sv:
                        async_sids = seq_negedge_sids[i] if prefix == "seq" else None
                        body_lines = _seq_body_to_sv_reads(body_lines, async_sids, func_internal_sids)
                    hoisted_cdefs, body_lines = _hoist_inline_cdefs(body_lines)
                    decls.extend(hoisted_cdefs)
                    joined = "\n".join(body_lines)
                    if "_clhs" in joined:
                        decls.append("    cdef long long _clhs")
                    if "_cdv" in joined:
                        decls.append("    cdef long long _cdv")
                    if "_cdm" in joined:
                        decls.append("    cdef long long _cdm")
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
                # Tracked per-target (not globally) so each routed-to file's own
                # first chunk is unprefixed, same as the single-stream case.
                stream_key = id(target_write_fn)
                if stream_key not in first_func_seen:
                    target_write_fn(chunk)
                    first_func_seen.add(stream_key)
                else:
                    target_write_fn("\n" + chunk)

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
            "            self.ctx.wide_snap_val[i] = 0",
            "            self.ctx.wide_snap_mask[i] = 0",
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
                lines.append(f"            self.ctx.wide_mem_{mid}_snap_val[i] = 0")
                lines.append(f"            self.ctx.wide_mem_{mid}_snap_mask[i] = 0")
            else:
                lines.append(f"        for i in range({depth}):")
                lines.append(f"            self.ctx.mem_{mid}_val[i] = 0")
                lines.append(f"            self.ctx.mem_{mid}_mask[i] = wmask(MEM_{mid}_WIDTH)")
                lines.append(f"            self.ctx.mem_{mid}_snap_val[i] = 0")
                lines.append(f"            self.ctx.mem_{mid}_snap_mask[i] = 0")
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
            if any("_cdm" in ln for ln in self._initial_lines):
                lines.append("        cdef long long _cdm")
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

        # NOTE: a "bootstrap combinational always blocks once at
        # construction" fix (mirroring `sim/scheduler.py`'s and `sim/vm/
        # vm_scheduler.py`'s `elaborate()`) was attempted here and
        # REVERTED -- calling `delta_loop()` unconditionally in `__init__`
        # runs EVERY combinational/continuous process immediately at
        # construction, before any caller has driven real stimulus. That
        # broke existing, deliberate tests (`test_while_loop_limit_raises`
        # et al. in `tests/test_sim/compiled/test_execution.py`, which
        # construct a `CompiledSim` for a module with a genuinely infinite
        # combinational loop and expect construction to succeed, only
        # raising the loop-limit error later once explicitly triggered)
        # and caused native crashes in division-heavy designs (dividing
        # against genuinely undriven/uninitialized operands at
        # construction time hit undefined behavior in the generated C,
        # not just a wrong Verilog x-result). The actual fix -- deferring
        # the bootstrap to the first settle() call instead of `__init__`
        # -- lives in `CompiledScheduler.settle()` (compiled_scheduler.py),
        # using the `mark_all_dirty()` method below to force every signal's
        # dirty flag on the first settle() call only, letting the existing
        # delta_loop() trigger machinery run every combinational/continuous
        # process once from there. `CompiledScheduler.run()` never needed
        # this: it already calls `self._sim.step()` unconditionally on
        # every call, and delta_loop()'s own "it == 0 and not changed"
        # fallback (see `_gen_delta_loop`) bootstraps everything on the
        # first such call for free, before anything has been marked dirty.

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
                "    cpdef void mark_all_dirty(self):",
                "        cdef int i",
                "        for i in range(N_SIGS):",
                "            self.ctx.dirty[i] = 1",
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
                "        memcpy(self.ctx.wide_snap_val, self.ctx.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
                "        memcpy(self.ctx.wide_snap_mask, self.ctx.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
                *self._mem_snap_memcpy_lines("        "),
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
                    "        memcpy(self.ctx.wide_snap_val, self.ctx.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
                    "        memcpy(self.ctx.wide_snap_mask, self.ctx.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
                    *self._mem_snap_memcpy_lines("        "),
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
        # Per-mid narrow-memory-element write dispatch, mirroring
        # `mem_write` above -- lets `batch_run`'s events schedule writes
        # into a memory-shaped port (e.g. a wide AXI-Stream `tdata` bus
        # modeled as a 2-D packed array, addressed element-wise as
        # `port[i]`) the same way plain-signal events already work,
        # entirely inside the nogil loop (no per-event Python call). Wide
        # (>64-bit) memory elements aren't supported by this event path
        # (documented limitation, not yet needed).
        narrow_mem_ids = [mid for mid in range(self._n_mems) if self._mem_info[mid][0] <= _WORD_BITS]
        mem_event_dispatch: list[str] = []
        for j, mid in enumerate(narrow_mem_ids):
            marker_sid = self._mem_marker_sigs[mid]
            kw = "if" if j == 0 else "elif"
            mem_event_dispatch.extend(
                [
                    f"                    {kw} ev_mem_mids[mem_ev_idx] == {mid}:",
                    f"                        self.ctx.mem_{mid}_val[ev_mem_addrs[mem_ev_idx]] = ev_mem_vals[mem_ev_idx]",
                    f"                        self.ctx.mem_{mid}_mask[ev_mem_addrs[mem_ev_idx]] = 0",
                    f"                        self.ctx.val[{marker_sid}] ^= 1",
                    f"                        self.ctx.dirty[{marker_sid}] = 1",
                ]
            )
        lines.extend(
            [
                "",
                "    cpdef int batch_run(self, int cycles, int clk_sid,",
                "                        int n_events=0, int[::1] ev_cycles=None,",
                "                        int[::1] ev_sids=None, long long[::1] ev_vals=None,",
                "                        int n_mem_events=0, int[::1] ev_mem_cycles=None,",
                "                        int[::1] ev_mem_mids=None, int[::1] ev_mem_addrs=None,",
                "                        long long[::1] ev_mem_vals=None):",
                "        cdef int i, ev_idx = 0, mem_ev_idx = 0, cycles_run = cycles",
                f"        cdef long long sv[{sn}]",
                f"        cdef long long sm[{sn}]",
                "        self.ctx.error_code = ERR_NONE",
                "        cdef int ev_applied",
                "        with nogil:",
                "            if self.ctx.val[clk_sid] != 0:",
                "                # The clock was left high entering this call (e.g. by",
                "                # prior reactive stepping) -- force a real negedge here",
                "                # so the loop's first posedge below is a genuine 0->1",
                "                # transition. Without this, driving clk high when it's",
                "                # already 1 is a no-op (no edge detected), silently",
                "                # dropping the caller's first requested cycle and",
                "                # shifting every subsequent edge by one (see",
                '                # notes/roadmap.md "batch_run() first-call clock-state").',
                *(f"                cont_{i}(&self.ctx)" for i in range(len(self._processes))),
                f"                memcpy(sv, self.ctx.val, {sn} * sizeof(long long))",
                f"                memcpy(sm, self.ctx.mask, {sn} * sizeof(long long))",
                "                memcpy(self.ctx.wide_snap_val, self.ctx.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
                "                memcpy(self.ctx.wide_snap_mask, self.ctx.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
                *self._mem_snap_memcpy_lines("                "),
                "                self.ctx.val[clk_sid] = 0",
                "                self.ctx.mask[clk_sid] = 0",
                "                self.ctx.dirty[clk_sid] = 1",
                "                delta_loop(&self.ctx, sv, sm)",
                "                if self.ctx.error_code != ERR_NONE:",
                "                    cycles_run = 0",
                "            for i in range(cycles if self.ctx.error_code == ERR_NONE else 0):",
                "                # Apply any scheduled events for this cycle",
                "                ev_applied = 0",
                "                while ev_idx < n_events and ev_cycles[ev_idx] == i:",
                "                    self.ctx.val[ev_sids[ev_idx]] = ev_vals[ev_idx]",
                "                    self.ctx.mask[ev_sids[ev_idx]] = 0",
                "                    self.ctx.dirty[ev_sids[ev_idx]] = 1",
                "                    ev_applied = 1",
                "                    ev_idx += 1",
                "                while mem_ev_idx < n_mem_events and ev_mem_cycles[mem_ev_idx] == i:",
                *(mem_event_dispatch if mem_event_dispatch else ["                    pass"]),
                "                    ev_applied = 1",
                "                    mem_ev_idx += 1",
                "                # Settle: propagate event through continuous assigns",
                "                # before snapshotting so port wiring (e.g. DUT rst port",
                "                # driven by bench rst reg) reflects the event in sv[].",
                "                if ev_applied:",
                f"                    memcpy(sv, self.ctx.val, {sn} * sizeof(long long))",
                f"                    memcpy(sm, self.ctx.mask, {sn} * sizeof(long long))",
                "                    memcpy(self.ctx.wide_snap_val, self.ctx.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
                "                    memcpy(self.ctx.wide_snap_mask, self.ctx.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
                *self._mem_snap_memcpy_lines("                    "),
                "                    delta_loop(&self.ctx, sv, sm)",
                "                    if self.ctx.error_code != ERR_NONE:",
                "                        cycles_run = i + 1",
                "                        break",
                "                # Settle combinational logic (cont_N) before EACH",
                "                # edge's snapshot, exactly like refresh_data_snapshot()",
                "                # does for the reactive step()/settle() path -- without",
                "                # this, a signal driven via this cycle's events (or",
                "                # reactively before this batch_run() call) that only",
                "                # reaches an always_ff body through an intervening",
                "                # continuous assign (e.g. input padding/format-",
                "                # conversion logic) is captured in sv[]/sm[] at its",
                "                # STALE pre-drive value, not the freshly-propagated one",
                "                # -- confirmed wrong against the real axis_pix_correction2",
                "                # RTL (input pixel data padded via a continuous assign",
                "                # before reaching axis_row_correct's own registers):",
                "                # driving stimulus via batch_run (with or without this",
                "                # method's own events -- reactive drive-then-batch_run(1)",
                "                # hit the identical bug) silently fed every downstream",
                "                # always_ff its garbage/stale pre-drive input forever,",
                "                # vs. the same stimulus working correctly via ordinary",
                "                # bench.step()/settle().",
                "                # Snapshot before posedge",
                *(f"                cont_{i}(&self.ctx)" for i in range(len(self._processes))),
                f"                memcpy(sv, self.ctx.val, {sn} * sizeof(long long))",
                f"                memcpy(sm, self.ctx.mask, {sn} * sizeof(long long))",
                "                memcpy(self.ctx.wide_snap_val, self.ctx.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
                "                memcpy(self.ctx.wide_snap_mask, self.ctx.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
                *self._mem_snap_memcpy_lines("                "),
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
                *(f"                cont_{i}(&self.ctx)" for i in range(len(self._processes))),
                f"                memcpy(sv, self.ctx.val, {sn} * sizeof(long long))",
                f"                memcpy(sm, self.ctx.mask, {sn} * sizeof(long long))",
                "                memcpy(self.ctx.wide_snap_val, self.ctx.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
                "                memcpy(self.ctx.wide_snap_mask, self.ctx.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
                *self._mem_snap_memcpy_lines("                "),
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
