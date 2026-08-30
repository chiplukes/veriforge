# Roadmap

Known future work items, organized by area.

> **Execution order lives in
> [notes/plans/work_plan_2026-07.md](plans/work_plan_2026-07.md)** — the
> concrete, ordered plan (quick wins → structural projects) synthesized from
> the July 2026 architecture and functionality reviews. Work items from that
> plan supersede overlapping entries below; this file remains the per-area
> backlog for items not yet scheduled.

## Hierarchy refactor tool

Items in `src/veriforge/refactor/`. Safety invariants and test requirements
for new refactor work are documented in `notes/developer_guide.md`.

### Extract-module edge cases

1. **Parameterized output port widths** — output ports whose declared width is
   parameterized in the extracted child are currently fail-closed. Requires
   threading the parameter environment through the output-side boundary signal
   inference.
2. **Downstream hierarchical reference detection** — the engine detects
   hierarchical references *inside* selected logic and rejects them, but does
   not detect references *from sibling modules into* the subtree being moved.
   Those would silently break after extraction. Add a scan and surface
   `unsupported-downstream-hierarchical-reference` as a blocking diagnostic.
3. **Bit-slice partitioning** — multiple selected procedural drivers writing
   different bits of the same signal are blocked with
   `multiple-selected-procedural-drivers`. Correct fix: bit-aware driver-shape
   model and slice-output rewrite machinery.
4. **Memory output lifting** — selections that drive a memory
   (`reg [W-1:0] mem [0:N-1]`) fail-closed. Requires net/var promotion logic
   that understands array element drivers.
5. **Generate block selections** — selections that contain or cross generate
   blocks are deferred. Add generate-aware boundary detection once the graph
   and source-range machinery is reliable for non-generated code.
6. **SV procedural keyword preservation** — extracted `always` blocks always
   emit as plain `always`; the model has no field for `always_ff` / `always_comb`
   / `always_latch`. Low priority until SV keyword preservation is addressed
   more broadly.

### Hierarchy boundary movement

1. **Unified core API for push-down** — range push-down routes through the
   extract engine; module/instance/subtree push-down routes through the push-down
   engine. Factor a common core for shared boundary validation, collision
   detection, and review payload construction. The design-wide **pull-up**
   side of this same problem (`refactor/_pull_up_engine.py`'s
   procedural/assigns/structural triplication) is now solved via a
   `_PullUpKindStrategy` `Protocol` (one small dataclass per selection kind)
   plus four shared pipeline functions — see that file for the pattern this
   item could reuse for push-down's extract/push-down engine split.
2. **Cross-tree moves (sibling parents)** — pull-up currently requires the
   target to be a strict ancestor. Moving logic to a sibling parent needs a
   copy-and-rewire strategy with cross-file awareness.
3. **Pull-up of file/module-scope selections** — selecting a top-level module
   definition with no parent context blocks with a diagnostic. Design an entry
   path that treats the file as the parent.
4. **Relaxed intermediate-erasable restriction** — multi-level same-tree
   pull-up requires every intermediate wrapper to be fully erasable. Consider
   allowing intermediate localparams not referenced by downstream parameter
   overrides, with a `non-empty-intermediate-wrapper` info diagnostic.
5. **Cross-file moves** — all edits today stay within one parent file.
   Pull-up/push-down across files needs file-creation/deletion semantics in the
   edit plan and a multi-file blast-radius review surface in the editor.

### Wrapper classification rewrite

Classification recognizes five classes but only `pure_pass_through` is
rewriteable:

1. **Structural wrappers** — safe rewrite for wrappers containing only
   instances, nets, continuous assigns, parameters, and simple generate blocks.
2. **Adapter wrappers** — define explicit transform rules (invert, slice, concat)
   and expose a preview for the curated subset.
3. **Parameterized wrappers** — compose parent/child parameter maps during
   collapse so parameterized pass-through and structural wrappers can be
   collapsed without losing parameter intent.
4. **Generate/interface wrappers** — visualize-only until generate and
   interface/modport handling is reliable across the rest of the stack.

### Source-preserving generate-case edits

Top-level child and parent sites already use localized `SourceLocation`-based
edits. Remaining fallback cases still re-emit the full module:

