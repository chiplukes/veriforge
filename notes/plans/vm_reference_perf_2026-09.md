# VM and reference simulator performance — September 2026

## Objective

Improve VM throughput without per-design native compilation, and improve the
pure-Python reference engine while preserving its usefulness as an independent
correctness baseline. Start with VM native batching and reuse the existing
testbench lowering infrastructure.

## Investigation baseline

Measured on 2026-09-27 using CPython 3.14.3 and `benchmarks/benchmark.py`'s
ALU/register-file/FSM/counter/LFSR DUT. Median of three short runs; parsing and
elaboration excluded. Step runs used 5,000 cycles; compiled batch used 100,000.
These execution modes include different clock/setup overheads and are indicative,
not isolated kernel comparisons.

| Mode | Cycles/second |
| --- | ---: |
| Reference | 9,076 |
| Python VM (`vm`) | 5,888 |
| Cython VM (`vm-fast`) | 194,905 |
| Compiled step | 342,401 |
| Compiled batch | 9,331,339 |

The VM already has a native delta loop, but the outer event loop and pre-scheduled
clock events execute in Python. `compile_native()` already lowers supported bench
primitives to HDL wrappers usable by all engines. Native batching is currently
compiled-only. Reference profiling found repeated AST evaluation, signedness/width
analysis, and full process/continuous-assignment scans. The reference scheduler
builds `_sig_to_procs` but does not use it to select runtime work.

## Phase 1 — native VM cycle runner (implemented)

- [x] Add a Cython multi-cycle runner reusing the existing delta-loop kernel.
- [x] Support `Simulator.batch_run()` and `run_cycles()` with `engine="vm-fast"`.
- [x] Preserve initialization, external-drive settling, NBA ordering, both clock
      edges, simulation time, chunked execution, and early `$finish` behavior.
- [x] Apply ordered cycle-relative stimulus before the rising edge. Start with
      known integer scalar signals up to 64 bits; reject unsupported event targets
      explicitly. Wide internal signals and narrow memories retain VM semantics.
- [x] Reject unsupported batch configurations before execution: absent/stale
      native extension, timed HDL processes, queued events, waveform callbacks,
      and monitors. Keep the ordinary event-driven API available for these cases.
- [x] Add `engine="vm-fast"` to `LoweredDesign.batch_run()` while preserving the
      compiled default. Initially support one clock domain.
- [x] Differential tests against ordinary VM execution and reference behavior,
      including reset, dependency chains, memories, wide values, repeated chunks,
      output, early termination, and rejected configurations.
- [x] Add a reproducible VM batch benchmark, report initialization separately from
      execution, compare final state, and record results here.

The first increment kept both clock edges and the existing propagation algorithm.
Phase 2 measures changes separately. Never silently discard unsupported
callbacks/events.

## Phase 2 — VM propagation and synchronization

- [x] Profile the native runner on larger sparse designs, lowered protocols, and
  memory-heavy designs as well as the small benchmark.
- [x] Replace continuous-assignment scans with indexed candidate queues; preserve
  deterministic ordering, diamond reactivation, feedback convergence, and limits.
- [x] Evaluate dependency ordering; implement a guarded batch-only path for pure,
  single-writer acyclic networks with no procedural observers of assigned signals.
- [x] Snapshot only edge-sensitive signals and clear only edge-sensitive
  process flags in the native batch loop; use bulk operations for dense cases.
- [x] Measure coroutine signal/memory synchronization in event-driven VM runs.
- [x] Avoid whole-memory coroutine synchronization when a conservative body
  analysis proves the coroutine cannot read or write the memory.
- [x] Measure and add a falling-edge shortcut only when no process or continuous
  assignment can observe the falling clock edge.

## Phase 3 — VM instruction execution

- [x] Measure compiled and executed opcode distributions and native batch cost
  on representative arithmetic, propagation, memory, and benchmark workloads.
- [x] Remove redundant `RESIZE` before narrow direct signal stores; make native
  stores consume the low word when the producer is wide.
- Evaluate fused common instructions, precomputed masks/widths, and a narrow-only
  execution path. Preserve unknown masks, signedness, and wide fallbacks.
- Consider a register-based instruction format only if simpler changes leave
  dispatch/stack traffic dominant. Avoid a broad VM rewrite without evidence.

## Phase 4 — reference engine

- [x] Use `_sig_to_procs` to select affected processes with stable ordering and correct
  edge detection; retain the old behavior as a differential baseline during work.
