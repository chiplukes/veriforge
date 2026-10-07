# Work-Queue Delta Engine — Scaling Fix for Large Flattened Designs

## Status

**Stages 0, 1, and 2 are done, plus redundant-rerun elimination.** The queue
engine is verified equivalent to the scan engine (identical signal values and
identical delta-iteration counts on every step). **Default (2026-10-06):
`auto`** -- queue for designs with more than 64 continuous-assign/combinational
processes, scan otherwise (`AUTO_QUEUE_MIN_PROCESSES`); `VERIFORGE_DELTA_ENGINE`
forces either. Measured queue/scan: 1.5-5.6x on larger designs at
low-to-moderate activity, 0.65-1.0x on synthetic fully-active designs, ~0.73x
on the tiny `benchmark.py` DUT (which `auto` keeps on scan).

**`gfwx-fpga` result (2026-10-06):** scan ~444.9 us/cycle, queue ~431.0
us/cycle (~1.03x), with faster compiles (290.5 s vs 371.1 s). So the
delta-loop bookkeeping this plan targeted is *not* what dominates that
design. **Root cause found** by reading its generated module
(`gfwx-fpga/.cycache/vtc_d1f6eb95_*`): every `batch_run` snapshot copied
**6.93 MB of memory state** (every element of all 131 memories, value + mask),
6.0 MB of it one 384-bit x 65536 memory -- the testbench's stimulus chunk
buffer, written only by `load_memory` once per chunk round, never by the
design. And a falling-edge snapshot ran **every cycle** too, because eight
constant tie-offs (`assign x = 1'b0;`, empty sensitivity) defeated the
negedge-skip logic. ~13.9 MB copied per cycle is ~400-450 us at memcpy
bandwidth: essentially all of the observed time. Two fixes (2026-10-06):
1. **Constant drivers no longer block the negedge skip**
   (`_cont_assign_is_idempotent`; empty-sensitivity combinational blocks
   still do). Synthetic tie-off + 1 MB memories: 72.5 -> 35.9 us/cycle.
2. **Incremental memory snapshots**: a memory is copied only if its marker
   signal was marked dirty since the last snapshot (`sdirty`, set by
   `mark_dirty` -- every memory write marks its marker, since that's how
   readers get re-triggered). `VERIFORGE_CHECK_MEM_SNAPSHOT=1` verifies the
   invariant (compares every skipped memory to its snapshot; mismatch
   raises), and the full test suite passes in that mode. gfwx-shaped
   synthetic (1.5 MB stimulus memory read once per cycle, tie-off):
   118 -> 0.02 us/cycle.
