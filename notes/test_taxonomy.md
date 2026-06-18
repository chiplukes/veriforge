# Test Taxonomy

Quick reference for the test suite layout, pytest markers, and CI policy.

## Directory layout

| Directory | Role |
| --- | --- |
| `tests\test_verilog_parser` | Grammar, parser, and per-rule examples. |
| `tests\test_model` | Semantic model construction, round trips, comments, corpus tests, and model-level analysis. |
| `tests\test_analysis` | Analysis passes such as width inference, constant folding, lint, clock/reset extraction, and SystemVerilog type analysis. |
| `tests\test_dsl` | DSL builder, DSL libraries, Verilog-to-DSL conversion, and DSL round trips. |
| `tests\test_formatter` | Formatter and style behavior. |
| `tests\test_preprocessor` | Preprocessor behavior. |
| `tests\test_project` | Multi-file project parsing and project-level examples. |
| `tests\test_sim` | Simulator engines, scheduler/evaluator/executor behavior, hierarchy, memory, real-world constructs, and compiled-engine regressions. |
| `tests\test_refactor` | Hierarchy refactor tool: collapse, extract, pull-up, push-down previews and apply. |
| `tests\test_validation` | External simulator and cross-simulator validation. |
| `tests\test_lsp` | LSP index, trace, and server behavior. |

## Markers

Registered in `tests\conftest.py`:

| Marker | Purpose |
| --- | --- |
| `slow` | Marks slow tests (skipped by default; use `--run-slow` to include). |
| `grammar` | Grammar-rule tests. |
| `section_a1` | Section A.1 source text tests. |
| `section_a2` | Section A.2 declaration tests. |
| `section_a6` | Section A.6 behavioral tests. |
| `section_a8` | Section A.8 expression tests. |
| `synthesizable` | Synthesizable construct tests. |

## Custom pytest options

| Option | Purpose |
| --- | --- |
| `--run-slow` | Include tests marked `slow` (skipped by default). |
| `--clear-cython-cache` | Delete cached Cython compiled extensions before the run. |
| `--vcd-dir DIR` | Write simulator VCD outputs for tests that support tracing. |

## CI policy

`.github\workflows\test.yml` uses a two-job policy:

**`fast` job** — runs on every `push` and `pull_request`:

```
uv run --extra test pytest
  tests/test_verilog_parser/test_all.py
  tests/test_model/test_module.py
  tests/test_model/test_instances.py
  tests/test_model/test_roundtrip.py
  tests/test_model/test_tree_to_model_characterization.py
  tests/test_analysis/test_width_inference.py
  tests/test_analysis/test_const_fold.py
  tests/test_preprocessor/test_preprocessor.py
  tests/test_formatter/test_formatter.py
  --tb=short -q
```

Also runs: `mypy src/veriforge/ veriforge_lsp/`, `ruff check .`, and `python tools/check_overview.py`.

**`full` job** — runs on `workflow_dispatch` only, Python 3.10/3.11/3.12 matrix:

```
uv run --extra test pytest tests/
  --ignore=tests/test_sim/test_bench_native.py
  --ignore=tests/test_sim/test_compiled.py
  --ignore=tests/test_sim/test_sim_sv.py
  --tb=short -q
```

The three ignored files are the heaviest compiled-engine and SV regressions; run them locally with `--run-slow` as needed.