- [x] Queue affected continuous assignments instead of repeatedly scanning all
  of them; retain a declaration-order scan when candidates are dense.
- [x] Cache declaration signedness per elaborated context; keep function scopes
  separate and context-dependent expression widths dynamic.
- [x] Investigate redundant `Value` resizing/allocation after scheduler
  improvements; remove repeated assignment-width work instead of changing
  `Value` construction semantics.
- Keep tree walking and independent semantic implementations. Validate against
  pre-change traces and, where supported, an external simulator in addition to
  VM/compiled comparisons.

## Validation and acceptance

Each increment must preserve observable values/masks, memory state, event ordering,
and documented time/phase behavior. Tests cover supported behavior and explicit
rejections; native tests must actually load the extension. Run existing relevant VM,
lowering, and compiled API tests after shared API changes. Record measured throughput
without promising a speedup across all workloads. No changes to the reference
execution engine are required for Phase 1.

## Implementation results

Implemented native cycle batching in `CyContext`, reusing the delta-loop kernel.
`Simulator.batch_run()` and `run_cycles()` now accept `vm-fast`;
`LoweredDesign.batch_run(engine="vm-fast")` uses the same runner. The compiled
lowering default is unchanged. Display output retains phase timestamps, zero
cycles does not bootstrap, and `$finish` leaves time at the stopping phase and
returns only fully completed cycles.

The initial tests exposed an existing native-kernel bug: after a blocking write,
merging the dirty signal could overwrite `$finish`'s status with a successful
buffer-push status. The kernel now preserves execution status. Native initial
blocks also synchronize writes made before `$finish` back to native storage.

Validation:

- Native extension rebuilt successfully with the existing optimized build.
- 38 focused batch/lowered-roundtrip tests passed, including reference/Python VM/
  native VM comparisons, memory state, wide unknown masks, initialization/NBAs,
  edge timestamps, partial stops, and unsupported configurations.
- 501 existing VM, cross-engine validation, compiled `run_cycles`, NBA-capacity,
  and lowering tests passed (40 compiled fallback warnings).
- 330 VM tests passed with `VERIFORGE_DISABLE_CYTHON_VM=1`.
- The lowering/batch selection with the extension disabled passed 122 tests;
  38 native-only tests skipped as intended.
- New tests added to both VM CI configurations; native-only tests skip when the
  extension is intentionally disabled.
- Ruff checks and whitespace validation passed.

Benchmark: `python benchmarks/vm_batch.py --cycles 100000 --repeat 5`.
Both modes execute 100,002 cycles including two reset cycles; all final signal
values, masks, widths, and memory cells match. Parsing and construction are
reported separately; execution includes initialization and clock/event setup.

| Mode | Median execution | Cycles/second |
| --- | ---: | ---: |
| Native VM, event loop | 0.5848 s | 170,994 |
| Native VM, batch loop | 0.0981 s | 1,019,623 |

Measured speedup: **5.96x** on this DUT. Construction was approximately 3.5–3.7 ms
for both modes. An earlier 20,002-cycle, three-repeat run measured 5.36x. This is a workload-specific result, not a speedup guarantee.

## Phase 2 results to date

Native continuous-assignment propagation now has a reverse signal-to-assignment
index. It gathers only assignments affected by the current changed-signal set,
deduplicates them, and executes them in the original declaration order. The
existing reactivation loop still handles diamonds and feedback. A second change
skips the clock's falling-edge delta pass only when every process that observes
the clock is explicitly posedge-triggered. Designs with negedge logic, a
combinational clock read, or a derived clock keep the full falling-edge pass.

The reproducible workload suite is
`uv run python benchmarks/vm_propagation.py --cycles 10000 --repeat 3 --assigns 256`.
It checks final signal and memory state against the event-driven VM. The numbers
below are medians from the same host; construction and parsing are excluded.

| Workload | Batch execution | Batch cycles/second | Batch vs. step |
| --- | ---: | ---: | ---: |
| Sparse 256 assignments | 0.0018 s | 5,449,618 | 28.23x |
| Active 256 assignments | 0.1099 s | 91,032 | 1.68x |
| 256-entry memory | 0.0012 s | 8,306,779 | 38.69x |
| Lowered AXI-Stream loopback | 0.0027 s | 3,744,839 | 39.94x |

