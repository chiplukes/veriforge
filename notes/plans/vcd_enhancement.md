# VCD tracing enhancements (plan, 2026-10-07)

Goals:

1. VCD tracing on the compiled engine should cost roughly in proportion to
   *activity in the traced signals*, not to design size.
2. Tracing a small part of a huge design (one lane of a generate-loop
   datapath, e.g. axis_pix_correction2) should be easy, and the untraced
   remainder should cost nothing per step.
3. Capture windows: record only around an arbitrary trigger condition, or
   around a failure, instead of dumping the whole run.
4. Separately (track C below): large flattened designs must compile without
   the C compiler running out of memory.

Guiding rule: keep anything tricky out of the reference engine and out of
code paths that don't pay for themselves. Tracing behavior (what gets
dumped, when) is shared Python; only change detection and buffering go into
generated C.

## Where we are (measured 2026-10-07)

`benchmarks/wide_bench_gen.make_wide_bench(500, 8)` (3004 signals,
8 of 500 lanes active per cycle), compiled engine:

| mode | ms/cycle |
|---|---|
| `batch_run` | 0.008 |
| `run()` | 0.013 |
| `run()` + `attach_vcd` (all signals) | 3.78 (~290x `run()`) |

Profile of the traced run: 98.7% of the time is the time-step callback
(`trace.py` `_record_callback`). Every time step it calls
`sched.read_signal()` on **every** traced signal (dict lookup, `Value`
construction), then `VcdWriter.change()` formats each value to a binary
string and compares strings to detect a change. Only ~1% of the 1.2M checks
found a change; file writes were 0.2% of the time.

Other current limitations:

- `batch_run()`/`run_cycles()` never call time-step callbacks, so a trace
  forces `run()`, which crosses into Python at every clock edge.
- `$dumpvars` ignores its arguments (`executor._exec_dumpvars` always
  registers every signal). The compiled engine has a separate, somewhat
  faster `$dumpvars` path (`compiled_scheduler._wire_vcd_from_ref`) that
  still does an all-signals Python loop per step. Two implementations of
  the same thing.
- `attach_vcd(signal_names=...)` can restrict signals, but only via an
  explicit list.
- `$dumpfile`/`$dumpvars` only; no `$dumpoff`/`$dumpon`/`$dumpall`/
  `$dumplimit`/`$dumpflush`.
- Flattened hierarchical names are dotted paths with generate indices,
  e.g. `gen_lane[2].u_lane.q`, so scopes can be selected by name prefix.

## Track V: tracing

### V1. Change detection in generated C (compiled engine)

Add a generated `trace_poll()` to `CompiledSim`:

- A trace set is installed once (`trace_set(sids)`): an array of traced
  signal ids, plus a "last dumped" (val, mask) copy per traced signal
  (wide signals: per word).
- `trace_poll()` compares each traced signal with its last-dumped copy
  (narrow: two compares; wide: `memcmp` of its words), updates the copy,
  and appends changed sids to a change list. It returns the count.
- Python formats only the changed signals and writes one block per time
  step: `#t` then the changes.
- Memories are traced per element only when selected (see V2). Compare the
  selected memory's arrays against a last-dumped copy, but only when the
  memory's marker was dirtied since the last poll (reuses the existing
  marker/`sdirty` idea; no new hot-path work when nothing is traced).

The VCD text formatting stays in Python (`vcd.py`), shared by all
engines. reference/vm/vm-fast keep a Python poll, but rewritten to compare
raw `(val, mask)` pairs instead of formatting every value (cheap to do,
and keeps output identical across engines).

Unify `attach_vcd` and `$dumpvars` on this one implementation; delete
`_wire_vcd_from_ref`'s private fast path.

Acceptance: wide_bench(500, 8) traced `run()` within ~2x of untraced
`run()`; output byte-identical (minus `$date`) to the current writer on
the existing VCD tests (`tests/test_sim/test_vcd*.py`), checked with
`sim/vcd_compare.py`.

