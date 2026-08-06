# Developer Guide

## 1. Development environment

```bash
# Install uv (if not present)
https://docs.astral.sh/uv/getting-started/installation/

# Clone and install
git clone https://github.com/chiplukes/veriforge.git
cd veriforge
uv sync
```

- **Python**: 3.10+
- **Package manager**: `uv` — use `uv run` instead of `python` or `pip` directly
- **Lint**: `uv run ruff check <path>`
- **Format**: `uv run ruff format <path>`
- **Type check**: `uv run mypy src/veriforge/ veriforge_lsp/`
- **Line length**: 120 characters (enforced by `ruff`)

## 2. Running tests

```bash
# Fast suite (everything except slow compiled tests)
uv run pytest tests/ --tb=no -q

# Full regression (all engines, parallel — this is what CI runs)
uv run pytest --run-slow -n auto

# Specific subsystem
uv run pytest tests/test_sim/ --tb=no -q
uv run pytest tests/test_dsl/ --tb=no -q

# Compiled engine (slow — uses Cython compilation)
uv run pytest tests/test_sim/compiled/ -n auto
uv run pytest tests/test_sim/compiled/ -n auto --run-slow  # ~4600 tests

# Focused bench/proxy regression (fast)
uv run pytest tests/test_sim/test_bench_native.py tests/test_sim/test_bench_runtime.py --tb=no -q
```

The full regression runs with `-n auto` (xdist) which distributes tests across
worker processes. Each worker gets its own isolated compiled-engine cache via
the autouse fixture in `tests/conftest.py` — see [notes/simulation/cycache.md](simulation/cycache.md)
for details on how cache growth is bounded.

See [notes/test_taxonomy.md](test_taxonomy.md) for test directory layout and pytest marker definitions.

## 3. CI

GitHub Actions runs three workflows:

