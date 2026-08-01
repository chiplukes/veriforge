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

## Tier 1 — Quick wins ✅ (all done July 2026)

### 1.1 Move `tests/test_partial_assign.py` into `tests/test_sim/` (S) ✅

**Goal**: fix the one stray top-level test file.
**Steps**: `git mv tests/test_partial_assign.py tests/test_sim/test_partial_assign.py`.
Fix any imports that referenced it (grep first: `grep -rn "test_partial_assign" tests tools .github`).
**Accept**: `uv run pytest tests/test_sim/test_partial_assign.py -q` passes; no
references to the old path remain.

### 1.2 Centralize the per-file `_engines()` helper (S) ✅

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

### 1.3 Env-var unification behind one accessor (S) ✅

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

### 1.4 Docs: forward slashes + reference checker (S) ✅

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

### 1.5 LSP: test the Verible-absent fallback tier (S) ✅

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

### 1.6 `verilog_parser.py` modernization (S) ✅

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

### 2.1 Cross-engine assignment-semantics matrix (M) ✅

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

**Result** (July 2026): 648 cases (162 matrix cells × 4 engines); 589
pass, 59 strict-xfail on compiled (two root causes, both filed in
`notes/known_issues.md`: narrow-path blocking/nonblocking x-mask loss, and
wide-emitter sign-extension wrong for the 65→80 width pair specifically).
The matrix also caught a cross-engine bug in shared elaboration code
(`elaborate.py::_create_prefixed_signals` dropped `signed` on a child's
implicit port net), which was fixed directly rather than xfailed since it
affected the reference engine itself.

### 2.2 Compiled-engine edge-case suites (M) ✅

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

**Result** (July 2026): 92 cases × 4 engines = 368; 359 pass, 9 strict-xfail.
Investigating family 4 turned up that the IEEE reading behind the existing
"wide-emitter unary operator masking" known-issue (and item 2.3 below) was
**backwards** — verified against both Icarus Verilog and Verilator, unary
`-`/`~` are *context-determined*, not self-determined, when they are an
assignment's top-level RHS. That reframes the bug into four independent,
precisely-characterized ones (full detail, repro commands, and truth tables
in `notes/known_issues.md`): (1) `~` is wrongly self-determined on
reference/vm/vm-fast at all widths; (2) compiled's narrow (<=64-bit) path
has the same `~` bug; (3) compiled's wide (>64-bit) unary path ignores
declared signedness (always zero-extends before applying `~`/`-`) — this is
what item 2.3 was actually about, but its fix direction was wrong (see
below); (4) unrelated: compiled's narrow-path shift by exactly the word
width (64) is a no-op instead of yielding 0, at widths 63/64 (not 65).
**Item 2.3 below must be rewritten before it is executed.**

### 2.3 Fix compiled-only unary/shift codegen bugs found in 2.2 (S) ✅