### V2. Selecting what to trace

`attach_vcd(sim, path, *, scopes=None, depth=None, signals=None,
exclude=None, memories=None, ...)`:

- `scopes=["gen_lane[3].u_lane"]` -- everything under that instance
  path (name prefix match on the flattened names); `depth` limits how many
  levels below the scope (0 = unlimited, matching `$dumpvars` semantics;
  1 = just that scope's own signals).
- `signals=` / `exclude=` -- glob patterns (`fnmatch`, with `[` `]`
  escaped so generate indices match literally), applied after `scopes`.
- `memories=` -- `True`, `False`, or a list of patterns. Memories/packed
  2-D arrays are what blow up VCD size. Today `attach_vcd` includes every
  memory element (`name[i]`, from `signal_names()`), so the default stays
  `True` when nothing is selected (compatibility) and becomes `False` when
  `scopes=`/`signals=` are given (selection means "these signals").
- `sim.trace_scopes()` -- list the instance tree (with signal counts) so a
  user can find the path to pass.

`$dumpvars(level, scope...)` honors its arguments with the same selection
code (standard semantics: level 0 = all levels below, level 1 = the scope
only). `$dumpvars;` with no arguments keeps today's behavior (everything).

Untraced signals cost nothing per step because the C poll only iterates
the traced sid array.

### V3. Tracing inside batch_run / run_cycles (compiled)

When a trace session is active on the compiled engine, `batch_run` calls
`trace_poll()` after each edge's settle (posedge and negedge, since the
clock itself and negedge logic are traced) and appends change records to a
C buffer: `(time, sid, val, mask)` plus a side buffer for wide words.
Python drains the buffer when it fills (batch_run returns early at a
buffer-full boundary and resumes, like the existing `$finish` handling) and
at the end of the call.

Time stamps follow batch_run's own clock arithmetic (posedge at
`t`, negedge at `t + period/2`), matching what `run()` produces.

Acceptance: wide_bench(500, 8) traced `batch_run` within ~3x of untraced
`batch_run`; VCD from traced `batch_run` equals VCD from traced `run()` for
the same stimulus (test across several designs incl. wide signals and
memories).

vm-fast batch tracing: not in scope (vm-fast batch_run stays untraced;
document it).

### V4. Time windows and pause/resume

- `attach_vcd(..., start=t0, stop=t1)`.
- `session.pause()` / `session.resume()`; `$dumpoff` / `$dumpon` map to
  the same thing (on pause, dump x for every traced signal, per the
  standard; on resume, dump current values).
- `$dumpall`, `$dumpflush`, `$dumplimit` (stop dumping once the file
  exceeds N bytes) -- small and standard.

### V5. Capture around a trigger, or around a failure

Model: a **capture session** keeps the last `pre` cycles of changes in a
ring buffer and writes nothing until a trigger fires; it then writes the
ring buffer plus the next `post` cycles, and re-arms (up to `max_captures`).

```python
cap = attach_capture(sim, "captures/frame.vcd", scopes=[...],
                     pre=200, post=50, trigger=..., max_captures=5)
```

- Ring buffer storage: change records with *old and new* values. The
  starting state of a capture window is reconstructed by taking the
  current values and undoing the buffered changes newest-to-oldest, so no
  periodic full snapshots are needed. Bounded memory: `pre` cycles of
  traced-signal activity.
- Output: one VCD file per capture (`frame_000.vcd`, `frame_001.vcd`,
  ...), each with a full `$dumpvars` initial block at its window start.

Trigger kinds:

