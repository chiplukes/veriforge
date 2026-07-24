# Consolidated Work Plan — July 2026

Actionable synthesis of `architecture_review_2026-07.md` and
`functionality_review_2026-07.md` (rationale lives there; this file is the
execution order). Items are ordered **easiest first**. Each item is written to
be executable by an AI coding agent without further design work: design
decisions have already been made and are stated in the item.

**How to work this plan**
- One item per branch/PR. Do not batch unrelated items.
- Every item ends with the standard gate: `uv run ruff check .`,
  `uv run ruff format --check .`, `uv run mypy src/veriforge/ veriforge_lsp/`,
  `uv run python tools/check_overview.py`, plus the item's own tests. Use
  `uv run` for everything — this is a uv-managed project.
- If an item's Steps conflict with what you find in the code, stop and
  re-read the referenced review section — do not improvise a new design.
- Effort labels: **S** ≤ half a day, **M** 1–3 days, **L** a week or more.
- The DSL ergonomics work (functionality review §2) is already done — nothing
  from it appears here.

---

## Tier 1 — Quick wins

### 1.1 Move `tests/test_partial_assign.py` into `tests/test_sim/` (S)

**Goal**: fix the one stray top-level test file.
**Steps**: `git mv tests/test_partial_assign.py tests/test_sim/test_partial_assign.py`.
Fix any imports that referenced it (grep first: `grep -rn "test_partial_assign" tests tools .github`).
**Accept**: `uv run pytest tests/test_sim/test_partial_assign.py -q` passes; no
references to the old path remain.

### 1.2 Centralize the per-file `_engines()` helper (S)

**Goal**: one definition of the engine list instead of ~15 copies.
**Context**: many `tests/test_sim/*.py` files define an identical `_engines()`
returning `["reference", "vm", "vm-fast"]` plus `"compiled"` when a compiler
is available (see `tests/test_sim/test_precedence_and_fixes.py:56`).
**Decision**: create `tests/test_sim/engines.py` (a plain module, not
conftest, so it is import-friendly):

```python
"""Shared engine-list helper for sim tests."""
import shutil

_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")

def available_engines() -> list[str]: ...
ENGINES = available_engines()
STEPPED_ENGINES = [e for e in ENGINES if e in {"vm", "compiled"}]
```

Copy the probe and list logic from one existing `_engines()` verbatim
(see `tests/test_sim/test_precedence_and_fixes.py:48-66`, including the
`import Cython` try/except). Then, in each `tests/test_sim/*.py` that defines
`_engines()`: delete the local copy and `from .engines import ENGINES` (add
`STEPPED_ENGINES` where used). Do NOT change any test's engine list contents —
files with hand-written lists like `["reference", "vm"]` keep them.
**Also**: register the two markers from `notes/test_taxonomy.md` in
`tests/conftest.py` `pytest_configure` (next to the existing `slow` marker):
`cross_engine` and `compiled`. Applying the markers to tests is item 2.x
work — here only register them.
**Accept**: `grep -rn "def _engines" tests/` returns only `tests/test_sim/engines.py`;
`uv run pytest tests/test_sim/test_precedence_and_fixes.py -q` passes.

### 1.3 Env-var unification behind one accessor (S)

**Goal**: stop the `VERIFORGE_*` / `VERILOG_TOOLS_*` prefix split
(architecture review item 7).
**Decision**: new file `src/veriforge/_env.py`:

```python
"""Environment-variable access with legacy-prefix fallback."""
import os, warnings

_LEGACY_PREFIX = "VERILOG_TOOLS_"
_PREFIX = "VERIFORGE_"

def get_env(suffix: str, default: str | None = None) -> str | None:
    """Read VERIFORGE_<suffix>, falling back to VERILOG_TOOLS_<suffix> with a DeprecationWarning."""
    val = os.environ.get(_PREFIX + suffix)
    if val is not None:
        return val
    legacy = os.environ.get(_LEGACY_PREFIX + suffix)
    if legacy is not None:
        warnings.warn(
            f"{_LEGACY_PREFIX}{suffix} is deprecated; use {_PREFIX}{suffix}",
            DeprecationWarning, stacklevel=2,
        )
        return legacy
    return default
```