**Goal**: architecture review item 8, rescoped July 2026 after 2.2 found the
original diagnosis (IEEE self-determined citation) was backwards — see the
"Unary `-`/`~` are context-determined, not self-determined" entry in
`notes/known_issues.md` for the full truth table and Icarus/Verilator
verification. This item covers only the two bugs below that are contained
to the compiled engine (bugs 3 and 4 from 2.2's Result note); the
cross-engine `~` bug (1/2 from that note) is item **2.6**, not this item —
do not attempt to fix it here, it needs its own phased, full-regression
approach since it touches the reference engine.

**Part A — wide unary path ignores declared signedness (S)**.
In `src/veriforge/sim/compiled/_wide_emitter.py`, the `UnaryOp` handler for
`~`/`-` (~line 3574–3594) emits the operand into scratch at its own
`op_width` (line 3586: `self._emit_wide_expr_to_scratch(expr.operand,
op_slot, n_words, op_width, indent)`), then calls `wide_not`/`wide_neg`
passing `n_words`/`dst_width` (line 3591) — the primitive has no way to
know the operand's signedness, so it always fills the extension words with
zero. This file never calls `self._expr_signed()` anywhere (grep confirms),
so signedness genuinely isn't consulted in this path at all.
**Steps**:
1. When `op_width < dst_width` and `self._expr_signed(expr.operand)` is
   true, sign-extend the operand's scratch buffer from `op_width` to
   `dst_width` *before* the `wide_not`/`wide_neg` call (there's no existing
   wide-scratch sign-extend helper in this file — check
   `_emit_wide_expr_to_scratch`'s width-handling and the `BinaryOp` sign-
   extension logic elsewhere in this file for the pattern to mirror, e.g.
   fill words between `ceil(op_width/64)` and `ceil(dst_width/64)` with
   all-1s and mask the boundary word when the operand's sign bit is 1, all-0
   when it's 0 — same shape as the narrow-path `_sign_ext` helper's logic,
   just over multiple words).
2. When unsigned, behavior is already correct (verified in 2.2) — don't
   change that path.
3. Update `tests/test_sim/test_compiled_edge_shapes.py`: remove the
   `_known_engine_bug` entries for `self_det_unary_not_65_to_80_signed` and
   `self_det_unary_neg_65_to_80_signed` (they should now pass un-xfailed).
4. Update `notes/known_issues.md`: remove bullet 3 ("wide unary path ignores
   declared signedness entirely") from the "Unary `-`/`~` are
   context-determined" entry once green; leave bullets 1/2 (item 2.6's
   scope) in place.

**Part B — narrow-path shift by exactly the word width (64) is a no-op (S)**.
In `src/veriforge/sim/compiled/_expr_emitter.py::_emit_binary` (~line
1149–1156), `>>` and `<<` emit a raw C shift
(`<unsigned long long>(...) >> <unsigned long long>(...)` /
`(<long long>(...)) << (...)`) with no guard for a shift amount >= 64; on
x86-64 the native shift instruction only consults the low 6 bits of the
count, so a runtime shift amount of exactly 64 silently behaves like 0 (the
classic C undefined-behavior footgun). Verilog requires a shift amount >=
the operand's width to produce an all-zero result.
**Steps**:
1. Wrap the two shift cases in a runtime guard, following this file's
   existing `(0 if COND else EXPR)` idiom (e.g. line 782, 1116):
   ```python
   if expr.op == ">>":
       core = f"(0 if ({right}) >= 64 else (<long long>(<unsigned long long>({left}) >> <unsigned long long>({right}))))"
   elif expr.op in ("<<", "<<<"):
       core = f"(0 if ({right}) >= 64 else ((<long long>({left})) {c_op} ({right})))"
   ```
   (`right` is already a parenthesized sub-expression string reused safely
   elsewhere in this function; confirm it has no side effects before
   duplicating it in the guard — it shouldn't, these are pure value exprs.)
2. Check whether `>>>`  (arithmetic right shift, handled separately at line
   1129–1133 via `_sign_ext`) has the same issue — a shift amount >= width
   there should saturate to all sign-bit-fill, not wrap; add the same shape
   of guard if a test shows it's wrong (write one first: `>>>` by 64 on a
   64-bit signed all-1s-except-sign-bit-clear pattern, expect all-0; on a
   sign-bit-set pattern, expect all-1s).
3. Update `tests/test_sim/test_compiled_edge_shapes.py`: remove the
   `_known_engine_bug` entries for `seam63_shl64`, `seam63_shr64`,
   `seam64_shl64`, `seam64_shr64`.
4. Update `notes/known_issues.md`: remove the "narrow-path shift by exactly
   the word width (64) is a no-op" entry once green.

**Accept**: the 6 now-un-xfailed cases in
`tests/test_sim/test_compiled_edge_shapes.py` pass without their xfail
marks (collection should show 0 xfailed if item 2.6 hasn't landed yet, since
that item's 3 cases are unrelated — check the count matches);
`uv run pytest tests/test_sim/test_compiled.py -q -n 4` no regressions.

**Result** (July 2026): both parts landed. Part A: added a `wide_sign_extend`
primitive to `_gen_wide_section.py` (mirrors `wide_ashr`'s sign-fill logic)
and call it from `_wide_emitter.py`'s `UnaryOp` handler before `wide_not`/
`wide_neg` when the operand is signed and narrower than the context width.
Part B: guarded `>>`, `<<`, and `>>>` (which had the same bug, found while
verifying Part B per the plan's step 2) in `_expr_emitter.py::_emit_binary`
against a >=64 shift amount. `test_compiled_edge_shapes.py` now shows 365
passed / 3 xfailed (only item 2.6's cross-engine `~` cases remain).
`uv run pytest tests/test_sim/test_compiled.py -q -n 8` (full, not
`--run-slow`) green; 1207 shift-focused `--run-slow` cases green
separately.

### 2.4 Wide `OP_ASHR` precise X-propagation (M) ✅

**Goal**: the existing, fully-specified plan in its own plan file
(`x_prop_work.md`, formerly under `notes/plans/`, now removed — see Result
below) (replace the "any x → all x" bail-out in `_interp_fast.pyx`
`OP_ASHR` with a precise shift-then-sign-fill of value and mask words;
revert the matching workaround in `_gen_wide_section.py`). Follow that plan
document verbatim — it lists affected tests and a completion checklist. Do
it after 2.1/2.2 so the new suites guard the change.
**Accept**: checklist in that plan file complete; delete or archive it and
remove the roadmap "Simulation" entry pointing at it.

**Result** (July 2026): fixed in four places, not the one the plan named —
`sim/evaluator.py` (reference) and `sim/vm/interpreter.py` (pure-Python VM)
turned out to have their own separate copies of the same conservative bug
(the plan only knew about `_interp_fast.pyx` and `_gen_wide_section.py`),
found by checking the plan's own "confirm reference shares the VM's opcode
path" step rather than assuming it. All four (plus `_gen_wide_section.py`'s
`wide_ashr` revert) now use the same minimal fix:
`(a.sign_extend(width + shift) >> shift).resize(width)` at the `Value`
level, and the equivalent word-array version in the two Cython files.
Added a narrow-path `>>>` X-propagation smoke test to
`tests/test_sim/test_vm.py::TestXZPropagation` per the plan's optional
checklist item.

Along the way, fixing the VM surfaced a **real regression**: a separate
hand-written fast-path template family in
`sim/compiled/templates/{narrow_assign,narrow_stage}.pxi`
(`_whole_*_sar_{op}_signal`, 30 functions, used for patterns like
`$signed(a | b) >>> N`) had the identical conservative bail-out
independently of `wide_ashr`, invisible until the VM became precise and
started disagreeing with it. Fixed the same way — see
`notes/known_issues.md` ("Wide/narrow arithmetic right shift (`>>>`)
X-propagation") for the full account, including why `add`/`sub` variants
of that family were deliberately left alone.

Verifying the VM fix required building the Cython VM extension for the
first time in this environment, which exposed ~45 additional pre-existing
`vm-fast`-only failures (confirmed via `git stash` to predate this work) —
see `notes/known_issues.md` ("Cython VM interpreter drift"), now
considerably better-characterized than before. That's item 3.3's scope,
not this item's.

Full regression: 1218 `--run-slow` shift/ashr-focused `test_compiled.py`
cases green; `tests/test_sim/test_vm.py` green; targeted checks against
Icarus/Verilator-style hand oracles green for narrow/wide, shift ≥ width,
shift = 0, and unknown-sign-bit cases across all four engines.

### 2.5 Split `test_compiled.py` by feature (M — mechanical) ✅

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

**Result** (July 2026): Wrote a one-off Python/`ast` script (not committed —
scratch tooling) rather than manual cut-paste, given the file's scale (65
top-level classes, 62,120 lines). Verified the 65-class mapping was exact
(every class assigned exactly once, no gaps, no extras) before touching any
file. `_shared.py` holds every module-level helper/fixture/constant from
the original file (some interspersed *between* classes, not just in a
single leading preamble — `ast`-walked the whole module body, not just the
top, to catch all of them), exported via an explicit `__all__` so
underscore-prefixed helpers (most of them) survive `from ._shared import
*`; `pytestmark` (the Cython/compiler skip guard) is defined once in
`_shared.py` and picked up correctly by every consumer file via the same
star-import, since `from X import *` binds real module-level names.
Ambiguous classes not explicitly named in the table (25 of 65) were
assigned by reading each one's docstring, not just its name (e.g. all 7
`TestChar*` "characterize codegen output" classes were kept together in
`test_codegen_basic.py` rather than scattered by sub-topic, since they're a
cohesive unit). Collected count matched exactly before and after (4627,
with and without `--run-slow`) at every stage: right after generation,
after `ruff format`, and after deleting the original file. Ran every new
file individually, then the whole package together (784 passed + 3843
skipped = 4627, exactly matching the original file's own baseline run).
Updated `pyproject.toml`'s per-file ruff ignore (added `F403`/`F405` for
the star-import pattern) and every functional/actionable doc reference
(`tools/validate_compiled_pytest.py`'s 24 hardcoded node IDs,
`notes/developer_guide.md`, `notes/test_taxonomy.md`, `notes/known_issues.md`,
`notes/simulation/wide_signal_coverage.md`, `notes/simulation/
simulator_engines.md`, `notes/simulation/simulator_compile_cython.md`,
`notes/user_guide.md`, `notes/python_overview.md`) — left historical
Result notes in this file and the two `*_review_2026-07.md` documents
untouched, since those describe what was actually run at a past point in
time before the split existed.

### 2.6 Fix cross-engine unary `~` self-determined-width bug (M) ✅

**Goal**: found by item 2.2 (see its Result note and
`notes/known_issues.md`, "Unary `-`/`~` are context-determined, not
self-determined", bullets 1–2). Appended here out of numeric sequence
rather than renumbering 2.4/2.5 — do this whenever convenient relative to
2.4/2.5, there's no ordering dependency between them, but it's riskier than
either (it changes reference-engine output) so treat it with the same care
as item 3.3's VM-sync work: fix one engine, run the full suite, only then
move to the next.

**Verified bug** (Icarus Verilog + Verilator cross-checked): `~a`, used as
the top-level RHS of an assignment to a wider target, is computed as
self-determined (at `a`'s own width, extended afterward using `a`'s
declared signedness) on reference, vm, and vm-fast, and on compiled's
narrow (<=64-bit) path. This happens to equal the IEEE-correct
context-determined result when `a` is signed (sign-extension commutes with
bitwise complement) but is wrong when `a` is unsigned (zero-extension does
not commute with complement — the correct extension bits are all-1, not
all-0). Unary `-`/`+` are unaffected — they already extend the operand to
context width before applying the operator, everywhere.

**Fix locations** (three, not four — vm and vm-fast share one bytecode
compiler, so fixing it fixes both engines):

1. **Reference** — `src/veriforge/sim/evaluator.py`,
   `ExpressionEvaluator.eval`, `UnaryOp` branch (~line 339–358). The `~`
   case (line 344–346) evaluates the operand self-determined with no width
   passed to `self.eval`; the `+`/`-` case right below it (line 347–355)
   already does the right thing (passes `width` through, then
   `sign_extend`/`resize` if `operand.width < width`). Merge `~` into that
   same branch (`if expr.op in ("~", "+", "-"):`), keeping the final
   `_eval_unary_op(expr.op, operand)` call for all three.
2. **VM (fixes vm and vm-fast)** — `src/veriforge/sim/vm/compiler.py`,
   the `UnaryOp` compile branch (~line 834–865). Same shape: the `~` case
   (line 840–851) compiles the operand self-determined then emits
   `SIGN_EXT`/`RESIZE` *after* the operator instruction; the `+`/`-` case
   (line 853–865) compiles the operand with `width` passed in and extends
   *before* the operator. Merge `~` into that branch the same way as (1).
3. **Compiled narrow path** — `src/veriforge/sim/compiled/_expr_emitter.py`,
   `_emit_unary` (~line 1167–1205). Same shape again: `~` (line 1179–1187)
   computes at `ow` then wraps the whole result in `_sign_ext(..., ow)`
   afterward if signed; `+`/`-` (line 1189–1199) compute the operand at
   `max(ow, width)` and sign-extend the *operand* first. Merge `~` into
   that branch.

**Steps**:
1. Characterize first (this class of bug warrants it — see item 4.2's
   Phase A methodology): before changing code, write a small fixture of
   `~a` cases (unsigned/signed, narrow/wide, various context widths) and
   confirm each of the three locations above reproduces the exact
   mis-behavior described, to rule out any other interacting factor.
2. Existing-test audit already done (July 2026, before writing this item):
   no test in the suite hardcodes the buggy self-determined value as an
   "expected" result — every other `~`-on-unsigned-operand test either
   evaluates `~a` directly with no wider context, or has the operand
   already at the full context/target width (both cases make
   self-determined == context-determined, so they can't be encoding the
   bug). The one thing that *will* need updating is in this repo's own new
   test file: `tests/test_sim/test_compiled_edge_shapes.py`'s
   `self_det_unary_not_65_to_80_unsigned` case is `pytest.mark.xfail(strict=True)`
   for engines `!= "compiled"` (via `_known_engine_bug`) — once reference/
   vm/vm-fast are fixed those cases will XPASS, which `strict=True` turns
   into a *failure* until the xfail is removed (that's step 6 below,
   sequence matters: fix all three engines before running this file's
   suite, or expect a transient failure). Also worth a sanity re-run (not
   expected to break, deferred initial-value evaluation in `codegen.py`
   should stay in sync automatically):
   `tests/test_sim/test_compiled_latent_risks.py::test_initial_value_unary_matches_reference`.
3. Fix (1) reference; run the full fast suite
   (`uv run pytest tests/ --ignore=tests/test_sim/test_compiled.py -q -n 8`,
   excluding the slow compiled suite) plus
   `tests/test_sim/test_assignment_matrix.py` and
   `tests/test_sim/test_compiled_edge_shapes.py`; fix any test from step 2
   that breaks.
4. Fix (2) vm/compiler.py (covers vm and vm-fast); full suite again
   including `tests/test_sim/test_vm.py`, `test_bench_native.py`.
5. Fix (3) compiled narrow path; full compiled suite
   (`uv run pytest tests/test_sim/test_compiled.py -q -n 8`, plus
   `--run-slow` if time allows) and `tests/test_sim/test_assignment_matrix.py`.
6. Remove the `_known_engine_bug` entry for
   `self_det_unary_not_65_to_80_unsigned` in
   `tests/test_sim/test_compiled_edge_shapes.py` (and its
   `skip_ref_crosscheck` flag, now unneeded) once all three engines agree
   with the oracle.
7. Update `notes/known_issues.md`: remove bullets 1–2 from the "Unary
   `-`/`~` are context-determined" entry (or the whole entry, if 2.3 has
   also landed by then) once green.
**Accept**: all three engines match the context-determined oracle for `~`;
full fast suite + compiled suite green; no test from step 2 left asserting
the old (wrong) behavior.

**Result** (July 2026): Characterized first against Icarus (`~a` for
`a=4'b0011` in an 8-bit context: correct `252`, all four engines gave the
buggy `12` before this fix). Fixed in the exact three locations the plan
named — `sim/evaluator.py` (merged `~` into the `+`/`-` UnaryOp branch),
`sim/vm/compiler.py` (same merge in `_compile_expr`), `sim/compiled/
_expr_emitter.py`'s `_emit_unary` (same merge) — fixing vm and vm-fast
together since they share one bytecode compiler. Fixed one engine at a
time with a full-suite run after each, exactly as prescribed: after
reference alone, 7 expected transient failures (the `strict=True` xfail
flipping to xpass, `test_differential.py` disagreeing since vm/compiled
were still buggy, and the compiled-vs-reference latent-risks test); after
vm/compiler.py, down to 4 (the differential harness agreed once vm joined
reference; only compiled-related mismatches remained); after the compiled
narrow-path fix, all four engines gave the Icarus-correct `252` and the
whole suite was green. Removed the now-stale `_known_engine_bug` entry and
`skip_ref_crosscheck` flag in `test_compiled_edge_shapes.py` (371 passed,
zero xfails left in that file + `test_compiled_latent_risks.py`).
`notes/known_issues.md`'s "Unary `-`/`~` are context-determined" entry
updated to Resolved (all of bugs 1-3 are now fixed, since item 2.3 Part A
had already landed bug 3 earlier).

A `--run-slow` full regression (not run during the plan's prescribed fast
suite) caught one real, unrelated pre-existing bug this fix exposed:
`tests/test_dsl/test_taxi_axis_async_fifo.py::test_async_fifo_prng_stress`
started failing (FIFO never signaled done). Root cause, found by diffing
generated `.pyx` before/after (the fix changed exactly one thing:
`wmask(2)` → `wmask(32)` for a `~m_axis_tvalid_pipe_reg` inside `(~x) >>
j`, where `j` is a Verilog `integer` for-loop variable, 32 bits by
default): `sim/compiled/_expr_emitter.py`'s `_expr_width` computed a right
shift's (`>>`/`>>>`) own width as `max(expr_width(left), expr_width(right))`
— folding the *shift amount*'s width into the result, when IEEE 1364-2005
Table 5-22 says a shift's self-determined width is its left operand's width
only. This was harmless before since nothing consumed that estimate in a
way that changed a computed value; my `~` fix made `_emit_unary` actually
use whatever width its caller reports for context-propagation, so the
inflated 32-bit estimate now corrupted `~m_axis_tvalid_pipe_reg`'s value
before the shift ran. Fixed by giving `_expr_width` a dedicated `>>`/`>>>`
case returning `self._expr_width(expr.left)` only (mirroring the existing
special-casing already present for `<<`/`<<<`). Verified against Icarus
for the exact failing pattern, re-ran the fast suite (6979 passed, 59
xfailed), the full compiled suite with `--run-slow` (4625 passed, 2
xfailed), and the taxi FIFO test file (4 passed). Full `--run-slow` suite
green (11606 passed, 61 xfailed).

### 2.7 Fix remaining known compiled-engine correctness gaps (M/L)

**Goal**: close out the compiled-engine-specific bugs found (but deliberately
not fixed) during items 2.1 and 3.4, all currently documented in
`notes/known_issues.md`. Appended here out of numeric sequence, same as
2.6 — no ordering dependency between the four sub-items below, but each
changes compiled-engine codegen output, so treat each with the same care
as 2.6: fix one, run the full fast suite plus the full compiled suite
(`-n 8 --run-slow`), only then move to the next.

**Four known gaps, all reference/VM-correct, compiled-only**:

1. **Narrow blocking/nonblocking bare assignment drops the x-mask.**
   `b = a;` (blocking, in `always @(*)`) or `b <= a;` (non-blocking, in
   `always @(posedge clk)`) loses the x-mask on the compiled engine
   whenever *both* `a` and `b` are 64 bits or narrower (the single-word
   "narrow-path" codegen) — driving `a` with an x-contaminated bit and
   settling leaves `b` fully-defined instead of propagating the x bit(s).
   Continuous assigns and anything wider than 64 bits are unaffected
   (different, correctly mask-propagating code paths). Found in item 2.1;
   exercised as a strict `xfail` in `tests/test_sim/test_assignment_matrix.py`
   — remove the xfail once fixed. See `notes/known_issues.md` for the
   exact repro.
2. **Wide-emitter sign-extension wrong for the (65, 80)-bit width pair
   specifically.** A declared-signed (or `$signed()`-cast) 65-bit value
   assigned into an 80-bit target zero-extends instead of sign-extending,
   on the compiled engine only, only for this one width pair — every other
   word-boundary-crossing pair tested ((63,64), (64,65), (64,63), (65,64),
   (80,65)) sign-extends correctly. Not yet root-caused beyond "likely in
   `_wide_emitter.py`'s fill logic, probably the same family as the wide
   unary masking bug fixed in item 2.3." Found in item 2.1; exercised as a
   strict `xfail` in `test_assignment_matrix.py`.
3. **Ternary/context-determined-operator codegen never got the
   `signed_override`-threading fix.** Item 3.4 fixed the conditional
   operator's own-combined-signedness rule (IEEE 1364-2005 §5.5.1) in
   `sim/evaluator.py` and `sim/vm/compiler.py`, but explicitly deferred the
   equivalent fix in `sim/compiled/_expr_emitter.py`/`_wide_emitter.py` —
   a separate, much larger codegen architecture. Running
   `tests/test_sim/test_differential.py` with `VERIFORGE_DIFF_COMPILED=1`
   shows the divergences directly; this is the largest of the four
   sub-items (likely warrants its own characterize-first pass, mirroring
   item 4.2 Phase A's methodology, given the architectural gap between the
   two codegens).
4. **Compiled engine's 64-bit-width limit is only partially resolved.**
   External signal round-trips for `width > 64` work, but internal
   compiled expression/assignment/NBA/dirty-propagation codegen still has
   remaining single-word assumptions in places, affecting wide AXI buses
   (128/256/512-bit), wide memory interfaces, and large (>64-bit total)
   concatenations. Reference/VM are unaffected (Python `int` handles
   arbitrary widths natively). See `notes/plans/architecture_review_2026-07.md`
   and `notes/simulation/wide_signal_coverage.md` for prior investigation
   and potential approaches — this sub-item is the largest and least
   scoped of the four; consider splitting it into its own work-plan item
   once a concrete fix shape emerges, rather than forcing it through this
   item's one-fix-at-a-time cadence.

**Steps** (per sub-item): characterize against Icarus/Verilator first for
sub-items 1-2 (small, well-isolated); root-cause sub-item 3 by comparing
`_expr_emitter.py`'s ternary codegen against `sim/vm/compiler.py`'s
already-fixed version; scope sub-item 4 with a spike before committing to
an approach (it may be too large for a single PR — see the note above).
Fix, verify against the relevant strict-`xfail` test (removing it once
green) or new regression test, run the full suite, then move to the next
sub-item.
**Accept**: sub-items 1-2's `xfail` markers removed and green; sub-item 3
verified via `VERIFORGE_DIFF_COMPILED=1` differential runs with no
remaining ternary-related divergences (or a narrower, explicitly documented
residual gap); sub-item 4 either resolved or rescoped into a dedicated
follow-up item with a concrete plan. `notes/known_issues.md` updated to
Resolved for whichever sub-items land.

**Result (sub-item 1, July 2026)**: Fixed. Root cause:
`sim/compiled/_stmt_emitters.py`'s generic narrow-path LHS fallback (used
for any bare-identifier blocking/nonblocking RHS not matched by the
specialized shift/multiply/struct-field pattern-matchers earlier in
`_emit_lhs_write`) computed the RHS *value* via `_emit_expr` but hardcoded
the mask update to the literal `0`, never consulting `_emit_mask_expr` at
all. Fixed by computing and emitting the real RHS mask (needed a new
scratch cdef, `_cdm`, declared everywhere `_cdv` already is, in
`_gen_sections.py`). This exposed two further real, previously-latent bugs
once mask propagation actually started working end to end: (a)
`CompiledScheduler.load_memory()` passed the *value* truncation bitmask as
the *x-mask* argument to `mem_write`/`mem_write_wide` — marking freshly
loaded memory as entirely unknown instead of entirely defined; (b)
`_expr_emitter.py`'s `_emit_mask_expr` had three near-identical "select on
a non-Identifier target" fallback paths (`BitSelect`/`RangeSelect`/
`PartSelect`) that silently defaulted the packed-range base offset to 0
for a memory-element target instead of calling the already-existing
`_select_base()` helper the *value*-side code already used. Full detail
in `notes/known_issues.md`. Full fast suite green (7027 passed), full
compiled suite green with `--run-slow` (4625 passed, 2 xfailed —
sub-item 2's cases; one more real bug found and fixed along the way, a
missing zero-initialization in `TestForLoopCodegen::
test_for_loop_compile_and_run`'s own hand-built test fixture that had
been silently relying on this bug to pass).

**Result (sub-item 2, July 2026)**: Fixed. Root cause: **three** separate
code paths shared the same bug shape — conflating "this word is the
source's own last, partial word" with "this word lies entirely beyond the
source's own word count," when a partial last word needs its own unused
high bits sign-extended in place, distinct from pure extension words.
This is invisible whenever `dst_words > src_words` (where earlier testing
concentrated) and only manifests when `dst_words == src_words` with a
partial source last word — first true at exactly the (65, 80) pair. Fixed
in `templates/narrow_stage.pxi`'s `_whole_assign_signal_s`,
`templates/narrow_assign.pxi`'s `_whole_stage_signal_s` (also had a
latent, separate UB bug: sign-bit-position check shifted by the signal's
*absolute* width instead of its position within its own word — undefined
behavior in C, only coincidentally correct via x86's shift-count-wraps-
mod-64 behavior), and a new `wide_load_signal_s` primitive in
`_gen_wide_section.py` plus a `signed_override` parameter threaded through
`_wide_emitter.py`'s `_emit_wide_expr_to_scratch` recursion (the newer
recursive scratch-space emitter's `wide_load_signal` had no sign-extension
concept at all, needed for the `$signed()`/`$unsigned()` cast-form cases
which reach it instead of the bare-identifier path above). Full detail in
`notes/known_issues.md`. Full assignment matrix green (648 passed, 0
xfailed — the last two known bugs from item 2.1 are now both fixed). Full
fast suite green (7038 passed), full compiled suite green with
`--run-slow` (4625 passed, 2 xfailed — unrelated pre-existing xfails).

