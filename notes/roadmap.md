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
   detection, and review payload construction.
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
  - `refactor/_pull_up_engine.py:_preview_pull_up_child_range` (~243 lines)
    — validation / plan-build / diff phases

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