Memories written every cycle (gfwx's ~0.9 MB of line buffers) are still
copied in full when written; per-element tracking would be the next step if
that becomes the bottleneck. Remaining per-snapshot full copies:
`sv`/`sm` (~56 KB for gfwx) and wide-signal snapshot (~79 KB).

### Stage 2 results (2026-10-05)

**Correctness.** New `tests/test_sim/compiled/test_delta_engine_equivalence.py`
(29 tests) runs identical stimulus through both engines and asserts
identical signal values *and identical `step()` iteration counts* every
step, on designs chosen for the risky surfaces (D6) plus 12 randomized
modules from the cross-engine differential generators. A genuine
combinational cycle (ring oscillator) still raises the delta-limit error in
both engines; the value-convergence path (>= `DELTA_CONV_CHECK_START`
iterations, via a combinational block that clear-then-rewrites a memory it
reads) is exercised and guarded so the test can't silently stop covering it.
Full `tests/test_sim/compiled/` plus the differential fuzz suites with the
compiled engine enabled (`VERIFORGE_DIFF_COMPILED=1`,
`VERIFORGE_DIFF_STMT_COMPILED=1` -- note these are *off* by default, so the
default suite does not fuzz the compiled engine against the reference) all
pass with the queue engine; four codegen-text assertions on the old field
names were updated.

**Reproducer flaw found.** Sweeps A/B's `make_wide_bench` cannot isolate
bookkeeping cost: every lane's `en_i` reads `rr_base_reg`, which changes
every cycle, so every lane's enable process re-runs every cycle regardless
of `ACTIVE_LANES`; and every lane has its own always block, all of which
fire on every clock edge. Real per-cycle work there grows with `N_LANES`,
so Stage 0's gate confirmed "cost scales with size", not "cost scales with
size *because of bookkeeping*". Added `make_cont_bench` + Sweep C
(independent continuous-assign chains fed by input ports, one clocked
process, `batch_run` events changing exactly `k` lanes per cycle) to
measure the latter.

**Performance** (queue / scan throughput, same build):

| Sweep C: lanes (conts) | k=1 driven/cycle | k=8 | k=64 |
|---|---|---|---|
| 64 (320) | 3.06x | 1.23x | 0.95x |
| 256 (1280) | **5.08x** | 1.94x | 0.77x |
| 512 (2560) | 3.40x | 2.27x | 1.19x |

| Sweep A/B (`make_wide_bench`) | queue / scan |
|---|---|
| 8 lanes, 8 active | 0.72x |
| 384 lanes, 8 active | 1.15x |
| 256 lanes, 1 active | 1.25x |
| 256 lanes, 256 active (full) | 0.34x |
| `benchmark.py` DUT (9 processes) | ~0.80x |

Interpretation: per executed process, the queue engine costs ~2.3 ns more
than scan (bitmap pop, indirect `switch` jump, reader pushes); scan pays
~3-4 ns per process *per iteration whether or not it runs*. So queue wins
below roughly 50% activity and loses above it.

**Two remaining size-dependent costs**, both engines:
1. Sweep C at fixed `k=1`, queue still slows with size (3.0M -> 1.1M ->
   0.43M cyc/s for 64 -> 256 -> 512 lanes). Most likely `batch_run`'s
   per-cycle `sv`/`sm` snapshot `memcpy`s, which are O(N_SIGS) (~100 KB per
   cycle at 512 lanes). Restricting them to the sids sequential processes
   actually read pre-edge was investigated during the 2026-09 perf work and
   declined as negligible -- true then, when the scan engine's own costs
   dominated; not true once those are gone. It needs exact read sets from
   several emitters (see that session's notes), so it's its own task.
2. Compile time: unchanged in character (the dispatch `switch` keeps one
   inlined call site per process, like the scan engine's straight-line
   dispatch); the wall noted in Stage 0 remains.

**Redundant reruns (resolved 2026-10-06 -- implemented, see below).** To stay identical by
construction, the queue engine reproduces the scan engine's redundant
reruns: a process re-runs in iteration `k+1` because an input was written
in iteration `k` even if it already ran *after* that write. In dense
regimes that's about half of all executions. A throwaway experiment
skipping them (valid only for that all-pure design; not committed) took
the fully-active `make_wide_bench(64, 64)` case from 0.64x to **1.13x of
scan** -- i.e. queue would win at every activity level measured. Doing it
for real means pushing, after each process at rank `w` writes `s`, the
readers of `s` with rank `<= w` into a *next-iteration* pending set (and
catching every write, not just the first per iteration), instead of
re-seeding from all readers of `T_{k+1}`. Two new assumptions come with it:
- **Purity**: only processes whose rerun with unchanged inputs is a no-op
  may skip it. Not all continuous assigns qualify (memory-writing assigns
  toggle marker signals; user-function calls write function-internal
  signals), so purity must be classified -- preferably as an allowlist at
  the emission sites in `_process_compiler.py` (default: impure, keep
  exact semantics), not by scanning generated text.
- **Complete sensitivity sets**: today's redundant reruns can mask a
  sensitivity gap (a process re-running because one input changed picks up
  another input that changed without triggering it). Eliminating them
  would expose any such pre-existing gap as a divergence.
Iteration counts would still be identical (a skipped rerun is a no-op that
dirties nothing), so the existing A/B suite -- values *and* iteration counts
-- plus the differential fuzz remain the check.

**Recommendation for `gfwx-fpga`**: measure it directly with
`VERIFORGE_DELTA_ENGINE=queue` vs `scan` before changing the default. Its
profile (3112 conts, 21 seq processes) is the target regime, but a streaming
image pipeline may well be *dense* (most of the datapath active every
cycle), where queue is at parity or somewhat slower.

### Redundant-rerun elimination (2026-10-06)

Implemented in the queue engine only; the scan engine is unchanged.

- **Purity, decided on the IR** (`_cont_assign_is_rerun_pure` in
  `_process_compiler.py`) once per `assign`, then applied to every process
  that assign compiles to (all ~60 emission sites run inside one loop, so
  per-assign marks cover them without touching any site). Allowlist: the
  LHS writes only plain signals (no memory targets -- those toggle marker
  signals on every write); the RHS calls nothing outside a short list of
  side-effect-free system functions (no user functions -- they write
  function-internal signals; no `$random`/`$time`); non-empty sensitivity.
  Combinational `always` blocks are never pure (intermediate blocking
  writes re-mark signals dirty even when end values don't change). Anything
  unrecognized is impure and keeps the exact scan schedule.
- **Engine.** Iteration 0 seeds every reader of the externally dirtied sids
  (nothing has run yet in the call). Later iterations seed only *impure*
  readers of `T_k` (via a second, impure-only reader index, so pure readers
  aren't walked just to be skipped), plus a next-iteration bitmap `_pnext`.
  After each process at rank `w`, every sid it changed -- from a per-call
  write log kept by `mark_dirty` (deduped by an epoch bumped per call), so a
  second write to a sid in the same iteration counts too -- marks its
  readers with rank `> w` pending now and its *pure* readers with rank
  `<= w` in `_pnext`: those already ran before the write and need exactly
  one more run.
- **Correctness.** A skipped rerun is a no-op that dirties nothing, so
  values and iteration counts stay identical; the A/B suite (now 31 tests,
  adding a continuous assign that reads a combinational block's output --
  the `_pnext` path -- and a unit test of the purity classifier) asserts
  both on every step. The compiled suite and differential fuzz suites pass
  in queue mode.
- **Performance** (queue / scan, same build; before -> after elimination):

| Case | before | after |
|---|---|---|
| Sweep A 64 / 256 / 384 lanes (8 active) | 0.74 / 0.99 / 1.15x | 1.56 / 1.61 / 1.76x |
| Sweep B 256 lanes, 1 / 8 active | 1.25 / 1.01x | 1.92 / 1.78x |
| Sweep B 256 lanes, 64 / 256 active | 0.47 / 0.34x | 0.81 / 0.65x |
| Sweep C k=1: 64 / 256 / 512 lanes | 3.06 / 5.08 / 3.40x | 2.43 / 5.62 / 4.16x |
| Sweep C k=8: 256 / 512 lanes | 1.94 / 2.27x | 2.11 / 2.61x |
| Sweep C k=64: 64 / 256 / 512 lanes | 0.95 / 0.77 / 1.19x | 0.83 / 0.90 / 1.45x |
| `make_wide_bench(64, 64)`, fully active | 0.64x | 1.01x |
| `benchmark.py` DUT (9 processes) | ~0.80x | ~0.73x |

  Full tables in `notes/benchmarks_work_queue.md`.

**Possible next steps.**
1. Measure `gfwx-fpga` with both engines; set the default from that.
2. If dense designs matter: the remaining per-executed-process cost is the
   indirect `switch` dispatch plus write-log bookkeeping (~2x scan's cost
   per process it actually runs). A density-adaptive traversal (walk ranks
   in order with direct calls when most are pending) would remove the
   indirect jump, at the cost of a second call site per process (code size
   and compile time).
3. A codegen-time `auto` mode (queue above some process count) would avoid
   the tiny-design regression, but can't see activity, so large dense
   designs would still lose up to ~35%.
4. The `sv`/`sm` snapshot-copy reduction (see "Two remaining size-dependent
   costs" above).

- Stage 0: `benchmarks/wide_bench_gen.py` + `benchmarks/scan_vs_activity_bench.py`
  added; results in `notes/benchmarks_work_queue.md`. Gate confirmed: Sweep A
  (fixed activity, N_LANES 8->384) throughput drops 37.6x for a 48x size
  increase; Sweep B (fixed size=256, activity 1->256 lanes) only varies
  2.6x. The plan's own suggested sweep top values (N_LANES 1024/2048/4096)
  had to be scaled back to {8,64,256,384}/256 -- both 1024 and 4096 exceeded
  a 600s Cython/C compile timeout on the dev machine, and even 512 didn't
  finish in 300s. **This is a separate compile-time scaling wall** (not the
  delta-loop runtime cost this plan targets) worth factoring into Stage 2's
  own design and testing -- the real `gfwx-fpga` design (3112 cont
  processes) sits in a size range where this may already bite.
- Stage 1: `_cont_settle_first_cycle_lines()`'s separate, cont-only,
  unconditional `N_cont`-bounded settle replaced with a call through
  `delta_loop()` itself (snapshot-then-settle, mirroring the pre-existing
  `ev_applied` branch's identical pattern -- see `d667ac3`'s commit message
  for why this is safe: `sv[sid] == c.val[sid]` for every signal at the
  point of the call, so no seq process can spuriously fire during this
  settle). Verified against `TestContinuousAssignSnapshotConvergence`,
  full `tests/test_sim/compiled/` (793 passed), and full `tests/test_sim/`
  (5617 passed, 0 failed, 3853 skipped).

**Next steps**: see "Possible next steps" at the end of "Redundant-rerun
elimination" above. Changing the default engine waits on measuring
`gfwx-fpga` with both engines.

---

Found while stress-testing the compiled engine against `gfwx-fpga`'s real
full-scale target (a large, deeply-composed DSL design — see "Provenance"
below). This is a **sequel** to `notes/plans/compiled_engine_perf_2026-09.md`:
that plan's Findings 1–3 are, as of this investigation, already implemented
and working as designed. This plan addresses a different axis of the same
subsystem that only becomes dominant at a much larger design scale than that
plan's own benchmark DUT exercised.

## Provenance

Found via `gfwx-fpga` (a separate project using veriforge as its DSL/RTL
simulation backend — see its own `notes/` for context if needed), while
building a real, full-scale (9216×8857 pixel image, 5-level wavelet
pipeline, 32 parallel lanes) end-to-end compiled-engine test. That design
compiles to **3591 signals (`N_SIGS`), 3112 continuous-assign processes
(`N_cont`), 21 sequential processes (`N_seq`)** once fully flattened —
roughly two orders of magnitude larger than this project's own existing
benchmark DUT (31 signals, 7 cont processes, per `notes/benchmarks.md`).

Measured on that real design:
- Reference engine: 60,519 µs/cycle
- Compiled engine (`batch_run`): 445.2 µs/cycle (isolated via direct
  profiling — confirmed this is ~100% of end-to-end time, not Python-side
  overhead from the new file-streaming feature built alongside it)
- Ratio: ~136×, within this project's documented "50–100×" compiled-vs-
  reference ballpark, but far short of the "~900×" a dev's own mental model
  (30× Icarus-class × 30× reference-vs-Icarus) predicted for a design this
  large. That gap is what this investigation chased down.

## Summary of Findings

1. **Not a cyclic-dependency / iteration-count problem.** Verified directly
   (temporary debug instrumentation, see Methodology below) that
   `_cont_dependency_order()`'s topological sort succeeds and is **acyclic**
   for all 3112 continuous-assign processes in the real design. Combined
   with same-pass `dirty[]` visibility (a process ordered later in the same
   iteration sees an earlier process's write immediately), this means
   `delta_loop` should — and, per code reading, does — converge in
   essentially one real pass plus one empty confirmation pass per clock
   edge, regardless of combinational chain depth. This is **Finding 1 from
   `compiled_engine_perf_2026-09.md`, confirmed working as designed.**

2. **The dominant cost is per-iteration work that scales with total design
   size, not with real per-cycle activity.** Each delta iteration
   (`_gen_delta_loop()`, `_gen_sections.py:1410`) unconditionally pays:
   - `memcpy` + `for i in range(N_SIGS): changed |= trigger[i]` — an
     O(N_SIGS) scan just to detect whether *anything at all* changed
     (`_gen_sections.py:1450-1451` and similar at lines 1457, 1515, 1684,
     1706, 1722, 1896).
   - A linear walk over all `N_cont` continuous-assign dispatch sites, each
     gated by `trigger[sid] or c.dirty[sid]` for that process's own
     sensitivity set (`_emit_sens_check_lines`, `_gen_sections.py:108`) —
     O(N_cont) branch evaluations every iteration, independent of how many
     actually fire.
   - The "early exit once nothing interesting remains dirty" check
     (`_emit_no_dirty_check_lines`, `_gen_sections.py:129`) scans
     `interesting_sids` — the union of every process's sensitivity set.
     **Measured directly on the real design: 3234 of 3591 signals (90%)
     are "interesting."** For a densely-wired design like this one, that
     "fast" early-exit check costs almost as much as the full scan it
     exists to short-circuit.

   None of these three costs depend on how much of the design is actually
   toggling on a given edge — they are purely a function of total flattened
   design size (`N_SIGS`, `N_cont`). At the September 2026 benchmark DUT's
   scale (31 / 7), this is negligible. At 3591 / 3112, it is not.

3. **Secondary, independently-fixable finding**: `_cont_settle_first_cycle_lines()`
   (`_gen_sections.py:545`) emits an *ungated*, worst-case-`N_cont`-iteration
   fixpoint loop (4 full-array memcpys + all `N_cont` cont calls, called
   *unconditionally*, every iteration, with no dirty-flag gating at all)
   that runs once at the start of **every** `batch_run()` call (guarded to
   `i == 0` within that call, not once per whole simulation). This exists
   for a real correctness reason — settling externally-driven signals
   before the first posedge snapshot of a fresh `batch_run()` call (this is
   `compiled_engine_perf_2026-09.md`'s Finding 2, already partially
   addressed: the *hot-path, every-cycle* calls from that finding are gone;
   only the `i==0` instance remains). The problem: `gfwx-fpga`'s new
   `LoweredDesign.batch_run_streaming()` feature (built this session, for
   unrelated reasons — streaming AXI test data from disk in chunks) calls
   `batch_run()` repeatedly, once per file-chunk round, specifically
   *because* file-streamed stimulus can't all be resident in the compiled
   module's fixed-size `chunk_mem` registers at once. Every one of those
   rounds re-pays the `i==0` settle cost. For the real full-scale run,
   that's on the order of 70 rounds (default 65536-beat chunk size) each
   paying a cost that is, by construction, *not* gated by actual activity
   and bounded only by `N_cont` in the worst case.

## Why `compiled_engine_perf_2026-09.md`'s fixes didn't catch this

That plan's Finding 1 (topological sort) fixed **iteration count** — it
collapsed what used to be "N_hop_chain_depth iterations" down to "~2
iterations regardless of chain depth." It did not and could not fix
**iteration cost** — the fact that each of those ~2 iterations still does
O(N_SIGS + N_cont) work. The two axes are independent: a small design with
a deep chain used to need many cheap iterations; a huge design with a
shallow chain now needs few expensive iterations. Both can dominate
runtime; only the first was visible at the scale that plan's own DUT
exercised (31 signals — a full scan is noise at that size).

## Proposed Fix: Runtime Work-Queue / Activity-List Delta Dispatch

`_cont_dependency_order()` (`_gen_sections.py:59-105`) already builds a
reverse index — `readers: dict[sid, list[int]]`, signal ID → the process
indices sensitive to it — purely to compute the topological sort, then
**discards it** once `order` is returned. That index is exactly what a
real activity-driven dispatcher needs at runtime.

**Sketch:**

1. **Codegen**: export `readers` as flat static C arrays baked into the
   generated module instead of discarding it — e.g. `reader_offsets[N_SIGS+1]`
   (CSR-style offsets) + a flat `reader_ids[total_edges]` array of process
   indices, built once at codegen time, zero runtime construction cost.

2. **Runtime** (`_gen_delta_loop()` rewrite): replace the O(N_SIGS) "scan
   for anything dirty" and the O(N_cont) "walk every dispatch site" with an
   explicit pending-process queue:
   - Seed the queue each edge from whatever actually became dirty (the
     clock signal itself, any externally-applied event, any NBA commit) by
     walking `readers[sid]` for each dirtied `sid` — O(signals actually
     dirtied × their fan-out), not O(N_SIGS).
   - Pop queue entries, call the corresponding `cont_i()`/`seq_i()`; any
     signal that call's own body dirties pushes *its* `readers[]` onto the
     queue in turn (this is where the existing topo-sort ordering still
     matters — it determines a good drain order, not correctness, since
     the queue mechanism itself handles arbitrary firing order correctly).
   - `changed` becomes "queue non-empty" — O(1).
   - The `interesting_sids` early-exit scan becomes unnecessary — an empty
     queue already means nothing left to do.

3. **Two subtleties to get right, flagged but not resolved here:**
   - **Cold-start / idle-edge wake-up**: the current `it==0 and not changed:
     mark everything dirty` special case (used when an edge fires with
     nothing externally dirty — e.g. a pure reset release) needs an
     equivalent "seed the whole queue" path in the new scheme.
   - **Livelock/oscillation detection**: `DELTA_CONV_CHECK_START`/`conv_val`/
     `conv_mask` currently detect non-convergence (a genuine combinational
     race or a latch inference) via full-array snapshot comparison after
     iteration 16. A queue-based engine needs an equivalent detector that
     doesn't rely on a full-array scan — e.g., bound how many times any
     single process may be re-queued within one edge, distinct from
     bounding `it` itself. **Do not drop this detector** — it is the only
     thing standing between a real design bug and a silent infinite loop
     or incorrect "converged" result. Any design with a genuine
     combinational cycle must still hit `ERR_DELTA_LIMIT`, not silently
     settle on a wrong value.

4. **Keep the current scan-based path available during development**
   (behind a flag, or simply uncommitted dead code) for direct A/B
   correctness comparison until the new path passes the full existing
   suite — do not remove the old implementation until then.

### Stage 2 design decisions (settled before implementation)

**D1 — Preserve the iteration structure exactly; change only per-iteration
cost.** The queue engine keeps `delta_loop`'s outer `for it in
range(DELTA_LIMIT)` loop and runs *exactly the same set of processes, in
exactly the same order, in each iteration* as the scan engine. Only the
bookkeeping that decides which processes run changes. Consequences:

- Results are bit-identical, and **`delta_loop`'s return value (the
  iteration count) is identical** — which makes the equivalence directly
  testable (D6).
- **Both subtleties flagged above dissolve rather than needing new
  designs.** Because `it` counts the same iterations, `DELTA_LIMIT`,
  `DELTA_CONV_CHECK_START`, and the value-convergence detector (with its
  memory-marker handling) are kept *verbatim*. No new livelock detector is
  invented, so there is nothing new to get subtly wrong: a genuine
  combinational cycle hits `ERR_DELTA_LIMIT` at the same iteration it did
  before. Cold start (`it == 0` with nothing dirty) is kept verbatim as
  "every process is pending in iteration 0".
- Rejected alternative: a free-running event queue (pop a process, run it,
  push its readers, no iterations). Simpler loop, but it changes process
  execution order and count, which (a) can change which stable state a
  design with combinational feedback (latch-like structures) settles into,
  and (b) forces a new livelock detector with different trip points.

**D2 — The exact per-iteration rule being preserved.** In the scan engine,
process `P` at static rank `r` (its position in the emitted dispatch order:
topo-sorted conts, then combos in declaration order) runs in iteration `k`
iff it has an empty sensitivity list, or some `s in sens(P)` is in
`T_k ∪ D_k(<r)`, where `T_k` is the set of sids marked dirty during
iteration `k-1` (or at entry, for `k = 0`; or every sid, for the cold
start) and `D_k(<r)` is the set marked dirty during iteration `k` before
`P`'s turn (by seq bodies, NBA apply, or processes of rank `< r`).

The queue engine reproduces this with a per-iteration pending bitmap over
ranks, scanned forward:
- iteration start: mark pending every reader of every sid in `T_k`, every
  empty-sensitivity process, and every reader of every sid dirtied by the
  seq-fire/NBA-apply phase (which precedes all conts, i.e. rank `-1`);
- after running the process at rank `w`: for each sid it newly dirtied,
  mark pending each reader with rank `> w`. Readers with rank `<= w` are
  *not* marked — exactly as in the scan engine, they run next iteration
  via `T_{k+1}`;
- pop the lowest pending rank `>= ` the cursor; pushes only ever go
  forward of the cursor, so a forward bitmap scan visits ranks in order.

Equivalence: both engines visit ranks in increasing order, run each rank
at most once per iteration, and run `P` iff the rule above holds. Note this
deliberately *preserves* the scan engine's redundant reruns (a process
re-running in iteration `k+1` because an input was written in iteration `k`
even though it already ran after that write) — eliminating them would
change the execution count of impure combo blocks (`$display`, memory
writes that toggle marker signals). That is a possible follow-up, not part
of this stage.

**D3 — Sparse dirty/NBA tracking via helpers, enforced by a field
rename.** `dirty[N]` becomes `dbit[N]` + `dlist[N]` + `dcount` (a
sparse set), and `nba_dirty[N]` becomes `nba_bit[N]` + `nba_list[N]` +
`nba_count`. Every one of the ~300 write sites (`c.dirty[X] = 1`,
`self.ctx.dirty[X] = 1`, `c.nba_dirty[X] = 1`, across five emitter `.py`
files and three `.pxi` templates) goes through `mark_dirty(c, X)` /
`mark_nba(c, X)`. **Renaming the struct fields is the safety net**: any
site the rewrite misses becomes a Cython *compile error* (no such field)
rather than a silently lost trigger — the failure mode that made the
earlier regex-based dirty-clearing attempt unsafe. The test suite compiles
hundreds of designs exercising every emitter.

**D4 — Static reader index baked into the module.** `_cont_dependency_order`
already builds `readers[sid]` to compute the topo sort; the same relation
(over ranks) is emitted as CSR arrays (`READER_OFF[N_SIGS+1]`,
`READER_RANK[E]`), plus `ALWAYS_RANK[]` (empty-sensitivity processes) and
`IS_INTERESTING[N_SIGS]` (the existing early-exit set), as `static const`
C arrays via a verbatim `cdef extern from *` block — zero runtime
construction cost. Dispatch from a rank to its `cont_i`/`combo_i` call is
an `if r == 0: ... elif r == 1: ...` chain, which Cython lowers to a C
`switch` (verified in the generated C, not assumed) so per-call dispatch is
O(1) and each body is still inlined at a single call site — compile-time
characteristics unchanged from today.

**D5 — Seq edge detection stays O(N_seq) per iteration, unchanged.**
Restricting it to processes whose edge sids were just dirtied would rely
on the invariant "every change to `c.val[e]` also marks `e` dirty", which
nothing currently enforces. `N_seq` is small in the target design (21),
and when a clock edge fires, running the seq bodies is itself O(N_seq)
real work, so this doesn't change the asymptotics. Possible follow-up.

**D6 — Keep the scan engine selectable for A/B, and test equivalence
directly.** `VERIFORGE_DELTA_ENGINE=scan` selects the legacy loop (default:
`queue`). Both share the renamed fields and helpers. The mode is folded
into the elaboration-cache key — which also required fixing a pre-existing
latent cache bug found while scoping this: the elab cache's
"codegen infrastructure" hash covered a hardcoded file list that omitted
`_gen_wide_section.py`, all four `_gen_narrow_*.py` generators, their
`templates/*.pxi`, and `compiler.py`, so editing any of those reused stale
compiled modules. New A/B tests run the same stimulus through both engines
and assert identical signal values **and identical `step()` iteration
counts** every cycle, across designs chosen for the risky surfaces: deep
cont chains declared out of order, combo blocks, memories with combo
writes (the marker-toggle / value-convergence path), wide signals, and
genuine combinational cycles — which must raise the delta-limit error in
both engines (no existing compiled test covered this).

## Secondary Fix (independent, do first — much smaller)

Route `_cont_settle_first_cycle_lines()`'s per-`batch_run()`-call settle
through the already-correct, already-sorted, already-gated `delta_loop()`
itself, instead of maintaining a second, separate, ungated,
worst-case-`N_cont`-bounded implementation. Once routed through
`delta_loop()`, this inherits both Finding 1's fast convergence (already
true) and this plan's Stage 2 activity-proportional cost (once Stage 2
lands) for free, and stops being a maintenance fork of the same logic.

## New Synthetic Reproducer DUT (standalone — no `gfwx-fpga` dependency)

The goal is a tiny, self-contained Verilog source (same style as
`benchmarks/benchmark.py`'s existing embedded `module bench(...)` text, not
a DSL-authored design) that reproduces the *specific* symptom — cost
dominated by total design size, independent of real per-cycle activity —
with two independently tunable knobs, so the hypothesis can be checked
*before* investing in Stage 2, and the fix's impact can be measured
*after*, without needing `gfwx-fpga`'s large, slow-to-compile RTL at all.

**Knobs:**
- `N_LANES` — total design size (controls `N_SIGS`/`N_cont` ~linearly;
  each lane contributes one register, three short-chain continuous
  assigns, and one enable comparison — roughly 5 processes/lane, so
  `N_LANES=700` approximates the real design's 3112-process scale).
- `ACTIVE_LANES` — how many of the `N_LANES` lanes actually change state on
  any given edge, via a rotating round-robin enable window. This is the
  **real per-cycle activity**, held independent of `N_LANES`.

**Generator** (new file: `benchmarks/wide_bench_gen.py`):

```python
def make_wide_bench(n_lanes: int, active_lanes: int) -> str:
    """Self-contained `module bench(...)` Verilog source: n_lanes
    independent lanes (counter behind a 3-hop combinational chain), of
    which only active_lanes are enabled on any given edge via a rotating
    round-robin window. Explicit per-lane instantiation (no generate/
    genvar), matching how gfwx-fpga's own flattened hierarchy produces its
    3112 cont processes -- not a single generate-block construct that
    might elaborate differently.
    """
    lines = [
        "module bench(",
        "    input clk,",
        "    input rst,",
        f"    output [{n_lanes * 16 - 1}:0] q_flat",
        ");",
        "  reg [31:0] rr_base_reg;",
        "  always @(posedge clk) begin",
        "    if (rst) rr_base_reg <= 0;",
        f"    else rr_base_reg <= (rr_base_reg + {active_lanes}) % {n_lanes};",
        "  end",
    ]
    for i in range(n_lanes):
        lines += [
            f"  reg [15:0] ctr_{i};",
            f"  reg [15:0] q_{i};",
            f"  wire en_{i} = ((({i} + {n_lanes} - rr_base_reg) % {n_lanes}) < {active_lanes});",
            f"  wire [15:0] a_{i} = ctr_{i} ^ 16'hABCD;",
            f"  wire [15:0] b_{i} = a_{i} + 16'h1111;",
            f"  wire [15:0] c_{i} = b_{i} ^ (b_{i} >> 3);",
            f"  always @(posedge clk) begin",
            f"    if (rst) begin ctr_{i} <= {i}; q_{i} <= 0; end",
            f"    else if (en_{i}) begin ctr_{i} <= ctr_{i} + 1; q_{i} <= c_{i}; end",
            f"  end",
            f"  assign q_flat[{i * 16 + 15}:{i * 16}] = q_{i};",
        ]
    lines.append("endmodule")
    return "\n".join(lines)
```

Why this isolates the right thing: when a lane is NOT enabled, `ctr_i`
holds its value, so `a_i`/`b_i`/`c_i` recompute to the *same* value as
before — their output doesn't actually change, so their `dirty[]` bits
never get set, and the current engine correctly never calls their
`cont_N()` bodies. The scan-based *gating checks* for those lanes still
run every iteration regardless — that fixed cost, present even for fully
inactive lanes, is exactly what this plan claims is the bottleneck.

**Benchmark methodology** (new file:
`benchmarks/scan_vs_activity_bench.py`, same CLI conventions as the
existing `benchmarks/benchmark.py` — `--update` writes a results table,
reusing its `batch_run`/cycles-per-second measurement code path directly
rather than reimplementing it):

- **Sweep A** (expose the bug): fix `ACTIVE_LANES=8`; vary `N_LANES` ∈
  `{8, 64, 256, 1024, 4096}`. **Current-engine prediction**: cycles/s drops
  roughly ∝ `1/N_LANES` even though real work-per-cycle is pinned constant
  — this is the smoking gun. If this does NOT appear, the hypothesis needs
  revisiting before Stage 2 is attempted (see Stage 0 below — this is a
  gate, not just a confirmation).
- **Sweep B** (expose the insensitivity): fix `N_LANES=2048`; vary
  `ACTIVE_LANES` ∈ `{1, 8, 64, 512, 2048}` (2048 = fully active).
  **Current-engine prediction**: cycles/s roughly flat across this sweep —
  the engine's cost today doesn't respond to how much is actually
  happening.
- **After Stage 2**: re-run both. **Prediction**: Sweep A flattens (cost
  tracks `ACTIVE_LANES`, not `N_LANES`); Sweep B becomes roughly linear in
  `ACTIVE_LANES`.
- Compile time at `N_LANES=4096` should be seconds, not minutes — this is
  a flat, shallow design, nothing like `gfwx-fpga`'s real nested
  hierarchy. Confirm this directly rather than assuming it, so a slow
  compile doesn't get mistaken for part of the phenomenon under test.

## Staged Implementation Plan

**Stage 0 — Build the reproducer, confirm the hypothesis (no engine
changes).** Add `benchmarks/wide_bench_gen.py` +
`benchmarks/scan_vs_activity_bench.py`. Run Sweeps A and B against the
*current* engine. Gate: if Sweep A doesn't show the predicted
size-dependent, activity-independent cost, stop and re-investigate before
Stage 1/2.

**Stage 1 — Secondary fix (small, independent, do first).** Route
`_cont_settle_first_cycle_lines()`'s settle through `delta_loop()` itself.
Verify: `tests/test_sim/compiled/test_scheduling.py::TestContinuousAssignSnapshotConvergence`,
full `tests/test_sim/compiled/`, Sweeps A/B (expect a small uniform
improvement only — this is not the main fix).

**Stage 2 — Core work-queue delta engine (the main fix).**
- 2a. Extend `_cont_dependency_order()` (or a sibling) to export `readers`
  instead of discarding it.
- 2b. Emit it as flat static C arrays in the generated module.
- 2c. Rewrite `_gen_delta_loop()`'s per-iteration body per the sketch
  above, preserving the cold-start wake-up case and an equivalent
  livelock/oscillation detector (see subtleties above — these need actual
  design decisions, not just porting).
- 2d. Keep the old path available for A/B comparison until the new path
  passes the full suite.

**Stage 3 — Validate.** Full `pytest tests/test_sim/ -x -q` (not just the
compiled subset — cross-engine equivalence tests catch semantic drift
fastest). Re-run Stage 0's sweeps, confirm the predicted flattening. If
time allows, re-run (or a reduced-scale slice of) `gfwx-fpga`'s own
`sim_h3_full_pipeline_stream_full_bp_frame.py` end-to-end and compare
wall-clock against the 445.2 µs/cycle baseline recorded in this
investigation.

**Stage 4 — Document.** Regenerate `notes/benchmarks.md` (`--update`).
Record before/after numbers for this plan's own findings in a short
follow-up (append to this file or a new `work_queue_delta_engne_results.md`
— whichever this project's own convention prefers by then), matching
`compiled_engine_perf_2026-09.md`'s reporting style.

## Correctness Risk and Test Anchors

- `tests/test_sim/compiled/test_scheduling.py::TestContinuousAssignSnapshotConvergence`
  — the standing anchor for out-of-order/settle correctness (per
  `compiled_engine_perf_2026-09.md`). Must stay green through every stage.
- Full `pytest tests/test_sim/ -x -q` after each stage — not just the
  compiled-engine subset.
- Specifically scrutinize designs with memories (async-read BRAM-style
  reads feeding combinational logic), wide (multi-word) signals, and
  **genuine combinational cycles** (intentional combinational loops /
  inferred latches) — a design in the last category must still correctly
  hit `ERR_DELTA_LIMIT`, not silently "converge" under the new
  queue-based detector. This is the single highest-risk correctness
  surface in this whole plan.
- This specific codebase has a track record of subtle, hard-to-see
  scheduler-class bugs that passed initial testing — a dangling-else
  parser bug that re-parsed `if(A) if(B) X; else Y;` incorrectly
  (invisible until emit+reparse), an `AXI4Responder` R-channel bug, and
  once a "bug" that was actually a stale `.venv` wheel masking an already-
  fixed issue. Treat this change with the same discipline: verify against
  a freshly-installed wheel/editable-install, don't trust a first green
  run alone, and prefer re-deriving expected behavior from the Verilog
  semantics over trusting that "it looks like the old numbers."

## File / Line Reference (current, as of this investigation)

| Location | What's there |
|---|---|
| `src/veriforge/sim/compiled/_gen_sections.py:59` | `_cont_dependency_order()` — topo sort; builds then discards `readers` |
| `:108` | `_emit_sens_check_lines()` — per-process `trigger`/`dirty` gating condition |
| `:129` | `_emit_no_dirty_check_lines()` — the `interesting_sids` early-exit scan (90% of `N_SIGS` on the real design) |
| `:425` | `_cont_settle_fixpoint_lines()` — ungated, `N_cont`-bounded fixpoint loop template |
| `:545` | `_cont_settle_first_cycle_lines()` — the per-`batch_run()`-call instance (Stage 1 target) |
| `:754` | `_struct_field_lines()` — SimCtx layout (unrelated; `compiled_engine_perf_2026-09.md` Finding 3) |
| `:1410` | `_gen_delta_loop()` — main per-iteration scan body (Stage 2 target) |
| `:1419` | `interesting_sids` computation |
| `:2156` | `cpdef int batch_run(...)` — hosts the per-call settle + per-cycle `delta_loop()` calls |
| `notes/plans/compiled_engine_perf_2026-09.md` | Prior plan; its Findings 1–3 are implemented as of this investigation |
| `src/veriforge/sim/bench/lowering.py` — `LoweredDesign.batch_run_streaming()` | The new chunked-round caller that makes the Secondary Finding's cost repeat per round |

## Methodology Notes (for reproducing or extending this investigation)

- The `acyclic=True`, `n_cont=3112` fact was obtained by temporarily adding
  a one-line debug print after the `_cont_dependency_order()` call site in
  `_gen_delta_loop()`, deleting the matching Layer-2 elaboration cache
  entry (`.cycache/_elab_*.json` in the consuming project) to force
  codegen to re-run (codegen itself is fast — ~0.2s — regardless of how
  long the subsequent C compile would take), and reading the debug line
  from stderr before killing the process (no need to wait through the
  C-compiler phase). The debug print was reverted after use; no functional
  change was made to `_gen_sections.py` during this investigation.
- The `interesting_sids` = 3234/3591 fact was obtained by counting the
  `if c.dirty[...]:` lines between the `_any_interesting_dirty = 0` marker
  and the following `if not _any_interesting_dirty:` line in the (already
  cached, previously generated) `.pyx` source for the real design.
- No changes to `tests/`, `src/`, or any cache were left in place by this
  investigation — this document is the only artifact.

## Effort Assessment: Opus or Sonnet?

- **Stage 0** (reproducer DUT + benchmark harness) and **Stage 1** (routing
  the settle loop through `delta_loop()`): well-scoped, mechanical,
  low-risk — **Sonnet-level**. Similar in shape to the benchmark/test
  scaffolding already produced during this investigation.
- **Stage 2** (the work-queue rewrite of `_gen_delta_loop()`):
  **recommend Opus.** This touches the correctness core of a scheduler
  that every compiled-engine simulation in this project depends on, in a
  codebase with a documented history of subtle, non-obvious scheduler bugs
  surviving initial review (see Correctness Risk above). Getting the
  cold-start wake-up path and the livelock/oscillation detector right
  under a fundamentally different dispatch model — without silently
  breaking convergence detection for designs with genuine combinational
  cycles — is exactly the kind of correctness-critical, easy-to-get-subtly-
  wrong work where deeper reasoning before writing code earns its cost.
  Sonnet can implement Stage 2 from a fully-specified design, but the
  *design decisions* flagged as subtleties above (how the new livelock
  detector works, how queue re-entrancy within one pass is bounded) should
  get Opus-level scrutiny before code is written, and Opus should review
  the diff even if Sonnet drafts it.
- **Stage 3** (validation) and **Stage 4** (documentation): **Sonnet-level**,
  though Stage 3's results should be read by whoever made the Stage 2
  design decisions (ideally Opus) before declaring the fix done — a green
  test suite confirms "didn't break existing behavior," not "the new
  livelock detector is actually equivalent to the old one on designs nothing
  in the existing suite happens to exercise."

**Overall recommendation**: don't hand the whole plan to one model tier.
Sonnet for the scaffolding and validation (Stages 0, 1, 3, 4); Opus for the
Stage 2 design-and-implement step specifically, with Opus also doing a
final read of the Stage 3 results.