**Steps**:
1. Replace every direct `os.environ.get("VERILOG_TOOLS_X")` /
   `os.environ["VERILOG_TOOLS_X"]` read in `src/` with `get_env("X")`.
   Current variables (grep to confirm): `COMPILE_CACHE`, `NO_COMPILE_CACHE`,
   `COMPILED_WIDE_TRANSPORT_ONLY` (in `sim/compiled/compiler.py`,
   `sim/compiled/codegen.py`), and `DISABLE_CYTHON_VM` (already `VERIFORGE_`-
   prefixed, in `sim/vm/vm_scheduler.py` — route it through `get_env` too).
   `VERIFORGE_CODEGEN_PROFILE` likewise.
2. Tests that *set* these variables keep working (legacy names still read);
   update `tests/conftest.py` and test helpers to set the new names anyway.
3. Docs: update `notes/developer_guide.md` §10, `notes/simulation/cycache.md`,
   `notes/simulation/simulator_compile_cython.md` to the `VERIFORGE_*` names,
   with one line noting the legacy names still work but warn.
**Accept**: `grep -rn "VERILOG_TOOLS_" src/ --include='*.py'` shows matches only
inside `_env.py`; full fast suite green:
`uv run pytest tests/test_sim/test_compiled.py -q -n 4` (cache env vars are
exercised by the compiled tests).

### 1.4 Docs: forward slashes + reference checker (S)

**Goal**: make stale doc references impossible to reintroduce
(architecture review item 9, steps 2–3).
**Steps**:
1. In `notes/support_matrix.md`, convert every backslash path
   (`notes\...`, `docs\...`, `tests\...`) to forward slashes.
2. Extend `tools/check_overview.py` with a second check (new function,
   called from `main`): scan every `notes/**/*.md`, `README.md`, and
   `CONTRIBUTING.md` for (a) markdown link targets — bracket-text followed by
   a parenthesized path — and (b) backtick
   references matching `` `notes/...md` `` / `` `docs/...md` ``; resolve
   relative to the file (links) or repo root (backtick refs); exit 1 listing
   any that do not exist. Skip `http`/`mailto` links and paths containing
   `<`/`*` placeholders.
3. Run it; fix anything it finds.
**Accept**: `uv run python tools/check_overview.py` passes and fails when you
temporarily add a bogus `notes/nope.md` reference (verify both, then remove
the bogus ref). CI already runs this tool, so no workflow change is needed.

### 1.5 LSP: test the Verible-absent fallback tier (S)

**Goal**: the debounced Lark syntax-diagnostic fallback in
`veriforge_lsp/workspace.py` is implemented but untested
(functionality review §4.3).
**Steps**: add `tests/test_lsp/test_lark_fallback.py`. Pattern-match the
existing tests in `tests/test_lsp/` for how a `Workspace` is constructed.
Force Verible absence (monkeypatch `Workspace._find_verible` to return `None`
before construction). Feed a buffer with a syntax error via the didChange
path, flush/await the debounce timer (call the timer function directly or
reduce the debounce interval via monkeypatch — inspect `workspace.py`'s
timer creation and trigger it synchronously rather than sleeping), and assert
a diagnostic is produced. Add a companion case with valid text asserting no
diagnostics.
**Accept**: `uv run pytest tests/test_lsp/test_lark_fallback.py -q` passes and
fails if the fallback wiring is commented out (verify once locally).

### 1.6 `verilog_parser.py` modernization (S)

**Goal**: bring the oldest file up to project standard without breaking API
(architecture review item 10 note).
**Steps** in `src/veriforge/verilog_parser.py`:
1. Delete the commented-out dead code (lines 1–2, the `parse_interactive` line).
2. Rename `class verilog_parser` → `class VerilogParser`; add
   `verilog_parser = VerilogParser` alias at module bottom (the name is
   re-exported via `from .verilog_parser import *` in `veriforge/__init__.py`,
   and used by `project.py`, `veriforge_lsp/workspace.py`, tests).