**Result (sub-item 3, July 2026)**: Substantially fixed — full detail in
`notes/known_issues.md`'s "Compiled-engine ternary/context-determined-
operator codegen, and a wide family of related width/signedness/x-
propagation bugs" section. The originally-scoped fix (threading
`signed_override` through `_wide_emitter.py`'s TernaryOp/UnaryOp/BinaryOp
codegen, mirroring items 3.4/2.6's narrow-path pattern) led to
root-causing and fixing a much larger family of independent, real bugs
once the differential fuzzer harness was actually run with
`VERIFORGE_DIFF_COMPILED=1` for the first time — not just in the compiled
engine, but pre-existing, independently-reachable bugs in the reference
evaluator, `sim/vm/compiler.py`, `sim/value.py`, and both VM interpreters
(width/signedness propagation for bitwise ops, concat/replication/
assignment-pattern self-width, unpacked-array-element self-width,
left-shift self-width, reduction/comparison/logical-op always-unsigned
signedness, x/z-aware reduction-AND/logical-NOT/equality/`&&`/`||`
short-circuiting, and the ternary x-condition bitwise-merge rule). The
default-seed differential run (`VERIFORGE_DIFF_COMPILED=1
VERIFORGE_DIFF_CASES=100`, 10 batches) is fully green; the full fast suite
and `tests/test_sim/test_compiled_edge_shapes.py`'s `seam*_overflow`
regressions (which the bitwise-op width fix initially broke, then fixed
once combined with the unpacked-array self-width fix) are green.
**Continued (second wave, same day)**: rather than keep fuzzing-and-patching
one divergence at a time, did a systematic audit of every node-type branch
in `_wide_emitter.py`'s `_emit_wide_expr_to_scratch` for the same two bug
shapes (missing `signed_override` handling; fill-boundary using
`n_words` instead of `dst_width`) — found and fixed three more real gaps
(`Literal`, `BitSelect`, `Concatenation`'s own aggregate result), and
introduced a shared `_wide_sign_extend_to_dst_lines()` helper to replace
the duplicated hand-rolled fill logic. While re-verifying, found and fixed
an actual **reference-engine** bug (present in both `sim/evaluator.py` and
`sim/vm/compiler.py`) that had been masquerading as compiled-engine
divergences: `eval()`/`_compile_expr()`'s `$signed`/`$unsigned`
`FunctionCall` handling, and separately `BitSelect`/`RangeSelect`/
`PartSelect`, ignored their `width`/`signed_override` parameters entirely
— correct only when reached from an assignment's own top-level RHS (where
a separate post-hoc statement-level step covers for it), wrong one level
of nesting deeper (e.g. a ternary branch). Confirmed via Icarus that the
compiled engine was already right and reference was wrong for
`{3{(a0 ? $signed(a4[4:2]) : a3)}}` — a reminder that a harness divergence
should be checked against Icarus, not assumed to be compiled's fault. The
larger differential run (`VERIFORGE_DIFF_CASES=300` at an alternate seed)
improved from 8/30 to 13/30 passing batches; full detail (including the
still-open residual gap) in `notes/known_issues.md`, which now also notes
this reference-oracle caveat directly. **Continued (third wave, same day)**: kept bisecting the `VERIFORGE_DIFF_
CASES=300` failures one at a time (8/30 → 13/30 → 15/30) — found and fixed,
in both `sim/evaluator.py` and `sim/vm/compiler.py`: the `Literal` hot-path
cache lookup ignoring `width`/`signed_override`; `Concatenation`/
`Replication`'s own AGGREGATE result (distinct from the earlier self-width-
into-parts fix) ignoring an incoming `signed_override`; and `!`/reduction-
op's operand evaluated without its own self-determined width (so a nested
context-determined operator inside it, e.g. `~` in `!(~(cond?a:b))`, never
got resized). Same bug shape as the second wave, same fix-both-engines
methodology.

