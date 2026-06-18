# veriforge
[![CI](https://github.com/chiplukes/veriforge/actions/workflows/ci.yml/badge.svg)](https://github.com/chiplukes/veriforge/actions/workflows/ci.yml)
[![Changelog](https://img.shields.io/github/v/release/chiplukes/veriforge?include_prereleases&label=changelog)](https://github.com/chiplukes/veriforge/releases)
[![License](https://img.shields.io/badge/license-MIT-blue)](https://github.com/chiplukes/veriforge/blob/main/LICENSE)

A Python library for parsing, analyzing, generating, and simulating Verilog/SystemVerilog designs, built on the [Lark](https://github.com/lark-parser/lark) parser.

## Features

- **Parse** Verilog 2005 (with SystemVerilog extensions) into a semantic model
- **Preprocess** source files (`` `define ``, `` `ifdef ``, `` `include ``, `` `timescale ``, etc.)
- **Multi-file project** support — parse directories, link cross-module instances
- **Analyze** designs — width inference, constant folding, clock/reset extraction, lint checks
- **Emit** formatted Verilog from the model (round-trip, configurable style)
- **Python DSL** — build hardware with operator-overloaded Python, emit to Verilog or simulate directly
- **Component library** — FIFO, CDC, codec, AXI-Stream, AXI4-Lite, DSP, RAM, Xilinx inference
- **Auto-generate testbenches** from any module
- **Convert** parsed Verilog to DSL code (Verilog → Python translation)
- **Simulate** — event-driven 4-state simulator with three engines (reference, bytecode VM, compiled Cython)
- **VCD output** — IEEE 1364-2001 waveform dumps, cross-simulator validation
- **Inspect** semantic models through lookup helpers and JSON serialization
- **Language Server** — `veriforge-lsp` provides editor diagnostics, symbols, navigation, hover, and custom hierarchy/trace commands (install [Verible](https://github.com/chipsalliance/verible) for fast between-save diagnostics; the server falls back to the built-in Lark parser when Verible is absent)

## Documentation

- [Getting Started](notes/getting_started.md) — installation and quick workflows
- [User Guide](notes/user_guide.md) — detailed guide with API examples
- [Architecture](notes/architecture.md) — layer overview and links to sub-topics
- [Developer Guide](notes/developer_guide.md) — setup, testing, contributing
- [Public API Guide](notes/public_api.md) — recommended imports for user code
- [DSL Reference](notes/dsl/dsl_guide.md) — Python DSL syntax reference
- [Support Matrix](notes/support_matrix.md) — practical support status across project surfaces
- [LSP Server](notes/veriforge_lsp.md) — Verilog/SystemVerilog Language Server Protocol support
- [Roadmap](notes/roadmap.md) — known future work items
- [Grammar Support Status](docs/grammar_support.md) — parser-rule metadata table
- [Grammar Dependencies (JSON)](docs/grammar_deps.json) — machine-readable rule dependency map

## Quick Start

### Parse a Verilog file

```python
from veriforge.project import parse_file
from veriforge.codegen import emit_module

design = parse_file("rtl/counter.v", preprocess=True)
for mod in design.modules:
    print(emit_module(mod))
```

### Build hardware with the Python DSL

```python
from veriforge.dsl import Module, posedge
from veriforge.codegen import emit_module

with Module("counter") as m:
    clk = m.input("clk")
    rst = m.input("rst")
    count = m.output_reg("count", width=8)

    with m.always(posedge(clk)):
        with m.if_(rst):
            count <<= 0
        with m.else_():
            count <<= count + 1

print(emit_module(m.build()))
```

### Simulate directly from Python

```python
from veriforge.sim import Simulator, Clock

sim = Simulator(m.build())
sim.fork(Clock(sim.signal("clk"), period=10))

def test(s):
    s.drive("rst", 1)

sim.run(test, max_time=200)
print(sim.read("count"))
```

### Analyze a project

```python
from veriforge.project import parse_directory
from veriforge.analysis import analyze_design, lint_design

design = parse_directory("rtl/", preprocess=True)
analyze_design(design)
for w in lint_design(design):
    print(f"[{w.code.name}] {w.message}")
```

## Installation

### Prerequisites
- Python (CPython 3.10+ or PyPy 3.10+)
    - See [python.org](https://www.python.org) for standalone install
    - For uv-based install see [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/)

### Dependencies
- Lark
- Rich
- treelib

### PyPy Support (Optional — ~4x Simulation Speedup)

The full test suite passes under PyPy. Running the simulator under PyPy gives
approximately **4x faster simulation** compared to CPython thanks to JIT
compilation, with zero code changes required.

1. Install PyPy 3.10+ from https://www.pypy.org/download.html
2. Install dependencies: `pypy3 -m pip install lark rich treelib`
3. Run: `pypy3 -m veriforge ...`

### Install with uv (recommended)

```bash
git clone https://github.com/chiplukes/veriforge
cd veriforge
uv sync --extra test
```

### Install with pip

```bash
git clone https://github.com/chiplukes/veriforge
cd veriforge
python -m venv .venv
# Activate:
#   Linux/macOS:       source .venv/bin/activate
#   Windows PowerShell: .venv\Scripts\activate
pip install -e .[test]
```

## CLI Usage

The CLI is subcommand-based (legacy `-f/-t/-r` flags remain supported):

```bash
# Parse a file and print the syntax tree
uv run veriforge tree -f path/to/file.v

# Reconstruct Verilog text from the parsed tree
uv run veriforge reconstruct -f path/to/file.v

# Parse summaries (support --json for automation)
uv run veriforge parse-file -f rtl/top.v
uv run veriforge parse-directory rtl/

# Generate a Python testbench skeleton
uv run veriforge generate-python-testbench --file rtl/top.v

# Export a parsed project to Python DSL files
uv run veriforge export-dsl rtl/ out_dsl/

# Inspect hierarchy / wrapper candidates (--format text|dot|mermaid)
uv run veriforge hierarchy graph rtl/

# Grammar tree visualization
uv run python -m veriforge.lark_file.gen_tree --all --depth 5
```

See `veriforge <command> --help` for full flag listings and
[notes/cli_json_schema.md](notes/cli_json_schema.md) for the `--json` output contract.

## Running Tests

```bash
# Same representative fast slice used by push/PR CI
uv run --extra test pytest tests/test_verilog_parser/test_all.py tests/test_model/test_module.py tests/test_model/test_instances.py tests/test_model/test_roundtrip.py tests/test_model/test_tree_to_model_characterization.py tests/test_analysis/test_width_inference.py tests/test_analysis/test_const_fold.py tests/test_preprocessor/test_preprocessor.py tests/test_formatter/test_formatter.py --tb=no -q

# Full local suite
uv run --extra test pytest tests/ --tb=no -q
```

## Examples

Runnable examples are in the `examples/` directory. See
[`examples/README.md`](examples/README.md) for prerequisites and category
guidance.

- `examples/basics/` — counter, shift register, FSM, ALU, testbench
- `examples/library/` — FIFO, CDC, codec, DSP, Xilinx components
- `examples/axi/` — AXI-Stream and AXI4-Lite usage
- `examples/composability/` — pipeline generators, design exploration, register banks
- `examples/darkriscv/` — real-world RISC-V SoC integration target
- `examples/femtorv/` — compact RISC-V processor integration target
- `examples/picorv32/` — PicoRV32 processor integration target
- `examples/serv/` — SERV bit-serial RISC-V processor integration target
- `examples/ibex/` — Ibex-related validation assets
- `examples/pulp/` — imported validation targets based on `pulp-platform` designs

## Current Limitations

veriforge targets RTL-level behavioral simulation and analysis. Before using it, it is worth knowing where the current boundaries are:

**Simulation scope**
- This is a behavioral RTL simulator, not a replacement for Icarus Verilog, Verilator, or commercial tools for full-chip verification. It is well-suited for unit-level testbenches, design exploration, and cross-validating specific behaviors.
- X/Z propagation is modeled but corner cases in complex expressions may not match the IEEE spec in all situations. For designs where X-propagation correctness is critical, cross-validate with `IcarusCosim`.
- Specify blocks (timing annotations) and gate-level / UDP primitives are parsed and emitted but not executed.
- The compiled Cython engine falls back to reference coroutines for `#delay` / `@(posedge)` inside `initial`/`always` blocks; a `warnings.warn` is emitted when this happens. The workaround is to move timing control into the Python testbench layer.

**SystemVerilog subset**
- The SystemVerilog verification layer is out of scope: classes, SVA assertions, covergroups, `randomize`/constraints, dynamic arrays, queues, `bind`, and `program` blocks are not simulated.
- Packed structs, interfaces, and parameterized interfaces work for common RTL patterns but may require flat wrapper modules for complex cases. The [support matrix](notes/support_matrix.md) has the per-construct breakdown.
- Functions and tasks cover the common RTL patterns used by the validation examples; unusual calling conventions or recursive functions may fail.

**Verilog-to-DSL conversion**
- The converter (`export-dsl`) is intentionally conservative. Control-flow-heavy constructs, complex always blocks, and module-level generate blocks often require manual rewriting. See [notes/dsl/dsl_conversion_coverage.md](notes/dsl/dsl_conversion_coverage.md) for the detailed gap list.

**Hierarchy refactor tooling**
- Structural, behavioral, parameterized, and generate-containing wrappers are detected and classified but collapse is intentionally blocked pending safer transforms. Extract and boundary-move operations cover common direct-wiring cases; complex connectivity patterns fail closed with a diagnostic. See [notes/roadmap.md](notes/roadmap.md) for the backlog.

**Performance**
- Even with the compiled Cython engine, throughput is lower than C-based simulators. For simple sequential testbenches on medium-sized designs, performance is practical. For very large designs or workloads requiring millions of cycles, prefer a dedicated simulator and use veriforge for the analysis and testbench-generation layers.

## License

[MIT](https://choosealicense.com/licenses/mit/)