Before the reverse index, the sparse 256-assignment microbenchmark took
approximately 0.0127 s for 10,000 cycles; after indexing, approximately
0.0025 s before the falling-edge shortcut and 0.0018 s with it. The active
case went from approximately 0.1138 s to 0.1099 s, as expected when nearly
every assignment runs. The original ALU/register-file/FSM benchmark reached
approximately 1.09 million batch cycles/second in a 100,000-cycle run,
versus 1.02 million in Phase 1. These are workload-specific measurements.

### Dependency scheduling

The native batch runner now evaluates eligible continuous assignments in
dependency order. An affected assignment runs once after its affected
predecessors. This removes repeated evaluation in reconvergent networks, where
an assignment depends on both a changing source and a chain driven by that
source. A simple chain already needed only one evaluation per assignment, so
changing its scheduling has little effect.

Ready assignments use a two-level bitset, so wide fanout does not require a
heap operation per assignment and sparse activity skips inactive groups. The
planner and its native storage are built once at elaboration.

Eligibility is deliberately conservative and applies to the entire continuous
assignment network. Each assignment must contain only allowlisted pure expression
instructions and one whole-signal store. Bytecode reads must match sensitivity
metadata. Destinations must have unique writers, cannot be written by procedural
blocks, and cannot trigger procedural blocks. Cycles, partial writes, memory
accesses, inlined functions with scratch writes, random expressions, and timed
processes retain the existing path. Independent assignments and single-input
chains/fanout also keep their existing scheduler: they offer no redundant
evaluations for dependency scheduling to remove. Width, sign, and unknown-bit
operations still use the same bytecode executor.

The observer restriction matters even in an acyclic network: unequal-length
paths can make a wire change temporarily and return to its previous value. The
existing scheduler records that change and can activate a combinational observer.
Collapsing the paths must not suppress that activation. A regression exercises
this exact case. Ordinary event-driven execution and between-batch settling
retain declaration-order passes. A batch also falls back when its delta limit
is smaller than the network's maximum dependency depth.

The benchmark now includes a 64-stage chain, a 64-stage reconvergent network,
and wide fanout with a short dependency chain.
Use `uv run python benchmarks/vm_propagation.py --cycles 10000 --repeat 5 --compare-legacy`
to compare against both event-driven execution and the previous batch scheduler
on the same workload, with final signal and memory state checked in all modes.

Final native batch medians for 10,000 cycles and five repeats on this host:

| Workload | Execution | Cycles/second | Versus prior batch scheduler |
| --- | ---: | ---: | ---: |
| 64-stage reconvergent network | 0.0246 s | 405,882 | 34.20x |
| 64-stage single-input chain | 0.0230 s | 434,784 | 0.98x |
| 256-assignment shallow fanout | 0.0989 s | 101,106 | 1.00x |
| 256 active assignments | 0.0984 s | 101,625 | 1.00x |
| 256-entry memory | 0.0011 s | 9,330,047 | 1.00x |
| Lowered AXI-Stream loopback | 0.0026 s | 3,831,321 | 1.02x |

The large gain is specific to a network with repeated evaluations; the other
workloads have little or no change. The event-driven VM and previous batch
paths matched the final signal and memory state in every benchmark repeat.

Validation: 484 VM, cross-engine, memory, batch, and lowering regression tests
passed with the native extension; the 76 native-only focused tests skipped as
expected when the extension was disabled. Ruff, whitespace, and repository file
checks passed. The new tests cover random dependency graphs, wide and unknown
values, procedural observers, fallback cases, and bitset word/group boundaries.

### Batch edge bookkeeping

The native batch loop now builds unique lists of signals with explicit edge
triggers and processes with edge controls at elaboration. Before each active
phase it snapshots and clears only those entries. When at least one quarter of
all signals or processes are edge-sensitive, it uses the existing bulk copy or
clear operation. Ordinary event-driven execution retains its full snapshots.
This preserves old-value edge detection through derived clocks, asynchronous
reset inputs, both clock edges, and event stimulus.

The before/after measurements exclude parsing and construction and compare all
final signal state against the event-driven VM. With 20,000 cycles and five
repeats on the same host, dormant-1024 changed from about 3.07 ms to 1.10 ms,
dormant-4096 from 22.62 ms to 1.20 ms, and sparse-1024 from 14.29 ms to 2.75 ms.
The reproducible workload is
`uv run python benchmarks/vm_edge_snapshots.py --cycles 20000 --repeat 5`.
These deliberately sparse designs isolate snapshot and flag-clearing overhead;
they do not predict the gain for dense activity.

