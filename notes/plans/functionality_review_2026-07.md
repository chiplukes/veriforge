# Functionality Review — July 2026

Mechanism-level ("how it works") review of the simulator engines, DSL,
testbench generator, LSP, and test suite. Companion to
`architecture_review_2026-07.md` (code-structure findings live there; overlap
is not repeated). Decisions recorded July 2026: **the compiled VM
(`_interp_fast`) stays** — the pure-Python VM interpreter is slower than the
reference engine, so `vm-fast` is the useful form of the VM engine and must be
kept in sync with the Python interpreter.

---

## 1. Simulator engines — mechanism assessment

### What's solid

- **Two-region scheduling model** (Active → NBA, delta-converged, with dirty-set
  continuous-assign propagation) is the right simplification of IEEE 1364
  scheduling for an RTL-subset simulator, and it is implemented consistently.
- **Flatten-based elaboration** (`elaborate.flatten_module` inlines all
  instances before any engine runs) is a good choice: it makes all three
  engines see the same single-module world and pushes hierarchy complexity
  into one place. Verilator does the same.
- **The (val, mask) int-pair Value encoding** is elegant — pure-0/1 signals
  degrade to plain int arithmetic, and x-propagation composes well.
- **The VM's design** (stack bytecode over integer-indexed signal arrays,
  constant pool, single dispatch loop) is textbook-correct for a Python-hosted
  interpreter.
- **Compiled engine two-tier strategy** (narrow ≤64-bit signals as C
  `long long`; wide signals as multi-word arrays) is the right performance
  shape; the bugs cluster at the tier boundary, which is a testing problem
  (see §5), not a design problem.

### Weaknesses / suggestions

