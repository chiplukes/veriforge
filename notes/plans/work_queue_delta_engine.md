# Work-Queue Delta Engine — Scaling Fix for Large Flattened Designs

## Status

**Stage 0 (reproducer, confirm hypothesis) and Stage 1 (secondary fix) are
done, committed, and verified** (see `56b8366` and `d667ac3`). **Stage 2
(the work-queue rewrite) has not been started** -- paused here on purpose,
per the Effort Assessment below, to switch to Opus before writing it.

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

**Next step for whoever picks this up: Stage 2** (`_gen_delta_loop()`
rewrite to a runtime work-queue/activity-list dispatch) -- read the
"Proposed Fix" and "Secondary... subtleties" sections below in full before
writing any code; the cold-start wake-up path and the livelock/oscillation
detector are the two places flagged as needing real design decisions, not
just porting.

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
