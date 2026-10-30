# Compiled simulator: build efficiency and event scaling

## Scope and status

Implement the 2026-10-30 dispatch/build review in the order below. Preserve the
table-driven dispatch introduced by `4526a61`, four-state values, process order,
snapshot semantics, and the public Simulator API. Changes are authorized by the
request to formalize the review and proceed; stages do not require another
approval round. Record implementation evidence and remaining limitations here.

| Stage | Work | Status |
| --- | --- | --- |
| 1 | Cache correctness and reproducible build configuration | Implemented; 4 focused regressions passed |
| 2 | Required-helper emission and smaller worker preambles | Implemented; 139 helper/codegen checks passed |
| 3 | Clock/event-indexed sequential scheduling | Implemented; 436 edge/equivalence checks passed |
| 4 | Real, memory-bounded parallel source compilation | Implemented; resource and split integration checks passed |
| 5 | Parameter-specialized process-body sharing | Implemented opt-in; compiled and cross-engine regressions passed |
| 6 | Selective scalar/wide signal snapshots | Complete by default; broad and final focused regressions passed |

Bounded scan-fallback dispatch remains a follow-up investigation.

## Stage 6: Selective signal snapshots

- Retain scalar snapshots for every edge trigger, and scalar/wide snapshots for
  actual frozen reads in emitted process bodies, including whole-signal/slice
  helpers and combinational bodies that use the snapshot convention.
- Analyze before optional process sharing and routing, so streaming/split outputs
  use the same dependency set. Unknown/dynamic snapshot uses conservatively copy
  everything; keep an all-signals diagnostic mode in cache identity.
- Copy compact contiguous spans, retaining full memcpy for dense sets. Apply the
  same selection to snapshot(), refresh_data_snapshot(), event settling, and
  both batch edges. Preserve clock history, live-read exceptions, memory
  journals, and convergence snapshots.
- Validate across engines, scan/queue, batch/stepped/event-driven execution,
  four-state values, >64-bit whole/slice reads, split builds, and sharing.
- Measure sparse narrow/wide designs and dense controls before choosing the
  default; document measured gains and costs. Incremental dirty-signal journals
  are outside this stage and need separate lifecycle/overhead measurements.

## Baseline evidence

Review platform: Linux/WSL2, Python 3.12.13, Cython 3.2.4, setuptools 82.0.0,
GCC 15.2.0. Existing scan/queue equivalence suite: 47 passed.

- Setuptools parallelizes Extension objects; the current split build creates
  only one Extension, so `build_ext --parallel` does not parallelize its sources.
- Split and unsplit generation currently share an elaboration-cache key despite
  emitting different source hashes. The cache also needs explicit handling of
  `NO_COMPILE_CACHE` and `delta_limit`.
- A 64-instance example emitted 22,661 lines unsplit versus 124,735 across nine
  split files because helpers were duplicated in each worker.
- A temporary required-helper prototype reduced counter cold builds from
  6.35 to 3.87 seconds (16 bits) and 6.41 to 3.95 seconds (128 bits). Runtime was
  approximately unchanged. High-word/carry and X-mask checks agreed.
- One active counter took 10.8 ns/cycle in forced queue mode. Adding 512 idle
  scalar signals took 50.8 ns/cycle; adding 512 processes on a separate idle
  clock took 638.1 ns/cycle. Useful counter work was identical.
- A generate loop of 256 identical leaf modules emitted 2,048 process functions.

These are synthetic measurements, not promised application-level speedups.
Cold build samples were single builds; runtime samples were medians of five
100,000-cycle batches. Re-measure changed paths with isolated caches and record
source sizes as well as elapsed time.

## Stage 1: Cache correctness

- Use one normalized build-configuration fingerprint for compilation and
  elaboration keys, including split and subsequent optimization options.
- Include the requested delta limit in elaboration identity.
- Honor disabled caching before elaboration lookup, and avoid saving reusable
  elaboration metadata when caching is disabled.
- Cover warm-cache option changes, source changes, and disabled-cache behavior.
  Preserve legacy environment-variable support.

