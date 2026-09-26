# Compiled Engine Performance Investigation — September 2026

## Background

Recent correctness work on the compiled engine has introduced measurable performance
regressions. This plan documents the root causes identified, the specific code locations
involved, and the implementation approach for each fix, in priority order.

### Benchmark Baseline (as of Sep 2026)

DUT: `ALU + RegFile + FSM + Counter + LFSR + Accumulator + Continuous Assigns`
Run for 50,000 cycles. Full definition in `benchmarks/benchmark.py`.

| Engine              | cyc/s     | vs Icarus  |
|---------------------|-----------|------------|
| Compiled (batch)    | 177.9K    | 1.8×       |
| Compiled (step)     | 44.9K     | 0.46×      |
| Icarus Verilog      | 98.1K     | 1.0×       |
| Verilator           | 7.34M     | 75×        |

**Goal**: Close the gap with Icarus on step, and close some of the gap with Verilator on
batch. The step path is structurally limited by Python↔C boundary crossing per-cycle;
the batch path is where meaningful gains are achievable.

---

## Finding 1 — Delta Loop Iteration Count (PRIMARY BOTTLENECK)

### Root Cause

The benchmark DUT has a 4–5 hop continuous-assign dependency chain:

```
NBA writes → rd_addr_a/b → rd_data_a/b → sum_ab/diff_ab → combined → flag
```

The `delta_loop` in `_gen_sections.py:_gen_delta_loop()` (line 1275) is trigger-based:
each iteration copies `dirty[]` → `trigger[]`, clears `dirty[]`, fires triggered processes,
which may re-mark `dirty[]`. A signal dirtied in iteration N only appears in `trigger[]`
on iteration N+1. So a 5-hop chain requires ~6 iterations to converge.

Each iteration does:
- `for i in range(N_SIGS): trigger[i] = dirty[i]; dirty[i] = 0` — 31-element scan
- Fire all triggered seq processes (~5 processes × `fire_seq_N` guards)
- NBA drain loop
- Fire all triggered cont/combo processes (~7 cont + any combo)
- Optional convergence-check memcpy (after `DELTA_CONV_CHECK_START` iterations)

Verilator emits all processes in topological order and executes one pass with no dirty
flags and no iteration. This explains most of the ~41× gap.

### Proposed Fix: Topological Sort of Continuous Assigns at Codegen Time

At code generation time, analyze continuous-assign dependencies and emit `cont_N()`
calls inside `delta_loop` in dependency order. If achieved, `delta_loop` would converge
in 1–2 iterations for typical designs instead of N-hop iterations.

**Implementation sketch:**

1. In `_gen_sections.py`, after all processes are collected (before `_gen_delta_loop`),
   build a dependency graph among the cont processes:
   - For each `cont_i`, record which signal IDs it **reads** (its sensitivity set) and
     which it **writes** (its output signals).
   - A directed edge `cont_i → cont_j` means `cont_i` writes a signal that `cont_j` reads.

