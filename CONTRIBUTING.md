# Contributing to veriforge

## Quick start

```bash
git clone https://github.com/chiplukes/veriforge.git
cd veriforge
uv sync --group dev
uvx pre-commit install   # installs ruff check/format as a pre-commit hook
```

## Before submitting a PR

```bash
# Lint and format
uv run ruff check .
uv run ruff format .

# Type check
uv run mypy src/veriforge/ veriforge_lsp/

# Fast test suite (same slice as CI)
uv run pytest tests/test_verilog_parser/test_all.py \
  tests/test_model/ tests/test_analysis/ \
  tests/test_preprocessor/ tests/test_formatter/ \
  --tb=short -q
```

All three must pass — CI will reject PRs that fail lint or the fast test slice.

## Full test suite

The fast slice covers the parser, model, analysis, and formatter layers. For changes
touching simulation, DSL, or LSP, run the relevant subsystem tests locally:

```bash
uv run pytest tests/test_sim/    --tb=short -q   # simulation (slow)
uv run pytest tests/test_dsl/    --tb=short -q   # DSL and testbench generation
uv run pytest tests/test_lsp/    --tb=short -q   # LSP server (requires pygls)
uv run pytest tests/test_refactor/ --tb=short -q # hierarchy refactor tooling
```

The compiled-engine suite (`test_compiled.py`) and `test_bench_native.py` are
excluded from CI and must be run manually before releases.

## Adding a new language construct

See [notes/developer_guide.md](notes/developer_guide.md) — the "Adding a new
language construct" section lists the files to update across grammar, model,
simulation, and tests.

## Code style

- Line length: 120 characters
- Formatter: `ruff format` (enforced by CI)
- Linter: `ruff check` (enforced by CI)
- No new comments explaining *what* the code does — only *why* when the reason is non-obvious

## Filing issues

Use [GitHub Issues](https://github.com/chiplukes/veriforge/issues). Include a minimal
Verilog file that reproduces the problem, the Python version, and the full traceback.