Acceptance: a changed effective configuration selects the intended generated
binary, unchanged settings reuse it, and disabled caching performs a build.

## Stage 2: Required-helper emission

- Identify generated helper implementations and their dependencies. Retain only
  helpers referenced by emitted process/runtime code, including callbacks and
  function pointers. Keep declarations, imports, and external code intact.
- Apply selection consistently to string, streaming, and split generation; a
  worker retains its own dependency closure instead of the full helper library.
- Keep an all-helpers diagnostic mode for differential checks and rollback.

Acceptance: narrow, >64-bit, four-state, memory, function/task, and tracing cases
compile and match the unpruned path. Generated source and cold-build time improve
without a material runtime regression. Retention must be conservative when a
definition cannot be recognized safely.

## Stage 3: Indexed sequential scheduling

- Build an inverse index from distinct edge signals/types to subscribing
  sequential process IDs. Evaluate each edge once per iteration and dispatch
  affected processes in their original order.
- Retain the per-call fired/done semantics and pre-edge snapshots. Avoid work
  proportional to all idle sequential processes where possible.
- Validate derived clocks, asynchronous resets, simultaneous triggers, X edges,
  feedback, and wide values against scan/reference/VM behavior.

Acceptance: differential outputs and relevant iteration counts remain equal;
idle-clock scaling improves and dense/small cases remain competitive.

## Stage 4: Parallel compilation

- Schedule independent C object compilations explicitly, then link one extension.
  Preserve compatible Cython/public-header linkage and exception reporting.
- Limit workers by source count, CPUs, a configurable cap, and available memory.
  Use a serial fallback for unsupported compiler implementations/platforms.
- Test actual overlapping source jobs, exception propagation, and split/nonsplit
  output equivalence. Keep split generation opt-in until broad application
  performance and stability justify changing that default.

Acceptance: multiple source jobs really overlap, the cap is respected, failed
jobs fail the build, and resulting extensions import and simulate correctly.

## Stage 5: Shared process implementations

- Start with a conservative eligible subset of generated scalar processes.
  Canonicalize address operands while specializing resolved constants, widths,
  and signedness; use per-process address descriptors and distinct state.
- Share only proven-compatible bodies. Keep memories, stateful system calls,
  unsupported address patterns, and other uncertain cases on the existing path.
- Preserve process identity, event order, scope-sensitive behavior, and original
  source markers. Measure code size, build time, and dispatch/address overhead.
- Introduce a diagnostic/opt-in control if the eligible subset or runtime tradeoff
  needs more application validation before enabling it generally.

Acceptance: repeated leaf instances reuse implementations while keeping their
states independent; different parameters/widths remain distinct; all-engine and
wide-value regressions pass. Report the supported subset and measured tradeoffs.

## Validation and documentation

Run focused tests after each stage, then the relevant compiled-engine and
cross-engine suites. Follow AGENTS.md for long full-suite runs. Use Ruff, mypy,
file-inventory checks, and codemap anchor/link checks. Add reproducible benchmark
coverage for sparse/dense, repeated-instance, narrow/wide, and split cases.
Update simulator/cache notes, the codemap, and this status table as behavior lands.

## Implementation record

- Plan created before implementation; Stage 1 started.
- Stage 1: compilation and elaboration share normalized options, including split,
  helper mode, toolchain environment, and delta-engine selection. Elaboration keys
  include delta limits, and disabled caching bypasses both lookups and metadata
  writes. Four focused regressions passed.
- Stage 2: required-helper selection is on by default; `COMPILE_HELPERS=all`
  retains the diagnostic baseline. String and streaming outputs/hashes agree;
  split workers keep their own helper closure. Streaming finalization holds only
  the helper region, not the entire design, in memory. Helper/codegen tests:
  139 passed, including 16/128-bit memory and X-mask cases.
- Stage 3: inverse edge subscribers and ordered fired/done bitmaps replace the
  per-process edge scan. The smaller of changed signals and distinct edge signals
  is inspected. Edge/startup/equivalence checks: 436 passed; a separate 130-process
  mixed-edge/reset/X test matches all four engines in scan and queue modes.