1. **Signal conditions, evaluated in C** (fast; works inside
   `batch_run`). A small expression over traced or untraced signals,
   given as a Verilog-like string: `"u_dp.err && u_dp.state == 3"`,
   `"rose(u_dp.frame_start)"`, `"u_dp.count > 100"`. Parsed with the
   existing expression parser and restricted to: signal refs (incl. bit /
   part selects), literals, `== != < <= > >=`, `& | ^ ~ ! && ||`, and
   `rose()`/`fell()`/`changed()`. Lowered at attach time to a small
   postfix program evaluated by a generic C interpreter compiled into every
   module once (no per-trigger recompile, so attaching a trigger never
   invalidates the compile cache). Evaluated after each edge, the same
   point as `trace_poll()`.
2. **Python callable** `trigger=lambda sim: ...` -- flexible, but forces
   per-step Python (no batch speed). Documented as the slow option.
3. **Manual**: `cap.trigger(reason="scoreboard mismatch")` from
   testbench code (e.g. a scoreboard or bench endpoint).
4. **On failure** (`on_failure=True`, default on for capture sessions):
   - a Python exception propagating out of `run()`/`batch_run()`/
     `run_cycles()` while the session is active (delta-limit errors, bench
     endpoint assertion errors, `NBA queue overflow`, ...) and an exception
     leaving the session's `with` block;
   - HDL `$fatal`, and `$error` (configurable), once they are routed
     through a common "simulation error" hook in each engine (today vm
     treats `$error` as `$display`; needs a uniform event first -- see
     open questions).
   On failure the capture writes the ring buffer up to the failure time
   (there is no "post" window after a fatal error) and the exception
   continues to propagate.

`attach_vcd` (V1-V4) is a capture session with no trigger and an
unbounded window, so both share one implementation.

### V6. Deferred

- FST output (GTKWave's compressed format, typically much smaller than
  VCD): needs a writer dependency; revisit if files are still too large
  after scoping/captures.
- vm-fast batch tracing.

### Order and checkpoints

V1 -> V2 -> V3 -> V4 -> V5. Each phase lands with tests; full regression
after V2, V3, and V5 (batched, per the usual practice), plus a
`run()` vs `batch_run()` VCD-equivalence test added in V3 and kept as a
guard.

### Status (2026-10-08): V1-V5 implemented

Tests: `tests/test_sim/test_trace_vcd.py` (cross-engine VCD equality,
selection, `$dumpvars(level, scope)`, run() vs run_cycles() equality,
buffer-full resume with event rebasing, windows, pause/resume, HDL dump
control, captures).

- **Structure.** `sim/trace.py`: a per-scheduler `TraceHub` holds the
  union of all sessions' signals, polls once per time step (compiled:
  `CompiledSim.trace_set`/`trace_poll`, C over (pointer, mask pointer,
  word count) entries for signals, wide signals and memory elements;
  elsewhere a Python poller comparing raw (val, mask)) and delivers each
  change to the sessions tracing it. `VcdTraceSession` (`attach_vcd`,
  `$dumpvars`) and `CaptureSession` (`attach_capture`) are sinks.
  `$dumpvars` now goes through the same code on every engine (the
  executor records a `DumpRequest`; schedulers call `start_dump`), and its
  file is flushed, not closed, at the end of each `run()` (a second
  `run()` used to lose its trace).
- **V1 numbers** (wide_bench 500 lanes, 3004 signals, all traced):
  traced `run()` 3.78 -> ~0.09 ms/cycle (untraced ~0.02). What remains is
  formatting the signals that actually changed (~20 per step); the C poll
  of all 3004 is ~20 us/step. Fully traced, this design writes ~9 KB of
  VCD per cycle, so it is output-bound; scope selection cuts it in
  proportion.
- **V3**: `batch_run` records changes in C after each edge (and after the
  forced negedge) into a buffer; it stops at a cycle boundary when the
  buffer can't hold two more full polls, and the scheduler drains it and
  resumes (event cycles rebased). Traced `run_cycles` output is identical
  to traced `run()`. Speed is about the same as traced `run()` -- both
  are bound by Python formatting of the changes. Possible follow-up if
  needed: format VCD text in C (only worthwhile for whole-design traces).