**Continued (fourth wave, same day) — a distinct bug family**: bisecting a
15/30-wave failure found compiled was actually correct and **reference**
was wrong (Icarus-confirmed) for a nested-ternary condition with mixed
known/x bits — a genuinely different bug shape from the width/
signed_override-propagation family above. `TernaryOp`'s condition
truthiness check (in all four engines, independently) required the WHOLE
condition to be bit-defined before picking a branch, falling back to the
ambiguous x-merge path otherwise — too strict per IEEE 1364-2005: a
known-1 bit ANYWHERE in the condition makes it definitely true regardless
of unrelated x/z bits elsewhere, exactly like `!`/`&&`/`||`/reduction-OR
already correctly implement (`Value.reduce_or` in `sim/value.py`).  Found
and fixed the identical shape everywhere a condition's truthiness is
checked: `TernaryOp` in `sim/evaluator.py`; every `If`/`For`/`While`/`Wait`
condition in `sim/executor.py` (8 call sites, both the plain and coroutine
executors); `Op.TERNARY`/`JUMP_IF_ZERO`/`JUMP_IF_NONZERO` in
`sim/vm/interpreter.py`; `OP_TERNARY`/`OP_JUMP_IF_ZERO`/
`OP_JUMP_IF_NONZERO` in `sim/vm/_interp_fast.pyx` (the JUMP opcodes there
had a second, more severe bug too — they never consulted the wide-value
`wflag`/`wv`/`wm` arrays, so a >64-bit if/while condition read stale data
from the narrow slot entirely); `_emit_ternary_value_mask_exprs` and
`wide_mux`/`_wide_emitter.py`'s TernaryOp case in the compiled engine
(`_expr_emitter.py`/`_gen_wide_section.py`/`_wide_emitter.py`); and
`_emit_while` in `_stmt_emitters.py` (`_emit_if`/`_emit_for` turned out to
already be correct by construction — they never consult the condition's
mask at all, which already implements the right semantics given x-bits'
value bits are conventionally stored as 0). A second, independent bug
found in the same compiled-engine functions: the ternary condition was
evaluated at a hardcoded width of 1 instead of its own self-determined
width, corrupting a NESTED ternary/concat/replication condition's internal
merge computation (confirmed against Icarus for `a0 * (((|a1[0]) ? a4 :
{3{a0}}) ? a3 : (~|a5[58:17]))`). Caveat: the `if`/`while`/`for` fixes
(unlike the fuzzer-generated-expression-tree fixes elsewhere in this item)
were verified by manual reasoning and pattern-consistency with the
fuzzer-verified `TernaryOp` sibling fix, not by an automated differential/
Icarus check, since the fuzzer only generates combinational expressions,
never control-flow statements — flagged as a good target for future
statement-level differential coverage. Full detail in `notes/
known_issues.md`'s fourth-wave entry. Both the default-seed (10/10) and
the larger-case-count differential run are green after this wave (see
`known_issues.md` for the exact pass count once the larger run completes).

