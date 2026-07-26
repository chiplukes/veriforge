# Architecture Overview

```
Verilog/SV source text
        │
        ▼ Preprocessing (preprocessor.py + lark_file/)
        │   `define, `ifdef, `include, `timescale handling
        │
        ▼ Parsing (verilog_parser.py, lark_file/verilog.lark)
        │   Lark Earley parser → concrete syntax tree
        │
        ▼ Semantic model (model/, transforms/)
        │   Python object graph: modules, ports, signals, expressions, statements
        │
        ├──▶ Analysis (analysis/)
        │     clock/reset extraction, width inference, constant folding, lint
        │
        ├──▶ Code generation (codegen/, dsl/, convert/)
        │     emit Verilog, Python DSL builder/library, Verilog→DSL conversion
        │
        ├──▶ Simulation (sim/)
        │     three engines: reference, VM bytecode, compiled Cython
        │     testbench framework with auto-detected AXI/Stream/MemBus endpoints
        │
        ├──▶ Refactor tooling (refactor/)
        │     hierarchy collapse/extract/pull-up/push-down with LSP integration
        │
        └──▶ CLI / LSP (__main__.py, veriforge_lsp/)
              `veriforge` command family; `veriforge-lsp` language server
```

## Preprocessing

`preprocessor.py` resolves `` `define ``, `` `ifdef `` / `` `ifndef ``, `` `include ``, and `` `timescale ``
before the text reaches the parser. Grammar fragments used during preprocessing live in `lark_file/`.

See [notes/pcache.md](pcache.md) for parse cache behaviour.

## Parsing

`verilog_parser.py` feeds preprocessed text into a Lark Earley parser driven by
`lark_file/verilog.lark` (hand-translated Verilog 2005 BNF → EBNF). The result is a
Lark concrete syntax tree.

## Semantic model

`transforms/tree_to_model.py` walks the CST and constructs a Python object graph of
modules, ports, signals, expressions, and statements defined in `model/`.

See [notes/semantic_model.md](semantic_model.md) for full type hierarchy and node reference.

## Analysis

`analysis/` contains passes that operate on the semantic model: clock/reset extraction,
expression-width inference, constant folding, and lint checks.

See [notes/support_matrix.md](support_matrix.md) for the full coverage status by language surface.

## Code generation

`codegen/verilog_emitter.py` round-trips the model back to Verilog text.
`dsl/` provides a Python builder API (module/port/expression constructors, stdlib in `dsl/lib/`).
`convert/to_dsl.py` translates parsed Verilog model objects into equivalent Python DSL source.

- [notes/dsl/dsl_guide.md](dsl/dsl_guide.md) — DSL syntax reference
- [notes/dsl/dsl_conversion_coverage.md](dsl/dsl_conversion_coverage.md) — Verilog→DSL conversion coverage

## sim ↔ dsl: now acyclic

`sim/bench/lowering.py` imports `veriforge.dsl` at module load (to build DSL
`Module` objects for the native-lowering path) — this is the one
architecturally-sanctioned direction, since `sim.bench`'s job is to bridge
DSL-built designs into the simulator.

Testbench wrapper generation (formerly `dsl/testbench.py`, which imported
from `sim.endpoints` at module load) now lives in `sim/bench/skeleton.py`,
alongside `lowering.py` — this removed the only `dsl → sim` edge with real
code behind it, breaking what used to be a package-level cycle
(`sim.bench` → `dsl` → `dsl.testbench` → `sim.endpoints` → ... → `sim.bench`).
`dsl/testbench.py` still exists as a thin backward-compatible re-export shim
(`from veriforge.sim.bench.skeleton import *`) so existing
`from veriforge.dsl.testbench import ...` call sites keep working — this is
the one allow-listed exception in the layering test
(`tests/test_project/test_import_layering.py`), since it's a compat surface
with no real logic, not an architectural dependency.

The invariant going forward:

- **`dsl`** must never import `sim.*` beyond `sim.endpoints` at module load
  (lazy imports inside functions are OK; the layering test enforces this).
- **`sim.endpoints`** must never import `sim.bench.*` or `dsl` at module load.

## Simulation

Three engines with different performance and compatibility trade-offs:

| Engine | Module |
|--------|--------|
| Reference | `sim/evaluator.py` + `sim/executor.py` |
| VM bytecode | `sim/vm/` |
| Compiled Cython | `sim/compiled/` |

The testbench framework (`sim/bench/`) auto-detects AXI, Stream, and MemBus port bundles
and wires them to typed endpoint objects.

- [notes/simulation/simulator_engines.md](simulation/simulator_engines.md) — engine comparison and performance guidance
- [notes/simulation/simulation_model.md](simulation/simulation_model.md) — execution model
- [notes/simulation/bench_usage.md](simulation/bench_usage.md) — testbench framework
- [notes/simulation/bench_native_lowering.md](simulation/bench_native_lowering.md) — compiled/VM engine-native lowering
- [notes/simulation/endpoint_timing_model.md](simulation/endpoint_timing_model.md) — endpoint tick_pre/sample_pre/tick_post contract
- [notes/simulation/testbench_phase_contract.md](simulation/testbench_phase_contract.md) — phase contract for endpoints
- [notes/simulation/cycache.md](simulation/cycache.md) — compiled Cython cache
- [notes/simulation/wide_signal_coverage.md](simulation/wide_signal_coverage.md) — compiled-engine wide-signal operation coverage
- [notes/simulation/debug.md](simulation/debug.md) — debugging strategies and Python snippets for simulator issues

## Refactor tooling

`refactor/` implements design-wide transformations: hierarchy collapse, extract,
pull-up, and push-down, with LSP integration for in-editor apply.

## CLI / LSP

`src/veriforge/__main__.py` exposes the `veriforge` command family
(subcommands: `tree`, `reconstruct`, `generate-python-testbench`, `parse-file`,
`parse-directory`, `export-dsl`, `hierarchy`, `lint`, `format`). The top-level
`veriforge_lsp/` package implements the `veriforge-lsp` language server on pygls.

- [notes/veriforge_lsp.md](veriforge_lsp.md) — LSP server commands
- [notes/public_api.md](public_api.md) — public import surface