3. Replace both `raise Exception(...)` with `raise ValueError(...)` /
   `raise TypeError(...)` as appropriate; shorten the transformer message.
4. Add type annotations; keep behavior identical.
5. Update internal callers to the new name
   (`grep -rn "verilog_parser(" src veriforge_lsp tests tools` — call sites
   instantiate `verilog_parser(...)`); leave the alias for external users.
**Accept**: CI fast slice green
(`uv run pytest tests/test_verilog_parser/test_all.py tests/test_model/test_module.py -q`);
`from veriforge.verilog_parser import verilog_parser` still works
(add a one-line test asserting the alias).

---

## Tier 2 — Test infrastructure (do before engine bug-fixing)

### 2.1 Cross-engine assignment-semantics matrix (M)

**Goal**: enumerate the recent bug class — size-mismatched and
signed↔unsigned assignment — across all engines
(functionality review §5, "assignment matrix").
**Decision**: new file `tests/test_sim/test_assignment_matrix.py`. Build test
modules as Verilog source strings (parse with the existing `_parse_module`-style
helper used in `test_precedence_and_fixes.py`) so the same source exercises
every engine.

**The matrix** (curated, not full cartesian — target 300–500 cases):

- **Widths (src → dst)**: (4→8), (8→4), (8→8), (63→64), (64→63), (64→65),
  (65→64), (65→80), (80→65). This covers narrower/equal/wider and both sides
  of the compiled engine's 64-bit word seam.
- **Signedness**: for each width pair, four variants:
  unsigned→unsigned, `signed`→`signed`, `signed`→unsigned, unsigned→`signed`
  (declared signedness on the reg/wire declarations).
- **Cast forms**: additionally, for width pairs (4→8), (63→64), (65→80):
  `$signed(src)` and `$unsigned(src)` applied on the RHS of an
  unsigned→unsigned assignment.
- **Assignment kinds** (each cell of the above runs in all four):
  1. continuous: `assign dst = src;`
  2. blocking in `always @(*)`
  3. non-blocking in `always @(posedge clk)` (single clock edge, then sample)
  4. port crossing: parent instantiates
     `child(.in_port(src))` where the child does `assign out_port = in_port;`
     and parent reads `child.out_port` back into `dst` — child port width =
     src width, parent net = dst width.
- **Stimulus values** per case: `0`, `1`, all-ones of src width, MSB-set
  ("negative") value, and one x-contaminated value (drive via testbench
  `Value` with mask on the low bit).

**Oracle**: compute the expected `Value` in Python inside the test:
truncation = `val & ((1 << dst_w) - 1)`; extension = zero-fill unless the RHS
is *signed for assignment purposes* (declared-signed source or `$signed()`
form, per IEEE 1364-2005 §5.5 — the RHS here is a bare identifier or cast, so
no operator complications), in which case sign-fill. Write this as a ~20-line
helper with its own docstring, and assert every engine's result equals both
the oracle *and* the reference engine's result (double bookkeeping catches
oracle bugs).