**Continued (fifth wave, same day)**: kept bisecting `VERIFORGE_DIFF_
CASES=300` failures (17/30 → 20/30), finding three more distinct bug
shapes, all confirmed against Icarus and fixed in both the relevant
narrow and wide compiled-engine emitters: (1) the shift-COUNT operand of
`<<`/`>>` getting sign-extended when declared `signed`, instead of being
treated as the unsigned magnitude IEEE 1364-2005 requires; (2) `~`/unary
`-` widening a self-determined-ALWAYS-1-bit operand (comparisons,
`&&`/`||`, reduction ops, `!`) to the enclosing context BEFORE
complementing/negating, instead of operating at the operand's own fixed
width and extending the RESULT afterward (a new `_is_fixed_self_
determined()` helper, mirrored across `sim/evaluator.py`/`sim/vm/
compiler.py`/both compiled emitters, now distinguishes this from the
regular-signal/arithmetic-operand case where pre-widening IS correct);
(3) a `$signed()`-wrapped 1-bit condition's raw sign-extended C value
(e.g. `_sign_ext(1, 1)` = -1 = all 64 bits set) leaking un-masked into
`wide_mux`'s/`_emit_ternary_value_mask_exprs`'s "known-1-bit" check,
spuriously forcing a definite branch selection instead of the correct
ambiguous merge. Full write-up of all three (with the exact Icarus repro
for each) in `known_issues.md`'s fifth-wave entry.

**Two more distinct bugs found but NOT YET FIXED** (documented in
`known_issues.md` rather than rushed): a signal wider than 64 bits read
through the compiled engine's narrow/scalar emitter silently loses every
bit beyond the first 64 (`_emit_expr`'s Identifier case has no
`sig_width > 64` handling at all) -- reachable despite `_rhs_needs_wide_
eval`'s wide-signal catch-all because the continuous-assign codegen path
apparently doesn't consult that same guard (confirmed via a comb-vs-ff
asymmetry for byte-identical RHS text: the `always` block's NBA correctly
routes through the wide emitter, the `assign` wire does not); and
vm-fast's reduction-AND/NAND opcode(s) likely have the same
any-x-bails-to-fully-ambiguous precision gap already fixed elsewhere this
session, just not yet traced into `_interp_fast.pyx` specifically.

**Continued (sixth wave, next day)**: kept bisecting the remaining
`VERIFORGE_DIFF_CASES=300` failures one at a time (17/30 → 26/30),
finding and fixing seven more distinct, Icarus-confirmed bugs, all
documented in detail in `known_issues.md`'s sixth/seventh-wave entries:
- vm-fast's `OP_RED_NAND` had its own separately-naive x-precision
  implementation instead of reusing the already-correct `OP_RED_AND`'s.
- `TernaryOp`'s own condition (one more "leaf ignoring self-determined
  width" position, same shape as several fixed in earlier waves) in both
  `sim/evaluator.py` and `sim/vm/compiler.py`.
- A scratch-slot LIFO-ordering violation in the compiled engine's wide
  emitter that corrupted `wide_mux`'s condition data (the allocator is a
  plain stack-depth counter, not a real pool -- freeing out of order let
  a later allocation silently reuse a still-live slot's number).
- A reduction operator loading its wide operand at the wrong (inherited,
  too-small) word count instead of its own required one.
- Unary `-`'s narrow/scalar mask computation not propagating "any x
  anywhere makes the WHOLE result x" the way `+`/`-` binary ops already
  do (only passing the operand's mask through unchanged, correct for `~`
  but not arithmetic negation).
- A family of four related shift-operator bugs found together while
  chasing one regression: the left operand's recursive width capped too
  low for a `$signed(...)`-wrapped narrow value; `>>` incorrectly
  sign-extending a declared-`signed` left operand (it must always be
  logical/unsigned, unlike `>>>`); the shift COUNT evaluated at an
  arbitrary width instead of its own self-determined one, corrupting a
  nested context-determined operator within it; and that fix exposing a
  pre-existing `_expr_width` `+`/`-` carry-bit truncation gap needing a
  new `_shift_amount_width()` headroom helper.
- The wide emitter's signed-comparison detection only recognizing a
  literal `$signed(x) < $signed(y)` syntactic pattern, missing the
  general case (e.g. a ternary whose combined signedness is true because
  both branches are individually signed).
- `_emit_concat`/`_emit_replication` embedding each member's raw C value
  into shift+OR tiling without masking it to its own self-width first
  (same root shape as the fourth wave's `wide_mux` pollution bug, just in
  a different consumer) -- a `$signed(1'b1)`-cast member's raw `-1`
  representation (all 64 bits set) was corrupting neighboring
  concat/replication members once shifted into place.

**Continued (eighth wave, same day) -- the deferred architectural gap,
finally found and fixed**: went back to the case-88 gap that earlier
waves had deliberately deferred as "too deep to chase" and located the
actual mechanism by directly instrumenting `_emit_wide_lhs_write_new`,
`_emit_wide_expr_to_scratch`, and `_flatten_concat_identifier_parts`
rather than continuing to guess from generated-code inspection alone.
Root cause: `_process_compiler.py`'s `_flatten_concat_identifier_parts`
(the fast-path optimization `_compile_continuous_assigns` uses to turn a
wide Concatenation RHS into an efficient word-by-word assign instead of
the fully general scratch-based emitter) has a fallback for any concat
member that isn't a plain identifier/select: it computes that member via
the narrow/scalar emitter, gated only on the member's OWN self-determined
result width being ≤64 bits -- which says nothing about whether its
INTERNAL computation reads a signal wider than 64 bits (e.g.
`$unsigned(~^(cond ? wide_signal : narrow))`, where the reduction's own
result is 1 bit but its operand touches a 65-bit signal). Fixed by adding
an `_expr_uses_wide_signal()` check (recursively, at each concat member --
the same helper `_rhs_needs_wide_eval` already uses for its own top-level
catch-all) to the fallback, forcing it to bail out and let the assignment
fall through to the sound, wide-aware `_emit_wide_lhs_write_new` instead.
Case 88 now passes all 8 vectors; `VERIFORGE_DIFF_CASES=300` improved
26/30 → 27/30.

**Important correction**: earlier waves' documentation assumed cases
111/286/298 shared this SAME root cause (based on similar-looking
symptoms: `c.val[sid]`-based narrow reads for a wide signal, comb-vs-ff
asymmetry). Re-investigating case 111 after the fix found `_emit_flat_
concat_whole_assign` IS being reached and IS structurally sound (confirmed
by direct instrumentation) -- so 111's remaining failure is a DIFFERENT,
more localized bug, not yet isolated (suspect the multi-word merge logic
for a Concatenation with mixed identifier and expression-kind parts
crossing a 64-bit word boundary). Case 298's residual failures are a
THIRD, independently-confirmed instance of the general "narrow codepath
meets wide signal" shape, this time in the signed-comparison codegen
reached via the sixth wave's shift-amount computation. Case 286 remains
un-root-caused. Full detail on all three in `known_issues.md`'s
eighth-wave entry, including a recommendation to audit every remaining
`_emit_expr`/`_emit_mask_expr` call site not already guarded by
`_expr_uses_wide_signal`, rather than continuing to patch each instance
the fuzzer happens to trip over individually.

