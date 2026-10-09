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

from veriforge._env import get_env
from veriforge.sim.compiled._codegen_utils import resolve_delta_engine
from veriforge.sim.compiled._codegen_utils import (
    _WORD_BITS,
    _PROCESS_LOOP_LIMIT,
    _safe_const_name,
    _safe_ident,
    _cy_u64_hex,
    _const_int,
)
from veriforge.model.expressions import BitSelect, Concatenation, Expression, Identifier, PartSelect, RangeSelect
from veriforge.model.ports import PortDirection
from veriforge.model.statements import BlockingAssign
from veriforge.sim.compiled._gen_narrow_accessors import _gen_narrow_accessor_code
from veriforge.sim.compiled._gen_narrow_stage import _gen_narrow_stage_code
from veriforge.sim.compiled._gen_narrow_assign import _gen_narrow_assign_code
from veriforge.sim.compiled._gen_narrow_tail import _gen_narrow_tail_code
from veriforge.sim.compiled._gen_wide_section import _GenWideSectionsMixin


# Upper bound on a memory's snapshot journal (see CodeGenerator._mem_journal_cap).
_MEM_JOURNAL_MAX = 256

_BLOCKING_WRITE_RE = re.compile(r"^\s*c\.val\[(\d+)\]\s*=(?!=)")
_NARROW_LHS_RE = re.compile(r"^\s*_set_(?:val|mask)_word\s*\(\s*c\s*,\s*(\d+)\s*,")
# Wide-signal blocking writes go through helpers whose first argument after
# `c` is the destination sid. Without these, a read of a wide signal after a
# blocking write to it in the same body (`rz = x; rz[100] = ~rz[100];`) was
# redirected to the pre-edge snapshot.
_WIDE_BLOCKING_WRITE_RE = re.compile(r"\b(?:_whole_assign_\w+|wide_store_signal)\(\s*c\s*,\s*(\d+)\s*,")

# Memory-element counterparts of the two patterns above -- see
# _seq_body_to_sv_reads's "Memories" docstring section for why memories need
# their own (coarser, per-mid rather than per-address) taint tracking.
_MEM_BLOCKING_WRITE_START_RE = re.compile(r"^\s*c\.(?:wide_)?mem_(\d+)_val\[")
_ASSIGN_OP_RE = re.compile(r"\s*=(?!=)")