- **`ci.yml`** — runs on every push/PR to `main`:
  - **lint** job: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src/veriforge/ veriforge_lsp/`, `uv run python tools/check_overview.py`
  - **test** job (needs lint): fast test slice (parser/model/analysis/preprocessor/formatter) on Python 3.10/3.11/3.12/3.13
  - **sim-smoke** job (needs lint): runs `tests/test_sim/` minus
    `compiled/` and a handful of large cross-engine hardware-example suites
    (`test_ibex_examples.py`, `test_darkriscv_constructs.py`,
    `test_structural_patterns.py`, `test_differential.py`,
    `test_pulp_*_examples.py`) that each run every test across multiple
    engines including a fresh per-test Cython "compiled" build — those,
    plus `tests/test_dsl/`, pushed the job well past a smoke test's
    ~10-minute budget (the full `tests/test_sim/` tree alone measured ~33
    min locally with `-n 4`). This scope measures ~9 min locally. The
    excluded suites and `tests/test_dsl/` are *not* covered by any other CI
    job — run them locally, or add a dedicated job if they need CI
    coverage; `weekly.yml` below only covers the compiled engine and
    Icarus cross-validation.
  - **vm-equivalence** job (needs lint): builds the `_interp_fast` Cython
    extension, then runs `tests/test_sim/test_vm.py` +
    `tests/test_sim/test_bench_native.py` twice — once with the extension
    built, once with `VERIFORGE_DISABLE_CYTHON_VM=1` — requiring both green.
    This is the drift gate described in the sync policy above.
- **`weekly.yml`** — `on: schedule` (Mondays 06:00 UTC) + `workflow_dispatch`:
  - **compiled** job: `uv run pytest tests/test_sim/compiled/ -n auto
    --run-slow` (the full ~4600-test compiled-engine suite, not exercised
    by `ci.yml`). Caches `.cycache/` keyed on a hash of
    `src/veriforge/sim/compiled/**` to keep reruns fast.
  - **icarus** job: installs `iverilog`, then runs
    `tests/test_validation/` (cross-checks the VM/reference engines
    against Icarus Verilog as an external oracle).
  - Trigger manually via the Actions tab (`workflow_dispatch`) to validate
    before a release without waiting for the weekly schedule.
- **`publish.yml`** — triggered by `v*` tags or `workflow_dispatch`; builds and publishes to PyPI via OIDC trusted publishing

## 4. Project structure

See [notes/python_overview.md](python_overview.md) for the module-by-module file listing.

Key entry points:

| Path | Purpose |
|------|---------|
| `src/veriforge/project.py` | High-level parsing API (`parse_file`, `parse_files`, `parse_directory`) |
| `src/veriforge/scaffold.py` | Testbench generation (`build_testbench`, `build_testbench_plan`, `generate_python_testbench_skeleton`, `export_dsl_project`) |
| `src/veriforge/sim/bench/` | Testbench framework (`Testbench`, endpoints) |
| `src/veriforge/dsl/` | DSL builder |
| `src/veriforge/__main__.py` | CLI entry point (`veriforge` subcommands) |
| `veriforge_lsp/` | `veriforge-lsp` language server (top-level package, pygls-based) |

The package `__init__.py` re-exports the most common names — see [notes/public_api.md](public_api.md).

## 5. Adding a new language construct

When adding support for a new Verilog/SystemVerilog construct:

1. **Grammar** (`lark_file/verilog.lark`) — add grammar rule
2. **Model** (`model/`) — add or extend model node class
3. **Transform** (`transforms/tree_to_model.py`) — handle the new tree node → model object
4. **Emitter** (`codegen/verilog_emitter.py`) — emit valid Verilog from the model node
5. **Simulation** (`sim/evaluator.py`, `sim/executor.py`) — handle evaluation/execution
6. **VM compiler** (`sim/vm/compiler.py`) — emit bytecode (used by both `"vm"` and `"vm-fast"`)
7. **Compiled codegen** (`sim/compiled/codegen.py`) — emit Cython C code (may fallback to reference)
8. **Tests** — add to the appropriate test directory (see [notes/test_taxonomy.md](test_taxonomy.md))

When writing simulation tests, parametrize over all relevant engines. For pure
VM behaviour use `["vm", "vm-fast"]`; for broader cross-validation use
`["reference", "vm", "vm-fast"]` or the full `ENGINES` list that includes
`"compiled"` when available. See [notes/simulation/simulator_engines.md](simulation/simulator_engines.md)
for the testing strategy.

Not all steps are required for every construct; simple expressions may only need grammar + model + emitter + reference simulator.

**Cython VM sync policy**: `sim/vm/_interp_fast.pyx` is a hand-written Cython
reimplementation of `sim/vm/interpreter.py`'s opcode dispatch — it is not
generated from it. Any change to `sim/vm/interpreter.py` or `sim/vm/opcodes.py`
(new opcode, changed semantics, a bug fix) must land with the matching
`_interp_fast.pyx` change in the *same commit*. CI builds the extension and
runs the VM test selection twice — once with it built, once with
`VERIFORGE_DISABLE_CYTHON_VM=1` — and requires both green, so a missed sync
fails the build rather than silently drifting (see `notes/known_issues.md`
for the history of drift this caught).

## 6. Cross-simulator validation (cosim)

`IcarusCosim` in `src/veriforge/sim/cosim.py` compares our engines
against [Icarus Verilog](https://steveicarus.github.io/iverilog/).  Run
validation tests with:

```bash
# Requires iverilog + vvp on PATH
uv run pytest tests/test_validation/test_iverilog_validation.py --tb=short -q
```

`IcarusCosim.run_all_engines()` runs Icarus once and compares every available
engine against that VCD.  `run_cycle_by_cycle()` finds the first divergent
clock cycle for deeper debugging.

The generated testbench skeleton (`generate_python_testbench_skeleton` with
`dut_source_path=`) includes a `validate_with_icarus()` helper that
auto-generates a Verilog wrapper and runs the comparison.

See [notes/simulation/cosim.md](simulation/cosim.md) for the full API
reference, installation instructions, and test patterns.

## 7. Adding a new simulation endpoint

Endpoints live in `src/veriforge/sim/endpoints/`. Each endpoint must implement the three-phase lifecycle contract:

- `tick_pre()` — drive DUT inputs (idempotent)
- `sample_pre()` — capture DUT outputs
- `tick_post()` — commit state after clock edge

See [notes/simulation/testbench_phase_contract.md](simulation/testbench_phase_contract.md) for the complete contract and
[notes/simulation/endpoint_timing_model.md](simulation/endpoint_timing_model.md) for timing details.

The auto-detection system in `endpoints/detect.py` matches port bundles to endpoint types. If you add a new protocol, also update the detector.

## 8. Coding conventions

- **Line length**: 120 characters (`ruff` enforced)
- **Type annotations**: required on all public functions and class attributes; `mypy` runs in CI
- **Comments**: only where clarification is genuinely needed — don't comment obvious code
- **Test naming**: see [notes/test_taxonomy.md](test_taxonomy.md)

## 9. Refactor tool invariants

All refactor operations (extract, pull-up, push-down, collapse) must obey
these invariants. Any new rewrite path must pass them before the apply step
is enabled.

**Fail closed.** If the tool cannot prove a transform is safe, emit diagnostics
and refuse write/apply. Never silently produce a potentially incorrect result.

**Blocking diagnostics.** The following conditions must block apply and return
an error payload rather than an edit plan:

- Unresolved module instance in the selected scope
- Duplicate generated names after renaming (collision detection)
- Selected source range maps to no model nodes
- Selection crosses module boundaries
- Unsupported wrapper class for apply mode
- Unsupported expression rewrite in port map composition
- Source file changed after preview hash was computed (stale-preview protection)
- Downstream hierarchical references into the moved subtree (from sibling modules)

**Stale-preview protection.** Every apply handler must validate that the on-disk
source still matches the text that was hashed during preview. If the file changed
between preview and apply, reject and ask the user to re-preview.

**Naming policy.** Hoisted instance names are prefixed by the collapsed instance
name (`u_wrapper/u_core → u_wrapper__u_core`). Signals introduced from wrapper
internals use the same prefix. Edit plans record all renames explicitly so
previews can show them. New rewrite paths must reuse this policy and surface
collisions as blocking diagnostics rather than silently renaming.

**Warnings (non-blocking).** Comments may move; formatting may change; wrapper
contains unused declarations that will be dropped; generated module order may
differ from original source order.

**Test requirements for new refactor work.** Each new safe subset should land with:

- A small before/after Verilog fixture under `tests/test_refactor/fixtures/`
- Expected JSON graph / preview payload assertions
- Failure-mode tests proving the engine still fails closed on surrounding
  unsupported cases
- An integration test that parses the original design, applies the edit plan to
  temporary files, reparses, and asserts hierarchy/connectivity
- LSP tests for preview payload shape, code-action availability, workspace edit
  conversion, and stale-file hash rejection

## 10. Cache management

Two caches exist during development:

| Cache | Location | How to clear |
|-------|----------|--------------|
| Parse cache | `.pcache/` | `rm -rf .pcache` |
| Compiled Cython cache | `.cycache/` | `uv run pytest --clear-cython-cache` or `rm -rf .cycache` |

During a full regression the compiled-engine cache is automatically redirected
to per-test temp directories and cleaned up after each test, so `.cycache`
does not grow during test runs. Set `VERILOG_TOOLS_COMPILE_CACHE` in your
shell to opt out of this and use a persistent cache instead.

See [notes/pcache.md](pcache.md) and [notes/simulation/cycache.md](simulation/cycache.md) for details.

## Fuzzing

The grammar-driven fuzzer (`src/veriforge/fuzz/`) is a standalone tool for
finding simulation bugs by generating random Verilog modules and comparing
results across engines:

```bash
uv run -m veriforge.fuzz --max 100 --no-icarus
```

It is **not** a pytest test — it runs as a long-lived background process
and logs mismatches to `fuzz_output/`. When a mismatch is found, reduce it
to a minimal repro, add a deterministic test in `tests/test_sim/`, fix the
bug, and (if it cannot be fixed yet) document it in `notes/known_issues.md`.

The CI differential tests (`tests/test_sim/test_differential*.py`) are fast,
bounded regression tests that prevent known bug shapes from recurring.  The
fuzzer discovers new patterns that then feed into those tests.

See [notes/fuzzer.md](fuzzer.md) for full usage and architecture.