- **V5 deviation from the plan**: trigger conditions are *not* compiled
  to a C interpreter. They are evaluated in Python on the hub's change
  stream, only at steps where one of their signals changed (the only
  times the value can change). That is exact at edge granularity, works
  the same on every engine and inside `batch_run` (records carry their
  times), needs no codegen, and costs in proportion to the trigger
  signals' activity. Triggers fire on a false -> true transition of the
  condition (x = false); expressions are parsed with the normal parser
  and evaluated by the reference `ExpressionEvaluator`.
- **Not done**: HDL `$error`/`$fatal` as failure triggers. The engines
  don't agree on these tasks today (reference executor ignores `$fatal`;
  compiled treats it like `$finish`; vm prints `$error` like `$display`),
  so this needs a uniform "simulation error" event first. Failure capture
  covers Python exceptions from `run`/`run_step`/`batch_run`/
  `run_cycles`/`settle` and exceptions leaving the session's `with`
  block.

Found and fixed along the way:

- `Simulator.run()` rescheduled every forked `Clock` from t=0 on every
  call (and re-drove it low): a second `run()` replayed already-simulated
  clock edges with time going backwards, on all engines.
- Compiled `$time`/`$realtime`/`$stime` in expressions evaluated to 0
  (unsupported functions silently become 0), and `batch_run` never
  updated `sim_time`, so `$time`/`%t` inside a batch was stale.
- Known, not fixed: `$countones`, `$onehot`, `$onehot0`, `$isunknown`,
  `$size`, `$high`, `$low` are unimplemented on every engine (reference:
  x; compiled: silently 0).
- Known engine difference: a value driven from Python *between* `run()`
  calls is reported by vm/compiled at the current time and by reference
  at its next time step (only the VCD timestamp differs).

## Track C: compiler out of memory on large designs

Observed: `make_wide_bench(2000, 8)` (12,004 signals) -- the generated C
file is ~2.8M lines; `cc1` was killed by the OOM killer on a 27 GB
machine. gcc notes from that build: column tracking disabled in
`delta_loop` (line 2,146,755 -- i.e. most of the file is inside or inlined
into it), and "variable tracking size limit exceeded" in
`refresh_data_snapshot`. Build flags are Python's defaults
(`-g -O3 -Wall`); process functions are `cdef inline`, so `-O3` can inline
thousands of them into `delta_loop`, and `-g` makes gcc track variable
locations through that one enormous function. The opt-in split compile
(`VERIFORGE_COMPILE_SPLIT=1`) has an unexplained no-traceback crash noted in
`codegen.generate_to_files` -- consistent with the same OOM, multiplied by
parallel `gcc` jobs.

### C0. Measure

For wide_bench at 500/1000/2000 lanes and the benchmark DUT: peak RSS of
`cc1` (`/usr/bin/time -v`), compile wall time, and runtime per cycle.
Identify which functions dominate memory (compile with
`-fmem-report`/`-ftime-report`, or bisect by marking functions
`noinline`).

### C1. Cheap fixes first (expected to be enough)

In order, measuring after each:

1. Compile flags for generated modules: `-g0` (no debug info -- nobody
   debugs generated C with gdb by default; keep an env override to turn
   it back on) and, if still needed, `-fno-var-tracking-assignments`.
   Passed via `extra_compile_args` in the generated `setup.py`. Flags
   become part of the compile cache key.
2. Don't let gcc inline process bodies into `delta_loop` when there are
   many of them: emit process functions as plain `cdef` (not `inline`)
   above a process-count threshold (or always, if C0 shows no runtime
   cost). Measure runtime impact on gfwx-sized designs; expect small,
   since dispatch is already a switch/call per process.
3. Replace O(N_signals) straight-line generated code with table-driven
   loops where it exists: `__init__`'s per-signal width/mask/offset
   setup, `refresh_data_snapshot`, and similar -- static `const` arrays
   plus one loop instead of tens of thousands of statements.

