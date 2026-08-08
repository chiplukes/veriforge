# Architecture Review — July 2026

> **Execution**: the ordered, agent-executable plan derived from this review
> is [work_plan_2026-07.md](work_plan_2026-07.md). This document is the
> rationale record.

Findings from a full-project review (code + `notes/`), with a plan for the
items too involved to fix inline. Minor fixes (broken doc links, stale plan
references, packaging exclude for `tests/`, roadmap staleness) were applied
directly as part of the review; this file holds the rest, ordered by expected
payoff.

---

## 1. Unify expression semantics shared by all engines (highest priority)

**Problem.** Width computation, constant evaluation, and signedness handling
are re-implemented per engine, with drift between copies:

| Helper | Implementations |
| --- | --- |
| `_const_int` | `sim/scheduler.py`, `sim/vm/compiler.py`, `sim/compiled/_codegen_utils.py`, `analysis/width_inference.py` (+ `analysis/const_fold.const_int`) |
| `_range_width` | `sim/scheduler.py`, `sim/vm/compiler.py`, `sim/compiled/codegen.py`, `analysis/width_inference.py` |
| `_var_width` | `sim/scheduler.py`, `sim/vm/compiler.py`, `sim/compiled/codegen.py` |
| `_expr_width` | `sim/vm/compiler.py`, `sim/compiled/_expr_emitter.py` |

The copies are near-duplicates with real behavioral differences (e.g.
`scheduler._range_width` resolves bounds via its local `_const_int`, while the
VM's version calls `elaborate._eval_const_expr` — different const-eval
semantics). The recent commit history (sign-extension and width fixes applied
engine-by-engine: `7721ed2`, `ce68eb9`, `070d0f8`, `30db803`, `53d166f`,
`71897f4`, `2aa3124`…) is the symptom: every IEEE width/signedness rule lives
in ~4 places, so every fix must be found and re-applied ~4 times.

**Plan.**
1. Create a single semantics module (suggested: `veriforge/analysis/semantics.py`
   or a new `veriforge/semantics/` package) owning:
   - self-determined / context-determined width rules (IEEE 1364-2005 §5.4)
   - signedness propagation (§5.5) — fold in `evaluator._expr_signed`
   - constant expression evaluation with an explicit parameter-env type
     (subsuming `_const_int` / `_eval_const_expr` / `const_fold.const_int`)
   - Verilog literal parsing (`Value.from_verilog` stays, but sizing rules live here)
2. Migrate one consumer at a time, locking each migration with
   characterization tests (assert identical results on the existing test corpus
   before/after). Suggested order: scheduler → VM compiler → compiled codegen →
   width_inference.
3. Delete the per-engine copies once all consumers are migrated.

**Exit criteria.** `grep -rn "def _const_int\|def _range_width\|def _var_width"`
returns one definition each; a width/signedness bugfix touches one file plus tests.

## 2. Cross-engine conformance testing (prevents the next drift)

**Problem.** Engine-vs-engine divergence is currently found ad hoc (a design
misbehaves, then a targeted test is written). `IcarusCosim` exists but runs
only in local validation tests.

**Plan.**
1. Add a differential test harness that generates small random RTL expression
   trees (bounded widths incl. >64-bit, signed/unsigned mixes, x-injection)
   and asserts reference == vm == vm-fast == compiled on random stimulus.
   A seeded, bounded run (~hundreds of cases) is fast enough for every PR;
   a longer randomized run can be nightly.
2. When Icarus is available, include it as the oracle via `IcarusCosim`.
3. Apply the `cross_engine` / `compiled` pytest markers from
   `notes/test_taxonomy.md` (currently defined but unused) so the harness and
   existing cross-engine tests are selectable.

## 3. Decide the fate of the Cython VM (`_interp_fast.pyx`)

**Problem.** `setup.py`'s docstring records that the Cython VM has drifted from
the pure-Python interpreter (~18 failures in `test_bench_native.py`,
memory read-after-write divergence). The fallback is silent, the issue was
documented only in `setup.py` (now also in `notes/known_issues.md`), and no CI
job builds the extension — so the shipped-when-built artifact is effectively
untested.