The native extension rebuilt successfully. All 78 focused VM batch,
propagation, and lowered-roundtrip tests passed, including a new sparse
derived-reset and dual-edge comparison against the event-driven native VM,
Python VM, and reference engine. The standard 100,000-cycle VM batch benchmark
reported 1,067,404 cycles/s versus 179,412 cycles/s for event-driven VM;
all final states matched. Ruff, whitespace, and repository file checks passed.

An event-driven `vm-fast` cProfile run with `always #5 clk=~clk`, 100 rising
edges, and an otherwise unused 1,024-word memory recorded 401 coroutine syncs.
The memory copy into the reference context took 0.288 s cumulative; the copy
back took 0.028 s. The same run without memory spent under 0.001 s in each
direction. The VM now skips memory copies for timed `always` coroutines only
when a model-tree scan finds no memory reference, function/task call, or unknown
node. Timed `initial` blocks, memory-accessing blocks, and VCD callbacks retain
the full sync. Rerunning that profile reduced both memory-sync directions to
under 0.001 s cumulative. The old signal-only access set did not establish
whether a coroutine wrote memory, so the memory check includes assignment
targets as well as reads.

An isolated 100-rising-edge timed-clock run with 1,024 unused memory words,
five repeats, and simulator construction excluded measured 0.118 s median
with full coroutine memory sync and 0.0014 s with the guarded skip. Both runs
used the same VM implementation and source; a temporary benchmark toggled the
guard to force the full-sync comparison. This is an intentionally favorable
workload, not a general event-loop speedup estimate. Six focused differential
tests cover native VM, Python VM, and reference behavior for inactive memory,
memory reads/writes, write-only accesses, timed initial blocks, and call
fallback. A broader VM/memory run passed 504 tests; after the write-only
signal-sync correction, a final VM and timing-memory rerun passed 352 tests.
Ruff, formatting, whitespace, and repository file checks passed.

### Direct-store instruction reduction

`benchmarks/vm_opcode_profile.py` reports compiled opcodes and dynamically
executed Python VM opcodes. The trace is a guide to the instruction mix; native
batch scheduling can execute processes a different number of times. In the
100-cycle profiles, direct-store `RESIZE` was frequent, while both `STORE_SIG`
and `NBA_SIG` already apply the narrow destination mask. The compiler now omits
that resize for destinations up to 64 bits. The native store reads the low word
of a wide producer before masking; wide destinations retain their explicit
resize, which prepares the full word array.

The benchmark DUT's compiled program shrank from 411 to 364 instructions;
the 64-stage reconvergent chain shrank from 397 to 331. On this host, the
standard 100,000-cycle VM batch benchmark changed from 1,073,764 to 1,124,899
cycles/s (five repeats, parsing and construction excluded). A 2,000-cycle,
64-assignment active workload changed from 356,185 to 399,423 batch cycles/s;
the shorter runs are more sensitive to timing noise. All benchmark final-state
comparisons passed. The native extension rebuilt, and 452 VM, cross-engine,
batch, and wide-value regression tests passed. Targeted tests compare
wide-to-narrow blocking/NBA stores, unknown masks, signed extension, and wide
destinations against the Python VM and reference engine.

Further VM instruction work should use these profiles to justify narrower
execution paths or precomputed widths/masks.

### Reference continuous-assignment index

The pure-Python reference scheduler now builds a signal-to-continuous-assignment
index at elaboration. A dirty signal schedules only assignments that read it.
When an assignment changes an output, later assignments sensitive to that
output enter the same declaration-order pass; earlier assignments wait for the
next convergence pass, preserving the previous scheduler's behavior and delta
limits. If at least one quarter of assignments are initially eligible, it uses
the previous full scan to avoid heap overhead on dense activity.

The reproducible benchmark is
`uv run python benchmarks/reference_propagation.py --assigns 256 --cycles 200 --repeat 5`.
Five paired old/new scheduler runs on the same 200-cycle, 256-assignment
workloads measured 16.86 → 7.84 ms for sparse activity (2.15x) and
411.15 → 415.85 ms for active activity (within about 1% of baseline).
These timings exclude parsing and simulator construction. The durable benchmark
compares every final signal value, mask, and width with vm-fast.