### C2. If C1 isn't enough: make split compile trustworthy

Root-cause the split-compile crash, cap parallel `gcc` jobs by available
memory, and turn split on automatically above a measured size threshold.

### Acceptance

- wide_bench(2000, 8) and wide_bench(4000, 8) compile with peak `cc1`
  RSS under ~8 GB and within the existing 600 s build timeout.
- No runtime regression above ~5% on gfwx and `benchmarks/benchmark.py`.
- Out-of-memory/killed compiles produce a clear error message (detect
  `Killed signal` in stderr) suggesting the split/low-memory options.

Track C is independent of track V and can go first (it blocks tracing
experiments on the largest designs).

### Status: done (2026-10-08), C1 was enough

What C0 found (generated C function sizes, wide_bench 1000 lanes):
`delta_loop` 263k lines, `__init__` 219k, `batch_run` 79k,
`refresh_data_snapshot` 45k -- all from per-process / per-signal
unrolled code (an `if/elif` rank switch with one case per process,
per-seq-process `fire`/`done` locals and edge checks, the cont-settle
fixpoint calling every `cont_N` inline, per-signal init statements), with
every process body inlinable into them. gcc memory and time grew
superlinearly (250 -> 500 lanes: 0.64 -> 3.0 GB, 39 -> 288 s).

Fix (`_proc_table_lines` in `_gen_sections.py`): function-pointer tables
(`DL_CONT_FN`, `DL_RANK_FN`, `DL_SEQ_FN`, filled once at import), a CSR
edge table for seq processes (`DL_SEQ_EDGE_*`) with fire/done flag
arrays, `DL_NEG_REACT[sid]` for batch_run's negedge skip, and `DL_SIG_*`
static arrays for `__init__`; the generated code loops over them. Plus
`-g0` (generated `setup.py`) and a clear error when `cc1` is killed.

| design | before | after |
|---|---|---|
| wide_bench 250 (gcc only) | 39 s, 0.64 GB | 14 s, 0.48 GB |
| wide_bench 500 (gcc only) | 288 s, 3.0 GB | 33 s, 1.2 GB |
| wide_bench 1000 (gcc only) | >13 GB, not finished | 57 s, 1.1 GB |
| 2000 lanes, end to end | `cc1` OOM-killed (27 GB machine) | 108 s, 2.0 GB peak |
| 4000 lanes, end to end | -- | 244 s, 3.8 GB peak |

(End-to-end rows use a generate-loop version of the bench; the flat
wide_bench source spends ~210 s and ~8.9 GB in the Lark parser at 2000
lanes, unrelated to the compiled engine.) Runtime: same-session A/B over
the full `scan_vs_activity_bench` sweep (both engines) within noise
(±5%, repeated runs on the one outlier). Split compile still works
(identical results at 300 lanes) and stays opt-in; C2 not needed.

## Open questions

1. Trigger expression syntax: Verilog-like strings (proposed above) vs. a
   small Python builder API (`sig("u_dp.err") & (sig("u_dp.state") ==
   3)`). Strings reuse the existing parser and read naturally; a builder
   gives editor completion. Proposal: strings, with the builder as a
   possible later addition.
2. `$error` as a failure trigger: today engines treat `$error` like
   `$display` (vm) and `$fatal` like `$finish`. Do we want a uniform
   "simulation error" event across engines (counted, reported, optionally
   fatal), which the capture hooks into? Proposal: yes, as part of V5.
3. Captures: one file per capture (proposed) vs. one file with
   time gaps between windows.
4. Memory defaults: keep today's behavior where nothing is selected
   (`attach_vcd` with no selection includes memory elements; `$dumpvars`
   with no arguments doesn't), or make both exclude memories unless asked?
   Proposal: keep both as they are now for compatibility; selections
   default to no memories.