**Plan (decided July 2026): fix and gate — the compiled VM stays.**
The pure-Python VM interpreter is slower than the reference engine, so
`vm-fast` with the extension built is the only useful form of the VM engine;
the Python interpreter's role is executable specification. Therefore:

1. Repair the `_interp_fast.pyx` divergence (memory read-after-write cases in
   `test_bench_native.py`).
2. Add a CI job that builds `_interp_fast` and runs the VM test selection
   twice — `VERIFORGE_DISABLE_CYTHON_VM=1` and `=0` — failing on any
   difference. This makes the "compiled VM must match the Python VM" policy
   mechanical instead of conventional.
3. Sync policy going forward: changes to `sim/vm/interpreter.py` /
   `opcodes.py` land with the matching `_interp_fast.pyx` change in the same
   commit.

See `functionality_review_2026-07.md` §1 for the engine-role framing.

## 4. CI does not exercise the simulator

**Problem.** `ci.yml` runs lint + a parser/model/analysis slice only. The three
sim engines — the majority of the code and of the recent bug history — have no
CI coverage; full regression is manual (`-n 8` locally).

**Plan.**
1. Add a `sim-smoke` CI job: `tests/test_sim/` minus `test_compiled.py` and
   slow marks, `-n 4`, single Python version. (Measure locally first; the
   suite minus compiled tests should be a few minutes with xdist.)
2. Add a scheduled (weekly or pre-release) workflow for
   `test_compiled.py --run-slow -n auto` and the Icarus validation suite on a
   runner with iverilog installed.
3. If item 3 chooses (a), build `_interp_fast` in the smoke job.

## 5. Refactor engine triplication in `_pull_up_engine.py`

**Problem.** `refactor/_pull_up_engine.py` (3,323 lines) carries three parallel
families — `*_procedural`, `*_assigns`, `*_structural` — across ~6 function
groups (`_build_design_wide_pull_up_from_child_*`,
`_build_child_module_for_pulled_up_*`,
`_build_parent_module_for_pulled_up_child_*`,
`_design_wide_parent_*_edits`…). Each family repeats boundary validation,
collision detection, and payload construction with small per-kind differences.
The roadmap's "unified core API for push-down" is the same problem.

**Plan.** Introduce a `SelectionKind`-parameterized core: one
validate → build-child → rewrite-parent → edit-plan pipeline taking a small
strategy object per kind (procedural/assign/structural). Migrate one kind at a
time behind the existing fixture tests; the fail-closed diagnostics contract in
`developer_guide.md` §9 already defines the safety net.

## 6. Break the remaining package cycles structurally

Two module-level cycles exist (both currently "work" via import ordering):

- **`sim ↔ dsl`** — documented in `architecture.md`, held together by the
  invariant that `sim.endpoints` never imports `dsl`. Adopt the already
  documented option (a): move `dsl/testbench.py` into `sim/bench/` and
  re-export from `dsl` for compatibility. An invariant that must be preserved
  by convention is a regression waiting for an innocent import.
- **`project ↔ scaffold`** — `project.py` imports `scaffold` at module bottom
  purely for backward-compat re-exports. Replace with a module-level
  `__getattr__` (PEP 562) that lazily forwards `build_testbench` etc., or drop
  the re-export at the next minor version bump.

Add a lightweight import-linter check (e.g. `tach` or a small AST test like the
one used for this review) asserting the allowed dependency direction:
`model → {analysis, codegen, convert, transforms} → {sim, dsl, refactor} → {project, scaffold} → CLI/LSP`.

## 7. Environment variable naming is split across two prefixes

Code reads both `VERIFORGE_*` (`VERIFORGE_DISABLE_CYTHON_VM`,
`VERIFORGE_CODEGEN_PROFILE`) and legacy `VERILOG_TOOLS_*`
(`VERILOG_TOOLS_COMPILE_CACHE`, `VERILOG_TOOLS_NO_COMPILE_CACHE`,
`VERILOG_TOOLS_COMPILED_WIDE_TRANSPORT_ONLY`). Docs referenced a
`VERILOG_TOOLS_DISABLE_CYTHON_VM` that never existed in code (fixed inline).