**Mechanics**: parametrize with `@pytest.mark.parametrize("engine", ENGINES)`
(from item 1.2's module) and `@pytest.mark.cross_engine`. Generate the case
list at module import from the tables above (deterministic order, ids like
`"u63_to_s64_nba"`). Compiled cases compile one module per (widths, kind)
combo — reuse one module for all signedness/stimulus variants of that combo to
keep compile count ≈ 36, not 500.
**Accept**: suite passes on reference/vm/vm-fast; run compiled locally with
`uv run pytest tests/test_sim/test_assignment_matrix.py -n 4 -q`. Any
compiled failures are real bugs: file them in `notes/known_issues.md` and
xfail (strict) with a comment rather than weakening the oracle.

### 2.2 Compiled-engine edge-case suites (M)

**Goal**: deterministic tests for the recent-bug-shaped gaps
(functionality review §5 list). New file
`tests/test_sim/test_compiled_edge_shapes.py` (same cross-engine mechanics as
2.1 — these shapes are valuable on all engines, and the reference engine is
the oracle).

Implement these shape families (each ~5–15 cases; explicit, not random):

1. **Nested ternaries**: depth 2, 3, 4 chains (`a ? b : c ? d : e ...`);
   ternary in the *condition* position (`(a ? b : c) ? d : e`); arms of
   different widths (4-bit and 8-bit arms in a 16-bit context); x in the
   condition (expect merged-arm x semantics — take the reference engine's
   result as oracle); one case with 65+-bit arms; ternary as an index:
   `mem[a ? i : j]`.
2. **Port boundary crossings**: child port narrower than parent net, wider
   than parent net; `input signed [7:0]` child port fed by unsigned 16-bit
   parent net and vice versa; expression connection `.a(x + y)`; constant
   connection `.a(8'hFF)`; concat connection `.a({hi, lo})`; child output
   driving a range-select of a parent net (`assign net[11:4] = ...` pattern
   via port). All simulated flat (the flatten path is what is under test).
3. **Word-seam sweep**: for widths 63, 64, 65: `+`, `-`, `*` (low word),
   `<<` and `>>` by 1/31/64, `&`, `|`, `^`, `==`, `<`, concat of two such
   signals, `&`-reduction. Include one intermediate-overflow case per width:
   `lo | (hi << 32)` with all declared signals ≤ 64 bits (the `aef7f13` class).
4. **Self-determined width contexts**: `~a` and `-a` where `a` is 65+ bits
   assigned into an 80-bit target (this is the trigger for item 2.3 — write
   these tests *first*, expect them to fail on compiled, and hand off);
   `&`/`|`/`^` used directly as an `if` condition (the `71897f4` class);
   shift amounts that are x-contaminated.
5. **Dynamic part-selects near seams**: `sig[base +: 8]` with runtime `base`
   values 0, 56, 60, 63, 64, 120 on a 128-bit signal, read and write forms
   (the `5b0b0fa` class generalized).

**Accept**: all cases pass on reference/vm/vm-fast; compiled failures handled
as in 2.1 (known_issues + strict xfail, or fix if trivial). This file becomes
the regression home for future compiled bugs: add the failing shape here
before fixing.

### 2.3 Fix the latent wide unary masking bug (S — after 2.2 §4)

**Goal**: architecture review item 8. In
`src/veriforge/sim/compiled/_wide_emitter.py` (~line 3590), `wide_not`/
`wide_neg` receive `dst_width` as their final (tail-mask) parameter, but per
IEEE Table 5-22 unary `~`/`-` are self-determined to the *operand* width;
`op_width` is already computed in scope at ~line 3585.
**Steps**:
1. Confirm the failing test from 2.2 §4 exists and fails (if it does not
   fail, the primitive doesn't use the parameter for masking — investigate
   `wide_not`/`wide_neg` definitions in the `.pxi` templates or generated
   primitives in `_gen_wide_section.py` before changing anything, and record
   the finding in the test's docstring instead).
2. Change the emitted call to pass `op_width`; then ensure the result is
   extended to `dst_width`: after the primitive call, zero any words between
   `ceil(op_width/64)` and `n_words` and mask the boundary word — mirror how
   the narrow-path `_emit_unary` fix (May 2026) handled it; find that commit
   with `git log --oneline -S"_emit_unary"`.
3. Remove the corresponding entry from `notes/known_issues.md` ("wide-emitter
   unary operator masking") once green.
**Accept**: the 2.2 §4 cases pass on compiled;
`uv run pytest tests/test_sim/test_compiled.py -q -n 4` no regressions.

### 2.4 Wide `OP_ASHR` precise X-propagation (M)

**Goal**: the existing, fully-specified plan in `notes/plans/x_prop_work.md`
(replace the "any x → all x" bail-out in `_interp_fast.pyx` `OP_ASHR` with a
precise shift-then-sign-fill of value and mask words; revert the matching
workaround in `_gen_wide_section.py`). Follow that plan document verbatim —
it lists affected tests and a completion checklist. Do it after 2.1/2.2 so
the new suites guard the change.
**Accept**: checklist in `x_prop_work.md` complete; delete or archive that
plan file and remove the roadmap "Simulation" entry pointing at it.

### 2.5 Split `test_compiled.py` by feature (M — mechanical)

**Goal**: replace the 62k-line phase-organized file with a feature-organized
package (functionality review §5.1).
**Decision**: target layout `tests/test_sim/compiled/`:

| Target file | Classes (by current name) |
| --- | --- |
| `test_infra.py` | `TestRuntimeCompile`, `TestCaching`, `TestClearCache`, `TestCacheControls`, `TestDuplicateDefConstants` |
| `test_codegen_basic.py` | `TestCodegen`, `TestPhase2Codegen`, `TestPhase3Codegen`, `TestPhase4Codegen`, `TestPhase5Codegen`, `TestPhase7Codegen`, `TestForLoopCodegen` |
| `test_execution.py` | `TestCompiledExecution`, `TestPhase2Execution`, `TestPhase3Execution`, `TestPhase4Execution`, `TestPhase5Execution`, `TestPhase7Runtime`, `TestMultibitCondition` |
| `test_scheduling.py` | `TestPhase5Scheduler`, `TestDirtyMarkingRegression` |
| `test_cross_validation.py` | `TestCompiledCrossValidation`, `TestPhase2CrossValidation`, `TestPhase3CrossValidation`, `TestPhase4CrossValidation`, `TestPhase4CounterCross`, `TestPhase7Cross`, `TestWideUnifiedBehavioralCrossVal` |
| `test_memories.py` | `TestMemoryArrayDimensionRegression`, `TestCompiledReadmemh`, memory-named classes |
| `test_vcd_io.py` | `TestPhase4VCD`, `TestCompiledDumpvars` |
| `test_params_patterns.py` | `TestParameterResolutionRegression`, `TestAssignmentPatternFallback` |
| `test_wide_ops.py` | every `TestWideUnified*` and `TestNarrowSignalsWideIntermediates` class |
| `test_external_io_slow.py` | `TestWideSignalExternalIO` (the `@pytest.mark.slow` matrix) |

Remaining classes: place by the same keyword logic; when in doubt, match the
class docstring to the file topic. Phase names go into class docstrings
(`"""(formerly Phase 3)"""`) — do not rename classes (some are referenced in
docs/notes by name).
**Procedure**:
1. `uv run pytest tests/test_sim/test_compiled.py --collect-only -q | tail -1`
   → record the exact collected count.
2. Create the package with an `__init__.py` and a `_shared.py` holding the
   module-level helpers/fixtures/constants the classes use (copy from the top
   of `test_compiled.py`; several sections re-import with `E402` suppressions —
   preserve those imports per target file as needed).
3. Move classes file-by-file (cut-paste, no edits beyond imports). After each
   target file, run it: `uv run pytest tests/test_sim/compiled/<file> -q -n 4`.
4. When `test_compiled.py` is empty, delete it, and update the two per-file
   ruff ignores in `pyproject.toml` (`tests/test_sim/test_compiled.py` →
   `tests/test_sim/compiled/*.py`) and every doc reference
   (`grep -rn "test_compiled.py" notes README.md .github tools`).
5. `uv run pytest tests/test_sim/compiled/ --collect-only -q | tail -1` must
   equal the count from step 1 (and with `--run-slow` likewise).
**Accept**: identical collected counts; full compiled suite green locally
(`-n 8`, per project convention); docs updated.

---

## Tier 3 — CI and engine parity

### 3.1 CI sim-smoke job (S)

**Goal**: the simulator finally runs in CI (architecture review item 4.1).
**Steps**: in `.github/workflows/ci.yml` add a job after `lint` (model on the
existing `test` job's uv setup):

```yaml
  sim-smoke:
    needs: lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - <same uv setup as the existing test job, python 3.12>
      - run: uv sync --extra test
      - run: >
          uv run pytest tests/test_sim/ tests/test_dsl/
          --ignore=tests/test_sim/compiled
          -n 4 --tb=short -q
```

(Before 2.5 lands, the ignore path is `tests/test_sim/test_compiled.py`.)
First measure locally: `time uv run pytest tests/test_sim/ tests/test_dsl/ --ignore=... -n 4 -q`.
If wall time exceeds ~10 minutes, drop `tests/test_dsl/` from the job and note
that in the workflow comment.
**Accept**: green run on GitHub Actions; `notes/developer_guide.md` §3 updated
to describe the new job.

### 3.2 Scheduled full-regression workflow (M)

**Goal**: compiled suite + Icarus validation on a cadence
(architecture review item 4.2).
**Steps**: new `.github/workflows/weekly.yml`, `on: schedule` (weekly) +
`workflow_dispatch`:
- job `compiled`: uv setup + `uv sync --extra test --extra bench` (bench pulls
  cython/setuptools), then
  `uv run pytest tests/test_sim/compiled/ -n auto --run-slow --tb=short -q`.
  Cache `.cycache/` with `actions/cache` keyed on a hash of
  `src/veriforge/sim/compiled/**` to keep reruns fast.
- job `icarus`: `sudo apt-get install -y iverilog`, then
  `uv run pytest tests/test_validation/ --tb=short -q`.
**Accept**: `workflow_dispatch` run is green (trigger manually once);
developer_guide §3 documents it and drops the "compiled suite is not exercised
in CI" caveat.

### 3.3 Cython VM: fix drift, then gate equivalence in CI (M/L)

**Goal**: `vm-fast` with the built extension must match the pure-Python VM —
the compiled VM is a keeper (decision recorded July 2026; the pure-Python VM
is slower than the reference engine, so the extension is the VM's only
useful form).
**Steps**:
1. Build the extension locally:
   `uv run python setup_cython.py build_ext --inplace`.
2. Reproduce the drift: `uv run pytest tests/test_sim/test_bench_native.py -q`
   (expected ~18 failures, memory read-after-write divergence, per the note in
   `setup.py`'s docstring). If it is green, the drift is already fixed —
   skip to step 4.
3. Fix `src/veriforge/sim/vm/_interp_fast.pyx` to match
   `sim/vm/interpreter.py`. Debug approach: the failures are memory (array)
   read-after-write within a time step — diff the memory-opcode handlers
   (`OP_*MEM*` / mem NBA handling) between `interpreter.py` and the `.pyx`
   line by line; the Python interpreter is the specification. For each
   divergent test, `VERIFORGE_DISABLE_CYTHON_VM=1` vs unset localizes whether
   the extension is at fault.
4. Add to `weekly.yml` (and to `ci.yml` if it stays under ~5 min) a job:
   build the extension, then run the VM selection twice and require both green:

   ```
   uv run pytest tests/test_sim/test_vm.py tests/test_sim/test_bench_native.py -q
   VERIFORGE_DISABLE_CYTHON_VM=1 uv run pytest tests/test_sim/test_vm.py tests/test_sim/test_bench_native.py -q
   ```
5. Add the sync policy to `notes/developer_guide.md` §5: any change to
   `sim/vm/interpreter.py` or `sim/vm/opcodes.py` lands with the matching
   `_interp_fast.pyx` change in the same commit.
6. Update `setup.py`'s docstring and `notes/known_issues.md` (remove/resolve
   the drift entry).
**Accept**: both runs in step 4 green in CI; known_issues updated.

### 3.4 Randomized differential harness (M)

**Goal**: generated cross-engine conformance testing
(architecture review item 2), complementing the deterministic suites of
Tier 2.
**Decision**: new file `tests/test_sim/test_differential.py`, marked
`@pytest.mark.cross_engine`.
**Design (implement as specified)**:
- A generator builds random expression trees over a fixed signal set:
  8 input signals with widths drawn from {1, 8, 16, 63, 64, 65, 80}, half
  declared `signed`. Node set: the binary ops `+ - * / % & | ^ << >> < <= ==
  != && ||`, unary `~ - !` and reductions, ternary, concat of 2–3 operands,
  replication with count 2–3, bit-select and part-select with in-range
  constant indices, `$signed`/`$unsigned` casts. Max depth 4. Division and
  modulo operands get `| 1` wrapped on the RHS to avoid div-by-zero noise
  (x-results are still covered by the x-stimulus below).
- Each case becomes a module: inputs as above, plus
  `wire [95:0] y_comb; assign y_comb = <expr>;` and a registered copy
  `always @(posedge clk) y_ff <= <expr>;`.
- Stimulus: 8 random vectors per case; 2 of them x-contaminate one randomly
  chosen input via `Value(..., mask=...)`.
- Oracle: the reference engine. Assert vm, vm-fast, and (when available)
  compiled produce identical `Value` (val *and* mask) for `y_comb` and `y_ff`
  after settle / after one clock.
- Determinism: seed from `VERIFORGE_DIFF_SEED` (default 20260701), case count
  from `VERIFORGE_DIFF_CASES` (default 150). On failure, print the generated
  Verilog source and the seed in the assertion message — that is the repro.
- Compile budget: batch all compiled-engine cases into as few modules as
  possible (e.g. 10 expressions per module as `y0..y9` outputs) so the
  compiled run stays under ~20 compilations.
**Accept**: default run green on reference/vm/vm-fast in a few seconds; add to
`weekly.yml` with `VERIFORGE_DIFF_CASES=2000` and compiled enabled. Divergences
found → reduce to a deterministic case in `test_compiled_edge_shapes.py`
(2.2) before fixing, same known_issues/xfail protocol.

### 3.5 `Simulator.engine_report()` (S/M)

**Goal**: make compiled-engine fallback visible
(functionality review §1 suggestion 2).
**Context**: `sim/compiled/compiled_scheduler.py` already collects
`self._codegen.timing_diagnostics` and `self._always_timing_blocks` (~line
495–515) — the data exists; it is only surfaced as warnings.
**Steps**:
1. In `sim/testbench.py`, add `Simulator.engine_report() -> dict` returning:
   `{"engine": <name>, "native_processes": int, "fallback_processes": int,
   "fallback_reasons": list[str]}`. For the compiled engine, populate from
   the codegen fields above; for reference/vm engines, everything is "native"
   (fallback fields zero/empty).
2. Unit test in `tests/test_sim/` (new small file or an existing
   compiled-infra file): a module with `#5` inside `always` reports ≥1
   fallback process on the compiled engine, and zero on reference.
3. Document in `notes/simulation/simulator_engines.md` (timing-fallback
   section) and `notes/public_api.md`.
**Accept**: test green; docs updated.

---

## Tier 4 — Structural projects (one at a time, in this order)

### 4.1 Break the package cycles + layering test (M)

**Goal**: architecture review item 6.
**Steps**:
1. **project ↔ scaffold**: at the bottom of `src/veriforge/project.py`,
   remove the `from .scaffold import (...)` backward-compat block; replace
   with a module-level PEP 562 hook:

   ```python
   _SCAFFOLD_REEXPORTS = {"build_testbench", "build_testbench_plan",
                          "generate_python_testbench_skeleton", "export_dsl_project"}
   def __getattr__(name):
       if name in _SCAFFOLD_REEXPORTS:
           from . import scaffold
           return getattr(scaffold, name)
       raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
   ```

   (Copy the exact re-exported name list from the current import block before
   deleting it.)
2. **sim ↔ dsl**: `git mv src/veriforge/dsl/testbench.py
   src/veriforge/sim/bench/skeleton.py`. Create a new thin
   `src/veriforge/dsl/testbench.py` containing only
   `from veriforge.sim.bench.skeleton import *` plus the explicit names other
   modules import (`grep -rn "dsl.testbench\|dsl import testbench" src veriforge_lsp tests`
   first and re-export exactly those). Move `dsl/testbench_deps.py` the same
   way if it only serves the moved module (check its importers). Fix relative
   imports inside the moved file. Update the "sim ↔ dsl import cycle" section
   of `notes/architecture.md` — the invariant paragraph becomes a description
   of the now-acyclic structure. Update `notes/python_overview.md` tree.
3. **Layering test**: new `tests/test_project/test_import_layering.py` that
   walks `src/veriforge/**/*.py` with `ast`, extracts intra-package imports at
   module level (skip imports inside function bodies — those are the
   sanctioned lazy pattern), maps modules to their top-level subpackage, and
   asserts the edge set is a subset of the allowed DAG:

   ```
   model → (nothing)
   analysis, codegen, convert, transforms, preprocessor, lark_file → model, preprocessor
   sim → model, analysis, transforms, verilog_parser, project*
   dsl → model, analysis, sim.endpoints (only)
   refactor → model, analysis, codegen
   project → model, transforms, verilog_parser, preprocessor, analysis
   scaffold → everything above
   __main__ → everything
   ```

   Derive the exact current allowed set by running the checker first and
   encoding what remains after steps 1–2 (`sim → project` exists via
   `example_runner`/cosim — inspect and either allow-list it with a comment or
   convert to lazy import). The test's failure message must name the offending
   module and import.
**Accept**: full fast suite + `tests/test_dsl/` + `tests/test_sim/` green;
layering test green and demonstrably fails when a forbidden module-level
import is added temporarily.

### 4.2 Semantic core unification (L — the big one)

**Goal**: one implementation of width/signedness/const-eval semantics
(architecture review item 1). Do this **after** Tier 2 exists — those suites
are the safety net.
**Decision**: new module `src/veriforge/semantics.py` (single module, stdlib
+ model imports only) with this exact API:

```python
def const_int(expr, env: Mapping[str, int] | None = None) -> int | None
def range_width(rng: Range | None, env=None) -> int          # None → 1
def var_width(var: Variable, env=None) -> int
def net_width(net: Net, env=None) -> int
def expr_width(expr, width_of: Callable[[str], int], env=None) -> int   # self-determined, IEEE Table 5-22
def expr_signed(expr, signed_of: Callable[[str], bool]) -> bool         # IEEE §5.5
```

`width_of`/`signed_of` are callbacks so each engine keeps its own symbol
table; no engine data structures leak into semantics.
**Phased migration — one phase per PR, full suite after each**:
1. **Phase A — characterize.** Write
   `tests/test_analysis/test_semantics_parity.py`: a fixture list of ~60
   expressions (literals incl. based/sized, parameters, arithmetic on
   parameters, ranges `[W-1:0]`, `[$clog2(N)-1:0]`, shifts, ternaries,
   concats, hierarchically-prefixed names) evaluated through *each existing
   implementation* (`sim/scheduler.py:_const_int/_range_width/_var_width`,
   `sim/vm/compiler.py` versions, `sim/compiled/_codegen_utils.py:_const_int`,
   `sim/compiled/codegen.py:_range_width/_var_width`,
   `analysis/width_inference.py`, `analysis/const_fold.py:const_int`,
   `sim/elaborate.py:_eval_const_expr`). Emit a difference table. Every
   difference gets a written resolution in the test file's docstring
   (expected: `_eval_const_expr` is the most general const path; scheduler's
   fast path is an optimization to keep). **Deliverable of Phase A is this
   test + the resolution notes — no production change.**
2. **Phase B — build.** Implement `semantics.py` to the resolved behavior;
   port the Phase A fixture into direct tests of the new module.
3. **Phases C–F — migrate one consumer per PR**, in this order: reference
   scheduler → VM compiler → compiled codegen (+ `_codegen_utils`) →
   `analysis/width_inference` + `const_fold` (keep `const_fold.const_int` as
   a public wrapper delegating to semantics — it is a documented public API).
   Mechanic per phase: change the consumer's private helpers into one-line
   delegations to `semantics`, run the *full* suite incl. compiled
   (`-n 8` locally) and the Tier-2 matrix; only then delete the private
   helpers and update call sites.
4. **Phase G** — add a guard test (extend the layering test or a new one)
   asserting `grep`-equivalent via AST: no function named `_const_int`,
   `_range_width`, or `_var_width` is *defined* outside `semantics.py`.
**Accept**: phases land green individually; `notes/architecture.md` gains a
"Semantics" paragraph; architecture review item 1 exit criteria met.
**Explicit non-goal**: do not merge `_expr_width` of the VM/compiled
*emitters* in the first pass — those mix width computation with codegen slot
allocation. Migrate the pure helpers first; revisit `_expr_width` unification
as a follow-up once `expr_width` exists and parity tests cover it.

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