- **Phase 2**: generate-contained child selections — build localized
  generate-aware child removals that remove selected nodes within their generate
  branch, patch only affected child port declarations, and preserve untouched
  branches verbatim.
- **Phase 3**: generate-nested parent sites — build localized generate-branch
  insertion that rewrites only the affected instance site within its branch and
  inserts lifted logic in the same branch, not hoisted to module top.
- **Phase 4**: add review-focused regressions asserting that generate-contained
  selections produce localized child diffs, generate-nested sites produce
  localized parent diffs, and explicit `generate/endgenerate` wrappers are
  preserved.

### Open design questions

1. Should collapse apply preserve wrapper modules for reuse elsewhere, or
   remove now-unreferenced wrapper module definitions when safe?
2. Should extracted modules be written into the same file by default, or into
   a new `*_extracted.v` file?
3. How should user annotations be represented for "do not collapse" or "always
   treat as a wrapper"?
4. How should `previewId` values be stored and invalidated across buffer edits,
   reparses, and top-module changes?
5. For cross-file pull-up/push-down, what is the canonical representation of
   file creation/deletion in the edit plan?

## Simulation

- **Wide-signal pre-edge snapshot gap in the compiled engine** — **Fixed and
  fully resolved** (five layers, all landed and regression-clean: full suite
  8394 passed / 0 failed both after layer 3 and again after layer 5). Turned
  out to be five separate-but-related gaps, each surfaced by peeling back
  the previous fix against the real
  `axis_pix_correction2` repro (`axis_regslice.v`'s skid buffer, reached via
  a wide `{tuser,tlast,tdata}` port, driving a mocked `xpm_fifo_sync`):
  1. *Wide signals*: `_sig_extract_word_val_sv`/`_sig_extract_word_mask_sv`
     (`sim/compiled/templates/narrow_accessors.pxi`) bypassed the snapshot
     entirely for any signal with `c.wide_words[sid] > 0`, always reading
     the live value. Fixed with real `wide_snap_val`/`wide_snap_mask`
     `SimCtx` fields, populated everywhere `sv`/`sm` already are
     (`snapshot()`, `refresh_data_snapshot()`, `batch_run()`'s three
     snapshot points).
  2. *Memories*: a 2-D packed array used for per-element addressing (e.g. an
     AXI-Stream `tdata` bus modeled per-lane) is elaborated as a *memory*,
     not a signal, and memory reads (`c.mem_{mid}_val[addr]`,
     `_wmem{mid}_extract_val(c, ...)`) had no pre-edge concept at all,
     for any process kind. Fixed with per-memory `mem_{mid}_snap_val`/
     `wide_mem_{mid}_snap_val` fields plus `_wmem{mid}_extract_val_snap`/
     `_extract_mask_snap` accessors, and a coarse (per-mid, not
     per-address — a dynamic address expression makes per-element taint
     undecidable at codegen time) taint rule in `_seq_body_to_sv_reads`.
  3. *Shared, call-site-blind memory-write helpers*: `mem[addr] <= a_signal;`
     (a whole-element NBA write sourced from a plain signal, elem width
     > 64 bits — the actual `xpm_fifo_sync` mock's `mem[wr_addr] <= din;`)
     compiles to `_wmem{mid}_stage_insert_signal_slice`, a *shared* function
     (one definition, called from cont/combo/seq alike) whose body read the
     signal source live with no way to know its caller's context. Fixing
     this one couldn't reuse the text-substitution trick above (the
     surrounding call arguments can be arbitrary expressions, not safely
     regex-skippable) — instead added a real per-body blocking-write
     pre-scan (`_collect_blocking_write_sids`, run once before compiling a
     seq body's statements via a new `_compile_always_body(..., is_seq=True)`
     path) so the statement emitter can choose a `_sv`-suffixed twin
     (`_wmem{mid}_stage_insert_signal_slice_sv`) at *generation* time
     whenever the signal source is provably not a local blocking-written
     temp in that same body.
  4. *Whole-signal-to-signal wide NBA copies*: `m_axis_tdata <= s_axis_tdata;`
     (both plain wide signals, same width, no bit-range/concat at all —
     `axis_regslice.v`'s own skid-buffer registers) never went through
     `_sig_extract_word_val`/`_wmem{mid}_extract_val` in the first place, so
     none of fixes 1-3 touched it: `_emit_wide_signal_copy_lines`
     (`_wide_emitter.py`) routes straight to `_whole_stage_signal`/
     `_whole_stage_signal_s`, another shared, call-site-blind helper (same
     class of gap as item 3, different call shape) that reads
     `c.wide_val`/`c.val[src_sid]` live. Fixed the same way as item 3: new
     `_whole_stage_signal_sv`/`_whole_stage_signal_s_sv` twins
     (`templates/narrow_assign.pxi`) reading `wide_snap_val`/`sv[]`, selected
     by `_emit_wide_signal_copy_lines` via the same `_body_tainted_sids`
     check. Confirmed via direct C-level instrumentation
     (`debug_wide_snap`/`debug_wide_live`, temporary `cpdef` methods) that
     the source's snapshot was byte-for-byte correct at the moment of the
     copy — this fix is real and necessary, but turned out not to be
     sufficient by itself to explain the observed failure; see item 5.
  5. *`batch_run()`'s first-call clock-state assumption* — the actual root
     cause of the remaining failure (`lowered_batch_run_test.py`, the
     `events=`-scheduled 2-row/288-beat repro, and a raw-poke-per-cycle
     `batch_run(1)` loop both hung identically; none of fixes 1-4 alone
     resolved it). `batch_run()`'s C loop unconditionally treats its very
     first iteration as starting from clock-low: it drives `clk_sid` high
     ("posedge"), which is a no-op if the clock was already 1 entering the
     call (as it commonly is after prior reactive `bench.step()`-based
     driving, e.g. `init_bench()`'s warm-up cycles), silently dropping the
     caller's first requested cycle and shifting every subsequent edge by
     one — confirmed by direct C-level instrumentation: `clk=1'b1` on
     entry, and the design's `always_ff` block provably never fired at all
     during the first `batch_run(1)` call, only starting from the second.
     Fixed by checking `self.ctx.val[clk_sid] != 0` once at the top of
     `batch_run()`'s `nogil` block and, if so, forcing one real negedge
     (settle + snapshot + drive low + `delta_loop`) before the caller's own
     posedge/negedge loop begins, so its first posedge is always a genuine
     0→1 transition. This was the fix that actually closed the gap; 1-4
     were real, necessary, and independently regression-verified, but none
     of them were what was blocking the `axis_pix_correction2` repro.
  Verified against the real design: both the reactive-input +
  `batch_run()`-tail pattern (`test_lowered_source_with_batch_run`, and a
  from-scratch 3-row reactive-drive variant) *and* the original
  `events=`-scheduled multi-row `batch_run()` repro now produce fully
  correct output. Full regression suite re-run after fix 5: 8394 passed / 0
  failed, identical to the pre-fix baseline — no regressions.
  **Audit of the remaining call-site-blind helpers flagged above — done,
  two more confirmed and fixed**:
  6. `_whole_stage_insert_signal`/`_whole_stage_insert_signal_slice`
     (`templates/narrow_assign.pxi`) — same live-read bug as items 3/4,
     confirmed reachable with `is_nba=True` from four call sites in
     `_stmt_emitters.py` (struct-field-from-signal, struct-field-from-slice,
     and both the dynamic- and constant-bounds range-select-LHS-from-slice
     paths — i.e. `sig[msb:lsb] <= other_sig[range];]` and matching
     struct-field shapes). Fixed with `_whole_stage_insert_signal_sv`/
     `_whole_stage_insert_signal_slice_sv` twins and a shared
     `_whole_stage_call()` dispatch helper (`_stmt_emitters.py`) so all four
     sites pick the `_sv` variant consistently. Regression-verified: full
     suite 8394 passed / 0 failed (same as before this pair).
     **Likely fixed a second, independently-reported real-world bug as a
     side effect**: `cineform-fpga`'s `notes/veriforge_bugs_found.md` "Bug 3"
     (a signed range-select of a wide wire reading corrupted values,
     compiled engine only, only inside a large composed design,
     `bayer_encode_core`, ~190K generated Cython lines) — shape matches this
     fix exactly (a registered capture of a wide range-select). Re-ran all
     four of that report's own test cases against the real design after
     this fix: all four now produce a bit-exact match against pycineform's
     golden output on the compiled engine (previously required
     `engine="reference"` to avoid the corruption). The report's own
     `batch_run()` is never used in that test, ruling out item 5 as the
     explanation; this fix (or, less likely, one of 1-4) is the most
     plausible cause. Not bisected further to confirm which exact layer
     gets credit — the empirical result (4/4 real-design golden matches)
     is the evidence, not a traced root cause specific to that report.
  7. `_whole_stage_repeat_signal_slice`/`_whole_assign_repeat_signal_slice`
     (`{N{sig[range]}}` replication into a wide destination) — same
     live-read pattern, but confirmed **dead code**: no call site anywhere
     in the codegen (`_stmt_emitters.py`, `_process_compiler.py`,
     `_wide_emitter.py`) ever emits a call to it. Left unfixed (no way to
     exercise or verify a fix with zero callers) but flagged here — if a
     future codegen path starts emitting this shape, apply the same
     `_sv`-twin + `_body_tainted_sids` dispatch pattern before wiring it up.
  8. `_whole_assign_mem_elem_{mid}` (`mem[idx]` read into a plain signal) —
     confirmed **safe, no fix needed**: its only call site
     (`_process_compiler.py`) always appends to `self._processes` (cont_N),
     never reachable with `is_nba=True`; live reads are correct there by
     construction. No `_whole_stage_mem_elem_{mid}` (NBA) variant exists.
- **Unsized decimal literal treated as unsigned in arithmetic (all
  engines)** — **Fixed.** Found via `cineform-fpga`'s own bug report
  (`veriforge_bugs_found.md`, "Bug 1"). An unsized, unbased decimal number
  (`5`, not `8'd5`) is a *signed* integer per IEEE 1800-2017 SS5.7.1, but
  both `_build_decimal_number` (`transforms/_expressions.py`, the real
  Verilog parser path) and the DSL's `_to_expr_node`/`_to_lit`
  (`dsl/builder.py`, for a bare Python `int` used in a DSL expression)
  constructed these as `signed=False`. Under Verilog's own binary-op
  type-promotion rule ("either operand unsigned -> whole expression
  unsigned"), `5 * a` for a signed wire `a` silently computed as
  *unsigned*, reinterpreting `a`'s raw bit pattern instead of sign-
  extending it — confirmed exactly: `a = -709` (bits `0xFD3B`, read
  unsigned as 64827) gave `5 * 64827 = 324135` instead of the correct
  `5 * -709 = -3545`. Fixed by passing `signed=True` at both literal-
  construction sites. Verified: the report's own repro plus a sweep over
  `{-32768, -1, 1, 32767, -709, 0}` now give correct results on all four
  engines (reference, compiled, vm, vm-fast). Regression-verified against
  `tests/test_dsl/`, `tests/test_verilog_parser/`, `tests/test_model/`,
  and `tests/test_sim/test_fill_literal_width.py` (no failures); folded
  into the same full-suite run as items 6/7/8 above.
- **Compiled-engine elaboration-cache collision for non-file-backed Designs**
  — **Fixed.** `_compute_elab_hash` (`sim/compiled/compiled_scheduler.py`)
  hashed `Design.source_files` path *strings* as a fallback whenever
  `Path(sf).read_bytes()` raised `OSError` (i.e. the path doesn't exist on
  disk) — always true for any programmatically-constructed `Design` not
  backed by a real file (the grammar-driven fuzzer's `tree_to_design(tree,
  source_file="fuzz.v")`, and likely other in-memory/DSL-built designs).
  Since the fallback hash depended only on the fixed placeholder path
  string plus `module.name` (always `"t"` for the fuzzer) — never on the
  module's actual content — every module generated in a single fuzz run
  produced the *same* elaboration hash, so the elaboration cache
  transparently returned the *first* module's stale compiled `.so` for
  every subsequent module regardless of what that module actually
  contained. Confirmed directly (two different module bodies, same name
  `"t"`, same placeholder `source_file`): the second module silently read
  back the first module's compiled results, with zero "cache miss"/compile
  log output at all. In a smoke fuzz run (`--max 200 --no-icarus --engines
  ... compiled`, seed 42) this produced 61/104 modules "mismatching" against
  the reference engine — a systemic false-positive-mismatch generator for
  any `engine="compiled"` fuzz run, not a real simulator bug; almost
  certainly the reason `_detect_engines()` in `fuzz/_runner.py` requires an
  explicit opt-in (`VERIFORGE_DIFF_COMPILED=1` or `--engines ... compiled`)
  rather than including `compiled` by default. Fixed by falling back to a
  hash of the module's own emitted Verilog text (`emit_module(module)`)
  instead of the bare path string whenever a listed source file can't be
  read from disk — verified the two-different-bodies-same-name repro above
  now elaborates and compiles correctly for both, matching the reference
  engine.
- **`StreamingConcatenation` unsupported by `emit_expression`** — **Fixed.**
  `codegen/verilog_emitter.py::emit_expression` had no `isinstance` case for
  `StreamingConcatenation` at all (added in `69849a6`, wired into every
  simulation engine and the parser, but never into the text emitter) —
  silently fell through to `"/* unknown expression */"`. Found while adding
  streaming-concat generation to the fuzzer (any `{<<{...}}` node anywhere
  in a `Design` — including inside a module port connection, per this
  session's fuzzing-round plan — would corrupt on re-emission). Fixed by
  adding a case mirroring `Concatenation`/`Replication`, emitting `{<<` +
  optional `slice_size` + `{parts}}` (this node only ever represents `<<`;
  `>>` desugars to plain `Concatenation` at AST-build time, per the node's
  own docstring, so no `>>` case is needed here).
- **Icarus Verilog has no `{<<{...}}` support at all** — confirmed directly
  (`iverilog -g2012`: `"sorry: Streaming concatenation not supported"`).
  Not a veriforge bug, but the fuzzer's Icarus cross-check
  (`fuzz/_runner.py::_run_one`) now explicitly skips Icarus for any module
  containing a genuine `StreamingConcatenation` node (`mod.find(...)`) to
  avoid flooding `fuzz_output/` with false-positive "icarus failed"
  mismatches; cross-engine comparison (reference vs vm/vm-fast/compiled)
  is unaffected and still runs for these modules.
- **Native timing support in compiled engine** — `#delay` / `@(posedge)` inside
  `initial` / `always` blocks currently fall back to reference coroutines (slow
  path, with a `warnings.warn` diagnostic per falling-back process). A native
  compiled path would keep timing in the Cython scheduler.
- **Contract enforcement debug mode** (item 17 step 5) — the
  tick_pre/sample_pre/tick_post rules are documented but unenforced. Add a
  strict mode where the endpoint receives a guarded sim facade: raises on drives
  from `sample_pre`, warns on live signal reads from `tick_post` (post-NBA
  hazard), so contract violations surface at the call site instead of as data
  corruption.

## Endpoint detection

- **Near-miss reporting** (item 14) — **Done.** `endpoints/detect.py` now provides
  `detect_near_misses()` / `detect_relaxed_interfaces()`, and the bench planner
  surfaces `near-miss: …` explanations in plan warnings. Remaining follow-up:
  audit which optional signals should be relaxable by default (`tlast`-less AXIS
  is legal per ARM spec for unframed streams).

## LSP

- **Resilience without Verible** (item 15) — **Done.** `workspace.py` falls back
  to a debounced Lark parse of the open buffer for syntax diagnostics when
  Verible is absent, and the README documents the Verible dependency.

## Codebase health

See [notes/plans/architecture_review_2026-07.md](plans/architecture_review_2026-07.md)
for the July 2026 architecture review plan (semantic-core unification,
cross-engine conformance testing, CI sim coverage, cycle removal).

- **Move static Cython helpers to `.pxi` templates** (item 1) — **Done.** The
  static Cython source now lives in `sim/compiled/templates/*.pxi` and the
  `_gen_narrow_*.py` modules are thin file reads.
- **Decompose remaining oversized functions** (item 4 partial) — largest
  remaining (July 2026 measurement):
  - `sim/compiled/_gen_wide_section.py:_gen_wide_primitives` (~847 lines)
  - `sim/vm/interpreter.py:execute` (~773 lines — interpreter dispatch; may be
    acceptable as-is)
  - `sim/compiled/_stmt_emitters.py:_emit_concat_lhs` (~657 lines)
  - `sim/compiled/_process_compiler.py:_compile_concat_cont_assign` (~533 lines)
    — per-lane emission helper seam

## PULP / common_cells examples

- Continue importing `pulp-platform/common_cells` modules (FIFOs, CDCs, arbiters,
  etc.) as regression targets.
- Extract flat-wrapper generation pattern into a shared helper for designs with
  packed struct ports.
- Revisit example-local runners and colocated test files (currently all examples
  are tested from `tests/test_dsl/`).

## DSL builder

Known gaps in `notes/dsl/dsl_coverage.md` (medium priority):

- **Sized/based literals** — `8'hFF` style; DSL uses Python ints, which works but
  loses formatting intent.
- **Fork/join** — Parallel testbench processes (`fork ... join`).
- **Specify blocks** — ASIC timing annotations (`specify ... endspecify`); out of
  scope for RTL work but needed for timing-sign-off flows.
- **Intra-assignment timing controls** — `q <= #5 d` non-blocking with inline
  delay; distinct from standalone `m.delay()`.

Note: `generate for`/`generate if`, `function`, and `task` declarations are
intentionally absent from the DSL — Python's own `for`/`if` and functions serve
those roles at elaboration time. See `notes/dsl/dsl_coverage.md` Gap Analysis.

### Imperative-to-declarative migration tool

Requested July 2026: a good amount of existing DSL code (library
components, examples, project-specific modules) still uses the imperative
builder (`clk = m.input("clk")`) from before `ModuleSpec` existed
(`src/veriforge/dsl/spec.py`), and hand-converting each file to the
declarative style is tedious and error-prone at scale.

Proposed scope: an AST-based (Python `ast` module, not regex/string
rewriting — see `veriforge.convert.to_dsl` for the existing precedent of
walking a design and emitting DSL source) tool that rewrites one Python
file at a time:

- Detect the *mechanically convertible* shape: a `with Module("name") as
  m:` (or `m = Module("name")`) block whose ports/wires/regs/params are
  declared with simple `x = m.input("x", ...)`-style calls (variable name
  equals the string name argument) with no loop, no computed name, no
  `m.interface()`/`m.instance()` expansion feeding the port count.
- Rewrite that shape into a `ModuleSpec` subclass: one descriptor per
  declaration (`In`/`Out`/`OutReg`/`Inout`/`Wire`/`Reg`/`Param`, per the
  mapping table in `dsl_guide.md`), module name becomes the class name
  (with `module_name = "..."` only if it doesn't match), and the remaining
  behavioral code (assignments, `always`/`initial` blocks, instances,
  interfaces) becomes `body(self, m)` with `m.<name>` renamed to
  `self.<name>` for every converted declaration.
- Leave anything else (loops generating a variable signal count, computed
  names, `m.interface()` expansion, mismatched variable/string names)
  untouched in place — this is inherently the imperative builder's
  territory (see [dsl_guide.md](dsl/dsl_guide.md#the-imperative-builder))
  and should not be force-converted. Print a report of what was converted
  vs. left alone (with the reason) rather than silently skipping.
- Round-trip check: re-emit both the original and converted module and
  diff the Verilog output (or run both through the reference engine on the
  same stimulus) to catch a bad rewrite before it lands.
- Likely entry point: `veriforge convert-dsl-style <file>` alongside the
  existing `design_to_dsl`/`export_dsl_project` conversion commands (which
  go the other direction, Verilog → DSL); this tool goes DSL → DSL.

Not scoped yet: whether to attempt any partial conversion of loop-shaped
code (e.g. converting the *fixed* ports of a module that also has a
dynamically-sized internal array) — likely worth doing once the simple
case is solid, per the "mixing both styles in one module" pattern in
`dsl_guide.md`.

## Test infrastructure

Grammar-driven fuzzer ([notes/fuzzer.md](fuzzer.md)) — implemented.  Generates
arbitrary Verilog modules from the parse grammar, cross-checks all engines +
Icarus, logs mismatches to disk.  Runs as a standalone CLI tool
(`uv run -m veriforge.fuzz`).

Proposed markers from [notes/test_taxonomy.md](test_taxonomy.md) not yet applied:

- `cross_engine` — tests that parametrize behavior across engines
- `compiled` — tests that require the compiled simulator

## Parser / SystemVerilog coverage

The constructs marked **Partial**, **Limited**, or **Planned** in
[notes/support_matrix.md](support_matrix.md) represent the known parser/simulation
coverage frontier.
