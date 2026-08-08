# Consolidated Work Plan — July 2026

> **Tiers 1-3 and items 4.1-4.2 are complete.** Their full step-by-step
> plans and "Result" execution notes have moved to
> [`work_plan_2026-07_archive.md`](work_plan_2026-07_archive.md) (August
> 2026 cleanup, per `architecture_review_2026-07.md` item 9). This file
> now holds only a one-line-per-item completion checklist for that
> finished work, plus the full, still-actionable detail for what's
> actually left: **items 4.3, 4.4, and 4.5**.

## Completed (see archive for full detail)

**Tier 1 — Quick wins** (all ✅, July 2026):
1.1 stray test file moved · 1.2 `_engines()` helper centralized ·
1.3 env-var prefixes unified onto `VERIFORGE_*` · 1.4 doc forward-slashes +
reference checker · 1.5 LSP Verible-absent fallback tested ·
1.6 `verilog_parser.py` modernized.

**Tier 2 — Test infrastructure & compiled-engine bugs** (all ✅):
2.1 cross-engine assignment-semantics matrix · 2.2 compiled-engine
edge-case suites · 2.3 compiled-only unary/shift codegen bugs fixed ·
2.4 wide `OP_ASHR` X-propagation fixed · 2.5 `test_compiled.py` split by
feature · 2.6 cross-engine unary `~` self-determined-width bug fixed ·
**2.7 remaining compiled-engine correctness gaps — done except one
narrow, deliberately out-of-scope architectural gap** (wide user-defined
function port/return; see `notes/known_issues.md`'s open-defects
section, not this file, for its current status).

**Tier 3 — CI and engine parity** (all ✅):
3.1 CI sim-smoke job · 3.2 scheduled full-regression workflow ·
3.3 Cython VM drift fixed and gated in CI · 3.4 randomized differential
harness landed (and, across many later "waves" not tracked as separate
work-plan items, used to find and fix a long tail of real cross-engine
bugs — see `known_issues.md`'s open-defects section for what if
anything from that tail is still open) · 3.5 `Simulator.engine_report()`
shipped.

**Tier 4, items 4.1-4.2** (✅): 4.1 package cycles broken + layering
test added · 4.2 semantic core unification (the big one — one shared
implementation of width/signedness/const-eval semantics across all four
engines).

## Tier 4 — Structural projects still open (one at a time, in this order)

### 4.3 Testbench generator: thin skeletons + plan sidecar (M)

**Goal**: functionality review §3.1–3.2.
**Steps**:
1. Add `TestbenchPlan.to_dict()` / `TestbenchPlan.from_dict()` in
   `sim/bench/plan.py` (dataclass round-trip; cover clocks, resets,
   interface bindings, domain assignments; unit-test the round-trip).
2. In the skeleton renderers (`dsl/testbench.py` — or
   `sim/bench/skeleton.py` after 4.1): replace generated inference/setup code
   with a call to `make_bench(dut, overrides=...)`, keeping (a) the plan
   summary as a comment block, (b) one put/get example per detected
   interface, (c) the `validate_with_icarus()` helper. Target: generated file
   shrinks substantially without losing the runnable example property.
3. `generate_python_testbench_skeleton(..., emit_plan=True)` additionally
   writes `<name>_plan.json` (the `to_dict()` output). On regeneration, if
   the sidecar exists and differs from fresh inference, print a unified diff
   and keep the user's file unless `--force-plan`.
4. CLI: wire `--emit-plan` / `--force-plan` flags through
   `__main__.py generate-python-testbench`; document in
   `notes/cli_json_schema.md` and `notes/simulation/generator_tb.md`.
**Accept**: existing scaffold tests updated and green
(`uv run pytest tests/test_sim/test_generator_endpoint.py tests/test_dsl/ -q`
plus the scaffold-specific tests — locate with
`grep -rln "generate_python_testbench" tests/`); new round-trip and diff tests.

### 4.4 LSP: typed payloads, then split (M/L)

**Goal**: functionality review §4.1–4.2.
**Steps**:
1. New `veriforge_lsp/payloads.py`: one `@dataclass` per custom command
   request and response (enumerate commands from
   `veriforge_lsp/handlers/extended.py` `register()` — set_top_module,
   resolve_children, hierarchy_graph, trace_signal, preview/apply ×
   collapse/extract/pull-up/push-down/boundary-move, reparse). Each has
   `from_dict(cls, d)` (tolerant: unknown keys ignored, missing optional →
   defaults) and `to_dict()`. Error responses get a shared `ErrorPayload`.
2. Convert handlers one command per commit: parse request at the top,
   `to_dict()` at the return. The wire format must not change — assert this
   by running the existing `tests/test_lsp/` suite after each command.
3. Once all commands are typed, remove `veriforge_lsp.*` from the mypy
   `ignore_errors` override for `payloads.py` (narrow the override, don't
   drop it wholesale) and fix what surfaces in that file.
4. Split `extended.py` (~1800 lines) into `handlers/hierarchy.py`
   (tree/graph/trace), `handlers/refactor.py` (preview/apply + legacy
   adapters), keeping `extended.py` as the `register()` aggregator.
5. Document the payload schemas in `notes/veriforge_lsp.md` (same table style
   as `notes/cli_json_schema.md`).
**Accept**: `uv run pytest tests/test_lsp/ -q` green throughout; mypy green on
`payloads.py`; docs updated.

### 4.5 Pull-up engine de-triplication (L)

**Goal**: architecture review item 5. Only start after 4.2 is done (it
removes one source of churn in the same files).
**Steps**:
1. Read the three families in `refactor/_pull_up_engine.py`:
   `_build_design_wide_pull_up_from_child_{procedural,assigns,structural}`,
   `_build_child_module_for_pulled_up_{procedural,assigns,structural}`,
   `_build_parent_module_for_pulled_up_child_{logic,assigns,structural}`,
   `_design_wide_parent_{procedural,assign,structural}_edits`. Produce (as
   the PR description, not code) a table of what differs per kind at each of
   the four stages.
2. Define `class _SelectionKindStrategy(Protocol)` with one method per
   varying stage (from the step-1 table); implement three small strategy
   classes; write one shared pipeline function per family that takes the
   strategy.
3. Migrate one kind at a time (assigns first — smallest), leaving the other
   two families' functions delegating to the old code until their turn.
   `uv run pytest tests/test_refactor/ tests/test_lsp/ -q` after every step —
   these transforms are fail-closed, and the acceptance bar is **zero
   behavior change**: identical preview payloads, diagnostics, and edit plans
   (the fixture tests assert payload contents).
4. Delete dead per-kind functions; `_preview_pull_up_child_range` should fall
   out smaller — decompose it along its validation / plan-build / diff phases
   if it is still >150 lines.
**Accept**: refactor + LSP suites green; file shrinks meaningfully (expect
roughly 3300 → ~2000 lines); roadmap's "unified core API" item updated.

---

## Deliberately not planned

(Assessed in the reviews; recorded so they are not re-raised.)

- VCD writer/reader replacement with pyvcd/vcdvcd — custom code is small,
  tested, dependency-free. Revisit only if `$dumpoff/$dumpon`, nested scopes,
  or FST become requirements.
- CLI migration to click/typer — cosmetic.
- Reference-scheduler edge-detection rework (snapshot compare → per-signal
  trigger lists) — no current performance need.
- `$dumpoff`/`$dumpon` — parked until a user needs VCD windowing; would be a
  Tier-3-sized item touching all three engines.