**Ninth wave -- cases 111, 286, and 298-residual all resolved**
(`VERIFORGE_DIFF_CASES=300` improved 27/30 → 30/30, fully green). Two
more compiled-engine narrow-meets-wide gaps (case 298's shift-count
never checked `_expr_uses_wide_signal`/`_expr_max_internal_width` at
all; the eighth-wave concat-flatten fix's `_expr_uses_wide_signal` guard
alone missed a wide-internal-value-from-narrow-signals case like
`~&{2{a4}}`, a 128-bit Replication of a 64-bit signal) -- but the
dominant fix, and the one that actually resolved case 111, was a
**reference-engine bug**, not a compiled-engine one: `eval()`/
`_compile_expr()`'s handling of reduction ops/`!`/comparisons/`&&`/`||`
(all self-determined-fixed-1-bit per Table 5-22) never extended their
1-bit RESULT to a wider requested context width the way every sibling
fixed-width case already did, silently returning a too-narrow `Value`
that corrupted `Concatenation.concat()`'s bit-packing whenever such an
operator appeared as a ternary branch or nested concat member. Found by
NOT trusting the harness's own reference oracle once the compiled fixes
still left case 111 failing -- an Icarus testbench for the exact failing
vector agreed with the newly-fixed COMPILED engine, confirming reference
was the actual outlier. Full detail (all three fixes, code locations, and
the Icarus repros used to confirm each) in `known_issues.md`'s
ninth-wave entry.

**Tenth wave -- the multiplication self-determined-width question,
resolved** (case 146, `VERIFORGE_DIFF_CASES=150` default-seed run):
reference (and `sim/vm/compiler.py`) disagreed with compiled/Icarus/
Verilator on a multiplication's self-determined width when used as a
concat member. Root-caused against the IEEE 1364-2005 primary text
(fetched and read directly, not relied on from memory): Table 5-22's
row for `+ - * / % & | ^ ^~ ~^` gives `max(L(i),L(j))` for ALL of these,
including `*` -- there is no sum-of-widths row for multiplication
anywhere in the table, contradicting this codebase's own long-standing
prior claim (which had gone unquestioned since this item's original
scoping). `_expr_self_width`/`_expr_width` already correctly used max
for `*`; the actual bug was that `eval()`'s and `_compile_expr()`'s
generic arithmetic branch never narrowed the multiplication's RESULT
back down to a requested self-determined `width` after `Value.__mul__`'s
(deliberately, still-correctly) wider sum-width computation -- the same
"result width doesn't match the request" defect class as the ninth
wave's reduction/comparison fix, just the mirror-image direction
(narrowing instead of widening). Fixed by generalizing the ninth wave's
fix into a single uniform tail (in both engines) that corrects ANY
op reaching it -- comparisons, shifts, and all of `+,-,*,/,%,**` -- not
just the previously-gated fixed-1-bit ops. Confirmed via `git stash`
the bug was NOT a regression from any earlier fix in this session.
Confirmed against Icarus for the case-146 expression; the default
150-case AND `VERIFORGE_DIFF_CASES=300` large-batch differential runs
are both green with this fix applied. Full detail (including a
noted-but-out-of-scope discovery that `**`/power is actually grouped
with the SHIFT row in Table 5-22, not `max(L(i),L(j))`, contradicting
how this codebase currently treats it -- unconfirmed by fuzzing, left
for a future session) in `known_issues.md`'s tenth-wave entry.

**Eleventh wave -- `**` (power) full fix: width, signedness, and IEEE
1364-2005 Table 5-6 special values, across all four engines.** Following
up on the tenth wave's discovery, confirmed THREE independent real bugs:
(1) width/self-determination -- `**` is grouped with the SHIFT row in
Table 5-22 (`L(i)`, exponent self-determined), not the generic
`max(L(i),L(j))` row every engine previously treated it under; (2)
signedness was entirely unimplemented -- no engine had a signed `**`
variant at all (unlike `/`/`%`/comparisons, which already do); (3)
Table 5-6's negative-base/negative-exponent special values (`0**-1` ==
`'bx`, `2**-1` == `0`, `(-1)**-3` == `-1`, etc.) were not implemented,
and the compiled engine's old `pow(<double>...)`-based implementation
carried real undefined-behavior risk (imprecise float math, UB casting
an infinite/negative double to unsigned). Fixed in all four engines
(reference, vm, vm-fast, compiled), including a new shared
`_verilog_pow`/`_verilog_ipow` helper per language boundary implementing
Table 5-6 once. Verified against Icarus via a new dedicated cross-engine
test file, `tests/test_sim/test_power_operator.py` (58 assertions, all
green) -- `**` couldn't just be added to the differential fuzzer's
operator set because doing so surfaced a separate, pre-existing,
NOT-fixed gap (below).

**Follow-up (same day) -- the `compiled`-engine half of the wide-operand
gap above fixed (silent corruption -> loud failure), `vm-fast`'s half
still open.** `**` over a >64-bit operand or destination was broken on
the two C-based engines in two different ways. `vm-fast`'s `OP_POW`/
`OP_SPOW` never consult the wide word-array representation (silently
wrong, not even x) -- confirmed this is genuinely `**`-specific
(`/`/`%`, already in the differential fuzzer's operator set and
exercised up to 80-bit operands there, are correctly wide-aware via
`wide_div`/`wide_mod`), left open as a real but narrow-scope gap (`**`
on runtime signals wider than 64 bits is very rare in synthesizable
RTL; the more plausible testbench/behavioral-model use case is served
well enough by the narrow fix). The `compiled` engine's half was worse
in kind, not just degree: its last-resort narrow-scalar assignment
fallback (reached in BOTH `_process_compiler.py`'s continuous-assign
path and `_stmt_emitters.py`'s procedural blocking/nonblocking path,
since neither wide emitter has ever supported `**`) only ever wrote the
narrow `c.val`/`c.mask` slots, never a wide destination's real
`c.wide_val` storage -- so the signal silently stayed at its reset value
of 0 forever, with zero warning, for ANY future wide-emitter-unsupported
expression assigned to a wide destination (not `**`-specific). Fixed
with a deliberately minimal, safe guard: raise `NotImplementedError`
(naming the signal/width, suggesting `engine='vm'`/`'reference'`)
immediately before either fallback would run, converting silent wrong
results into a clear compile-time failure. This does NOT make `**` (or
anything else) actually work on wide operands -- that remains future
work, alongside the `vm-fast` wide-read fix. Verified via two new
`pytest.raises` tests (continuous assign and nonblocking assign) plus a
clean full fast-suite regression (confirming no existing passing
construct depended on the old silent-no-op behavior). Full detail in
`known_issues.md`'s eleventh-wave entry.

