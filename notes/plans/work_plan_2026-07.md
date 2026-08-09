# Consolidated Work Plan — July 2026

> **The entire plan (tiers 1-3 and all of tier 4) is complete.** Full
> step-by-step plans and "Result" execution notes for every item live in
> [`work_plan_2026-07_archive.md`](work_plan_2026-07_archive.md) (August
> 2026 cleanup, per `architecture_review_2026-07.md` item 9). This file now
> holds only a one-line-per-item completion checklist. See
> [`notes/roadmap.md`](../roadmap.md) for what's next — items not yet
> scheduled into a new dated plan live there.

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

**Tier 4** (all ✅): 4.1 package cycles broken + layering test added ·
4.2 semantic core unification (the big one — one shared implementation of
width/signedness/const-eval semantics across all four engines) ·
4.3 testbench generator thin skeletons + `TestbenchPlan` round-trip +
`--emit-plan`/`--force-plan` sidecar workflow · 4.4 LSP custom-command
payloads fully typed (`veriforge_lsp/payloads.py`, all 12 commands) +
`handlers/extended.py` split into `hierarchy.py`/`refactor.py` ·
4.5 `refactor/_pull_up_engine.py`'s procedural/assigns/structural
triplication collapsed behind a `_PullUpKindStrategy` `Protocol` + shared
pipeline (3323 → 3027 lines; zero behavior change per the 57-test pull-up
fixture suite).

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
