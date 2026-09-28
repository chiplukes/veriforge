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
- [ ] Evaluate dependency ordering with same-pass propagation for acyclic regions.
- [ ] Consider edge-only snapshots and selective coroutine signal/memory syncing.
- [x] Measure and add a falling-edge shortcut only when no process or continuous
  assignment can observe the falling clock edge.

## Phase 3 — VM instruction execution

- Measure opcode distribution and native execution cost.
- Evaluate fused common instructions, precomputed masks/widths, and a narrow-only
  execution path. Preserve unknown masks, signedness, and wide fallbacks.
- Consider a register-based instruction format only if simpler changes leave
  dispatch/stack traffic dominant. Avoid a broad VM rewrite without evidence.

## Phase 4 — reference engine

- Use `_sig_to_procs` to select affected processes with stable ordering and correct
  edge detection; retain the old behavior as a differential baseline during work.
- Queue affected continuous assignments instead of repeatedly scanning all of them.
- Cache provably static expression metadata per elaborated context; account for
  function scopes and context-dependent widths.
- Investigate redundant `Value` resizing/allocation after scheduler improvements.
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
`python benchmarks/vm_propagation.py --cycles 10000 --repeat 3 --assigns 256`.
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

A reverse-ordered dependent chain still takes multiple delta passes; a 64-stage
chain took approximately 0.035 s for 10,000 cycles. Same-pass dependency
ordering may help, but needs careful treatment of feedback, multi-path updates,
assignment ordering, and delta limits before implementation. Reference engine
work remains in Phase 4.