- Stage 4: Unix object jobs now overlap through a memory/CPU-bounded thread pool;
  custom compiler loops (including MSVC) retain their serial behavior. Split
  generation remains opt-in. Real split compilation exposed a Python inline
  comment leaking into the shared C struct header; header emission now strips
  those comments. Worker-cap, overlap, failure propagation, stack fallback, and
  split/nonsplit 256-bit output checks passed.
- Stage 5: `COMPILE_SHARE_PROCESSES=1` enables bounded scalar-body sharing,
  specialized by parameters, widths, and signedness. Per-process wrappers and
  address descriptors retain state and identity. Split worker pointer
  initialization is explicit, since worker Python initializers are not run.
  Narrow parameter variants, independent instances, streaming output identity,
  actual split builds, and 128-bit fallback regressions passed. Sharing remains
  opt-in pending broader application measurements.
- Added `benchmarks/compiled_scaling.py` with fresh-cache `helpers`, `idle`,
  `instances`, and `split` cases. Each reports generated-source size, cold build
  latency, and repeated runtime measurements while checking final values.

## Stages 1–5 validation

- Compiled subsystem: **872 passed, 3,843 skipped** in 22m43s. This includes
  helper selection, both delta engines, split builds, process sharing, and
  existing compiled regressions. Skipped tests were not validated by this run.
- Fast parser/model/analysis/preprocessor/formatter suites plus focused compiled
  and generate-select regressions: **1,209 passed** in 2m02s. These runs overlap;
  their counts should not be added as unique tests.
- Cross-engine checks cover `reference`, `vm`, `vm-fast`, and `compiled`, with
  128/256-bit values, four-state edges, asynchronous resets, parameter variants,
  and independent generated-instance state.
- Ruff lint/format, mypy, whitespace, codemap anchors/links, and the source-file
  inventory passed. Inventory includes new working-tree files before staging.
- Full-root pytest was not run. Native Windows/MSVC execution was not available;
  its custom compiler loop retains serial behavior.

## Stages 1–5 isolated measurements

Final measurements use the review platform above, forced queue mode, separate
temporary caches, no concurrent tests/builds, and the benchmark's default
100,000 cycles per batch with five runtime samples. Cold-build timings are
single samples and include simulator construction. Source size counts generated
`.pyx` lines, not binary size. All benchmark cases check their final counter value.

| Case | Comparison | Cold build | Generated lines | Runtime ns/cycle |
| --- | --- | --- | --- | --- |
| 16-bit counter | Full helpers → required helpers | 6.24 → 3.84 s | 13,461 → 1,209 | 10.93 → 10.91 |
| 128-bit counter | Full helpers → required helpers | 6.72 → 4.04 s | 14,461 → 2,320 | 13.51 → 13.04 |
| 64 leaf instances | Sharing off → on | 6.96 → 6.08 s | 9,333 → 7,927 | 2,144 → 2,134 |
| 64 leaf instances | Unsplit → split, four-job cap | 6.87 → 6.00 s | 9,333 → 13,241 | 2,150 → 2,231 |
| Same split design | One-job → four-job cap | 8.07 → 6.00 s | 13,241 → 13,241 | 2,232 → 2,231 |

Required helpers reduce these cold builds by 38–40%, with similar runtime.
Sharing produces four reusable implementations for the repeated scalar leaf
design, reducing source by 15% and this cold build by 13%; runtime is similar
within measurement noise. Sharing remains off by default because one synthetic
case does not establish its effect on application workloads.

The nine-file split build is 26% faster with a four-job cap than with one job.
Compared with the unsplit build it is 13% faster to build, emits more total
source, and runs about 4% slower in this example. Split mode therefore remains
opt-in. These measurements isolate the build-worker setting; the overlap test
also verifies that independent source jobs actually execute concurrently.
Reproduce the serial comparison by prefixing the split benchmark command with
`VERIFORGE_COMPILE_JOBS=1`.