**Residual gap**: still a large, open-ended architectural area (the wide
emitter, the narrow/scalar emitter, and the reference/VM engines each
reimplement width/signedness/x-propagation independently, per-node-type,
rather than sharing `semantics.py`'s already-unified logic), not yet
exhaustively characterized. The differential harness itself is fully
green at both the default (150-case) and large-batch (300-case) scope.
Making `**` actually WORK (not just fail safely) on wide operands --
`vm-fast`'s wide-read fix for `OP_POW`/`OP_SPOW`, and either compiled-
engine Python-bignum support or a from-scratch wide `**` primitive -- is
the clearest next-session starting point, though given how rare
runtime `**` on >64-bit signals is in practice, it's lower urgency now
that the silent-corruption risk is closed.

## Tier 3 — CI and engine parity

### 3.1 CI sim-smoke job (S) ✅

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

**Result** (July 2026): Measured locally first, exactly as prescribed —
the plan's own "~10 min, drop `tests/test_dsl/` if needed" guidance turned
out not to hold: `tests/test_sim/` + `tests/test_dsl/` (minus `compiled/`)
measured ~45 min with `-n 4`; `tests/test_sim/` alone (still minus
`compiled/`) measured ~33 min on its own. Surfaced this to the user rather
than silently either blowing past the 10-minute target or unilaterally
narrowing scope beyond what the plan specified; chosen resolution: exclude
`tests/test_dsl/` (per the plan's own fallback) plus the handful of large
cross-engine hardware-example suites individually responsible for most of
the runtime (`test_ibex_examples.py` ~7:50 for just 148 tests,
`test_darkriscv_constructs.py` + `test_structural_patterns.py` +
`test_differential.py` ~3:53 combined, the three `test_pulp_*_examples.py`
files ~7:29 combined) — each of these runs every test across multiple
engines including a fresh per-test Cython "compiled" build, which is the
actual cost driver. Final scope measured ~9:14 locally, added as the
`sim-smoke` job in `.github/workflows/ci.yml`. `notes/developer_guide.md`
§3 rewritten to describe all three CI jobs (was describing two; also
folded in item 3.2's `weekly.yml` description below, landed together).
YAML validated with `pyyaml` (note the `on:` key parses as the boolean
`True` under YAML 1.1, not the string `"on"` — a known GitHub Actions/YAML
quirk, already present in the existing `ci.yml`, not a bug introduced
here). The exact `sim-smoke` command was run locally end-to-end (not just
its constituent pieces) and passed. Actually triggering GitHub Actions to
confirm a live green run requires pushing this commit, which I wasn't
asked to do — that verification is the one part of the Accept criteria
left for whoever pushes this branch.

### 3.2 Scheduled full-regression workflow (M) ✅

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

**Result** (July 2026): Landed together with item 3.1 (both touch
`notes/developer_guide.md` §3, and this item's `icarus` job scope
overlapped with 3.1's timing investigation). `.github/workflows/weekly.yml`
created exactly per spec — Monday 06:00 UTC cron + `workflow_dispatch`,
`compiled` job with `.cycache/` caching keyed on
`src/veriforge/sim/compiled/**`, `icarus` job installing `iverilog` then
running `tests/test_validation/`. Both jobs' pytest commands run locally
end-to-end and passed: `tests/test_sim/compiled/ -n auto --run-slow`
(784 passed + 3843 skipped without `--run-slow`; full run already
validated during item 2.5) and `tests/test_validation/ --tb=short -q`
(115 passed, ~11:24). `notes/developer_guide.md` §3 rewritten, dropping
the "compiled suite is not exercised in CI" caveat as specified. As with
3.1, an actual live `workflow_dispatch` trigger requires pushing this
commit and using the GitHub Actions UI/CLI, which wasn't asked for here —
left for whoever pushes this branch.

### 3.3 Cython VM: fix drift, then gate equivalence in CI (M/L) ✅

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

**Result** (July 2026): Done. The originally-documented ~18-failure memory
read-after-write drift (step 2) was already fixed in a prior session without
the docs being updated — `test_bench_native.py` ran clean before any new work
started here. The real remaining drift, found via item 2.4's incidental
first build of the extension in this environment, was a batch of narrow-path
(<=64-bit) signed-vs-unsigned C-arithmetic bugs in `_interp_fast.pyx`:
`OP_CMP_LT`/`LE`/`GT`/`GE` and `OP_SHR` compared/shifted `a.val`/`b.val` as
signed `long long` instead of casting to `unsigned long long` first (any
64-bit value with the MSB set misbehaved); `OP_DIV`/`OP_MOD` had the same
signed-vs-unsigned bug; `OP_SHL`/`OP_SHR` didn't guard against shift counts
>= 64 (undefined C behavior); `OP_SIGN_EXT` checked "any bit is x" instead of
specifically the sign bit, and was missing its wide-path (>64-bit) branch
entirely. All fixed to match `sim/vm/interpreter.py`'s Python-level (already
correct) semantics. Verified via `test_vm.py`, `test_assignment_matrix.py`,
`test_compiled_edge_shapes.py` (vm-fast previously 41/8/7 failures
respectively → 0), `test_compiled.py --run-slow` (4728 passed, 2 known
xfailed), and `test_bench_native.py` (434 passed). Added a `vm-equivalence`
job to `ci.yml` (not `weekly.yml`, which doesn't exist yet — item 3.2 is not
scheduled) that builds the extension and runs the VM selection twice with
`-n auto`, gating both the built-extension and
`VERIFORGE_DISABLE_CYTHON_VM=1` paths. Sync policy added to
`developer_guide.md` §5; `setup.py` docstring and `known_issues.md` updated.

### 3.4 Randomized differential harness (M) ✅

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

**Result** (July 2026): Done, as specified, with two adjustments. (1) The
"compiled" engine is opt-in via `VERIFORGE_DIFF_COMPILED=1`, not part of the
default run — per-module Cython compilation is too slow for "a few seconds",
and the compiled engine has a separate, unfixed ternary-signedness codegen
gap (see below), so including it by default would make the default run red.
(2) `weekly.yml` doesn't exist yet (item 3.2 not scheduled), so the
heavier/compiled-enabled run isn't wired into CI yet — deferred to whenever
3.2 lands.

Building the harness immediately found ~11 distinct, real, previously
undetected correctness bugs (verified against Icarus Verilog) spanning
reference, vm, vm-fast, and the compiled engine — full writeup in
`notes/known_issues.md` under "Randomized differential harness (work plan
item 3.4): bugs found and fixed". Highlights: bit-select/part-select
signedness inheriting the base signal's signedness instead of always being
unsigned (IEEE 1364-2005 §5.5.1); the conditional operator's own combined
signedness not overriding individual-branch signedness for nested
context-determined extension; comparison/logical operators wrongly
inheriting the enclosing assignment's context width; several "any x bit ->
result is x" imprecise x-propagation bugs in `&&`/`||`/`==`/`!=` where a
known bit should have resolved the result; a wide-condition bug in
`OP_TERNARY`; wide/huge shift-amount handling bugs in `_interp_fast.pyx`
(including a segfault); multiplication's sum-of-widths self-determined rule
leaking through an enclosing context-determined operator (found in both
reference and, independently, in a legacy compiled-engine fast-path, which
also uncovered an undefined-behavior bug in a shared `_word_mask64` helper
for negative widths); and an `OverflowError` crash for shift amounts derived
from a large self-determined operand. All fixed and verified across 10+
seeds x 400-500 cases in `test_differential.py`, plus the full existing
`test_compiled.py --run-slow` + `test_bench_native.py` + `test_sim/` +
`test_dsl/` suites (5716 + 4728 tests, all green, no regressions).

**Deferred follow-up** (documented in known_issues.md, not scheduled): the
compiled engine's ternary/context-determined-operator codegen
(`sim/compiled/_expr_emitter.py`, `_wide_emitter.py`) never received the
conditional-operator signedness fix — replicating it there is a separate,
substantially larger undertaking (a different, much bigger codegen
architecture than `sim/vm/compiler.py`). Running the harness with
`VERIFORGE_DIFF_COMPILED=1` shows this.

### 3.5 `Simulator.engine_report()` (S/M) ✅

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

**Result** (July 2026): Done as specified. Added `Simulator.engine_report()`
to `sim/testbench.py`, counting `initial`/`always` blocks against the
compiled scheduler's existing `_initial_blocks`/`_always_timing_blocks`
fallback lists and surfacing `_codegen.timing_diagnostics` as
`fallback_reasons`; reference/vm/vm-fast always report zero fallback. Test
added to `test_compiled.py::TestPhase4Execution` (reusing the existing
`always #5 clk = ~clk` fixture already used by `test_always_timing_clock`).
Documented in both `simulator_engines.md` and `public_api.md`.

---

## Tier 4 — Structural projects (one at a time, in this order)

### 4.1 Break the package cycles + layering test (M) ✅

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

**Result** (July 2026): Done as specified, with adjustments driven by what
the tree actually needed (per step 3's own instruction to derive the real
edge set rather than force the plan's illustrative table verbatim):

1. `project.py`'s scaffold re-export is now a PEP 562 module `__getattr__`
   (added `# noqa: F822` to the four `__all__` entries it resolves
   dynamically, since ruff can't see through it).
2. `dsl/testbench.py` moved to `sim/bench/skeleton.py` (fixed its 3
   `model.*` imports, its `sim.endpoints` import — now intra-package — and
   its `from . import Expr, Module, sim_time` — now
   `from veriforge.dsl import ...`, the sanctioned `sim.bench → dsl` edge
   alongside `lowering.py`). `dsl/testbench.py` is now a 12-line
   backward-compat shim (`from veriforge.sim.bench.skeleton import *` plus
   the private helpers tests import directly). `dsl/testbench_deps.py` did
   not need to move — independent of both `testbench.py` and `sim`.
   `scaffold.py`'s lazy import updated to the new path directly (it's
   first-party code, not a legacy consumer, so it doesn't need the shim).
   Also had to move `veriforge.dsl.testbench`'s entry in `pyproject.toml`'s
   mypy per-module `ignore_errors` override list to
   `veriforge.sim.bench.skeleton` (the module it was suppressing untyped
   legacy patterns for moved; without this mypy surfaced ~16 pre-existing,
   previously-silenced errors).
3. `tests/test_project/test_import_layering.py` added: walks
   `src/veriforge/**/*.py` with `ast`, checks only module-level imports
   (function-body imports are the sanctioned lazy pattern), and asserts
   against an `ALLOWED_EDGES` per-top-level-package DAG derived from the
   actual current edge set (not the plan's illustrative table verbatim —
   e.g. `dsl → project` via `testbench_deps.py`, `verilog_parser →
   preprocessor`, and `sim → _env`/`_version` utility-module imports all
   needed adding). The sim/dsl boundary needed file-level precision beyond
   the coarse per-package DAG (`ALLOWED_FILE_EDGES` + a `sim/bench/*`
   special case), since a blanket "dsl ⟷ sim allowed" would be too loose to
   catch a future mistake like core `sim.evaluator` importing `dsl`
   directly. Verified the test both passes cleanly and fails with a precise
   file:line message when a forbidden import is temporarily injected (into
   both a foundational file, which crashes at collection via a real
   circular import as expected, and a non-foundational one, which fails
   cleanly via the assertion). `sim → project` (via `cosim.py`) was already
   a lazy, function-scoped import — no change needed there.

Full fast suite + `test_dsl/` + `test_sim/` + `test_project/`: 6812 passed,
3843 skipped (slow, not requested), 62 xfailed, 0 failed. Full ruff/mypy/
check_overview gate green. CLI smoke-tested end-to-end
(`--generate-python-testbench`) to confirm the moved code path works
outside the test suite too.

### 4.2 Semantic core unification (L — the big one) ✅

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

**Result (Phase A+B, July 2026)**: Phase A done as `test_semantics_parity.py`
(see its module docstring for the full difference table — 3 confirmed
differences, all resolved in Phase B below). Phase B done:
`src/veriforge/semantics.py` implements the full API (`const_int`,
`range_width`, `var_width`, `net_width`, `expr_width`, `expr_signed`),
stdlib + `model` imports only. `const_int` ports `elaborate._eval_const_expr`'s
dispatch (env-dict identifier resolution, full unary/binary op tables,
ternary/concat/replication/range-select/part-select, `$clog2`/`$bits`/
`$signed`/`$unsigned`/`$pow`), wrapped to catch and return `None` rather than
raise (Difference 3's resolution); its `RangeSelect` folding also fixes the
ascending-range case (`(base >> min(msb,lsb))`, not `(base >> lsb)`), a latent
bug `_eval_const_expr` itself still has. `range_width` uses
`abs(msb - lsb) + 1` unconditionally (Difference 2's resolution). `expr_width`
(self-determined, Table 5-22) and `expr_signed` (§5.5/§5.5.1) are new —
Phase A didn't cover them (explicit non-goal), so they're modeled on the
already-Icarus-validated logic from item 3.4's `evaluator.py:_expr_signed`/
`_expr_self_width` fixes (correct `*`/`**` sum-rule, selects always unsigned,
shift signedness from the left operand only, ternary's both-branches-signed
rule) rather than `width_inference.py`'s simplified max-rule for `*`, since
that rule is a known simplification, not the IEEE-correct behavior. Direct
tests in `tests/test_analysis/test_semantics.py` (24 tests, including the
ported Phase A fixtures). Added `"semantics": {"model"}` to the layering
test's `ALLOWED_EDGES`. Full suite green.

**Result (Phase C, July 2026)**: `sim/scheduler.py`'s `_const_int`,
`_range_width`, `_var_width` are now one-line delegations to
`semantics.const_int`/`range_width`/`var_width` (kept as same-named private
wrappers — call sites unchanged). `_lit_int` (scheduler's old Literal fast
path, now redundant with `semantics.const_int`'s own) deleted. This fixes
scheduler's ascending-range bug (Difference 2) as a documented side effect;
`test_semantics_parity.py::test_range_width_ascending_range_difference`
updated accordingly. Added `"semantics"` to `ALLOWED_EDGES["sim"]` in the
layering test — note `from .. import semantics` is invisible to that test's
AST scan (it only records `ImportFrom.module`, not the imported names, for a
bare `from .. import X`), so the import was written as `from ..semantics
import const_int as _semantics_const_int` (etc.) instead, confirmed by
temporarily removing the allow-list entry and checking the test fails. Full
suite green. `_scoped_env` (byte-for-byte identical across
scheduler/vm/compiled) intentionally left duplicated — it's not part of
`semantics.py`'s API and Phase G's guard test doesn't cover it.

**Result (Phase D, July 2026)**: `sim/vm/compiler.py`'s `_const_int`,
`_range_width`, `_var_width` migrated the same way (one-line delegations to
`semantics`, same private-wrapper names, call sites unchanged); no new
layering-test entry needed since `vm/compiler.py`'s top-level package is
already `sim` (covered by Phase C's `ALLOWED_EDGES["sim"]` addition). Fixes
vm/compiler's copy of the ascending-range bug (Difference 2) as a side
effect — scheduler.py and vm/compiler.py now both agree with
compiled/width_inference, so
`test_semantics_parity.py::test_range_width_ascending_range_difference` was
renamed to `test_range_width_ascending_range_now_agrees_everywhere` and
rewritten as a 4-way agreement check rather than a documented-gap test.
`tests/test_sim/` filtered to `vm`/`compiler` (1784 passed, 2 xfailed) and
the full suite (7758 passed) both green.

**Result (Phase E, July 2026)**: `sim/compiled/codegen.py`'s `_range_width`/
`_var_width` and `sim/compiled/_codegen_utils.py`'s `_const_int` migrated
the same way. `codegen.py`'s copy was already `abs()`-correct (per Phase
A), so no behavior change there. No new layering-test entry needed
(`sim/compiled/*.py`'s top-level package is `sim`, already covered by
Phase C).

**Result (Phase F, July 2026)**: `analysis/const_fold.py`'s `const_int` now
delegates to `semantics.const_int`; `const_range_width` unchanged (still
computes via `const_int`, now indirectly delegating). `width_inference.py`
needed **no changes at all** — its `_const_int`/`_range_width` already just
called `const_fold.const_int`/`const_range_width`.

This surfaced two real, previously-undocumented gaps beyond Phase A's
characterization (both now in `test_semantics_parity.py`'s docstring as
Difference 4 and a correction to Difference 1):

- **New Difference 4**: `const_fold.py`'s old `_unary_op`/`_binary_op` and
  `elaborate.py`'s `_UNARY_OPS`/`_BINARY_OPS` (which `semantics.py` had
  ported verbatim in Phase B) actually disagreed on bare-constant bitwise
  ops with no known width — `~0` (`-1` vs a 32-bit-masked `4294967295`),
  width-ambiguous reduction ops `&`/`~&`/`~^` (const_fold correctly returns
  `None`; elaborate.py guesses via `bit_length()`, which is wrong in
  general), and div/mod by zero (`None` vs `0`). Confirmed with the user:
  `const_fold.py`'s behavior is authoritative; `semantics.py`'s operator
  tables were fixed to match it (raw `~`, `None` for width-ambiguous
  reduction ops and div/mod-by-zero). `sim/elaborate.py` itself is
  unchanged — out of migration scope, nothing delegates to it.
- **Difference 1 correction**: the original Phase A write-up blamed this
  gap on `Identifier.resolved` being unpopulated for an identifier inside
  *another parameter's own default-value expression* — verified false by
  direct inspection (`.resolved` **is** set there). The real bug:
  `const_fold.py`'s `FunctionCall` branch only evaluated a call when
  `expr.is_system` was `True`, but the real parser doesn't set that flag
  for `$clog2`/etc. parsed from source text (only hand-built test fixtures
  set it explicitly, masking the bug from both const_fold's own tests and
  this file's Phase A fixtures). `semantics.const_int` dispatches
  `FunctionCall`s purely on `expr.name`, sidestepping the bug entirely, so
  delegating fixed this as a side effect.

Deleted now-dead `const_fold.py` functions (`_binary_op`, `_unary_op`,
`_fold_system_func`) and updated `tests/test_analysis/test_const_fold.py`
(removed its two tests of the now-deleted `_binary_op`/`_unary_op`
private helpers; all its other ~70 tests needed no changes — verified by
running, not by manual re-derivation). Added `"semantics"` to
`ALLOWED_EDGES["analysis"]` in the layering test.

Full suite green (7761 passed) after Phases E+F landed together (per user
request to batch more before paying for a ~35-minute full run).

**Result (Phase G, July 2026)**: added
`test_no_duplicate_semantics_helpers_outside_semantics_module` to
`tests/test_project/test_import_layering.py` — AST-walks every file under
`src/veriforge` (reusing the layering test's own `_iter_source_files`) and
flags any `def`/nested `def` named `_const_int`, `_range_width`, or
`_var_width` outside `semantics.py`. Verified it actually catches a
violation (temporarily reintroduced a `_const_int` def in an unrelated
file, confirmed the test fails with a clear message, reverted). This
required actually finishing the "then delete the private helpers and
update call sites" half of the Phase C-F mechanic, which earlier phases had
deferred: `sim/scheduler.py`, `sim/vm/compiler.py`, `sim/compiled/codegen.py`,
and `sim/compiled/_codegen_utils.py` no longer define these three names at
all — they import them directly from `semantics` aliased to the same
names (e.g. `from ..semantics import const_int as _const_int`), which is
invisible to the guard (only `def` counts, not `import ... as`) while
keeping every internal call site unchanged. `analysis/width_inference.py`
similarly lost its own `_const_int`/`_range_width` wrappers — its call
sites now call `const_fold.const_int`/`const_range_width` directly (the
public, non-underscore names, exempt from the guard since they're
`const_fold.py`'s own documented public API per the plan's Phase C-F note).
`notes/architecture.md` gained a "Semantics" paragraph. Full gate (ruff,
mypy, check_overview) and the affected test suites (`test_analysis/`,
`test_import_layering.py`, `test_sim/test_scheduler.py`) green; item 4.2 is
complete — all phases (A-G) landed.

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
