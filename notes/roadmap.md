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
- **Streaming-concat slice_size > 64 rejected only by vm/compiled, not
  reference/vm-fast** — `_streaming_concat` in the fuzzer's expression
  generator (added alongside the streaming-concat fuzzing round above) hit
  this directly: a declared parameter used as slice_size can carry any
  value (params are generated with the same edge-case-biased widths as
  everything else), and `sim/vm/compiler.py` / `sim/compiled/_wide_emitter.py`
  both explicitly raise `NotImplementedError` for `slice_size > 64` while
  `reference`/`vm-fast` apparently accept it. Worked around in the
  generator itself (only offers a parameter as slice_size when its constant
  value already falls in `1..64`) rather than fixed at the engine level —
  needs a decision: either lift the 64-bit limitation in vm/compiled to
  match reference/vm-fast, or make reference/vm-fast raise the same error
  for consistency, so all four engines agree on what's legal.
- **`StreamingConcatenation` operands invisible to sensitivity/dependency
  tracking on `reference`/`vm`/`vm-fast` — Fixed.** This was the fuzzing
  round's headline finding, and turned out NOT to be a `compiled`-engine
  bug at all (the opposite of the original suspicion below, kept for the
  investigation trail): a signal referenced ONLY inside a
  `{<<{...}}`/`{<<N{...}}` streaming concatenation was invisible to
  `sim/scheduler.py`'s `_walk_expr_reads` and `sim/vm/compiler.py`'s
  `_walk_expr_signals` -- both walkers have a case for every other
  expression node (`Concatenation`, `Replication`, ...) but none for
  `StreamingConcatenation`, so a continuous assign or `always @(*)` block
  driven solely by such a signal was registered with a sensitivity set
  that never included it, and so never got scheduled to re-run after
  elaboration -- leaving the output permanently stuck at its
  elaboration-time `x`, REGARDLESS of what the streaming concatenation's
  operands were subsequently driven to. `StreamingConcatenation` itself
  evaluates completely correctly in isolation (`Value.stream_reverse` is
  fine) -- the bug is purely in whether the containing process ever runs
  again. `compiled` was unaffected (collects signal references via a
  generic reflective walk, not hand-maintained per-node dispatch), so
  every prior mismatch attributed "to compiled" in this bug's earlier
  write-up was actually reference/vm/vm-fast failing to update at all,
  with `compiled` alone producing the correct answer. This is the EXACT
  same gap class already fixed once for `AssignmentPattern` in both of
  these same functions (see their own comments) -- `StreamingConcatenation`
  was added later (`69849a6`) and never got the same treatment.

  Confirmed with a minimal, no-parameter, no-hierarchy repro:
  `assign o = {<<{a}};` (or `always @(*) o = {<<{a}};`) with `a` driven to
  a concrete, fully-defined value AFTER the `Simulator` is constructed --
  `reference`/`vm`/`vm-fast` all returned all-`x` for `o` regardless of
  `a`'s value; `compiled` alone computed the correct bit-reversal. Only
  visible via `sim.settle()` (a continuous assign has no protection even
  on its first `settle()` call; a combinational `always @(*)` gets a
  one-time bootstrap pass on its FIRST `settle()` only, so needs two
  drive-then-settle cycles to expose) -- every PRE-EXISTING streaming-concat
  test used `sim.run(max_time=0)` instead, which always does a full,
  sensitivity-independent pass regardless of this bug (`settle()`'s own
  docstring: "`run()` already does the equivalent unconditionally on every
  call"), so none of them caught it despite covering the `<<` form
  directly.

  Fixed by adding a `StreamingConcatenation` case (walking `.parts` and
  `.slice_size`) to both walkers. Verified: all 8 of this bug's own new
  regression tests (`tests/test_sim/test_streaming_concatenation_reversal.py`,
  `TestStreamingConcatenationReversalSensitivity`) fail without the fix and
  pass with it; re-ran every one of the 42 flat (non-hierarchical) modules
  from the 300-module full cross-check run below against all four
  engines -- 30 previously-mismatching-or-worse cases (including the
  original hand-built repro, `mismatch_00090`, `mismatch_10082`,
  `mismatch_10203`, `mismatch_10262`, `mismatch_10174`, and the
  `Strategy.HIERARCHICAL` "concat out of a port" case) now agree across
  the board; full `tests/test_sim/` suite re-run clean after the fix.

  **Residual findings from the first re-verification pass, since resolved
  or reclassified** (kept here for the investigation trail):
  - `mismatch_10017`, `mismatch_10075`: on inspection, the ACTUAL
    mismatching signal in each had nothing to do with streaming concat at
    all (`o10 <= clk;` / `o11 <= $unsigned(clk);` -- a plain clock-to-reg
    copy) -- this turned out to be a real, separate, now-fixed bug (see
    "Clock/trigger signal read within its own triggered process body"
    below), not a streaming-concat issue. Confirms the earlier "~9/48 are
    compiled-engine-only, no streaming concat" bucket and this one were the
    same underlying bug family.
  - `mismatch_10055`, `mismatch_10024`, `mismatch_10096`, `mismatch_10104`,
    `mismatch_10151`, and 6 more of the original "~9/48 compiled-only"
    bucket: ALL resolved by the clock-read fix below except the four
    covered in its own "Residual" subsection immediately following this
    entry (`mismatch_10024`/`10055` -- vm-fast, root-caused, see below;
    `mismatch_10096`/`10104` -- compiled, not yet reduced).
- **Clock/trigger signal read within its own triggered process body gave
  its stale PRE-edge value, `compiled` only — Fixed.** Found while
  re-verifying the streaming-concat fix above against the full
  300-module cross-check's remaining mismatches -- turned out to explain
  the large majority of them, and had nothing to do with streaming concat
  at all. Minimal repro:
  ```verilog
  module t (input clk, output reg [2:0] o);
    always @(posedge clk) o <= clk;
  endmodule
  ```
  Drive `clk` low, settle, drive `clk` high, settle: `reference`/`vm`/
  `vm-fast` all correctly capture `o <= 1` (clk's own just-transitioned
  value); `compiled` gave `o <= 0` (the stale pre-edge value) instead.

  **Root cause**: `_seq_body_to_sv_reads` (`sim/compiled/_gen_sections.py`)
  rewrites ordinary signal reads inside a seq process body to read from
  `sv[]`/`sm[]` (a snapshot taken BEFORE the edge, giving other registers
  correct NBA-race-free pre-edge values) -- correct for every signal
  EXCEPT the process's own edge-trigger signal(s), whose value has, by
  definition, already genuinely transitioned to the new state by the time
  the triggered body runs (`sv[clk_sid]` is deliberately left at its
  pre-transition value so `step()`'s own separate edge-DETECTION logic can
  compare old-vs-new -- correct for THAT purpose, wrong for a body read).
  This exception already existed (`async_sids`/`seq_negedge_sids`) but was
  scoped to negedge sensitivity signals only (async reset inputs), never
  extended to the ordinary posedge clock case despite the identical
  mechanism applying equally to it. Fixed by broadening `seq_negedge_sids`
  to include every edge-trigger signal, posedge or negedge.

  Verified: 3 new regression tests
  (`tests/test_sim/compiled/test_scheduling.py`,
  `TestClockSignalReadWithinItsOwnTriggeredBody`, including a guard test
  confirming an ORDINARY non-trigger signal still correctly uses the
  pre-edge snapshot -- this fix must not turn into "never use sv[] at
  all") confirmed to fail without the fix and pass with it; re-verified
  the full 300-module cross-check's 42 flat modules again -- mismatching
  files dropped from 14 to 4.

  **Follow-up: all 4 residuals triaged and fixed** (see the three entries
  immediately below):
  - `mismatch_10024`, `mismatch_10055` (`vm-fast`) -- fixed, see
    "`vm`/`vm-fast` streaming concatenation exceeding fixed wide-value
    capacity" below.
  - `mismatch_10104` (`compiled`) -- fixed, see "`compiled` bitwise-op
    (`&`/`|`/`^`) mask computation ignored its own combined signedness"
    below.
  - `mismatch_10096` (`compiled`) -- fixed, see "`compiled` pre-edge
    snapshot built from only one pass through continuous assigns" below.
    Resisted single-piece reduction hard (every attempt to drop any one
    piece of the module made the divergence disappear, even with
    everything else fully restored) precisely because the actual bug is
    about DECLARATION ORDER among `assign` statements, not any one
    construct -- confirmed by the one reduction that finally worked:
    simply swapping the order of two `assign` statements (no content
    change at all) made the divergence vanish completely, which is what
    pointed at the real root cause below.
- **`compiled` pre-edge snapshot built from only one pass through
  continuous assigns — Fixed.** Root cause: `refresh_data_snapshot()` and
  each of `batch_run()`'s three snapshot points (`sim/compiled/
  _gen_sections.py`) ran every continuous-assign process (`cont_0()` ...
  `cont_N()`) exactly ONCE, in DECLARATION order, immediately before
  copying `ctx.val`/`ctx.mask` into the `sv`/`sm` snapshot a sequential
  process's NBA reads use. That's only correct when declaration order
  happens to already match dependency order. Minimal repro pattern:
  ```verilog
  assign o10 = ~o12;        // declared FIRST, but READS o12
  assign o12 = {...};       // declared SECOND, computes the NEW value
  always @(posedge clk) r7 <= o10;
  ```
  In one top-to-bottom pass, `o10`'s own process runs BEFORE `o12`'s, so
  `o10` computes from `o12`'s STALE (pre-this-settle) value; nothing
  re-runs `o10` afterward before the snapshot is taken. That stale `o10`
  then gets baked into `sv[]` itself, so `r7 <= o10;` captures the WRONG
  value at literally every single clock edge, forever -- not a one-off
  glitch. Confirmed as exactly a declaration-order issue (not a content
  issue) by simply swapping the two `assign` statements in the original
  fuzzer-found module, with zero other changes, which made the divergence
  disappear entirely.

  Fixed by settling continuous assigns to a genuine FIXED POINT before
  every snapshot point, not one blind pass: a new shared helper,
  `_cont_settle_fixpoint_lines`, repeats the full `cont_0()...cont_N()`
  sequence (bounded by the number of continuous-assign processes -- a safe
  convergence bound for any acyclic dependency graph among them, same
  bounded-iteration philosophy as `DELTA_LIMIT` elsewhere) with an
  early-exit convergence check after each pass, reusing the `conv_val`/
  `conv_mask`/`conv_wide_val`/`conv_wide_mask` scratch buffers
  `delta_loop`'s own oscillation-detection check already uses (deliberately
  NOT `dirty[]`, which `delta_loop`'s own triggering depends on seeing
  exactly as external drives/events left it -- reusing it here would have
  corrupted that). The common case (already-correct declaration order)
  costs exactly one extra full-array compare beyond what a single pass
  already needed; only a genuinely out-of-order chain pays for additional
  passes.

  Verified: new regression test
  (`tests/test_sim/compiled/test_scheduling.py`,
  `TestContinuousAssignSnapshotConvergence`) using the original
  fuzzer-found module and its exact reproducing stimulus, confirmed to
  fail without the fix (fresh, uncached compile) and pass with it.
- **`vm`/`vm-fast` streaming concatenation exceeding fixed wide-value
  capacity — Fixed (raised + guarded).** Root cause: `vm`/`vm-fast` share
  one compiled bytecode whose wide (>64-bit) values -- signals, constants,
  AND intermediate stack values alike -- are stored in a fixed-size word
  slot (`WIDE_WORDS` in `sim/vm/_interp_fast.pyx`, kept in sync with
  `_WIDE_WORDS` in `vm_scheduler.py` and `_VM_FAST_WIDE_WORDS` in
  `compiler.py`). A streaming concatenation's PRE-reversal combined width
  is the sum of all its parts -- unlike a plain `Concatenation`, which the
  compiler can narrow to a smaller destination width up front, chunk
  reversal needs the full stream materialized first, so that combined
  width can exceed the fixed capacity even when every individual operand
  and the final destination are comfortably narrow. Two real fuzzer-found
  modules landed at 397 and 385 combined bits, just over the old 384-bit
  (6-word) cap, and silently returned wrong (frequently all-zero) results
  on `vm-fast` only. Fixed two ways:
  1. Raised the cap a modest amount (6 -> 8 words, 384 -> 512 bits) --
     enough to correctly compute both fuzzer-found cases outright. A large
     increase was deliberately rejected: it's a fixed per-value allocation
     applied to every wide signal/constant/stack-slot in every vm-fast
     design, not just ones using streaming concatenation, so raising it
     is a blanket memory/performance cost paid by every vm-fast user.
  2. Added a compile-time guard (`sim/vm/compiler.py`, `StreamingConcatenation`
     case) that raises a clear `NotImplementedError` if the combined width
     still exceeds the (new) cap, matching the existing `slice_size > 64`
     guard, instead of silently corrupting.

  Verified: 4 new regression tests
  (`tests/test_sim/test_streaming_concatenation_reversal.py`,
  `TestStreamingConcatenationVmFastWideCapacity`) covering both the
  raised-cap correctness case and the still-over-the-new-cap clear-error
  case; both original fuzzer-found modules now compute matching, correct
  values on all four engines. Required rebuilding the Cython extension
  (`_interp_fast.c`/`.so` regenerated from the edited `.pyx`).
- **`compiled` bitwise-op (`&`/`|`/`^`/`~^`/`^~`) mask computation ignored
  its own combined signedness — Fixed.** Root cause: `_emit_binary`
  (`sim/compiled/_expr_emitter.py`, the VALUE-side emitter) has an explicit
  case computing a bitwise op's own combined signedness from its two
  operands (IEEE 1364-2005 SS5.5.2: "if any operand is unsigned, the
  result is unsigned") and passing that decision down into each operand's
  own compilation -- but `_emit_mask_expr` (the MASK-side emitter) had NO
  matching case for these five operators, silently falling through to a
  generic branch that just passed an OUTER `signed_override` through
  unchanged (or `None`) instead of computing this op's own decision.
  Minimal repro (`y` undriven, i.e. fully-x):
  ```verilog
  module t (input clk, input y, output reg [24:0] x);
    always @(posedge clk) x <= $signed(y) | 25'd0;
  endmodule
  ```
  Since `25'd0` is unsigned, IEEE's combining rule makes the WHOLE
  expression unsigned, so `$signed(y)` must be read as if it were
  `$unsigned(y)` here -- zero-extended, not sign-extended, to the OR's
  own working width. The VALUE side got this right (`x`'s value correctly
  reads back 0, fully defined in bits [24:1]). The MASK side, missing the
  same combining-rule case, defaulted to the cast's OWN literal decision
  ($signed always sign-extends) instead of the overriding unsigned
  context -- sign-extending `y`'s x sign bit into every one of those same
  upper bits, corrupting an almost-fully-defined 25-bit result into an
  entirely-ambiguous one. Fixed by adding the matching bitwise-op case to
  `_emit_mask_expr`, mirroring `_emit_binary`'s existing one exactly.

  This was NOT a self-reference/NBA-snapshot bug despite first appearing
  inside a self-referential seq body during reduction (`mismatch_10104`'s
  actual shape) -- confirmed general: reproduces identically for a plain
  combinational `assign` and for two entirely unrelated signals, with no
  self-reference needed at all.
- **Signed parameter value >= 2^(width-1) zero-extended instead of
  sign-extended when read into a wider signal, ALL FOUR engines — Fixed.**
  Found via the fuzzing round's own parameter generation (task #28)
  hitting a large enough edge-case value for the first time; NOT specific
  to streaming-concat/hierarchy. Minimal, fully reduced repro:
  ```verilog
  module a (output signed [67:0] o);
    parameter signed [63:0] p = 64'sd14019245667914476225;  // bit 63 set
    assign o = p;
  endmodule
  ```
  `reference`/`vm`/`vm-fast`/`compiled` all agreed with each other and were
  all wrong: the top 4 bits of `o` came back `0000` (zero-extension)
  instead of the correct `1111` (sign-extension, since bit 63 of `p`'s
  value is set -- it's a negative 64-bit number in two's complement).
  Confirmed **specific to parameters**: the exact same value used instead
  as a signed *input port* or as a bare signed *literal* in the same
  widening-assign shape sign-extended correctly on every engine already.

  **Root cause** (a parser gap, not the shared-runtime-value-path
  hypothesis first suspected): `_extract_parameters`
  (`transforms/_declarations.py`) only populated `Parameter.signed`/
  `.width` from a `parameter_type` grammar wrapper node -- but
  `parameter_declaration`'s own rule (`KW_PARAMETER KW_SIGNED? range?
  ...`) puts `KW_SIGNED`/`range` directly as this node's own children, with
  no such wrapper (that wrapper is a separate rule, `parameter_type:
  KW_INTEGER | KW_REAL | ...`, only for typed parameters). Every plain
  `parameter signed [msb:lsb] name = value;` (port-list or body style)
  silently got `signed=False`, `width=None` regardless of its own
  declaration -- confirmed directly: `p.signed` was `False` even for a
  parameter written explicitly `signed`. Fixed by scanning for
  `KW_SIGNED`/`range` directly on `parameter_declaration`'s own children
  too, in addition to the existing `parameter_type`-wrapper case.

  Verified: `tests/test_model/test_module.py` (parser → model attributes)
  and `tests/test_sim/test_param_width.py`
  (`TestSignedParameterSignExtension`, end-to-end across all four engines)
  regression tests added, confirmed to fail without the fix and pass with
  it; full `tests/test_verilog_parser/`, `tests/test_model/`,
  `tests/test_analysis/`, `tests/test_dsl/` (2515 passed) and
  `tests/test_sim/` (5289 passed) re-run clean after the fix.
- **Full cross-check run (task-tracking: fuzzing-round item 32) — 300
  modules, all four engines + Icarus, seed 10000**: 48/300 mismatching at
  the time, before either fix above landed (`compiled`: 39, `iverilog`: 7,
  `vm-fast`: 6, `vm`: 4; some mismatches hit more than one engine). Triaged
  by inspecting each `mismatch_NNNNN/module.v` + `mismatches.txt`; both of
  the bugs found through this triage are now fixed (see above) --
  remaining, not-yet-addressed categories:
  - **~9/48** are `compiled`-engine-only value/mask mismatches with no
    streaming concat present (`mismatch_10030`, `10104`, `10109`, `10184`,
    `10216`, `10271`, `10279`, `10280`, `10291`) -- plausibly more instances
    of this session's already-known compiled-engine wide/whole-signal bug
    family (see the wide-signal pre-edge-snapshot items earlier in this
    section), and/or the same clock-timing issue found via `mismatch_10017`/
    `10075` above, but none of these has been individually reduced/
    confirmed as such yet.
  - **A handful of small, distinct, NOT-yet-characterized leads**, each
    worth its own reduction pass in a future session:
    - `mismatch_10063`/`mismatch_10239`: off-by-one-in-the-low-bits
      `iverilog`/`compiled` divergences (values differ by exactly 1, upper
      bits agree) -- possibly a rounding/truncation edge case, unrelated to
      the sign-extension bug's upper-bits shape.
    - `mismatch_10053`/`mismatch_10268`: purely combinational (no clock)
      `reference`-vs-`iverilog` **mask** divergences (same value where both
      resolve it, disagreement only on which bits are X) -- a different
      flavor of X-propagation disagreement than the streaming-concat one,
      since neither module contains streaming concat.
    - `mismatch_10108`: `iverilog` `vvp` timeout ("likely unbounded
      simulation loop") on a clocked module with a very wide (125-bit
      parameter) division chain and no loops at all -- more likely an
      Icarus bignum-division performance artifact from the large parameter
      values task #28 now generates than a real infinite-loop bug; worth
      revisiting if it recurs (e.g. capping generated parameter magnitude,
      or a longer `vvp` timeout) rather than assuming it's a correctness bug.
- **`logic`-declared signal fuzzing + Verilator cross-check (task-tracking:
  fuzzing-round item 33) — Done.** Built out the deferral noted in
  `notes/fuzzer.md`: the fuzzer now generates `logic`-typed inputs/outputs/
  wires/regs (~35% independent chance per signal,
  `fuzz/_signal_context.py::Signal.use_logic`), and a new opt-in
  `--verilator` fuzzer flag cross-checks results against Verilator
  (`fuzz/_runner.py::_simulate_verilator`/`_compare_verilator`), needed
  because Icarus's own SystemVerilog support for `logic` is weaker than its
  Verilog-2005 core.

  **Prerequisite bug found and fixed**: `_extract_port_declaration`
  (`transforms/_declarations.py`) walked every child of an
  `input_declaration`/`output_declaration`/`inout_declaration` subtree but
  had no branch for the grammar's optional `net_type` child at all -- so
  `input logic clk` / `input wire foo` silently lost the keyword during
  parsing (`Port.net_type` stayed `None`) even though `Port.net_type` and
  the emitter's rendering of it already existed and worked correctly.
  Confirmed directly: parsing `input logic clk` gave a `Port` indistinguishable
  from a plain untyped `input clk`. Every one of our own 4 engines shares
  this same extraction path before elaboration, so `logic`-typed ports would
  have been silently untyped for all internal purposes, making port-level
  `logic` fuzzing meaningless. Fixed by adding the missing branch (mirrors
  the existing `_net_kind_from_tree` net-declaration extraction). Verified:
  `tests/test_sim/test_port_net_type_roundtrip.py` (new file) -- parse/
  round-trip/re-emit assertions plus a cross-engine simulation check for
  `logic`-typed ports.

  **Verilator integration**: `_simulate_verilator` reuses the *same*
  `_build_testbench` Icarus already uses (confirmed directly that the
  identical `$display`-based testbench runs correctly under Verilator's
  `--binary` mode, which compiles+links a self-contained executable in one
  step -- no hand-written C++ harness needed). `_compare_verilator` is
  value-only and skips any `(vector, signal)` pair where the *reference*
  engine itself shows ambiguity (`mask != 0`), since Verilator has no
  `x`/`z` state (confirmed directly: `4'bxxxx` into a `logic` net reads back
  as `0000`).

  **Verilator oracle limitation found (not a veriforge bug) — documented,
  worked around**: smoke-testing `--verilator` on the first 40 fuzzer seeds
  surfaced several apparent mismatches, all traced to one root cause:
  Verilator's `{<<n{...}}` streaming concatenation disagrees with the LRM
  (and with veriforge) whenever the combined operand width isn't an exact
  multiple of the slice size `n` (a "ragged" chunk) -- confirmed by
  hand-deriving IEEE 1800-2017 §11.4.14.1 independently of both simulators:
  `{<<3{8'b11010010}}` should give `10100110` (matches veriforge's
  `reference`/`vm`/`vm-fast`, and `Value.stream_reverse`'s own docstring
  citation of the same LRM section); Verilator gives `01001011` instead. The
  evenly-divisible case (`{<<4{...}}}` on the same operand) matches exactly
  in both, isolating the gap to the ragged case specifically. See
  `notes/known_issues.md` ("Verilator ragged streaming-concat chunking
  gap") for the full derivation. Worked around by extending the fuzzer's
  existing `has_streaming_concat` whole-module skip (already used for
  Icarus, which rejects the construct outright) to also cover the Verilator
  cross-check. Re-verified after the fix: 0 Verilator-specific mismatches
  across a ~110-module combined smoke/extended run (seeds 0-40 and
  1000-1070), the only 2 logged mismatches being the already-documented,
  pre-existing `vm`/`vm-fast` wide-value-capacity `NotImplementedError`
  guard (unrelated to this feature).

  Verified: full `tests/test_sim/` suite (5316 passed, 0 failed) after all
  changes; `mypy` clean on all touched files.
- **`vm`/`vm-fast`: whole-array continuous-assign read of a memory written
  element-wise by generate-loop child instances read permanently X — Fixed.**
  Picked up from
  [[project_axis_pix_correction2_compiled_vm_bugs_2026_08]]'s open item
  ("vm-fast... every pixel reading exactly 0" on the full-scale production
  design) -- root-caused this session by building a fast (~seconds, not the
  ~30-minute full-design elaboration) reduced repro using the exact,
  unmodified `axis_col_correct.sv`/`axis_col_correct_channel.sv` real RTL
  source, driven through `Testbench`/`AXIStreamProxy` (manual always-high
  `tvalid` stimulus never exercised the failing transition, since it
  requires the valid signal to genuinely PULSE and then dereassert).
  Reduced further to an 18-line, fully synthetic, engine-independent
  minimal repro:
  ```verilog
  module leaf (input clk, input [17:0] d, input dv, output [17:0] q);
      logic s1=0; logic [17:0] d1=0;
      logic s2=0; logic [17:0] d2=0;
      always_ff @(posedge clk) begin s1 <= dv; d1 <= d; end
      always_ff @(posedge clk) begin s2 <= s1; d2 <= d1; end
      assign q = d2;
  endmodule

  module top #(parameter N = 4) (input clk, input [N-1:0][17:0] d, input dv, output [N-1:0][17:0] q);
      logic [N-1:0][17:0] gen_q;
      genvar i;
      generate
          for (i = 0; i < N; i++) begin : gen_ch
              leaf u_leaf (.clk(clk), .d(d[i]), .dv(dv), .q(gen_q[i]));
          end
      endgenerate
      assign q = gen_q;
  endmodule
  ```
  Driving `dv` for 2 cycles then deasserting it: `reference` correctly
  shows `q` tracking `d` with a 2-cycle pipeline delay
  (`0x0 0x1234 0x1234 0x1234 ...`); `vm` and `vm-fast` (which share this
  compiler's bytecode) both show `q` as **fully X on every single cycle,
  including the very first**, never once reflecting a real value.

  **Root cause, three distinct bugs, all in
  `sim/vm/vm_scheduler.py::VMScheduler.drive_signal`**:
  1. Its memory-array write branches (both the indexed-element `mem[i] =
     ...` form and the whole-array `mem = ...` form) correctly wrote the
     new value into `mem_val`/`mem_mask` and marked `interpreter.dirty`,
     but never added the memory's dirty "marker" signal to
     `_pending_drives`. `settle()` seeds its delta loop from
     `_pending_drives`, not `interpreter.dirty` (that set only matters
     *mid*-delta-loop, once a loop is already running) -- so `settle()`
     saw an empty `_pending_drives` and returned immediately, silently
     no-op'ing the entire drive for any process reading the memory.
     Explains `q` reading X from cycle 0: the drive into `d` (also a
     memory, `input [N-1:0][17:0] d`) never propagated into the child
     instances at all.
  2. Fixing (1) surfaced a second bug on the Cython (`vm-fast`) path
     specifically: driving *any* memory-backed signal called
     `_cy_ctx.sync_mem_from_lists(self.compiler.mem_val, self.compiler.
     mem_mask)` -- copying ALL memories' cells from the compiler's own
     Python-side lists. Those lists are populated once at elaboration
     time and never kept in sync with whatever `_cy_ctx` has since
     computed at runtime for *other* memories via its own delta-cycle
     execution -- so this blanket sync clobbered every other,
     already-correctly-computed memory-backed signal back to its stale
     elaboration-time value on every single drive. Confirmed directly:
     driving `d` correctly propagated into `gen_q`/`q` (per-element reads
     matched), but the very next drive of `d` reset `gen_q`/`q` straight
     back to X. Fixed by using the existing `_cy_ctx.write_mem(mid, idx,
     val, mask)` (a targeted single-element write, mirroring the
     already-correct single-signal `write_signal` pattern used for plain
     scalar drives) for just the elements actually being driven, instead
     of the blanket `sync_mem_from_lists` call.
  3. Fixing (1) introduced its own regression, caught by the existing
     `test_whole_array_nba_copy_over_64_elements[vm]` full-suite run: the
     "snapshot pre-drive signal state for `settle()`'s edge detection"
     logic (`_prev_sig_val`/`_prev_sig_mask`, needed so a later
     `always_ff @(posedge clk)` can tell old-vs-new) only ever lived
     inside the plain-signal branch, gated on `if not self._pending_
     drives:` ("first drive in this batch"). Once memory drives started
     adding their marker to `_pending_drives` (fix 1), a batch whose
     *first* drive happened to be a memory element (e.g. writing 100
     `mem_in[i]` elements before any plain-signal drive) made
     `_pending_drives` non-empty before a later plain-signal drive
     (`clk`) ever got a chance to see it empty -- so the snapshot never
     ran at all, and `settle()`'s edge detection later indexed into a
     stale/undersized `_prev_sig_val` (`IndexError: list index out of
     range` in `_edge_fired`). Fixed by extracting the snapshot logic
     into `_snapshot_before_first_drive()` and calling it from all three
     `drive_signal` branches (plain signal, indexed memory element,
     whole-array memory), not just the first.

  **Why this mattered**: this was the actual explanation for the original
  "vm-fast returns a complete frame with every pixel reading exactly 0"
  symptom -- `axis_pix_correction2`'s row/column-correction and LUT stages
  all use exactly this "generate-loop-of-per-channel-instances feeding a
  whole-array continuous assign" shape, fed from a memory-shaped top-level
  input port. Confirmed the standalone leaf module
  (`axis_col_correct_channel` alone, no wrapper) behaved correctly on both
  engines even before the fix; only the wrapper's generate-loop + a driven
  memory-shaped input triggered it -- any design driving a 2-D-packed/
  unpacked-array-shaped signal from outside (a top-level input port, or
  `sim.drive("mem[i]", ...)`/`sim.drive("mem", ...)` directly) under `vm`
  or `vm-fast` was affected, not just this specific RTL shape.

  Verified: all 4 engines (`reference`/`vm`/`vm-fast`/`compiled`) now agree
  exactly on the minimal repro across every cycle. New regression test
  `tests/test_sim/test_2d_array_through_child_instance.py::
  TestWholeArrayReadOfGenerateLoopWrittenMemory` (no longer needs the
  `xfail` marks the initial root-cause commit added -- both bugs fixed).
  `mypy` clean; full `tests/test_sim/` suite re-run after the fix.
- **Long confidence-building fuzzing round (task-tracking: fuzzing-round
  item 34) — 2-hour run, seed 5000, 3025 modules, all engines +
  `--verilator`**. One genuine fuzzer bug found and fixed; two genuine
  simulator-divergence classes found, root-caused, and documented (not
  yet fixed — see `notes/known_issues.md`); the rest were either the
  already-known `vm`/`vm-fast` wide-value-capacity guard or the
  already-documented Icarus first-activation artifact (1172 auto-filtered).
  - **Fixed: `HIERARCHICAL` strategy could generate a self-referential
    parent output (`assign o9 = o9;` or `assign o9 = {w7, o9};`), which
    Verilator (correctly) rejects as unresolvable circular combinational
    logic** ("Wire inputs its own output") -- a fuzzer generation bug, not
    a simulator bug, but one that flooded the Verilator cross-check with
    false-positive "mismatches" that were really just Verilator refusing
    to compile a degenerate module. Root cause: `_module_gen.py::
    _gen_hierarchical`'s "parent output woven from instance wires" step
    calls `ctx.add_output()` for the new output *before* building its RHS
    expression, then builds that RHS via `expr_gen.expr(...)`/
    `expr_gen.leaf(...)` **without** the `exclude=` parameter every other
    strategy passes to `pick_readable()` to prevent exactly this
    (`pick_readable`'s own docstring: "a same-statement self-reference...
    forms a combinational loop with simulator-implementation-defined
    behavior") -- so the freshly-added output was eligible to pick
    itself as an operand of its own definition. Fixed by passing
    `exclude=out.name` at both call sites (the plain-expression branch and
    the single-instance-output-wire-needs-a-second-leaf branch). Verified:
    a targeted sweep of 3000 fresh `HIERARCHICAL`-strategy modules (seeds
    3000-5999) found zero self-referential assigns after the fix, versus
    a nonzero rate before it.
  - **Root-caused, not fixed: `vm`/`vm-fast` — a combinational `always
    @(*)` block containing a `for`/`while` loop, wrapped one level inside
    a child instance, does not re-fire on a SECOND `settle()` after only
    the parent's plain (non-memory) input changes -- `vm-fast` reads a
    stale, frozen value from the block's first-ever activation forever
    after; `vm` was unaffected in the two cases checked.** Minimal repro
    (drive `i2=0`, `settle()`, drive `i2=12345`, `settle()`):
    ```verilog
    module c (input [31:0] i2, output logic [79:0] o6);
        reg [7:0] wc1;
        logic [7:0] wc2;
        logic [79:0] w4;
        always @(*) begin
            wc1 = 0;
            while (wc1 < 5) begin
                wc2 = 0;
                while (wc2 < 3) begin
                    w4 = !(|i2[31:23]);
                    wc2 = wc2 + 1;
                end
                wc1 = wc1 + 1;
            end
            o6 = {wc2, wc1, i2 ? w4 : wc1};
        end
    endmodule
    module t (input [31:0] i2, output [89:0] o7);
        wire [79:0] w4;
        assign o7 = w4;
        c u6 (.i2(i2), .o6(w4));
    endmodule
    ```
    `reference`/`vm`/`compiled` all give `o7` reflecting the *second*
    `i2` value (correct); `vm-fast` gives the value from the *first*
    settle, frozen. Confirmed the loop is essential (removing it, keeping
    everything else, stops reproducing) and the hierarchy is essential
    (running `c` standalone with the identical two-settle drive sequence
    does NOT reproduce it) -- narrows the suspect to something in how
    `vm-fast`'s delta-cycle re-triggering interacts with a loop-bearing
    combinational process specifically when its sensitivity signal
    arrives via a cross-instance continuous assign rather than a direct
    top-level drive, but this wasn't traced further into the scheduler
    given time already spent this session. Two real fuzzer-found
    instances of this exact shape: `mismatch_06451`, `mismatch_07803`
    (both `vm-fast`-only). Next step for whoever picks this up: trace
    `sim/vm/vm_scheduler.py`'s `_collect_triggered`/`_edge_fired` and the
    Cython `_run_delta_loop_core`'s combo-process re-trigger logic for
    this exact shape.
  - **Investigated, likely NOT a bug (same family as the existing "Icarus
    first-activation x-extension artifact"): a combinational `always @(*)`
    block that reads an output/variable's value BEFORE writing it later in
    the SAME activation** (a genuine inferred-latch/feedback shape, e.g.
    `always @(*) begin o5 = o6; ... o6 = ~something; end`) **gives
    different (but each internally self-consistent) answers across
    reference, `vm`/`vm-fast`, AND Verilator** -- not just one oracle
    disagreeing with the rest. `mismatch_07406`/`mismatch_07834`
    (`reference` vs `vm`/`vm-fast`) and `mismatch_05031` and others
    (`reference` vs Verilator) all share this exact pattern: an output
    read before its own first write within one process activation. Since
    THREE independently-implemented tools (this codebase's own reference
    engine, `vm`/`vm-fast`, and Verilator) each give a different but
    self-consistent answer, this looks like genuine simulator-defined
    behavior for a construct with no well-defined synchronous or
    combinational semantics (real hardware would synthesize a latch, and
    which "previous" value a fresh simulation run starts from before the
    first real activation is implementation-defined), not a bug in any
    one of them -- analogous to (though a distinct construct from) the
    already-investigated-and-closed Icarus first-activation artifact in
    `notes/known_issues.md`. Not reduced to a from-scratch minimal repro
    (attempts with hand-written simplified versions didn't reproduce,
    likely due to interaction with `settle()`'s own combinational
    bootstrap converging past the ambiguous first activation before the
    value is ever read back) -- flagged here with the real fuzzer-found
    modules preserved as artifacts rather than chased further given time
    spent. Possible future improvement: extend the fuzzer's
    `_is_icarus_first_activation_artifact`-style filtering to recognize
    this broader "self-referential/latch-forming combinational read"
    shape across all three oracles, to keep future surveys from
    re-surfacing it as fresh-looking noise.
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