2. Topological-sort the cont processes by this graph (Kahn's algorithm or DFS).
   - If the graph is acyclic (combinational), the sorted order is the single-pass order.
   - If there's a cycle (combinational loop — a design error), fall back to the current
     trigger-based approach (the loop will hit `DELTA_LIMIT` regardless).

3. In `_gen_delta_loop`, emit cont process calls in the sorted order rather than the
   declared order. The `trigger[]` check per cont process can then be simplified or
   removed entirely — in sorted order, a single unconditional pass suffices since each
   process's inputs are already settled by prior calls.

4. The `_cont_settle_fixpoint_lines` helper in `batch_run` (see Finding 2) then becomes
   fully redundant since a single sorted pass guarantees fixpoint in one iteration.

**Where to look:**
- `src/veriforge/sim/compiled/_gen_sections.py` — `_gen_delta_loop()` at line 1275
- Process data lives in `self._processes` (cont), `self._seq_processes`, `self._combo_processes`
- Signal sensitivity info should be available from the process IR objects

**Expected impact**: 2–3× speedup on compiled batch path for typical designs.

**Risk**: Medium. The topo-sort logic must correctly handle wide signals, memories, and
multi-output processes. Requires new tests covering out-of-order declarations (the
`TestContinuousAssignSnapshotConvergence` test in
`tests/test_sim/compiled/test_scheduling.py` is the existing regression anchor).

---

## Finding 2 — Redundant `_cont_settle_fixpoint_lines` Calls in `batch_run`

### Root Cause

`_cont_settle_fixpoint_lines` (`_gen_sections.py:349–414`) generates a fixpoint settle
loop: runs all `n` cont processes + 31-element stability scan, repeated up to `n` times
with early exit. For the benchmark DUT: `range(7)`, 4 memcpys, 7 cont calls, 31-element
stability scan per pass.

It is currently called at **four points** in generated code:

| Location | Line in `_gen_sections.py` | Context |
|----------|---------------------------|---------|
| `refresh_data_snapshot()` | 1824 | Python step path pre-snapshot |
| `batch_run()` clock-was-high path | 2008 | First-call recovery negedge |
| `batch_run()` **before posedge snapshot** | 2065 | Hot path, every cycle |
| `batch_run()` **before negedge snapshot** | 2083 | Hot path, every cycle |

The calls at **lines 2065 and 2083** were added to fix a real correctness bug
(documented in `notes/developer/roadmap.md` as "mismatch_10096"): if a cont assign is
declared out of order relative to its dependencies, a single blind pass at snapshot time
bakes the stale pre-settle value into `sv[]` forever. The fix was correct.

**However**, the calls at 2065/2083 are now provably redundant once Finding 1's
topological sort is implemented: a sorted pass is already a fixpoint in one pass, so the
fixpoint loop at 2065/2083 exits after its first iteration having done the exact same work
`delta_loop` already completed.

**Even before Finding 1**, the calls at 2065/2083 are partially redundant: `delta_loop`
runs to convergence before the snapshot, so by the time the fixpoint loop at line 2065
runs, signals are already settled (the loop exits after one no-op iteration). The cost
is exactly: 4 memcpys + 7 cont calls + 31-element compare + branch = ~42 operations,
twice per cycle.

### Proposed Fix

**After implementing Finding 1 (topo sort):** Remove the calls at lines 2065 and 2083
entirely. The topo-sorted delta_loop already guarantees fixpoint.

**Before Finding 1 (standalone fix):** The calls at 2065/2083 are still doing real work
for the `ev_applied` path (lines 2036–2045), where `delta_loop` is called to settle an
externally driven event, but then immediately another snapshot is taken. In that specific
case, removing the fixpoint calls would be unsafe. However, the common case (no events
applied) still pays the full cost. A conditional guard — `if ev_applied:` around the
fixpoint call at 2065 — would be a safe partial fix.

**The call at line 2008** (clock-was-high recovery path) should be **retained**: it's a
cold-start path that needs settling before the negedge snapshot.

**The call in `refresh_data_snapshot()`** (line 1824) should also be **retained**: that
path is used by the reactive step/settle interface and does not have a preceding
`delta_loop` call to rely on.

**Where to look:**
- `src/veriforge/sim/compiled/_gen_sections.py` lines 2046–2083 for the batch_run
  posedge/negedge snapshot section with the comments explaining the correctness rationale

**Expected impact**: ~3–5% on batch path (minor).

**Risk**: Low. The existing `TestContinuousAssignSnapshotConvergence` test covers the
correctness case that motivated these calls. Run it (and the full compiled test suite)
after any change to these call sites.

---

## Finding 3 — SimCtx Struct Layout (Cache Pressure)

### Root Cause

In `_struct_field_lines()` (`_gen_sections.py:620–686`), the SimCtx struct field
ordering places `out_buf[65536]` (64 KB) **before** `finished`, `error_code`, and all
memory arrays. For the benchmark DUT (31 signals, 1 memory):

```
Offset  0     : val[31], mask[31], width[31], ...    (~1.5 KB of hot simulation state)
Offset ~2,184 : out_buf[65536]                        (64 KB output buffer)
Offset ~67,720: out_count, finished, error_code       (3 ints — checked constantly)
Offset ~67,732: mem_0_val[32], mem_0_mask[32], ...    (memory arrays — accessed each NBA)
Offset ~67,988: nba_mem_count, nba_mem_mid[], ...     (NBA memory queue)
```

`finished` and `error_code` are checked after **every process call** inside `delta_loop`:

```c
if c.finished: return it          # line ~1352 in generated delta_loop
if c.error_code != ERR_NONE: return it
```

With ~12 process calls × 6 delta iterations = ~72 reads of `finished` per cycle, these
should be resident in L1 cache. But at offset 67.7 KB in a 32 KB L1 cache, they
alias to the same cache sets as state at offset 67720 - 32768 = 34952 bytes earlier —
potentially evicting hot simulation state on every access.

Memory arrays (`mem_0_val` etc.) at ~67.7 KB offset also land cold on first NBA drain
each cycle.

### Proposed Fix

Move `out_buf` to the **last** field in SimCtx. Change `_struct_field_lines()` so the
order becomes:

```
val, mask, width, wide_words, wide_offset,
nba_val, nba_mask, wide_nba_val, wide_nba_mask,
nba_dirty, dirty, nba_pending,
wide_val, wide_mask, wide_snap_val, wide_snap_mask,
conv_val, conv_mask, conv_wide_val, conv_wide_mask,
sim_time,
finished, error_code, out_count,   ← moved before out_buf
<memory arrays>,                    ← moved before out_buf
<nba_mem queue>,                    ← moved before out_buf
out_buf[OUT_BUF_MAX]               ← last: cold output buffer
```

This keeps all hot simulation state within the first ~4 KB of SimCtx, fully within L1.

**Where to look:**
- `src/veriforge/sim/compiled/_gen_sections.py` `_struct_field_lines()` lines 627–686
- The struct is declared in-place in both `_gen_struct()` (line 688) and
  `_gen_struct_extern()` (line 691); both delegate to `_struct_field_lines()` so only
  one change is needed.
- The compiled `.pyx` files are **generated** — no need to edit `.pyx` files directly.
  The `.cycache` directory is rebuilt on next import or explicit recompile.

**Correctness note:** The struct layout only affects C-level access patterns; the Python
API is unchanged. The only risk is a field-offset mismatch if any code hard-codes struct
offsets (it does not — all access is by field name via Cython's struct support).

**Expected impact**: ~5–10% on batch path for memory-heavy designs; smaller for the
benchmark DUT which has only 1 shallow memory.

**Risk**: Trivial. One-line reorder per field block. Invalidates the `.cycache` (expected
— any struct change does).

---

## Implementation Order

1. **Finding 3 first** (trivial, standalone, no correctness risk): reorder `out_buf`
   in `_struct_field_lines()`. Verify by running benchmarks and confirming no test
   regressions.

2. **Finding 2 partial fix** (add `if ev_applied:` guard around fixpoint call at 2065,
   independently of Finding 1): small gain, confirms correctness guardrails.

3. **Finding 1** (topo sort): largest impact, most involved. Once done, the full fixpoint
   calls at 2065/2083 can be removed entirely (Finding 2 full fix).

---

## Key Correctness Anchors

Before and after each change, run:

```bash
pytest tests/test_sim/compiled/ -x -q
pytest tests/test_sim/compiled/test_scheduling.py::TestContinuousAssignSnapshotConvergence -v
pytest tests/test_sim/ -x -q   # full cross-engine suite
python benchmarks/benchmark.py  # confirm numbers improve
```

The `TestContinuousAssignSnapshotConvergence` test in
`tests/test_sim/compiled/test_scheduling.py` is the specific regression guard for the
out-of-order continuous-assign fix. It must pass before any change to the
`_cont_settle_fixpoint_lines` call sites is considered safe.

---

## File Reference

| File | Purpose |
|------|---------|
| `src/veriforge/sim/compiled/_gen_sections.py` | All codegen; primary edit target |
| `src/veriforge/sim/compiled/_gen_sections.py:349` | `_cont_settle_fixpoint_lines()` definition |
| `src/veriforge/sim/compiled/_gen_sections.py:620` | `_struct_field_lines()` — struct layout |
| `src/veriforge/sim/compiled/_gen_sections.py:1275` | `_gen_delta_loop()` — delta loop codegen |
| `src/veriforge/sim/compiled/_gen_sections.py:1979` | `_gen_compiled_sim()` — batch_run codegen |
| `src/veriforge/sim/compiled/_gen_sections.py:2008` | `_cont_settle_fixpoint` call #1 (keep) |
| `src/veriforge/sim/compiled/_gen_sections.py:2065` | `_cont_settle_fixpoint` call #2 (optimize) |
| `src/veriforge/sim/compiled/_gen_sections.py:2083` | `_cont_settle_fixpoint` call #3 (optimize) |
| `src/veriforge/sim/compiled/_gen_sections.py:1824` | `refresh_data_snapshot` (keep) |
| `benchmarks/benchmark.py` | Benchmark script + DUT definition |
| `tests/test_sim/compiled/test_scheduling.py` | Key correctness regression tests |
| `notes/developer/roadmap.md:460` | "mismatch_10096" — root cause of fixpoint fix |

---

## Notes on the Step Path

The compiled **step** path (44.9K cyc/s, 0.46× Icarus) is structurally limited by
Python↔Cython boundary overhead: every `step()` call crosses the boundary, and
`snapshot()` does a full `N_SIGS`-element memcpy of `sv[]`/`sm[]` before the call.
These are architectural costs of the step API that cannot be eliminated without changing
the public interface. The batch path is the right target for performance work.