**Plan.** Standardize on `VERIFORGE_*`; read the old name as a fallback for
one release and emit a `DeprecationWarning`; update
`developer_guide.md` / `cycache.md` / `simulator_compile_cython.md` together.
A single `veriforge/_env.py` accessor keeps the fallback logic in one place.

## 8. Latent compiled-engine wide unary masking bug (RESOLVED — this review's own diagnosis was backwards)

**Status (August 2026)**: not a bug. Re-verified against Icarus with this
item's own suggested repro (`~a`/`-a`, `a` both 1-bit and 71-bit, evaluated
in a 96-bit destination context) — all four engines, including compiled,
match Icarus exactly.

This review originally described masking the `wide_not`/`wide_neg`
primitive's result at `dst_width` (rather than the operand's own
self-determined width) as an IEEE Table 5-22 violation. That got the rule
backwards: unary `~`/`-` are *context-determined* operators (Table 5-22),
which means the OPERAND must be extended to the full destination context
width BEFORE the operator runs — masking the primitive's result at
`dst_width` is exactly correct once the operand has already been recursed
into at `dst_width` (which `_wide_emitter.py`'s `_emit_wide_expr_to_scratch`
already does — see its own extensive comment at the `if op in {"~", "-"}:`
branch). This was conclusively established by a much later, more
rigorous investigation (see `notes/known_issues.md`'s seed 2182 entry: a
systematic truth-table sweep across six fixed-self-determined operators
and both `~`/unary `-`, which found Icarus extends-then-applies in every
case, never the reverse). The current code already implements the correct
behavior — whether that was already true when this review was written and
misdiagnosed, or fixed since as a side effect of the seed 2182 work, is
not worth re-litigating; what matters is the current, verified state.

## 9. Documentation architecture: statuses live in too many places (RESOLVED)

**Status (August 2026)**: done. All three plan steps completed:
1. `known_issues.md` now holds only currently-open defects (trimmed from
   ~4200 to ~130 lines; the full historical record of resolved defects
   moved to `known_issues_archive.md`). `roadmap.md` was already
   appropriately terse/future-work-only on inspection and needed no
   changes. `support_matrix.md` was already serving as the status index.
   (The same treatment was also applied to `work_plan_2026-07.md`, not
   explicitly called out in this item's original plan but the same
   problem in the same place: trimmed to a completion checklist plus the
   still-open items 4.3-4.5, full execution history moved to
   `work_plan_2026-07_archive.md`.)
2. `tools/check_overview.py` already had the staleness check running in
   CI (`All doc references valid (N files checked)` — confirmed still
   passing after the above reorganization, 51 files checked).
3. `support_matrix.md`'s backslash paths (`notes\...`) — found still
   present despite item 1.4's "done" mark (that item covered other
   docs, not this file) — converted to forward-slash markdown links.

Original problem statement, for context: support status was spread
across `roadmap.md`, `known_issues.md`, `support_matrix.md`,
per-subsystem notes, and (until this review) `setup.py`. Several
"planned" roadmap items were already implemented (near-miss detection,
LSP Lark fallback, `.pxi` templates) — i.e. the docs lagged the code in
the *optimistic* direction, which is the benign failure mode, but it
still cost trust.

## 10. Deferred / rejected simplifications (assessed, not planned)

- **VCD writer/reader → `pyvcd`/`vcdvcd`.** Custom `vcd.py` (251 lines) and
  `vcd_compare.py` (342 lines) are small, dependency-free, tested, and
  integrated with the `Value` x-representation. Swapping saves little and adds
  two deps. Not worth it now; revisit only if VCD scope grows ($dumpoff,
  nested scopes, FST).
- **CLI → click/typer.** 1,400-line argparse CLI is verbose but stdlib-only
  and works. Cosmetic.
- **`verilog_parser.py` modernization.** Oldest file in the repo
  (`class verilog_parser(object)`, bare `Exception` raises, dead commented
  code). Renaming to `VerilogParser` with a compat alias + typed exceptions is
  cheap but touches the wildcard re-export in `__init__.py`; do it alongside
  item 6's import-surface cleanup.
- **Custom event queue / union-find / tree walk.** All small, stdlib-based
  (`heapq`), hot-path-justified. Keep.