1. **`vm` (pure Python) has no performance niche** — slower than the reference
   engine (per project owner's measurement). Its real role is *executable
   specification* for `_interp_fast`. Suggest making that official:
   - Document `vm` as the spec/debug engine, `vm-fast` as the production VM.
   - **Sync policy**: any change to `sim/vm/interpreter.py` or `opcodes.py`
     must land with the matching `_interp_fast.pyx` change in the same commit.
     Enforce mechanically, not by convention: a CI job that builds
     `_interp_fast` and runs the VM test selection twice —
     `VERIFORGE_DISABLE_CYTHON_VM=1` and `=0` — and fails on any difference.
     This also flushes out the existing ~18-test drift (known_issues.md).
   - The 41 existing `["vm", "vm-fast"]` parametrizations only exercise both
     paths when the extension is built; today CI never builds it, so
     `vm-fast` silently collapses to `vm` and the parametrization is a no-op.
2. **Timing-control fallback opacity.** The compiled engine falls back to
   reference coroutines for `#delay`/`@(edge)` in processes with only a
   `warnings.warn`. Add a `Simulator.engine_report()` (or extend `hierarchy()`)
   that lists which processes run native vs fallback, so users can see *why*
   a compiled run is slow instead of discovering the warning in a log.
3. **No `$dumpoff/$dumpon`, no mid-sim VCD window control** — cheap to add and
   useful for long compiled runs where full-run VCD is too large.
4. **Edge detection via full signal snapshot compare** (`_snapshot_signals` in
   the reference scheduler) is O(signals) per step; fine at current scale,
   but if reference-engine performance ever matters, edge-triggered process
   lists keyed by watched signal (as the VM/compiled engines already do) is
   the known fix. Low priority.

## 2. DSL — ergonomics improvements

The core design (Expr proxies + operator overloading + `with` blocks) is
right, and the guardrails are unusually good (`__bool__` raises with a helpful
message, `data[i] = x` typo detection via `__setitem__`, M11/M12 blocking/NBA
lint warnings). The clunkiness is concentrated in a few places, each with a
concrete Python remedy:

1. **Name duplication (`count = m.output_reg("count", width=8)`) — the biggest
   ergonomic tax.** Python's `__set_name__` descriptor protocol eliminates it.
   Offer an optional declarative class layer on top of the existing builder:

   ```python
   class Counter(ModuleSpec):
       clk   = In()
       rst   = In()
       count = OutReg(8)

       def body(self, m):
           with m.seq(self.clk):
               with m.if_(self.rst):
                   self.count.next = 0
               with m.else_():
                   self.count.next = self.count + 1
   ```

   `In()`/`OutReg(8)` are descriptors; `__set_name__` captures the attribute
   name at class-creation time — no frame inspection, no magic strings.
   The imperative builder stays for generator-style code (loops that create
   ports programmatically).
   For imperative code, a bulk declarator also removes most repetition:
   `clk, rst, en = m.inputs("clk rst en")` and
   `a, b = m.regs("a:8 b:16")`.

2. **`m.seq(clk)` / `m.comb()` shorthands.** `with m.always(posedge(clk)):` is
   the most-typed line in DSL code. `m.seq(clk)` (posedge + optional
   `rst=`, `rst_val=` generating the standard reset if/else skeleton) and
   `m.comb()` (`always @(*)`) match how designers actually think, and the
   sensitivity classifier (`_classify_sensitivity`) already knows the split.

3. **`.next =` as a readable alias for `<<=`.** A property setter on `Signal`
   (`count.next = count + 1`) reads better than `count <<= count + 1` to
   people coming from MyHDL, costs ~10 lines, and coexists with `<<=`.
   Consider `.now =` for blocking (`@=`) symmetry — or leave `@=` alone since
   blocking assigns are rarer.

4. **Conditional-expression chains.** Nested `mux(c1, v1, mux(c2, v2, v3))` is
   the DSL's least-readable output shape. Add a small expression builder:

   ```python
   value = when(c1, v1).when(c2, v2).otherwise(v3)   # priority chain
   ```

   which folds into the same nested `TernaryOp` tree. A dict-style
   `select(sel, {0: a, 1: b}, default=c)` covers the case-expression pattern.

5. **Part-select naming.** `sig.part_select(base, w)` / `part_select_down` are
   verbose for a common operation. Short aliases `sig.ps(base, w)` /
   `sig.psd(base, w)`, or a keyword form `sig.bits(lsb=k, width=8)`, would
   help. (Overloading slice `step` for `+:` was considered — too cryptic.)

6. **Small polish**
   - `cat()` accepting nested iterables (`cat(*bytes_list)` already works;
     `cat(bytes_list)` should too).
   - `m.input("data", 8)` — allow width as the second positional argument;
     `width=` stays for readability.
   - A `veriforge.dsl.prelude` module for the common
     `Module, posedge, negedge, cat, rep, mux` import block.

   Not recommended: `@`-as-concat operator (conflicts mentally with `@=`),
   and name inference via frame inspection / `varname` (fragile; the
   `__set_name__` layer solves it cleanly where it matters).

## 3. Testbench generator — mechanism assessment

The two-stage design is right: **plan inference** (`bench/planner.build_plan`:
clock/reset extraction + interface detection + strict-mode refusal on
ambiguity) is separated from **rendering**, overrides are explicit and typed
(`PlannerOverrides`), and near-miss explanations surface in plan warnings.
Strict-fail-on-ambiguity is the correct default for generated code.

Suggestions:

1. **Generate thinner skeletons.** `dsl/testbench.py` renders substantial
   Python source via f-string line assembly (`_render_bench_testbench`,
   `_render_native_bench_testbench`). Much of what is emitted duplicates what
   `make_bench()` already does at runtime. The generated file should trend
   toward: construct bench from plan + overrides, show one put/get example
   per detected interface, and keep the plan-summary comment block. Less
   generated code = less to drift when the runtime API evolves.

2. **Persist the plan, not just the code.** Emit the inferred
   `TestbenchPlan` as a JSON/TOML sidecar (or a commented literal in the
   skeleton). Re-running the generator can then diff inference against the
   user's edited plan instead of overwriting, making regeneration after RTL
   port changes a merge instead of a rewrite.

3. **Blocking proxy model is fine — document its limits.** `put()`/`get()`
   proxies with cycle-stepping underneath are much easier to teach than
   cocotb-style coroutines; concurrent stimulus is covered by
   `@domain.generator`. The generator-endpoint yield contract ("yield = clock
   edge") is good; make it more prominent in `bench_usage.md` since it is the
   escape hatch users will need first.

## 4. LSP — mechanism assessment

The three-tier pipeline (Verible didChange → debounced Lark fallback →
full-project parse on save) is a sound economy: fast diagnostics where
possible, full-fidelity model when it matters. The custom-command surface
(hierarchy tree, trace, preview/apply refactors) reuses the same engines as
the CLI, so there is one implementation of every refactor. Stale-preview hash
rejection is the standout correctness feature.

Suggestions:

1. **Type the custom-command payloads.** `handlers/extended.py` (1,807 lines)
   passes raw `dict`s end-to-end and is mypy-exempted. Define
   dataclasses (or TypedDicts) per command request/response and convert at the
   handler boundary. This is the highest-value LSP change: the payload shapes
   are the de-facto editor API and currently exist only implicitly.
   `notes/cli_json_schema.md` already does this for the CLI — extend the same
   contract style to LSP commands.
2. **Split `extended.py`** by operation (hierarchy/trace/collapse/extract/
   boundary-move) once payloads are typed; the legacy-payload adapters
   (`_legacy_*_payload_from_unified`) can then live next to their operation.
3. **Test the fallback tier.** `tests/test_lsp/` covers commands and workspace
   behavior; add a test that simulates Verible-absent operation and asserts
   the debounced Lark tier publishes syntax diagnostics on didChange.
4. **Consider `workspace/didChangeWatchedFiles`** so external edits (e.g.
   refactor apply from a second session, git checkout) invalidate the model
   without a manual `reparse` command.

## 5. Test suite — structure and coverage

### Structure

Overall good: top-level directories mirror `src/` subsystems, engine
parametrization is pervasive, real-world example suites (ibex, darkriscv,
pulp) serve as integration regression. Two organizational problems:

1. **`test_compiled.py` is a 62,000-line single file** (60% of all sim-test
   lines; 65 classes). Worse, it is organized by *implementation phase*
   (`TestPhase2Codegen`, `TestPhase3Execution`, `TestPhase7Cross`,
   `TestWideUnifiedPhase1Emitter`…) — archaeology, not taxonomy. A reader
   cannot find "ternary tests" or "NBA tests" without grepping. Plan:
   split into `tests/test_sim/compiled/` package organized by feature —
   `test_infra.py` (compile/cache), `test_narrow_ops.py`, `test_wide_ops.py`,
   `test_memories.py`, `test_control_flow.py`, `test_scheduling.py`,
   `test_cross_validation.py`, `test_external_io.py` (the slow matrix) — as a
   mechanical move (no test rewrites), keeping git history via `git mv` of the
   largest chunks first. Phase names survive as docstrings if wanted.
2. **`_engines()` is copy-pasted into ~15 test files.** Move to
   `tests/test_sim/conftest.py` as a fixture/constant, and apply the
   `cross_engine` / `compiled` markers from `test_taxonomy.md` while touching
   those files (they are defined but unused today).

Minor: `tests/test_partial_assign.py` sits at top level; it belongs under
`test_sim/`.

### Width / signedness assignment coverage — current answer: *not yet thorough where it matters*

The recent bug areas (size-mismatched and signed↔unsigned assignment) have
good *reference-engine* coverage but thin *compiled-engine* coverage:

- `test_value_widths.py` (the width-promotion/assignment matrix,
  10 test classes) exercises **only the reference evaluator/executor** — zero
  engine parametrization.
- `TestSignedExtension` (precedence_and_fixes) runs
  `["reference", "vm"]` — the **compiled engine is excluded** from exactly
  the `$signed`-to-wider-target cases that were recently fixed there.
- `TestSignedDeclarationSupport` (test_testbench.py) does run all four
  engines — good, but it covers declared-signedness, not the full
  cast × direction × width matrix.

**Plan: a dedicated cross-engine assignment-semantics suite**
(`tests/test_sim/test_assignment_matrix.py`), parametrized over all engines ×
assignment kind (continuous, blocking, NBA, port connection) × the matrix:

| Axis | Values |
| --- | --- |
| Source width vs dest | narrower, equal, wider; and straddling 63/64/65 bits |
| Signedness | unsigned→unsigned, signed→signed, signed→unsigned, unsigned→signed |
| Sign source | declared `signed`, `$signed()` cast, `$unsigned()` cast |
| LHS shape | whole signal, bit-select, range-select, concat LHS |
| Value cases | positive, negative (MSB set), all-ones, x-contaminated |

Perhaps 300–500 generated-but-deterministic cases; each asserts all available
engines agree with the reference engine (and the expected literal). This
converts the recent bug class from "found by examples" to "enumerated".

### Compiled-engine edge cases (recent-bug-shaped gaps)

Confirmed gaps worth closing, in priority order:

1. **Nested ternaries** — no nested-ternary test exists anywhere in the
   compiled suite. Add shapes: depth 2–4 nesting, ternary in condition
   position, mixed-width arms, arm widths ≠ context width, x in condition,
   ternary straddling the narrow/wide boundary, ternary as index expression.
2. **Port-boundary crossings** — `test_hierarchy.py` has no width- or
   sign-mismatch port-connection tests. Add: child port narrower/wider than
   connected parent net, signed child port ↔ unsigned parent net (both
   directions), expression port connections (`.a(x + y)`), constant and
   concat connections, output port driving a range-select of a parent net —
   each through the flatten path on all engines.
3. **Narrow/wide tier boundary** — systematic sweep of operations at widths
   {63, 64, 65} and intermediates that exceed 64 while operands don't (the
   `lo | (hi << 32)` class fixed in `aef7f13`): arithmetic, shifts,
   comparisons, concat, reduction.
4. **Self-determined vs context-determined width contexts** — unary `~`/`-`
   of >64-bit operands in strictly wider contexts (the latent `wide_not`/
   `wide_neg` masking issue — arch review item 8), shift amounts from
   x-contaminated signals, `&`/`|`/`^` in condition context (the `71897f4`
   class).
5. **Dynamic part-select near word boundaries** — `sig[base +: w]` where
   `base` is a runtime signal and the selected window crosses a 64-bit word
   seam (the `5b0b0fa` class, generalized).

Note the overlap discipline: the *randomized differential harness* is
architecture-review item 2; the enumerated shapes above are its deterministic
complement and belong in the regular suite either way.

### Missing test category

There is no **statement-shape inventory test** for the compiled engine: a
single module per statement/expression shape from `support_matrix.md`,
compiled and cross-checked, that fails with a clear name when codegen for that
shape regresses. The phase-organized tests approximate this accidentally; the
feature-organized split (above) is the opportunity to make it deliberate.