| Idle state added to one active counter | Review baseline ns/cycle | Indexed scheduler ns/cycle |
| --- | --- | --- |
| None | 10.8 | 10.96 |
| 128 scalar signals | 30.2 | 30.05 |
| 128 processes on an idle clock | 196.9 | 34.78 |
| 512 scalar signals | 50.8 | 44.90 |
| 512 processes on an idle clock | 638.1 | 49.73 |

The idle-process cases improve about 5.7× and 12.8× relative to the recorded
review measurements. Signal snapshot work remains proportional to signal count;
the signal-only controls show that cost is still material. The earlier baseline
and final measurements were separate runs, so small differences are not evidence
of an optimization. Selective snapshots are evaluated below.

## Stage 6 implementation and measurements

Implemented default `COMPILE_SNAPSHOTS=needed`, with `all` retaining the full
signal-copy diagnostic path. Both cache layers include the effective setting.
Snapshot dependencies are collected from emitted bodies before optional sharing,
including whole-signal/slice helpers, with conservative fallback on unrecognized
or dynamic uses. Edge-only wide signals require only their scalar mirror.
Contiguous runs use memcpy, sets at least half full keep full copies, and more
than four disjoint runs use compact tables. Table plans budget twelve extra slots
per run and retain a full copy when fragmentation makes that cheaper. All seven
signal snapshot sites
share this plan; memory journals and convergence checks are unchanged.

Reproduce with:

```
uv run python benchmarks/compiled_scaling.py --case snapshots --output snapshots.jsonl
```

The same platform, isolated-cache protocol, 100,000-cycle batches, and five-sample
runtime median described above apply. Comparison is `all` → `needed` within the
new implementation; each row checks its final counter value.

| Design | Runtime ns/cycle | Cold build seconds | Generated lines |
| --- | --- | --- | --- |
| Small 16-bit counter | 11.12 → 11.06 | 3.68 → 3.62 | 1,198 → 1,198 |
| Counter + 512 unused scalar signals | 59.37 → 8.48 | 3.63 → 3.63 | 3,414 → 3,416 |
| 128-bit counter + 512 unused 256-bit signals | 581.27 → 12.00 | 3.78 → 3.78 | 4,539 → 4,537 |
| Counter + 512 idle-clock counter processes | 53.20 → 52.30 | 6.65 → 6.66 | 10,114 → 10,114 |
| 64 active leaf instances | 2,109 → 2,087 | 6.14 → 6.05 | 9,322 → 9,322 |
| 64 counters interleaved with unused state | 383.90 → 386.98 | 4.40 → 4.18 | 3,138 → 3,138 |

Sparse scalar/wide cases improve about 7×/48×. Dense and small controls have
similar runtime and build cost; small differences are measurement noise, not
claimed improvements. The sparse examples deliberately contain unconsumed state;
application gains depend on how much state actually needs frozen reads. Idle
processes still read their counter state and therefore retain those snapshots.
Dirty-signal journaling is a possible later investigation, with extra writes and
snapshot-lifetime complexity to assess before implementation.

The fragmented control initially ran at 504 ns/cycle versus 392 for full
copies. The per-run overhead budget retains full copies in this case and
removes that regression. The first five rows use identical emitted copy plans
before and after this adjustment. Use `--case fragmented` to isolate this
control; it is also included in `--case snapshots`.

The final **19 snapshot tests passed**, including scan/queue and all-signal versus
selective comparisons against all four engine modes, >64-bit values and X masks,
batch reset events versus stepped execution, unknown-helper fallback,
streaming/hash identity, cache-option separation, and bounded sparse code size.
Broad compiled and cross-engine regression validation: **1,385 passed, 3,843
skipped** in 23m02s. This includes the compiled subsystem, startup/edge tests,
batch propagation, wide-signal catchall, and narrow-to-wide writes. Final focused
checks after the fragmentation adjustment also exercise table execution with
8/128-bit state and the full-copy fallback. Ruff lint/format, mypy (170 sources),
whitespace, 142 codemap anchors/local links, and the working-tree source inventory
passed. Full-root pytest and native Windows execution were not run.