A differential run loaded the pre-change reference scheduler from Git and
compared complete time-step signal and memory snapshots for eight shuffled
dependency graphs, a memory case, and a concat-LHS case; all 10 traces matched.
The broader reference scheduling and cross-engine suite passed 227 tests.

### Reference process index

The reference scheduler now selects combinational and sequential always blocks
through its existing signal-to-process index. It restores declaration order
before executing selected blocks, retains the once-per-time-step sequential
guard, and remembers edge-signal changes across delta cycles. A declaration-
order scan remains for dense activity. The sparse process benchmark with 256
dormant combinational and sequential blocks and one active counter measured
63.8 → 9.3 ms over 200 cycles (6.9x) in three-run medians. A 64-block,
100-cycle active workload measured 68.8 → 71.0 ms (about 3% slower), within
the cost of sparse candidate selection. The durable benchmark is
`uv run python benchmarks/reference_processes.py`.

Pre-change and indexed schedulers produced identical complete time-step
signal and memory snapshots for derived-clock, async-reset, combinational-
chain, and memory-update cases (40 snapshots each). The focused scheduler
and cross-engine suite passed 203 tests.

### Reference signedness metadata cache

Profiling the mixed reference DUT showed `_expr_signed` called about 61,000
times per 1,000 cycles. Its result depends on declarations, not signal values,
so an elaborated `EvalContext` now caches results by expression object after
all declarations and functions are registered. User-function calls use fresh
contexts and therefore cannot inherit module-scope results. Self-width remains
uncached: external drives and task ports can change the width of an identifier.

The paired benchmark `uv run python benchmarks/reference_metadata.py --repeat 9`
measured 125.7 → 119.9 ms (1.05x) for the mixed DUT and 73.5 → 67.4 ms
(1.09x) for 64 active processes over 100 cycles. It compares final signal and
memory state between cached and uncached runs. Complete time-step traces also
matched for a signed user function (80 snapshots), memory updates (80), and
the mixed DUT (413). Focused evaluator/scheduler tests passed 108 cases, and
the signed/function/wide-array cross-engine suite passed 114 cases.

### Reference assignment width reuse

On the mixed DUT, cProfile recorded only 378 `Value.resize()` calls over
2,000 cycles, so resizing itself is not a useful target. The assignment path
did make about 64,000 redundant sign-extension helper calls in the same run.
Procedural and continuous assignments now use their already-computed LHS
width and only check signedness when the evaluated RHS is narrower. This keeps
the same extension rule without repeating LHS width evaluation.

Nine paired runs against the pre-change scheduler/executor measured
120.1 → 116.0 ms (1.04x) for the mixed DUT and 64.9 → 61.6 ms (1.05x) for
64 active processes over 100 cycles. Old/new signal and memory snapshots
matched at every time step in four signed-assignment, function, and memory
cases (80 snapshots each). The assignment, width, function, and cross-engine
suite passed 221 tests. `Value` construction and resize behavior were left
unchanged.

### Main benchmark harness refresh

`benchmarks/benchmark.py` now measures `vm-fast` through `Simulator.run()`,
`run_step()`, and native `batch_run()` as separate rows. Step and batch run two
reset cycles outside the timed section, then execute the requested number of
full cycles. The harness checks their final signal and memory state for equality.
The refreshed 50,000-cycle report is in `notes/benchmarks.md`: VM-fast step
reached 163.2K cycles/s and batch reached 1.12M cycles/s (about 6.9x faster
than step) on this host. The event-loop row includes a different setup boundary,
which the report states explicitly. Native-disabled runs skip both new modes.

### Reference active continuous propagation

cProfile on the 256-assignment active workload found about 103,000 continuous
assignment evaluations over 200 cycles. Expression evaluation dominates, but
the scheduler also repeatedly resolves plain output names to read their old
value and width. `_run_dirty_continuous_assigns` now reads those directly from
signal storage for simple, present, non-memory Identifier targets. It still
uses the executor for writes and retains the existing path for memories,
hierarchical names, bit/range selects, and concatenations.

Seven-run medians from `uv run python benchmarks/reference_propagation.py`
(`--assigns 256 --cycles 200 --repeat 7`) changed from 0.3714 to 0.3066 s in the active case
(about 1.21x), while the sparse case remained about 0.008 s. Final state
matched vm-fast. The scheduler, reference-propagation, and VM/reference
comparison suite passed 72 tests. These measurements are workload-specific.

Next: profile expression dispatch for the next reference-engine bottleneck.