def _mem_blocking_write_mid(line: str) -> int | None:
    """The memory id a line blocking-writes (``c.mem_{mid}_val[...] = ...`` or
    the ``wide_mem`` form), else None. The index is bracket-matched, since it
    usually contains nested subscripts (``c.wide_mem_0_val[(c.val[2]) * 2] =``)
    -- a regex with a bracket-free index pattern missed every such write, so
    the memory's reads (and the writes themselves) were redirected to its
    pre-edge snapshot."""
    m = _MEM_BLOCKING_WRITE_START_RE.match(line)
    if m is None:
        return None
    depth = 1
    for i in range(m.end(), len(line)):
        ch = line[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return int(m.group(1)) if _ASSIGN_OP_RE.match(line, i + 1) else None
    return None


_MEM_ASSIGN_HELPER_RE = re.compile(r"_wmem(\d+)_assign_insert")
_MEM_VAL_RE = re.compile(r"c\.mem_(\d+)_val\[")
_MEM_MASK_RE = re.compile(r"c\.mem_(\d+)_mask\[")
_WIDE_MEM_VAL_RE = re.compile(r"c\.wide_mem_(\d+)_val\[")
_WIDE_MEM_MASK_RE = re.compile(r"c\.wide_mem_(\d+)_mask\[")
_WMEM_EXTRACT_VAL_RE = re.compile(r"_wmem(\d+)_extract_val\(c,")
_WMEM_EXTRACT_MASK_RE = re.compile(r"_wmem(\d+)_extract_mask\(c,")

# Maximum number of signal IDs in one sensitivity-check expression. A signal
# contributes both trigger[] and dirty[] terms in the delta-loop dispatch.
_MAX_INLINE_SENS = 6


_DIRTY_WRITE_RE = re.compile(r"\bmark_dirty\(c,\s*(\d+)\)")


def _cont_dependency_order(processes: list) -> tuple[list[int], bool]:
    """Indices of continuous-assign processes in dependency (topological) order.

    Edge ``i -> j`` when ``i`` writes a signal in ``j``'s sensitivity set.
    Writes are recovered from the ``mark_dirty(c, N)`` calls in the generated
    body (writes through shared helpers with a runtime sid aren't visible here).  This only affects *ordering* (performance): ``delta_loop`` still
    iterates to a fixpoint, so an imprecise write set can never change
    results.  Ties keep declaration order; nodes on a combinational cycle are
    appended in declaration order.  Returns ``(order, acyclic)``.
    """
    n = len(processes)
    writes = [{int(m) for line in body for m in _DIRTY_WRITE_RE.findall(line)} for _sens, body in processes]
    # Reverse index: signal id -> processes sensitive to it, so edges are
    # found in O(total writes + total sensitivity-set size) rather than
    # O(n^2) -- the naive all-pairs scan is fine for a handful of cont
    # processes but becomes the dominant codegen cost past a few hundred.
    readers: dict[int, list[int]] = {}
    for j, (sens, _body) in enumerate(processes):
        for sid in sens:
            readers.setdefault(sid, []).append(j)
    succ: list[list[int]] = [[] for _ in range(n)]
    indeg = [0] * n
    for i in range(n):
        seen_j: set[int] = set()
        for sid in writes[i]:
            for j in readers.get(sid, ()):
                if j != i and j not in seen_j:
                    seen_j.add(j)
                    succ[i].append(j)
                    indeg[j] += 1
    import heapq

    ready = [i for i in range(n) if indeg[i] == 0]
    heapq.heapify(ready)
    order: list[int] = []
    while ready:
        i = heapq.heappop(ready)
        order.append(i)
        for j in succ[i]:
            indeg[j] -= 1
            if indeg[j] == 0:
                heapq.heappush(ready, j)
    acyclic = len(order) == n
    if not acyclic:
        seen = set(order)
        order.extend(i for i in range(n) if i not in seen)
    return order, acyclic


def _c_const_array(ctype: str, name: str, values: list[int]) -> tuple[str, str, int]:
    """A ``static const`` C array definition for a verbatim ``cdef extern
    from *`` block: ``(text, name, length)``."""
    vals = values or [0]  # C forbids zero-length arrays
    rows = [", ".join(str(v) for v in vals[i : i + 24]) for i in range(0, len(vals), 24)]
    body = ",\n        ".join(rows)
    return f"    static const {ctype} {name}[{len(vals)}] = {{\n        {body}\n    }};", name, len(vals)


def _c_const_array_block(arrays: list[tuple[str, str, int]], ctypes: dict[str, str]) -> list[str]:
    """Wrap ``_c_const_array`` results in one ``cdef extern from *`` block
    that defines them in C and declares them to Cython. *ctypes* gives each
    array's Cython element type."""
    lines = ["cdef extern from *:", '    """', *(text for text, _n, _l in arrays), '    """']
    lines.extend(f"    {ctypes[name]} {name}[{length}]" for _t, name, length in arrays)
    lines.append("")
    return lines


def _gen_dirty_helpers(write_log: bool = False) -> list[str]:
    """The only code allowed to write ``SimCtx.dbit``/``dlist``/``dcount``
    and ``nba_bit``/``nba_list``/``nba_count``.

    Each is a sparse set: setting a bit that was clear appends the sid to the
    list, so the list holds exactly the sids whose bit is set, without
    duplicates (and so never more than ``N_SIGS`` entries). Consumers that
    clear bits must clear every listed bit and reset the count together.
    ``mark_nba`` deliberately does not set ``nba_pending`` -- call sites set
    it themselves, as they always have (memory NBAs set it without staging a
    scalar sid at all).

    ``sdirty[sid]`` is set on every mark (never by the bit/list dedup) and
    cleared only by a memory snapshot; it is what lets a snapshot skip
    copying a memory nothing has written since the last snapshot.
    ``mark_dirty_nosnap`` is ``mark_dirty`` without that: only for a memory
    write that records the slot it wrote in the memory's snapshot journal
    instead (see ``_mem_journal_lines``), so the snapshot copies just that
    slot. Every other memory write path uses ``mark_dirty`` and so gets a
    full copy -- a path that is never converted stays correct.

    *write_log* (queue delta engine only): also record each distinct sid
    written since ``wepoch`` was last bumped into ``wlog`` -- the queue
    engine bumps it before every process call, so after the call ``wlog``
    holds every sid that call changed (not just the first write of each sid
    per iteration, which is all ``dlist`` records). Dedup by epoch bounds
    ``wlog`` at ``N_SIGS`` entries.
    """
    log = (
        [
            "    if c.wmark[sid] != c.wepoch:",
            "        c.wmark[sid] = c.wepoch",
            "        c.wlog[c.wcount] = sid",
            "        c.wcount += 1",
        ]
        if write_log
        else []
    )
    return [
        "cdef inline void mark_dirty_nosnap(SimCtx *c, int sid) noexcept nogil:",
        *log,
        "    if not c.dbit[sid]:",
        "        c.dbit[sid] = 1",
        "        c.dlist[c.dcount] = sid",
        "        c.dcount += 1",
        "",
        "cdef inline void mark_dirty(SimCtx *c, int sid) noexcept nogil:",
        # Snapshot-dirty: memory snapshots copy a memory only if its marker
        # sid was marked since the last snapshot (_mem_snap_memcpy_lines).
        "    c.sdirty[sid] = 1",
        *log,
        "    if not c.dbit[sid]:",
        "        c.dbit[sid] = 1",
        "        c.dlist[c.dcount] = sid",
        "        c.dcount += 1",
        "",
        "cdef inline void mark_nba(SimCtx *c, int sid) noexcept nogil:",
        "    if not c.nba_bit[sid]:",
        "        c.nba_bit[sid] = 1",
        "        c.nba_list[c.nba_count] = sid",
        "        c.nba_count += 1",
        "",
    ]


def _emit_sens_check_lines(sorted_sids: list[int], indent: str, also_dirty: bool = False) -> list[str]:
    """Return a Cython sensitivity check ending in an ``if`` body opener.

    A line-wrapped ``or`` expression is still one left-deep Cython AST. Split
    large sensitivity sets into separate shallow statements instead, while
    preserving short-circuit evaluation after a hit.
    """
    term = (lambda s: f"trigger[{s}] or c.dbit[{s}]") if also_dirty else (lambda s: f"trigger[{s}]")
    if len(sorted_sids) <= _MAX_INLINE_SENS:
        cond = " or ".join(term(s) for s in sorted_sids)
        return [f"{indent}if {cond}:"]
    lines = [f"{indent}_sens_hit = 0"]
    for i in range(0, len(sorted_sids), _MAX_INLINE_SENS):
        chunk = sorted_sids[i : i + _MAX_INLINE_SENS]
        cond = " or ".join(term(s) for s in chunk)
        lines.append(f"{indent}if not _sens_hit and ({cond}):")
        lines.append(f"{indent}    _sens_hit = 1")
    lines.append(f"{indent}if _sens_hit:")
    return lines


def _emit_no_dirty_check_lines(sorted_sids: list[int], indent: str) -> list[str]:
    """Return lines that ``break`` unless some sid in *sorted_sids* is dirty.

    Used by :func:`_gen_delta_loop`'s early-exit: once none of the sids any
    process could possibly react to (``interesting_sids``) are dirty, no
    further iteration can do anything, so stop immediately instead of
    paying for one more iteration just to rediscover that via the normal
    dirty[]->trigger[] top-of-loop bookkeeping.

    Emitted as a flat sequence of simple ``if c.dbit[s]: _any_int = 1``
    statements rather than one large ``or``-chained boolean expression
    (however line-wrapped): a single expression is one left-deep AST node
    per term regardless of how many display lines it's spread across, and
    a real design's ``interesting_sids`` (a union across every process,
    unlike any one process's own small sensitivity set) got large enough
    to blow Cython's own parser recursion limit --
    ``RecursionError: maximum recursion depth exceeded`` from inside
    ``cythonize()`` itself, confirmed on ``ibex_cs_registers`` (a real,
    large RTL module). A flat statement sequence has no such limit: each
    ``if`` is its own shallow statement, however many there are.
    """
    if not sorted_sids:
        return [f"{indent}break"]
    lines = [f"{indent}_any_interesting_dirty = 0"]
    for s in sorted_sids:
        lines.append(f"{indent}if c.dbit[{s}]:")
        lines.append(f"{indent}    _any_interesting_dirty = 1")
    lines.append(f"{indent}if not _any_interesting_dirty:")
    lines.append(f"{indent}    break")
    return lines


def _seq_body_to_sv_reads(
    body_lines: list[str],
    async_sids: set[int] | None = None,
    func_internal_sids: set[int] | None = None,
    external_input_sids: set[int] | None = None,
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

    **Exception** — signals listed in *external_input_sids* (the top-level module's
    own `input` ports) must also use c.val[]/c.mask[] not sv[]/sm[], because the
    whole point of the pre-edge snapshot is protecting against an NBA-write RACE
    between sequential processes *within this design* -- an ordinary top-level
    input port has no internal driver at all (Verilog forbids a module assigning
    its own input), so there is no such race to protect against, and using the
    snapshot is actively wrong: the testbench drives a fresh value onto the port
    from outside every cycle via `sim.drive()`, and the snapshot only gets
    refreshed on its own schedule, not necessarily in lockstep with every drive.
    Confirmed via a minimal repro: `always @(posedge clk) if (m_axis_tvalid &&
    m_axis_tready) cnt <= cnt + 1;` with `m_axis_tready` a plain top-level input
    driven low most cycles (a throttled AXI-Stream sink, ~1% ready duty) --
    `cnt` incremented on cycles where the *stale* `sv[]` snapshot of
    `m_axis_tready` still showed an earlier `1` even though the signal had
    long since been driven back to `0`, corrupting the beat count (confirmed
    against `reference`, which reads the live value and counts correctly).
    Real-world instance: `axis_pix_correction2`'s own `m_beat_cnt`/`m_row_cnt`
    bookkeeping (`m_axis_pixout_tready` is exactly this shape) miscounted beats
    under sustained backpressure, eventually desynchronizing frame boundaries
    enough that a downstream `tlast` never arrived within a generous timeout --
    originally suspected to be an RTL bug, root-caused via VCD tracing to this
    compiled-engine pre-edge-snapshot gap instead.

    Substitutions performed (safe because NBA writes use c.nba_val/c.nba_mask/mark_nba):
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
    if external_input_sids:
        tainted.update(external_input_sids)
    tainted_mem: set[int] = set()
    for line in body_lines:
        m = _BLOCKING_WRITE_RE.match(line)
        if m:
            tainted.add(int(m.group(1)))
        m = _NARROW_LHS_RE.match(line)
        if m:
            tainted.add(int(m.group(1)))
        for m in _WIDE_BLOCKING_WRITE_RE.finditer(line):
            tainted.add(int(m.group(1)))
        mem_mid = _mem_blocking_write_mid(line)
        if mem_mid is not None:
            tainted_mem.add(mem_mid)
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

    def _cont_settle_fixpoint_lines(self, indent: str) -> list[str]:
        """Lines that run every continuous-assign process repeatedly, to a
        fixed point, instead of one single pass in declaration order.

        Emitted at every point that settles continuous assigns immediately
        before snapshotting `ctx.val`/`ctx.mask` into `sv`/`sm`
        (`refresh_data_snapshot()` and each of `batch_run()`'s three
        snapshot points -- NOT plain `snapshot()`, which has no continuous
        assigns to settle at all, just a raw pre-edge capture). A single
        pass is NOT enough when a multi-hop continuous-assign dependency
        chain is declared "out of order" relative to its own data
        dependencies -- e.g. `assign o10 = ~o12; assign o12 = {...};`:
        `o10`'s own process runs BEFORE `o12`'s in one top-to-bottom pass,
        so `o10` computes from `o12`'s STALE (pre-this-settle) value; only
        a later, genuinely unrelated trigger would ever re-run `o10` for
        real. That stale value then gets baked into the snapshot itself,
        so a sequential process's NBA read captures the WRONG value at
        every single clock edge, forever -- confirmed wrong (against the
        reference oracle) via the grammar-driven fuzzer, and confirmed to
        be exactly this ordering issue by simply swapping the two `assign`
        statements, which made the divergence disappear entirely. See
        notes/developer/roadmap.md ("mismatch_10096").

        Bounded by the number of continuous-assign processes -- a safe
        convergence bound for any acyclic dependency graph among them (a
        genuine combinational LOOP, a design error, would never converge
        regardless of how many passes are allowed, so this bound doesn't
        mask a real infinite loop, it just stops chasing one, same as
        `DELTA_LIMIT` elsewhere). Reuses the `conv_val`/`conv_mask`/
        `conv_wide_val`/`conv_wide_mask` scratch buffers `delta_loop`'s own
        oscillation-detection check already uses (safe: this always
        finishes before `delta_loop` starts, so there's no overlap) rather
        than disturbing `dirty[]`, which `delta_loop`'s own triggering
        depends on seeing exactly as external drives/events left it.

        Callers must declare `cdef int _cont_settle_it, _cont_settle_stable,
        _cont_settle_sid` themselves -- ONCE per enclosing Cython function,
        since `batch_run()` calls this three times within the same
        function and a repeated `cdef` would be a redeclaration error.
        """
        if not self._processes:
            return []
        n = len(self._processes)
        sn = max(self._n_sigs, 1)
        return [
            f"{indent}for _cont_settle_it in range({n}):",
            f"{indent}    memcpy(self.ctx.conv_val, self.ctx.val, {sn} * sizeof(long long))",
            f"{indent}    memcpy(self.ctx.conv_mask, self.ctx.mask, {sn} * sizeof(long long))",
            f"{indent}    memcpy(self.ctx.conv_wide_val, self.ctx.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
            f"{indent}    memcpy(self.ctx.conv_wide_mask, self.ctx.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
            f"{indent}    for _cont_settle_sid in range({n}):",
            f"{indent}        DL_CONT_FN[_cont_settle_sid](&self.ctx)",
            f"{indent}    _cont_settle_stable = 1",
            f"{indent}    for _cont_settle_sid in range({sn}):",
            f"{indent}        if self.ctx.val[_cont_settle_sid] != self.ctx.conv_val[_cont_settle_sid]"
            f" or self.ctx.mask[_cont_settle_sid] != self.ctx.conv_mask[_cont_settle_sid]:",
            f"{indent}            _cont_settle_stable = 0",
            f"{indent}            break",
            f"{indent}    if _cont_settle_stable:",
            f"{indent}        for _cont_settle_sid in range(N_WIDE_WORDS):",
            f"{indent}            if self.ctx.wide_val[_cont_settle_sid] != self.ctx.conv_wide_val[_cont_settle_sid]"
            f" or self.ctx.wide_mask[_cont_settle_sid] != self.ctx.conv_wide_mask[_cont_settle_sid]:",
            f"{indent}                _cont_settle_stable = 0",
            f"{indent}                break",
            f"{indent}    if _cont_settle_stable:",
            f"{indent}        break",
        ]

    def _negedge_block_lines(self, sn: int) -> list[str]:
        """batch_run's clk-fall step.  When nothing can react to a falling
        edge of ``clk_sid`` (no negedge-triggered seq process on it, no
        cont/combo sensitive to it, no always-run cont/combo), the snapshot
        and delta_loop() are skipped -- only the clk value is updated; the
        next posedge re-snapshots anyway.

        Always-run processes that are idempotent constant drivers (e.g.
        ``assign tie = 1'b0;``, see ``_cont_assign_is_idempotent``) don't
        block the skip: re-running one changes nothing. Without this, a
        single tie-off forced a full snapshot -- including every memory --
        plus a delta_loop on every falling edge; on gfwx-fpga that doubled
        ~6.9 MB of per-cycle snapshot copies."""
        body = [
            "                # Snapshot before negedge (delta_loop above converged)",
            f"                memcpy(sv, self.ctx.val, {sn} * sizeof(long long))",
            f"                memcpy(sm, self.ctx.mask, {sn} * sizeof(long long))",
            "                memcpy(self.ctx.wide_snap_val, self.ctx.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
            "                memcpy(self.ctx.wide_snap_mask, self.ctx.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
            *self._mem_snap_memcpy_lines("                "),
            "                # Negedge: drive clk low",
            "                self.ctx.sim_time = t0 + i * period + period // 2",
            "                self.ctx.val[clk_sid] = 0",
            "                self.ctx.mask[clk_sid] = 0",
            "                mark_dirty(&self.ctx, clk_sid)",
            "                delta_loop(&self.ctx, sv, sm)",
            "                if self.ctx.error_code != ERR_NONE:",
            "                    cycles_run = i + 1",
            "                    break",
            "                if self._rec_on:",
            "                    self._trace_record(t0 + i * period + period // 2)",
            "                if self.ctx.finished:",
            "                    cycles_run = i + 1",
            "                    break",
        ]
        if self._negedge_reacting_sids() is None:
            return body
        check_lines = ["                if DL_NEG_REACT[clk_sid]:"]
        return [
            *check_lines,
            *("    " + ln for ln in body),
            "                else:",
            "                    self.ctx.val[clk_sid] = 0",
            "                    self.ctx.mask[clk_sid] = 0",
            "                    if self._rec_on:",
            "                        self._trace_record(t0 + i * period + period // 2)",
        ]

    def _trace_method_lines(self) -> list[str]:
        """``CompiledSim`` methods for VCD change detection (see
        ``sim/trace.py``). ``trace_set(specs)`` installs the traced items --
        ``(0, sid)`` for a signal, ``(1, mid, addr)`` for a memory element --
        as (value pointer, mask pointer, word count) into the context arrays,
        plus a copy of each one's current value. ``trace_poll()`` compares
        every traced item against its copy in C, updates the copies, and
        returns ``[(index, val, mask), ...]`` for the ones that changed, so
        Python only touches changed signals. Untraced signals cost nothing.
        """
        lines = [
            "",
            "    def __dealloc__(self):",
            "        self._trace_free()",
            "        self.trace_record_end()",
            "",
            "    cdef void _trace_free(self):",
            "        free(self._tr_vp)",
            "        free(self._tr_mp)",
            "        free(self._tr_nw)",
            "        free(self._tr_off)",
            "        free(self._tr_lv)",
            "        free(self._tr_lm)",
            "        free(self._tr_chg)",
            "        self._tr_vp = NULL",
            "        self._tr_mp = NULL",
            "        self._tr_nw = NULL",
            "        self._tr_off = NULL",
            "        self._tr_lv = NULL",
            "        self._tr_lm = NULL",
            "        self._tr_chg = NULL",
            "        self._tr_n = 0",
            "",
            "    cpdef void trace_set(self, list specs):",
            "        cdef int i, w, n = len(specs), total = 0, sid, mid, addr",
            "        self._trace_free()",
            "        if n == 0:",
            "            return",
            "        self._tr_vp = <unsigned long long **>malloc(n * sizeof(unsigned long long *))",
            "        self._tr_mp = <unsigned long long **>malloc(n * sizeof(unsigned long long *))",
            "        self._tr_nw = <int *>malloc(n * sizeof(int))",
            "        self._tr_off = <int *>malloc(n * sizeof(int))",
            "        self._tr_chg = <int *>malloc(n * sizeof(int))",
            "        for i in range(n):",
            "            spec = specs[i]",
            "            if spec[0] == 0:",
            "                sid = spec[1]",
            "                if self.ctx.wide_words[sid] > 0:",
            "                    self._tr_vp[i] = &self.ctx.wide_val[self.ctx.wide_offset[sid]]",
            "                    self._tr_mp[i] = &self.ctx.wide_mask[self.ctx.wide_offset[sid]]",
            "                    self._tr_nw[i] = self.ctx.wide_words[sid]",
            "                else:",
            "                    self._tr_vp[i] = <unsigned long long *>&self.ctx.val[sid]",
            "                    self._tr_mp[i] = <unsigned long long *>&self.ctx.mask[sid]",
            "                    self._tr_nw[i] = 1",
            "            else:",
            "                mid = spec[1]",
            "                addr = spec[2]",
            "                self._tr_nw[i] = self._mem_slot_ptrs(mid, addr, &self._tr_vp[i], &self._tr_mp[i])",
            "            self._tr_off[i] = total",
            "            total += self._tr_nw[i]",
            "        self._tr_words = total",
            "        self._tr_lv = <unsigned long long *>malloc(total * sizeof(unsigned long long))",
            "        self._tr_lm = <unsigned long long *>malloc(total * sizeof(unsigned long long))",
            "        for i in range(n):",
            "            for w in range(self._tr_nw[i]):",
            "                self._tr_lv[self._tr_off[i] + w] = self._tr_vp[i][w]",
            "                self._tr_lm[self._tr_off[i] + w] = self._tr_mp[i][w]",
            "        self._tr_n = n",
            "",
            "    cpdef list trace_poll(self):",
            "        cdef int i, w, k, off, nch = 0, changed",
            "        cdef list out = []",
            "        with nogil:",
            "            for i in range(self._tr_n):",
            "                off = self._tr_off[i]",
            "                changed = 0",
            "                for w in range(self._tr_nw[i]):",
            "                    if self._tr_vp[i][w] != self._tr_lv[off + w] or self._tr_mp[i][w] != self._tr_lm[off + w]:",
            "                        changed = 1",
            "                        break",
            "                if changed:",
            "                    for w in range(self._tr_nw[i]):",
            "                        self._tr_lv[off + w] = self._tr_vp[i][w]",
            "                        self._tr_lm[off + w] = self._tr_mp[i][w]",
            "                    self._tr_chg[nch] = i",
            "                    nch += 1",
            "        for k in range(nch):",
            "            i = self._tr_chg[k]",
            "            off = self._tr_off[i]",
            "            if self._tr_nw[i] == 1:",
            "                out.append((i, self._tr_lv[off], self._tr_lm[off]))",
            "            else:",
            "                v = 0",
            "                m = 0",
            "                for w in range(self._tr_nw[i] - 1, -1, -1):",
            "                    v = (v << 64) | self._tr_lv[off + w]",
            "                    m = (m << 64) | self._tr_lm[off + w]",
            "                out.append((i, v, m))",
            "        return out",
            "",
            # -- batch_run recording: after each edge, batch_run calls
            # _trace_record(t), which does what trace_poll does but appends
            # (time, slot, words) records to a buffer instead of returning a
            # list. batch_run stops at a cycle boundary when the buffer can't
            # hold two more full polls (_rec_full); the caller drains it
            # (trace_drain) and resumes.
            "    cpdef void trace_record_begin(self, int cap):",
            "        cdef int maxnw = 1, i",
            "        self.trace_record_end()",
            "        for i in range(self._tr_n):",
            "            if self._tr_nw[i] > maxnw:",
            "                maxnw = self._tr_nw[i]",
            "        if cap < 2 * self._tr_n:",
            "            cap = 2 * self._tr_n",
            "        if cap < 1:",
            "            cap = 1",
            "        self._rec_cap = cap",
            "        self._rec_wcap = 2 * cap * maxnw",
            "        self._rec_slot = <int *>malloc(cap * sizeof(int))",
            "        self._rec_woff = <int *>malloc(cap * sizeof(int))",
            "        self._rec_time = <long long *>malloc(cap * sizeof(long long))",
            "        self._rec_w = <unsigned long long *>malloc(self._rec_wcap * sizeof(unsigned long long))",
            "        self._rec_n = 0",
            "        self._rec_wn = 0",
            "        self._rec_full = 0",
            "        self._rec_on = 1",
            "",
            "    cpdef void trace_record_end(self):",
            "        free(self._rec_slot)",
            "        free(self._rec_woff)",
            "        free(self._rec_time)",
            "        free(self._rec_w)",
            "        self._rec_slot = NULL",
            "        self._rec_woff = NULL",
            "        self._rec_time = NULL",
            "        self._rec_w = NULL",
            "        self._rec_on = 0",
            "        self._rec_n = 0",
            "        self._rec_wn = 0",
            "",
            "    cpdef int trace_record_full(self):",
            "        return self._rec_full",
            "",
            "    cdef inline int _trace_room(self) noexcept nogil:",
            "        return (self._rec_n + 2 * self._tr_n <= self._rec_cap"
            " and self._rec_wn + 4 * self._tr_words <= self._rec_wcap)",
            "",
            "    cdef void _trace_record(self, long long t) noexcept nogil:",
            "        cdef int i, w, off, nw, changed",
            "        for i in range(self._tr_n):",
            "            off = self._tr_off[i]",
            "            nw = self._tr_nw[i]",
            "            changed = 0",
            "            for w in range(nw):",
            "                if self._tr_vp[i][w] != self._tr_lv[off + w] or self._tr_mp[i][w] != self._tr_lm[off + w]:",
            "                    changed = 1",
            "                    break",
            "            if changed:",
            "                self._rec_slot[self._rec_n] = i",
            "                self._rec_time[self._rec_n] = t",
            "                self._rec_woff[self._rec_n] = self._rec_wn",
            "                self._rec_n += 1",
            "                for w in range(nw):",
            "                    self._tr_lv[off + w] = self._tr_vp[i][w]",
            "                    self._tr_lm[off + w] = self._tr_mp[i][w]",
            "                    self._rec_w[self._rec_wn + w] = self._tr_vp[i][w]",
            "                    self._rec_w[self._rec_wn + nw + w] = self._tr_mp[i][w]",
            "                self._rec_wn += 2 * nw",
            "",
            "    cpdef list trace_drain(self):",
            "        cdef int k, i, w, nw, woff",
            "        cdef list out = []",
            "        for k in range(self._rec_n):",
            "            i = self._rec_slot[k]",
            "            nw = self._tr_nw[i]",
            "            woff = self._rec_woff[k]",
            "            if nw == 1:",
            "                out.append((self._rec_time[k], i, self._rec_w[woff], self._rec_w[woff + 1]))",
            "            else:",
            "                v = 0",
            "                m = 0",
            "                for w in range(nw - 1, -1, -1):",
            "                    v = (v << 64) | self._rec_w[woff + w]",
            "                    m = (m << 64) | self._rec_w[woff + nw + w]",
            "                out.append((self._rec_time[k], i, v, m))",
            "        self._rec_n = 0",
            "        self._rec_wn = 0",
            "        self._rec_full = 0",
            "        return out",
            "",
            "    cdef int _mem_slot_ptrs(self, int mid, int addr, unsigned long long **vp, unsigned long long **mp):",
        ]
        for mid in range(self._n_mems):
            elem_w, _depth = self._mem_info[mid]
            kw = "if" if mid == 0 else "elif"
            lines.append(f"        {kw} mid == {mid}:")
            if elem_w > _WORD_BITS:
                words = self._mem_words(mid)
                lines.extend(
                    [
                        f"            vp[0] = &self.ctx.wide_mem_{mid}_val[addr * {words}]",
                        f"            mp[0] = &self.ctx.wide_mem_{mid}_mask[addr * {words}]",
                        f"            return {words}",
                    ]
                )
            else:
                lines.extend(
                    [
                        f"            vp[0] = <unsigned long long *>&self.ctx.mem_{mid}_val[addr]",
                        f"            mp[0] = <unsigned long long *>&self.ctx.mem_{mid}_mask[addr]",
                        "            return 1",
                    ]
                )
        lines.extend(
            [
                # Unreachable (trace.py only passes known mids); any valid
                # word keeps the poll in bounds.
                "        vp[0] = <unsigned long long *>&self.ctx.val[0]",
                "        mp[0] = <unsigned long long *>&self.ctx.mask[0]",
                "        return 1",
            ]
        )
        return lines

    def _negedge_reacting_sids(self) -> set[int] | None:
        """Sids whose falling edge something can react to: a cont/combo
        sensitive to it, or a seq process with a non-posedge trigger on it.
        None when some non-constant always-run process (empty sensitivity)
        means *every* falling edge needs the full step."""
        procs = [*self._processes, *self._combo_processes]
        n_cont = len(self._processes)
        if any(not sens and not (i < n_cont and i in self._const_conts) for i, (sens, _b) in enumerate(procs)):
            return None
        sids = {sid for sens, _b in procs for sid in sens}
        sids |= {sid for edges, _s, _b in self._seq_processes for sid, et in edges.items() if et != "posedge"}
        return sids

    def _settle_via_delta_loop_lines(self, sn: int, indent: str) -> list[str]:
        """Settle pending external perturbation by snapshotting the CURRENT
        (pre-settle) ``ctx.val``/``ctx.mask`` into ``sv``/``sm`` and calling
        ``delta_loop()`` itself, instead of a second, separate, cont-only,
        unconditional ``N_cont``-bounded implementation
        (``_cont_settle_fixpoint_lines``).

        Taking the snapshot from the CURRENT state first, rather than
        letting the caller's own upcoming "real" snapshot double as it, is
        what makes this safe to use for a pre-edge settle: it makes
        ``sv[sid] == c.val[sid]`` for every signal, including any clock,
        so ``delta_loop()``'s own edge-detection can never see a spurious
        transition here -- nothing has genuinely toggled anything yet, so
        this can only run cont/combo work gated by whatever dirty bits an
        external perturbation (an applied event, or a reactive ``drive()``
        before a fresh ``batch_run()`` call) left behind. It can never
        spuriously fire a sequential process. The caller's own real
        pre-edge snapshot that follows immediately after then captures the
        now-settled state, exactly as it always has.

        This is also exactly what ``batch_run()``'s own ``ev_applied``
        branch already does inline for a mid-batch scheduled event --
        factored out here so the "settle before this call's first real
        edge" case (see ``notes/plans/work_queue_delta_engine.md``, Stage
        1) can reuse the identical, already-tested pattern instead of
        ``_cont_settle_fixpoint_lines()``'s separate mechanism, which never
        dispatches combo blocks at all and pays for every cont process
        unconditionally regardless of design size.
        """
        return [
            f"{indent}memcpy(sv, self.ctx.val, {sn} * sizeof(long long))",
            f"{indent}memcpy(sm, self.ctx.mask, {sn} * sizeof(long long))",
            f"{indent}memcpy(self.ctx.wide_snap_val, self.ctx.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
            f"{indent}memcpy(self.ctx.wide_snap_mask, self.ctx.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
            *self._mem_snap_memcpy_lines(indent),
            f"{indent}delta_loop(&self.ctx, sv, sm)",
        ]

    def _mem_snap_memcpy_lines(self, indent: str) -> list[str]:
        """Lines that copy every memory's live val/mask arrays into their
        pre-edge snapshot (`mem_{mid}_snap_val`/`wide_mem_{mid}_snap_val`)
        counterparts. Emitted at every point that already snapshots
        `ctx.val`/`ctx.mask` into `sv`/`sm` (`snapshot()`,
        `refresh_data_snapshot()`, and each of `batch_run()`'s three
        snapshot points) -- see notes/developer/roadmap.md "Wide-signal pre-edge
        snapshot gap" for why a 2-D packed array (elaborated as a `memory`
        for per-element addressing, not a plain signal) needs this too: a
        memory fed by a continuous assign (e.g. a wide port connection) can
        still be re-derived by further delta-loop settling within the same
        clock edge, so a sequential process reading it needs the same
        frozen-at-the-edge value narrow/wide signals get from sv[]/sm[]/
        wide_snap_val/wide_snap_mask.

        Incremental: a memory is copied only if its marker sid was marked
        dirty since its last snapshot (``sdirty``, set by ``mark_dirty``).
        Every write path marks the marker -- that is how the memory's readers
        get re-triggered -- so an unmarked memory still equals its snapshot.
        For a large, rarely written memory (e.g. a testbench stimulus buffer
        refilled once per chunk round) this removes a full copy on every
        snapshot; on gfwx-fpga that was ~6 MB, twice per cycle.
        Per-slot: a write that records its slot in the memory's journal
        (``memj_{mid}``, see ``_mem_journal_lines``) marks the marker with
        ``mark_dirty_nosnap`` instead, so the snapshot copies only the
        journaled slots. A journal that would overflow sets ``sdirty``
        instead (full copy). Journal entries are slot indices into the flat
        val/mask arrays (a word index for a wide memory), so copying one is
        always correct, even if stale or duplicated -- only a missing entry
        could be wrong, and every unjournaled write path sets ``sdirty``.

        ``VERIFORGE_CHECK_MEM_SNAPSHOT=1`` verifies the invariant: after any
        non-full snapshot the memory is compared against its snapshot and
        any difference raises.
        """
        return [f"{indent}_mem_snapshot(&self.ctx)"] if self._n_mems else []

    def _mem_snap_helper_lines(self) -> list[str]:
        """The ``_mem_snapshot`` helper called by ``_mem_snap_memcpy_lines``,
        and ``_nba_mem_push``, which every NBA memory-queue entry ends with."""
        if not self._n_mems:
            return []
        push = [
            # Commit the entry just written at `nba_mem_range_count` -- only
            # while a free slot remains, so the next entry's writes stay in
            # bounds; on overflow, flag the error (raised after the step).
            "cdef inline void _nba_mem_push(SimCtx *c) noexcept nogil:",
            "    if c.nba_mem_range_count < NBA_MEM_RANGE_MAX - 1:",
            "        c.nba_mem_range_count += 1",
            "    else:",
            "        c.error_code = ERR_NBA_MEM_OVERFLOW",
            "",
        ]
        check = get_env("CHECK_MEM_SNAPSHOT", "0") in ("1", "true", "True")
        lines: list[str] = [
            *push,
            "cdef void _mem_snapshot(SimCtx *c) noexcept nogil:",
            "    cdef int _mj, _ms",
        ]
        for mid in range(self._n_mems):
            elem_w, depth = self._mem_info[mid]
            marker = self._mem_marker_sigs[mid]
            if elem_w > _WORD_BITS:
                n, ctype, pre = depth * self._mem_words(mid), "unsigned long long", "wide_mem"
            else:
                n, ctype, pre = depth, "long long", "mem"
            lines.extend(
                [
                    f"    if c.sdirty[{marker}]:",
                    f"        memcpy(c.{pre}_{mid}_snap_val, c.{pre}_{mid}_val, {n} * sizeof({ctype}))",
                    f"        memcpy(c.{pre}_{mid}_snap_mask, c.{pre}_{mid}_mask, {n} * sizeof({ctype}))",
                    f"        c.sdirty[{marker}] = 0",
                    f"        c.memj_{mid}_n = 0",
                    "    else:",
                    f"        for _mj in range(c.memj_{mid}_n):",
                    f"            _ms = c.memj_{mid}[_mj]",
                    f"            c.{pre}_{mid}_snap_val[_ms] = c.{pre}_{mid}_val[_ms]",
                    f"            c.{pre}_{mid}_snap_mask[_ms] = c.{pre}_{mid}_mask[_ms]",
                    f"        c.memj_{mid}_n = 0",
                ]
            )
            if check:
                lines.extend(
                    [
                        f"        if (memcmp(c.{pre}_{mid}_snap_val, c.{pre}_{mid}_val, {n} * sizeof({ctype}))"
                        f" or memcmp(c.{pre}_{mid}_snap_mask, c.{pre}_{mid}_mask, {n} * sizeof({ctype}))):",
                        f"            c.snap_stale_mid = {mid}",
                    ]
                )
        lines.append("")
        return lines

    def _mem_journal_cap(self, mid: int) -> int:
        """Snapshot-journal capacity for memory *mid*, in slots. Past ~1/4 of
        the memory a full memcpy is as cheap as per-slot copies, so a write
        that would overflow just forces the full copy."""
        elem_w, depth = self._mem_info[mid]
        slots = depth * self._mem_words(mid) if elem_w > _WORD_BITS else depth
        return max(1, min(_MEM_JOURNAL_MAX, slots // 4))

    def _mem_journal_lines(self, mid: int, slot_expr: str, indent: str) -> list[str]:
        """Mark memory *mid*'s marker after a write of the single flat slot
        *slot_expr*, journaling the slot so the next snapshot copies only it
        (see ``_mem_snap_memcpy_lines``). Replaces ``mark_dirty(c, marker)``
        at a write site; *slot_expr* must be exactly the index written."""
        marker = self._mem_marker_sigs[mid]
        return [
            f"{indent}if not c.sdirty[{marker}]:",
            f"{indent}    if c.memj_{mid}_n < {self._mem_journal_cap(mid)}:",
            f"{indent}        c.memj_{mid}[c.memj_{mid}_n] = {slot_expr}",
            f"{indent}        c.memj_{mid}_n += 1",
            f"{indent}    else:",
            f"{indent}        c.sdirty[{marker}] = 1",
            f"{indent}mark_dirty_nosnap(c, {marker})",
        ]

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

    def _nba_mem_queue_capacity(self) -> int:
        """Entries in the (single) NBA memory queue: the two former queues'
        combined capacity. Overflow is checked at every push
        (``_nba_mem_push``) and raises instead of writing past the array."""
        return 2 * max(64, self._nba_mem_queue_bound())

    def _gen_header(self) -> str:
        return (
            "# cython: language_level=3, boundscheck=False, wraparound=False\n"
            "# cython: cdivision=True, initializedcheck=False, nonecheck=False\n"
            "\n"
            "from libc.string cimport memcmp, memcpy, memset\n"
            "from libc.stdlib cimport free, malloc\n"
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
        lines.append("DEF ERR_NBA_MEM_OVERFLOW = 4")
        lines.append(f"DEF DELTA_LIMIT = {self._delta_limit}")
        # After this many delta iterations, start checking for value-level
        # stability (fixpoint) so designs whose dirty flags never quiet
        # (e.g. combo loops with intermediate writes) still terminate.
        lines.append(f"DEF DELTA_CONV_CHECK_START = {min(16, max(self._delta_limit - 2, 0))}")
        if self._n_mems > 0:
            # This queue buffers the non-blocking memory writes (whole
            # element or partial bit range, in program order) queued
            # during ONE delta-loop iteration (every
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
            # `_nba_mem_queue_bound` for how the real capacity is derived;
            # every push is now also bounds-checked (`_nba_mem_push`).
            lines.append(f"DEF NBA_MEM_RANGE_MAX = {self._nba_mem_queue_capacity()}")
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
            literals["NBA_MEM_RANGE_MAX"] = self._nba_mem_queue_capacity()
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
            # Sparse sets: a bit per sid plus a list of the sids whose bit is
            # set, so consumers iterate what's actually dirty instead of
            # scanning all N_SIGS. Written ONLY via mark_dirty()/mark_nba()
            # (see _gen_dirty_helpers) -- the fields were renamed from
            # dirty/nba_dirty so any write site that bypasses the helpers is
            # a Cython compile error, not a silently lost trigger.
            "    int       nba_bit[N_SIGS]",
            "    int       nba_list[N_SIGS]",
            "    int       nba_count",
            "    int       dbit[N_SIGS]",
            "    int       dlist[N_SIGS]",
            "    int       dcount",
            # Per sid: marked since the last memory snapshot (see
            # _mem_snap_memcpy_lines). Only memory-marker sids are consulted.
            "    unsigned char sdirty[N_SIGS]",
            # Sticky: a skipped memory snapshot was found stale (only set in
            # VERIFORGE_CHECK_MEM_SNAPSHOT=1 builds).
            "    int       snap_stale_mid",
            # Queue delta engine's per-process-call write log (see
            # _gen_dirty_helpers); unused by the scan engine.
            "    long long wmark[N_SIGS]",
            "    int       wlog[N_SIGS]",
            "    int       wcount",
            "    long long wepoch",
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
            # Snapshot journal: flat slots written since the last snapshot
            # (see _mem_snap_memcpy_lines).
            lines.append(f"    int       memj_{mid}[{self._mem_journal_cap(mid)}]")
            lines.append(f"    int       memj_{mid}_n")
        # NBA memory queue
        if self._n_mems > 0:
            lines.extend(
                [
                    "    int       nba_mem_range_count",
                    "    int       nba_mem_range_mid[NBA_MEM_RANGE_MAX]",
                    "    int       nba_mem_range_addr[NBA_MEM_RANGE_MAX]",
                    "    int       nba_mem_range_msb[NBA_MEM_RANGE_MAX]",
                    "    int       nba_mem_range_lsb[NBA_MEM_RANGE_MAX]",
                    "    long long nba_mem_range_val[NBA_MEM_RANGE_MAX]",
                    "    long long nba_mem_range_mask[NBA_MEM_RANGE_MAX]",
                ]
            )
        # Cold, large output buffer last so hot state stays packed together.
        lines.append("    char      out_buf[OUT_BUF_MAX]")
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
        # Emitted first: every later helper (narrow templates, wide/memory
        # helpers) and every process body marks signals dirty through these.
        lines.extend(_gen_dirty_helpers(write_log=self._delta_engine() == "queue"))
        lines.extend(self._mem_snap_helper_lines())
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
                    "        mark_dirty(c, dst_sid)",
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
        ``_sv``-suffixed twin -- see notes/developer/roadmap.md "Wide-signal pre-edge
        snapshot gap") can make that decision at emission time instead of via
        `_seq_body_to_sv_reads`'s later text-level substitution, which can't
        safely parse these particular call sites' other (arbitrary-
        expression) arguments.

        A bit/range/part-select LHS (``rz[127:12] = ...``) and every member
        of a concatenation LHS taint their base signal too: a later read of
        the whole signal in the same body must see the bits just written.
        These were once excluded, so e.g. ``rz[127:12] = x; wq <= rz;`` (rz
        wide) staged rz's pre-edge value. Memory-element targets are tracked
        separately (`_seq_body_to_sv_reads`'s per-mid taint).
        """
        tainted: set[int] = set()

        def add(target: Expression) -> None:
            if isinstance(target, Concatenation):
                for part in target.parts:
                    add(part)
                return
            while isinstance(target, (BitSelect, RangeSelect, PartSelect)):
                target = target.target
            if isinstance(target, Identifier):
                name = target.name
                if target.hierarchy:
                    name = ".".join(target.hierarchy) + "." + name
                sid = self._signal_map.get(name)
                if sid is not None:
                    tainted.add(sid)

        for assign in block_body.find(BlockingAssign):
            add(assign.lhs)
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

    def _external_input_sids(self) -> set[int]:
        """Signal IDs for the top-level module's own `input` ports.

        These have no internal driver at all (Verilog forbids a module
        assigning its own input port) -- see `_seq_body_to_sv_reads`'s
        `external_input_sids` exception docstring for why they must never
        be rewritten to the pre-edge snapshot (`sv[]`/`sm[]`) inside a
        sequential process body.
        """
        sids: set[int] = set()
        if self._module is None:
            return sids
        for port in self._module.ports:
            if port.direction != PortDirection.INPUT:
                continue
            sid = self._signal_map.get(port.name)
            if sid is not None:
                sids.add(sid)
        return sids

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
        external_input_sids = self._external_input_sids()

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
                        body_lines = _seq_body_to_sv_reads(
                            body_lines, async_sids, func_internal_sids, external_input_sids
                        )
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
        external_input_sids = self._external_input_sids()

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
                        body_lines = _seq_body_to_sv_reads(
                            body_lines, async_sids, func_internal_sids, external_input_sids
                        )
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

    # ── delta_loop ──────────────────────────────────────────────────────
    #
    # Two engines (codegen option, see `delta_engine_mode()`/`_delta_engine()`) share every
    # piece below except how each iteration decides which cont/combo
    # processes run. Both run the same processes in the same rank order every
    # iteration, except that the queue engine skips no-op reruns of pure
    # processes -- so results AND delta_loop's return value (the iteration
    # count) are identical, and DELTA_LIMIT / DELTA_CONV_CHECK_START / the
    # value-convergence detector behave identically. See
    # notes/plans/work_queue_delta_engine.md, "Stage 2 design decisions".

    def _delta_engine(self) -> str:
        """``"scan"`` or ``"queue"`` for this design (``auto`` resolved by
        process count). Both the delta loop and ``mark_dirty``'s write log
        depend on it, so they must agree -- hence one place decides."""
        return resolve_delta_engine(len(self._processes) + len(self._combo_processes))

    def _gen_delta_loop(self) -> str:
        tables = "\n".join(self._proc_table_lines())
        if self._delta_engine() == "scan":
            return tables + "\n" + self._gen_delta_loop_scan()
        return tables + "\n" + self._gen_delta_loop_queue()

    def _proc_table_lines(self) -> list[str]:
        """Tables that let generated code loop over processes instead of
        unrolling one statement block per process:

        - ``DL_CONT_FN`` (declaration order), ``DL_RANK_FN`` (dispatch rank
          order, cont then combo -- ``_delta_ranked_processes``),
          ``DL_SEQ_FN``: function pointers, filled once at module import.
        - ``DL_SEQ_EDGE_OFF``/``_SID``/``_POS``: each seq process's edge
          triggers, CSR (``POS`` 1 = posedge, 0 = negedge).
        - ``DL_NEG_REACT[sid]``: whether a falling edge of ``sid`` can make
          anything react (batch_run's negedge skip, ``_negedge_block_lines``).

        Unrolled per-process code made the generated C grow with design
        size inside a few functions (``delta_loop``, ``__init__``,
        ``batch_run``, ``refresh_data_snapshot``), and gcc could inline every
        process body into them -- a 2000-lane design's single ``cc1`` ran
        out of memory on a 27 GB machine. Calls through these tables can't
        be inlined into the loops, which keeps every generated function
        small.
        """
        ranked = self._delta_ranked_processes()
        n_cont, n_seq = len(self._processes), len(self._seq_processes)
        lines = [
            "ctypedef void (*_dl_proc_fn)(SimCtx *c) noexcept nogil",
            "ctypedef void (*_dl_seq_fn)(SimCtx *c, long long *sv, long long *sm) noexcept nogil",
            f"cdef _dl_proc_fn DL_CONT_FN[{max(n_cont, 1)}]",
            f"cdef _dl_proc_fn DL_RANK_FN[{max(len(ranked), 1)}]",
            f"cdef _dl_seq_fn DL_SEQ_FN[{max(n_seq, 1)}]",
            "",
            "cdef void _dl_init_fn_tables() noexcept nogil:",
            "    pass",
            *(f"    DL_CONT_FN[{i}] = cont_{i}" for i in range(n_cont)),
            # `call` is "cont_N(c)" / "combo_N(c)".
            *(f"    DL_RANK_FN[{r}] = {call.split('(')[0]}" for r, (call, _s, _p) in enumerate(ranked)),
            *(f"    DL_SEQ_FN[{i}] = seq_{i}" for i in range(n_seq)),
            "",
            "_dl_init_fn_tables()",
            "",
        ]
        off, sids, pos = [0], [], []
        for edges, _sens, _body in self._seq_processes:
            for sid, edge_type in edges.items():
                sids.append(sid)
                pos.append(1 if edge_type == "posedge" else 0)
            off.append(len(sids))
        n = max(self._n_sigs, 1)
        neg_react = [0] * n
        for sid in self._negedge_reacting_sids() or ():
            neg_react[sid] = 1
        wide_offsets, wide_words, _total = self._wide_layout()
        arrays = [
            _c_const_array("int", "DL_SIG_WIDTH", list(self._signal_widths[: self._n_sigs])),
            _c_const_array("int", "DL_SIG_WIDE_WORDS", list(wide_words[: self._n_sigs])),
            _c_const_array("int", "DL_SIG_WIDE_OFFSET", list(wide_offsets[: self._n_sigs])),
            _c_const_array("int", "DL_SEQ_EDGE_OFF", off),
            _c_const_array("int", "DL_SEQ_EDGE_SID", sids),
            _c_const_array("unsigned char", "DL_SEQ_EDGE_POS", pos),
            _c_const_array("unsigned char", "DL_NEG_REACT", neg_react),
        ]
        ctypes = {
            "DL_SIG_WIDTH": "int",
            "DL_SIG_WIDE_WORDS": "int",
            "DL_SIG_WIDE_OFFSET": "int",
            "DL_SEQ_EDGE_OFF": "int",
            "DL_SEQ_EDGE_SID": "int",
            "DL_SEQ_EDGE_POS": "unsigned char",
            "DL_NEG_REACT": "unsigned char",
        }
        return lines + _c_const_array_block(arrays, ctypes)

    def _delta_ranked_processes(self) -> list[tuple[str, set[int], bool]]:
        """``(call, sens, pure)`` per rank, in dispatch order: continuous
        assigns in dependency (topological) order, then combinational always
        blocks in declaration order. A process's rank is its position in this
        list. ``pure``: a rerun on unchanged inputs is a no-op (see
        ``_cont_assign_is_rerun_pure``); combinational always blocks are never
        treated as pure (intermediate blocking writes re-mark signals dirty
        even when the end values don't change)."""
        cont_order, _acyclic = _cont_dependency_order(self._processes)
        ranked = [(f"cont_{i}(c)", self._processes[i][0], i in self._pure_conts) for i in cont_order]
        ranked.extend((f"combo_{i}(c)", sens, False) for i, (sens, _b) in enumerate(self._combo_processes))
        return ranked

    def _delta_interesting_sids(self) -> list[int]:
        """Every sid some cont/combo sensitivity check tests, plus every seq
        process's own edge-trigger sid (so a delayed clock edge -- propagated
        through several cont-assign hops -- still gets another iteration to be
        detected, even if nothing reads that sid combinationally). A dirty sid
        outside this set can never cause anything to happen: nothing ever
        inspects it."""
        return sorted(
            {s for sens, _b in self._processes for s in sens}
            | {s for sens, _b in self._combo_processes for s in sens}
            | {s for edges, _sens, _body in self._seq_processes for s in edges}
        )

    def _delta_common_local_lines(self) -> list[str]:
        lines: list[str] = []
        # Locals for NBA memory range drain (partial byte-lane writes)
        if self._n_mems > 0:
            lines.append("    cdef int _rmr_msb, _rmr_lsb")
            lines.append("    cdef long long _rmr_mask")
        # Edge detection: per-seq-process fire/done flags, computed inside the
        # delta loop so that edges propagated through continuous assigns are
        # detected (see _delta_seq_fire_lines).
        n_seq = len(self._seq_processes)
        if n_seq:
            lines.extend(
                [
                    "    cdef int _q, _qe, _qs, _hit",
                    f"    cdef unsigned char _sfire[{n_seq}]",
                    f"    cdef unsigned char _sdone[{n_seq}]",
                    f"    memset(_sfire, 0, {n_seq})",
                    f"    memset(_sdone, 0, {n_seq})",
                ]
            )
        return lines

    def _delta_conv_snapshot_lines(self) -> list[str]:
        # Value-level convergence: once past DELTA_CONV_CHECK_START iterations,
        # snapshot all signal values at the top of the iteration.  If the
        # iteration produces no value change, the state is a fixpoint — the
        # processes are deterministic functions of state, so further
        # iterations cannot change anything even if dirty flags survive
        # (combo loops with intermediate writes keep re-marking dirty).
        return [
            "        if it >= DELTA_CONV_CHECK_START:",
            "            memcpy(c.conv_val, c.val, N_SIGS * sizeof(long long))",
            "            memcpy(c.conv_mask, c.mask, N_SIGS * sizeof(long long))",
            "            memcpy(c.conv_wide_val, c.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
            "            memcpy(c.conv_wide_mask, c.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
        ]

    def _delta_seq_fire_lines(self) -> list[str]:
        """Edge detection, checked every iteration so edges propagated through
        continuous assigns are caught; each sequential process fires at most
        once per delta_loop call, in index order. Table-driven
        (``DL_SEQ_EDGE_*``, ``DL_SEQ_FN``; see ``_proc_table_lines``)."""
        if not self._seq_processes:
            return []
        n_seq = len(self._seq_processes)
        return [
            "",
            f"        for _q in range({n_seq}):",
            "            if _sdone[_q] or _sfire[_q]:",
            "                continue",
            "            for _qe in range(DL_SEQ_EDGE_OFF[_q], DL_SEQ_EDGE_OFF[_q + 1]):",
            "                _qs = DL_SEQ_EDGE_SID[_qe]",
            "                if DL_SEQ_EDGE_POS[_qe]:",
            "                    _hit = (c.val[_qs] & 1) == 1 and (sv[_qs] & 1) == 0",
            "                else:",
            "                    _hit = (c.val[_qs] & 1) == 0 and (sv[_qs] & 1) == 1",
            "                if _hit:",
            "                    _sfire[_q] = 1",
            "                    break",
            "",
            f"        for _q in range({n_seq}):",
            "            if _sfire[_q]:",
            "                DL_SEQ_FN[_q](c, sv, sm)",
            "                if c.finished:",
            "                    return it",
            "                if c.error_code != ERR_NONE:",
            "                    return it",
            "                _sfire[_q] = 0",
            "                _sdone[_q] = 1",
            "",
        ]

    def _delta_nba_apply_lines(self) -> list[str]:
        """Commit staged NBAs. Iterates only the staged sids (``nba_list``),
        not all N_SIGS; per-sid commits are independent, so the order they're
        visited in can't affect the result."""
        if not self._seq_processes:
            return []
        lines = [
            "        if c.nba_pending:",
            "            for _k in range(c.nba_count):",
            "                i = c.nba_list[_k]",
            "                if not c.nba_bit[i]:",
            "                    continue",
            "                if c.wide_words[i] > 0:",
            "                    _wchg = 0",
            "                    for _j in range(c.wide_words[i]):",
            "                        if c.wide_val[c.wide_offset[i] + _j] != c.wide_nba_val[c.wide_offset[i] + _j]"
            " or c.wide_mask[c.wide_offset[i] + _j] != c.wide_nba_mask[c.wide_offset[i] + _j]:",
            "                            c.wide_val[c.wide_offset[i] + _j] = c.wide_nba_val[c.wide_offset[i] + _j]",
            "                            c.wide_mask[c.wide_offset[i] + _j] = c.wide_nba_mask[c.wide_offset[i] + _j]",
            "                            _wchg = 1",
            "                    if c.nba_val[i] != c.val[i] or c.nba_mask[i] != c.mask[i]:",
            "                        c.val[i] = c.nba_val[i]",
            "                        c.mask[i] = c.nba_mask[i]",
            "                        _wchg = 1",
            "                    if _wchg:",
            "                        mark_dirty(c, i)",
            "                else:",
            "                    _nbaw = wmask(c.width[i])",
            "                    if (c.nba_val[i] & _nbaw) != c.val[i] or (c.nba_mask[i] & _nbaw) != c.mask[i]:",
            "                        c.val[i] = c.nba_val[i] & _nbaw",
            "                        c.mask[i] = c.nba_mask[i] & _nbaw",
            "                        mark_dirty(c, i)",
            "                c.nba_bit[i] = 0",
            "            c.nba_count = 0",
        ]
        if self._n_mems > 0:
            # Drain the NBA memory queue in program order. Whole-element and
            # partial (bit-range) writes share this one queue -- a whole
            # element is a range write covering it -- so for two NBAs to the
            # same element in one edge the later one wins. (They were once
            # two queues drained element-first, so `m[i][3:0] <= x;` then
            # `m[i] <= y;` left m[i][3:0] = x.)
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
                lines.extend(self._mem_journal_lines(mid, addr_expr, "                    "))
            lines.append("            c.nba_mem_range_count = 0")
        lines.append("            c.nba_pending = 0")
        return lines

    def _delta_value_convergence_lines(self) -> list[str]:
        # Value-level convergence check (see the snapshot above): if this
        # iteration changed no signal value, we are at a fixpoint — stop
        # even though dirty flags may survive.  When the NBA-apply block is
        # emitted (seq processes exist), skip the check while an NBA is
        # pending — its application next iteration may still change state.
        # Without seq processes there is no apply block, so nba_pending can
        # never clear and must not gate the check.
        lines = [""]
        if self._seq_processes:
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
        lines.extend(
            [
                "            _stable = 1",
                "            for i in range(N_SIGS):",
                "                if c.val[i] != c.conv_val[i] or c.mask[i] != c.conv_mask[i]:",
                "                    _stable = 0",
                "                    break",
                "            if _stable:",
                "                for i in range(N_WIDE_WORDS):",
                "                    if c.wide_val[i] != c.conv_wide_val[i] or c.wide_mask[i] != c.conv_wide_mask[i]:",
                "                        _stable = 0",
                "                        break",
                "            if _stable:",
                "                break",
            ]
        )
        return lines

    def _gen_delta_loop_scan(self) -> str:
        """Scan engine (the default): each iteration scans all N_SIGS for dirty
        sids and walks every cont/combo dispatch site, so per-iteration cost
        scales with total design size. Cheapest per executed process, so it
        wins on small designs and when most of a design is active."""
        ranked = self._delta_ranked_processes()
        lines = [
            "cdef int delta_loop(SimCtx *c, long long *sv, long long *sm) noexcept nogil:",
            "    cdef int it, i, changed, _j, _k, _wchg, _stable, _any_interesting_dirty, _sens_hit",
            "    cdef long long _nbaw",
            f"    cdef int trigger[{max(self._n_sigs, 1)}]",
            *self._delta_common_local_lines(),
            "",
            "    for it in range(DELTA_LIMIT):",
            # Copy dirty -> trigger, then clear dirty (bits and list together,
            # keeping the sparse set consistent).
            "        changed = 0",
            "        memcpy(trigger, c.dbit, N_SIGS * sizeof(int))",
            "        memset(c.dbit, 0, N_SIGS * sizeof(int))",
            "        c.dcount = 0",
            "        for i in range(N_SIGS):",
            "            changed |= trigger[i]",
            "        changed = changed != 0",
            "",
            # On the very first iteration, if nothing was externally dirtied
            # we still need to run all assigns once (bootstrap).
            "        if it == 0 and not changed:",
            "            for i in range(N_SIGS):",
            "                trigger[i] = 1",
            "            changed = 1",
            "",
            "        if not changed:",
            "            break",
            "",
            *self._delta_conv_snapshot_lines(),
            *self._delta_seq_fire_lines(),
            *self._delta_nba_apply_lines(),
        ]
        # Each cont/combo, in rank order, gated on its sensitivity set: fires
        # if an input was dirtied last iteration (trigger) or earlier this
        # iteration (dirty -- so a multi-hop chain in rank order settles in
        # one pass instead of one hop per iteration).
        for call, sens, _pure in ranked:
            if sens:
                lines.extend(_emit_sens_check_lines(sorted(sens), "        ", also_dirty=True))
                lines.append(f"            {call}")
                lines.append("            if c.finished:")
                lines.append("                return it")
                lines.append("            if c.error_code != ERR_NONE:")
                lines.append("                return it")
            else:
                lines.append(f"        {call}")
                lines.append("        if c.finished:")
                lines.append("            return it")
                lines.append("        if c.error_code != ERR_NONE:")
                lines.append("            return it")
        # Early exit: if this iteration's dispatch left no dirty flag on any
        # sid anything could ever react to, no further iteration can do
        # anything -- stop now. A genuine combinational loop keeps some
        # interesting sid dirty forever, so this never fires for one --
        # DELTA_LIMIT and the value-convergence check remain the safety net.
        lines.extend(_emit_no_dirty_check_lines(self._delta_interesting_sids(), "        "))
        lines.extend(self._delta_value_convergence_lines())
        lines.extend(
            [
                # for-else: fires only when the loop is NOT exited via break.
                "    else:",
                "        c.error_code = ERR_DELTA_LIMIT",
                "",
                # Leave a clean slate for the next call.
                "    memset(c.dbit, 0, N_SIGS * sizeof(int))",
                "    c.dcount = 0",
                "    return it",
            ]
        )
        return "\n".join(lines)

    def _delta_queue_static_lines(self, ranked: list[tuple[str, set[int], bool]]) -> list[str]:
        """The queue engine's static tables, baked into the module as C
        arrays (zero runtime construction cost):

        - ``DL_READER_OFF``/``DL_READER_RANK``: CSR reader index -- the ranks of
          the processes whose sensitivity set contains each sid (the relation
          ``_cont_dependency_order`` builds as ``readers`` for the topo sort).
        - ``DL_ALWAYS_RANK``: processes with an empty sensitivity set, which run
          every iteration.
        - ``DL_IS_INTERESTING``: the early-exit set (`_delta_interesting_sids`).
        - ``DL_PURE``: per rank, whether the process may skip a no-op rerun.
        - ``DL_IREADER_OFF``/``DL_IREADER_RANK``: the reader index restricted to
          impure readers.
        """
        n = max(self._n_sigs, 1)
        readers: list[set[int]] = [set() for _ in range(n)]
        always: list[int] = []
        for rank, (_call, sens, _pure) in enumerate(ranked):
            if sens:
                for s in sens:
                    readers[s].add(rank)
            else:
                always.append(rank)
        off = [0]
        flat: list[int] = []
        for rs in readers:
            flat.extend(sorted(rs))
            off.append(len(flat))
        # Same index restricted to impure readers: iterations after the first
        # seed only those from T_k, so this keeps that seeding from walking
        # (and skipping) every pure reader.
        pure_ranks = {rank for rank, (_c, _s, pure) in enumerate(ranked) if pure}
        ioff = [0]
        iflat: list[int] = []
        for rs in readers:
            iflat.extend(sorted(rs - pure_ranks))
            ioff.append(len(iflat))
        interesting = set(self._delta_interesting_sids())
        is_int = [1 if s in interesting else 0 for s in range(n)]

        arrays = [
            _c_const_array("int", "DL_READER_OFF", off),
            _c_const_array("int", "DL_READER_RANK", flat),
            _c_const_array("int", "DL_IREADER_OFF", ioff),
            _c_const_array("int", "DL_IREADER_RANK", iflat),
            _c_const_array("int", "DL_ALWAYS_RANK", always),
            _c_const_array("unsigned char", "DL_IS_INTERESTING", is_int),
            _c_const_array("unsigned char", "DL_PURE", [1 if pure else 0 for _c, _s, pure in ranked]),
        ]
        lines = [
            "cdef extern from *:",
            '    """',
            "    #if defined(_MSC_VER)",
            "    #include <intrin.h>",
            "    static __inline int dl_ctz64(unsigned long long x) {",
            "        unsigned long i; _BitScanForward64(&i, x); return (int)i;",
            "    }",
            "    #else",
            "    static inline int dl_ctz64(unsigned long long x) { return __builtin_ctzll(x); }",
            "    #endif",
            *(text for text, _name, _len in arrays),
            '    """',
            "    int dl_ctz64(unsigned long long x) noexcept nogil",
        ]
        ctypes = {"DL_IS_INTERESTING": "unsigned char", "DL_PURE": "unsigned char"}
        for _text, name, length in arrays:
            lines.append(f"    {ctypes.get(name, 'int')} {name}[{length}]")
        lines.append("")
        return lines

    def _gen_delta_loop_queue(self) -> str:
        """Queue engine: per-iteration cost proportional to real activity.

        Runs the processes the scan engine would, in the same rank order,
        minus *redundant reruns of pure processes*. In the scan engine,
        process ``P`` at rank ``r`` runs in iteration ``k`` iff it has an
        empty sensitivity set, or one of its inputs was dirtied during
        iteration ``k-1`` (``T_k``) or earlier in iteration ``k`` before
        ``P``'s turn. The ``T_k`` rule re-runs ``P`` even when it already ran
        *after* that write in iteration ``k-1`` -- with unchanged inputs. For
        pure processes (``DL_PURE``: rerun on unchanged inputs is a no-op) the
        queue engine skips exactly those: since a no-op rerun dirties nothing,
        signal values *and* iteration counts are unchanged.

        Realized with two pending bitmaps over ranks. The current one is
        seeded at iteration start from: every reader of ``T_k`` on iteration
        0 (nothing has run yet in this call) or on a cold start, else only
        the impure readers (exact scan schedule) plus the next-iteration
        bitmap ``_pnext``; the always-run processes; and readers of sids
        dirtied by the seq/NBA phase (which precedes every process). After
        running rank ``w``, each sid it wrote (per-call write log, so a
        second write in the same iteration counts too) marks its readers
        with rank ``> w`` in the current bitmap -- they run later this
        iteration, after the write -- and its *pure* readers with rank
        ``<= w`` in ``_pnext``: they already ran before the write, so they
        need exactly one more run, next iteration. (Impure readers with rank
        ``<= w`` get theirs from ``T_{k+1}``, as in the scan engine.) Marks in
        the current bitmap only land ahead of the cursor, so a forward scan
        visits pending ranks in order, each once.
        """
        ranked = self._delta_ranked_processes()
        n_ranks = len(ranked)
        n_words = max((n_ranks + 63) // 64, 1)
        n_always = sum(1 for _call, sens, _pure in ranked if not sens)
        any_pure = any(pure for _call, _sens, pure in ranked)
        set_bit = "_pend[_p >> 6] |= (<unsigned long long>1) << (_p & 63)"
        set_next_bit = "_pnext[_p >> 6] |= (<unsigned long long>1) << (_p & 63)"

        def seed_readers(indent: str, sid_expr: str, *, impure_only: bool = False) -> list[str]:
            off, rank = ("DL_IREADER_OFF", "DL_IREADER_RANK") if impure_only else ("DL_READER_OFF", "DL_READER_RANK")
            body = [
                f"{indent}_s = {sid_expr}",
                f"{indent}for _e in range({off}[_s], {off}[_s + 1]):",
                f"{indent}    _p = {rank}[_e]",
            ]
            inner = indent + "    "
            return [
                *body,
                f"{inner}{set_bit}",
                f"{inner}if _p < _pmin:",
                f"{inner}    _pmin = _p",
                f"{inner}if _p > _pmax:",
                f"{inner}    _pmax = _p",
            ]

        lines = [
            *self._delta_queue_static_lines(ranked),
            "cdef int delta_loop(SimCtx *c, long long *sv, long long *sm) noexcept nogil:",
            "    cdef int it, i, changed, _j, _k, _e, _s, _p, _r, _w, _wchg, _stable",
            "    cdef int _tcount, _pmin, _pmax, _nmin, _nmax, _bootstrap, _any_int",
            "    cdef long long _nbaw",
            "    cdef unsigned long long _x",
            f"    cdef int _tlist[{max(self._n_sigs, 1)}]",
            f"    cdef unsigned long long _pend[{n_words}]",
            f"    cdef unsigned long long _pnext[{n_words}]",
            *self._delta_common_local_lines(),
            # Every bit of the current bitmap is popped before an iteration
            # ends, and _pnext is folded into it at the next iteration's start;
            # only an early return mid-dispatch (or a loop exit) can leave bits
            # set, so both are cleared once per call.
            f"    memset(_pend, 0, {n_words} * sizeof(unsigned long long))",
            f"    memset(_pnext, 0, {n_words} * sizeof(unsigned long long))",
            f"    _nmin = {n_ranks}",
            "    _nmax = -1",
            "",
            "    for it in range(DELTA_LIMIT):",
            # T_k: the sids marked dirty since the previous iteration (or, for
            # it == 0, since the previous call). Take them and clear their bits,
            # so this iteration's marks accumulate fresh in dlist.
            "        _tcount = c.dcount",
            "        memcpy(_tlist, c.dlist, _tcount * sizeof(int))",
            "        for _k in range(_tcount):",
            "            c.dbit[_tlist[_k]] = 0",
            "        c.dcount = 0",
            "        changed = _tcount > 0",
            # Cold start: nothing dirty on the very first iteration -> every
            # process is pending (same as the scan engine's trigger-everything).
            "        _bootstrap = 0",
            "        if it == 0 and not changed:",
            "            _bootstrap = 1",
            "            changed = 1",
            "        if not changed:",
            "            break",
            "",
            *self._delta_conv_snapshot_lines(),
            *self._delta_seq_fire_lines(),
            *self._delta_nba_apply_lines(),
        ]
        if n_ranks > 0:
            lines.extend(
                [
                    "",
                    f"        _pmin = {n_ranks}",
                    "        _pmax = -1",
                    "        if _bootstrap:",
                    f"            for _p in range({n_ranks}):",
                    f"                {set_bit}",
                    "            _pmin = 0",
                    f"            _pmax = {n_ranks - 1}",
                    # Iteration 0: no process has run yet in this call, so every
                    # reader of every externally dirtied sid must run.
                    "        elif it == 0:",
                    "            for _k in range(_tcount):",
                    *seed_readers("                ", "_tlist[_k]"),
                    "        else:",
                    # Impure readers keep the scan engine's T_k schedule exactly.
                    "            for _k in range(_tcount):",
                    *seed_readers("                ", "_tlist[_k]", impure_only=True),
                ]
            )
            if any_pure:
                lines.extend(
                    [
                        # Pure readers that still owe a run (marked during the
                        # previous iteration's dispatch).
                        "            if _nmax >= 0:",
                        "                for _w in range(_nmin >> 6, (_nmax >> 6) + 1):",
                        "                    _pend[_w] |= _pnext[_w]",
                        "                    _pnext[_w] = 0",
                        "                if _nmin < _pmin:",
                        "                    _pmin = _nmin",
                        "                if _nmax > _pmax:",
                        "                    _pmax = _nmax",
                        f"                _nmin = {n_ranks}",
                        "                _nmax = -1",
                    ]
                )
            if n_always:
                lines.extend(
                    [
                        f"        for _k in range({n_always}):",
                        "            _p = DL_ALWAYS_RANK[_k]",
                        f"            {set_bit}",
                        "            if _p < _pmin:",
                        "                _pmin = _p",
                        "            if _p > _pmax:",
                        "                _pmax = _p",
                    ]
                )
            lines.extend(
                [
                    # Sids dirtied by the seq-fire/NBA-apply phase, which
                    # precedes every cont/combo: all their readers are pending.
                    "        for _k in range(c.dcount):",
                    *seed_readers("            ", "c.dlist[_k]"),
                    "",
                    "        _r = _pmin",
                    "        while _r <= _pmax:",
                    "            _w = _r >> 6",
                    "            _x = _pend[_w] & ((~(<unsigned long long>0)) << (_r & 63))",
                    "            if _x == 0:",
                    "                _r = (_w + 1) << 6",
                    "                continue",
                    "            _r = (_w << 6) + dl_ctz64(_x)",
                    "            _pend[_w] &= ~((<unsigned long long>1) << (_r & 63))",
                    # Fresh per-call write log (mark_dirty dedups by epoch).
                    "            c.wepoch += 1",
                    "            c.wcount = 0",
                ]
            )
            # Rank -> process call through the function table (see
            # _proc_table_lines: an unrolled per-rank switch with inlined
            # bodies made delta_loop too large to compile for big designs).
            lines.append("            DL_RANK_FN[_r](c)")
            lines.extend(
                [
                    "            if c.finished:",
                    "                return it",
                    "            if c.error_code != ERR_NONE:",
                    "                return it",
                    "            for _k in range(c.wcount):",
                    "                _s = c.wlog[_k]",
                    "                for _e in range(DL_READER_OFF[_s], DL_READER_OFF[_s + 1]):",
                    "                    _p = DL_READER_RANK[_e]",
                    "                    if _p > _r:",
                    f"                        {set_bit}",
                    "                        if _p > _pmax:",
                    "                            _pmax = _p",
                    "                    elif DL_PURE[_p]:",
                    f"                        {set_next_bit}",
                    "                        if _p < _nmin:",
                    "                            _nmin = _p",
                    "                        if _p > _nmax:",
                    "                            _nmax = _p",
                    "            _r += 1",
                ]
            )
        lines.extend(
            [
                "",
                # Early exit: nothing dirtied this iteration that any process
                # (or any seq edge check) could react to -> no further
                # iteration can do anything. (A _pnext mark implies its sid was
                # dirtied and has a reader, so it can't be pending here.) A
                # genuine combinational loop keeps some interesting sid dirty
                # forever, so this never fires for one -- DELTA_LIMIT and the
                # value-convergence check remain the safety net.
                "        _any_int = 0",
                "        for _k in range(c.dcount):",
                "            if DL_IS_INTERESTING[c.dlist[_k]]:",
                "                _any_int = 1",
                "                break",
                "        if not _any_int:",
                "            break",
                *self._delta_value_convergence_lines(),
                # for-else: fires only when the loop is NOT exited via break.
                "    else:",
                "        c.error_code = ERR_DELTA_LIMIT",
                "",
                # Leave a clean slate for the next call.
                "    for _k in range(c.dcount):",
                "        c.dbit[c.dlist[_k]] = 0",
                "    c.dcount = 0",
                "    return it",
            ]
        )
        return "\n".join(lines)

    def _gen_compiled_sim(self) -> str:
        sn = max(self._n_sigs, 1)
        lines = [
            "cdef class CompiledSim:",
            "    cdef SimCtx ctx",
            f"    cdef long long _snap_v[{sn}]",
            f"    cdef long long _snap_m[{sn}]",
            # Trace set (see _trace_method_lines); NULL until trace_set().
            "    cdef int _tr_n",
            "    cdef unsigned long long **_tr_vp",
            "    cdef unsigned long long **_tr_mp",
            "    cdef int *_tr_nw",
            "    cdef int *_tr_off",
            "    cdef unsigned long long *_tr_lv",
            "    cdef unsigned long long *_tr_lm",
            "    cdef int *_tr_chg",
            "    cdef int _tr_words",
            # batch_run change recording (see _trace_method_lines).
            "    cdef int _rec_on, _rec_full, _rec_n, _rec_cap, _rec_wn, _rec_wcap",
            "    cdef int *_rec_slot",
            "    cdef int *_rec_woff",
            "    cdef long long *_rec_time",
            "    cdef unsigned long long *_rec_w",
            "",
            "    def __init__(self):",
            "        cdef int i, _iw",
            # Sparse-set counts first: init lines below may mark_dirty().
            "        self.ctx.dcount = 0",
            "        self.ctx.nba_count = 0",
            "        self.ctx.wcount = 0",
            "        self.ctx.snap_stale_mid = -1",
            "        self.ctx.wepoch = 1",
            "        for i in range(N_SIGS):",
            "            self.ctx.val[i] = 0",
            "            self.ctx.mask[i] = 0",
            "            self.ctx.width[i] = 0",
            "            self.ctx.wide_words[i] = 0",
            "            self.ctx.wide_offset[i] = 0",
            "            self.ctx.dbit[i] = 0",
            "            self.ctx.sdirty[i] = 1",
            "            self.ctx.wmark[i] = 0",
            "            self.ctx.nba_val[i] = 0",
            "            self.ctx.nba_mask[i] = 0",
            "            self.ctx.nba_bit[i] = 0",
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
        # Per-signal width/offset/mask init from the DL_SIG_* tables
        # (_proc_table_lines) -- one loop, not several statements per signal.
        lines.extend(
            [
                f"        for i in range({self._n_sigs}):",
                "            self.ctx.width[i] = DL_SIG_WIDTH[i]",
                "            self.ctx.wide_words[i] = DL_SIG_WIDE_WORDS[i]",
                "            self.ctx.wide_offset[i] = DL_SIG_WIDE_OFFSET[i]",
                "            self.ctx.mask[i] = wmask(DL_SIG_WIDTH[i])",
                "            for _iw in range(DL_SIG_WIDE_WORDS[i]):",
                "                self.ctx.wide_mask[DL_SIG_WIDE_OFFSET[i] + _iw] = _word_mask64(DL_SIG_WIDTH[i] - _iw * 64)",
            ]
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
            lines.append(f"        self.ctx.memj_{mid}_n = 0")
        if self._n_mems > 0:
            lines.append("        self.ctx.nba_mem_range_count = 0")

        # Native initial block execution (no timing)
        if self._initial_lines:
            lines.append("        # Initial block values")
            # Use a pointer alias so _emit_stmt's c.val[...] syntax works
            lines.append("        cdef SimCtx *c = &self.ctx")
            # `_hoist_inline_cdefs` (see its own docstring) hoists any
            # `cdef ... = expr` an emitter placed inside a conditional/loop
            # block up to this function's top -- Cython forbids `cdef`
            # there. This used to be a hand-maintained whitelist of known
            # scratch-var markers (`_mwi`/`_mwv`/...), which missed any
            # NEW inline-`cdef`-using helper added later (confirmed: a
            # wide, dynamically-indexed whole-memory-element write inside
            # an `initial`-block `for` loop uses `_emit_wide_mem_dynamic_
            # range_lines`'s own locals -- `idx`, `range_msb`, ... -- none
            # of which were on the whitelist, so they survived, nested
            # inside the loop, straight into the .pyx and failed to
            # compile: "cdef statement not allowed here"). Reusing the
            # same general hoisting pass the other process-function
            # generators already use (`_gen_sections.py`'s other two
            # `_hoist_inline_cdefs` call sites) fixes this whole class of
            # bug at once instead of whitelisting one more name. Its own
            # hoisted lines are always 4-space-indented (matching a
            # standalone `cdef ...:` function's body); this call site is
            # nested one level deeper (inside `__init__`), hence the extra
            # 4-space prefix below.
            hoisted_cdefs, initial_lines = _hoist_inline_cdefs(self._initial_lines)
            lines.extend(f"    {ln}" for ln in hoisted_cdefs)
            if any("_clhs" in ln for ln in initial_lines):
                lines.append("        cdef long long _clhs")
            if any("_cdv" in ln for ln in initial_lines):
                lines.append("        cdef long long _cdv")
            if any("_cdm" in ln for ln in initial_lines):
                lines.append("        cdef long long _cdm")
            if any("_sfv" in ln for ln in initial_lines):
                lines.append("        cdef long long _sfv")
            if any("_mchg" in ln for ln in initial_lines):
                lines.append("        cdef int _mchg")
            if any("_mwi" in ln for ln in initial_lines):
                lines.append("        cdef long long _mwi")
            if any("_mwv" in ln for ln in initial_lines):
                lines.append("        cdef long long _mwv, _mwm")
            if any("_mwvu" in ln for ln in initial_lines):
                lines.append("        cdef unsigned long long _mwvu, _mwmu")
            initial_joined = "\n".join(initial_lines)
            if "_rmw_msb" in initial_joined:
                lines.append("        cdef int _rmw_msb, _rmw_lsb")
                lines.append("        cdef long long _rmw_mask")
            if "_ps_lsb" in initial_joined:
                lines.append("        cdef int _ps_lsb")
                lines.append("        cdef long long _ps_mask")
            for m in sorted(set(re.findall(r"\b(_lv_\w+)\b", initial_joined))):
                lines.append(f"        cdef long long {m}")
            # Scratch arrays (`_emit_wide_expr_to_scratch`'s own recursive
            # temporaries) -- same reasoning as `_gen_process_functions`'s
            # identical scan: these are fixed-size C arrays, not simple
            # scalars, so `_hoist_inline_cdefs` above doesn't cover them.
            sc_indices = sorted({int(s) for s in re.findall(r"_sc(\d+)_[vm]", initial_joined)})
            if sc_indices:
                max_words = self._module_max_wide_words()
                for sc_i in range(sc_indices[-1] + 1):
                    lines.append(f"        cdef unsigned long long _sc{sc_i}_v[{max_words}]")
                    lines.append(f"        cdef unsigned long long _sc{sc_i}_m[{max_words}]")
            lines.extend(initial_lines)
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
                "            mark_dirty(&self.ctx, sid)",
            ]
        )

        lines.extend(
            [
                "",
                "    cpdef void mark_all_dirty(self):",
                "        cdef int i",
                "        for i in range(N_SIGS):",
                "            mark_dirty(&self.ctx, i)",
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
                "            mark_dirty(&self.ctx, sid)",
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
            # Settle all continuous assigns to a FIXED POINT (not just one
            # pass) so coro-driven signals (e.g. bench STALL_REQ) propagate
            # to port-connected submodule signals (e.g. u_stall.STALL_REQ),
            # and so a multi-hop continuous-assign chain declared "out of
            # order" relative to its own dependencies still converges
            # before we snapshot -- see `_cont_settle_fixpoint_lines`'s own
            # docstring for the full story (and the confirmed bug a single
            # pass here caused). Without full convergence, the snapshot
            # captures an under-propagated value and sequential processes
            # see stale data at the posedge.
            settle_lines = (
                [
                    "        cdef int _cont_settle_it, _cont_settle_stable, _cont_settle_sid",
                    *self._cont_settle_fixpoint_lines("        "),
                ]
                if self._processes
                else []
            )
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
                "        if self.ctx.error_code == ERR_NBA_MEM_OVERFLOW:",
                "            raise RuntimeError('Non-blocking memory write queue overflowed (more than "
                f"{self._nba_mem_queue_capacity() if self._n_mems else 0} pending memory NBAs in one delta"
                " iteration)')",
                "        if self.ctx.snap_stale_mid >= 0:",
                "            raise RuntimeError(f'Memory {self.ctx.snap_stale_mid} changed without updating its"
                " incremental snapshot tracking (dirty flag or slot journal): snapshot went stale"
                " (VERIFORGE_CHECK_MEM_SNAPSHOT)')",
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

        lines.extend(self._trace_method_lines())

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
                lines.append(f"            mark_dirty(&self.ctx, {marker_sid})")
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
                lines.append(f"            mark_dirty(&self.ctx, {marker_sid})")

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
                    f"                        mark_dirty(&self.ctx, {marker_sid})",
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
                "                        long long[::1] ev_mem_vals=None, long long t0=0, long long period=0):",
                "        cdef int i, ev_idx = 0, mem_ev_idx = 0, cycles_run = cycles",
                *(
                    ["        cdef int _cont_settle_it, _cont_settle_stable, _cont_settle_sid"]
                    if self._processes
                    else []
                ),
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
                '                # notes/developer/roadmap.md "batch_run() first-call clock-state").',
                *self._cont_settle_fixpoint_lines("                "),
                f"                memcpy(sv, self.ctx.val, {sn} * sizeof(long long))",
                f"                memcpy(sm, self.ctx.mask, {sn} * sizeof(long long))",
                "                memcpy(self.ctx.wide_snap_val, self.ctx.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
                "                memcpy(self.ctx.wide_snap_mask, self.ctx.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
                *self._mem_snap_memcpy_lines("                "),
                "                self.ctx.val[clk_sid] = 0",
                "                self.ctx.mask[clk_sid] = 0",
                "                mark_dirty(&self.ctx, clk_sid)",
                "                delta_loop(&self.ctx, sv, sm)",
                "                if self.ctx.error_code != ERR_NONE:",
                "                    cycles_run = 0",
                "                elif self._rec_on:",
                "                    self._trace_record(t0)",
                "            for i in range(cycles if self.ctx.error_code == ERR_NONE else 0):",
                "                if self._rec_on and not self._trace_room():",
                "                    # Trace buffer full: stop at this cycle boundary;",
                "                    # the caller drains it and resumes.",
                "                    self._rec_full = 1",
                "                    cycles_run = i",
                "                    break",
                "                # Apply any scheduled events for this cycle",
                "                ev_applied = 0",
                "                while ev_idx < n_events and ev_cycles[ev_idx] == i:",
                "                    self.ctx.val[ev_sids[ev_idx]] = ev_vals[ev_idx]",
                "                    self.ctx.mask[ev_sids[ev_idx]] = 0",
                "                    mark_dirty(&self.ctx, ev_sids[ev_idx])",
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
                "                # Snapshot before posedge.  Only the first cycle can",
                "                # enter with un-settled continuous assigns (reactive",
                "                # drives before this call); every later cycle follows a",
                "                # delta_loop() that already ran to convergence. Settle",
                "                # through delta_loop() itself (dirty-gated, activity-",
                "                # proportional) rather than a separate cont-only,",
                "                # unconditional N_cont-bounded pass -- see",
                "                # notes/plans/work_queue_delta_engine.md, Stage 1.",
                "                if i == 0:",
                *(f"    {ln}" for ln in self._settle_via_delta_loop_lines(sn, "                ")),
                "                    if self.ctx.error_code != ERR_NONE:",
                "                        cycles_run = i + 1",
                "                        break",
                f"                memcpy(sv, self.ctx.val, {sn} * sizeof(long long))",
                f"                memcpy(sm, self.ctx.mask, {sn} * sizeof(long long))",
                "                memcpy(self.ctx.wide_snap_val, self.ctx.wide_val, N_WIDE_WORDS * sizeof(unsigned long long))",
                "                memcpy(self.ctx.wide_snap_mask, self.ctx.wide_mask, N_WIDE_WORDS * sizeof(unsigned long long))",
                *self._mem_snap_memcpy_lines("                "),
                # $time inside processes: posedge at t0 + i*period, negedge
                # half a period later (matching the scheduler's own time
                # arithmetic after the call).
                "                self.ctx.sim_time = t0 + i * period",
                "                # Posedge: drive clk high",
                "                self.ctx.val[clk_sid] = 1",
                "                self.ctx.mask[clk_sid] = 0",
                "                mark_dirty(&self.ctx, clk_sid)",
                "                delta_loop(&self.ctx, sv, sm)",
                "                if self.ctx.error_code != ERR_NONE:",
                "                    cycles_run = i + 1",
                "                    break",
                "                if self._rec_on:",
                "                    self._trace_record(t0 + i * period)",
                "                if self.ctx.finished:",
                "                    cycles_run = i + 1",
                "                    break",
                *self._negedge_block_lines(sn),
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
