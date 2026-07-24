# Getting Started with `veriforge`

This guide walks through the major workflows in this repository, from parsing existing RTL to building, analyzing,
converting, and simulating designs in Python.

For a deep dive, see [user_guide.md](user_guide.md).

For the machine-readable CLI contract, see [cli_json_schema.md](cli_json_schema.md).

## 1) Install

```bash
# from repository root
uv sync
```

## 2) Parse Verilog from the CLI

The CLI now prefers subcommands in its top-level help. The older flag-based forms
shown below still work for compatibility.

Parse and print a syntax tree:

```bash
uv run python -m veriforge -f tests/test_verilog_parser/verilog/verilog_all.v -t
```

The installed console script is equivalent:

```bash
uv run veriforge -f tests/test_verilog_parser/verilog/verilog_all.v -t
```

Preferred subcommand form:

```bash
uv run veriforge tree --file tests/test_verilog_parser/verilog/verilog_all.v
```

Reconstruct Verilog text from the parse tree:

```bash
uv run python -m veriforge -f tests/test_verilog_parser/verilog/verilog_all.v -t -r
```

Preferred subcommand form:

```bash
uv run veriforge reconstruct --file tests/test_verilog_parser/verilog/verilog_all.v
```

Generate a Python testbench skeleton from a parsed RTL file (see
[section 8](#8-generate-a-python-testbench) for the full recommended workflow):

```bash
# Recommended: enhanced bench-style scaffold with auto dependency detection
uv run veriforge generate-python-testbench \
    --file rtl/my_dut.v --enhanced --style=bench --auto-deps \
    --output tb/test_my_dut.py

# Quick check — print the inferred plan without generating code:
uv run veriforge generate-python-testbench \
    --file rtl/my_dut.v --explain-plan
```

JSON responses use a common envelope:

```json
{
    "command": "generate-python-testbench",
    "success": true,
    "result": {
        "module_name": "regs",
        "output_path": "tb_regs.py",
        "written": true
    }
}
```

JSON-capable commands also use a structured error shape on runtime failures:

```json
{
    "command": "parse-file",
    "success": false,
    "error": {
        "type": "FileNotFoundError",
        "message": "[Errno 2] No such file or directory: 'rtl/missing.v'"
    }
}
```

Invalid command lines on JSON-capable subcommands use the same error envelope and
exit with code `2`.

## 3) Parse Verilog from Python

CLI summary for a single RTL file:

```bash
uv run veriforge parse-file --file rtl/top.v
```

Machine-readable summary:

```bash
uv run veriforge parse-file --file rtl/top.v --json
```

```python
from veriforge.project import parse_file

# Parse one file into a Design model
# (supports comments and optional preprocessing)
design = parse_file("rtl/top.v", comments=True, preprocess=True)

print([m.name for m in design.modules])
```

## 4) Parse multi-file projects

CLI summary for a project directory:

```bash
uv run veriforge parse-directory rtl --preprocess --include-path rtl/include
```

Machine-readable summary:

```bash
uv run veriforge parse-directory rtl --json
```

CLI export to Python DSL files:

```bash
uv run veriforge export-dsl rtl out_dsl --single-file
```

Machine-readable export result:

```bash
uv run veriforge export-dsl rtl out_dsl --json
```

```python
from veriforge.project import parse_directory

# Recursively parse .v/.sv/.vh/.svh files
# and link instances across modules
design = parse_directory("rtl", preprocess=True, include_paths=["rtl/include"])

top_modules = design.get_top_modules()
print([m.name for m in top_modules])
```

## 5) Analyze design connectivity and semantics

`analyze_design` runs four passes **in-place** (link instances → resolve names → resolve port connections → analyze connectivity):

```python
from veriforge.analysis import analyze_design

analyze_design(design)  # mutates model objects in-place, returns None

# After analysis, cross-references are populated:
for mod in design.modules:
    for inst in mod.instances:
        print(inst.name, "→", inst.resolved_module.name if inst.resolved_module else "?")
```

Additional analysis passes (run independently after `analyze_design`):

```python
from veriforge.analysis import infer_widths, fold_constants, lint_design
from veriforge.analysis import extract_clocks_resets_from_design

infer_widths(design)              # IEEE 1364-2005 expression width rules
fold_constants(design)            # evaluate constant / parameter expressions
warnings = lint_design(design)    # lint-style checks (returns list[LintWarning])
for w in warnings:
    print(f"[{w.code.name}] {w.message}")

cr = extract_clocks_resets_from_design(design)  # clock/reset extraction
```

Lint codes: `UNDRIVEN`, `UNUSED`, `MULTI_DRIVEN`, `LATCH_INFERRED`, `WIDTH_MISMATCH`, `MIXED_BLOCKING`, `MIXED_NONBLOCKING`, `UNCONNECTED_PORT`.

## 6) Emit and format Verilog

The emitter converts model objects to Verilog source text:

```python
from veriforge.codegen import emit_design, emit_module

text = emit_design(design)         # all modules, interfaces, packages
print(emit_module(design.modules[0]))  # single module
```

The formatter adds style-configurable layout (brace placement, indentation, port alignment):

```python
from veriforge.codegen import FormatStyle, fmt_design, fmt_module

# Using convenience functions (shortest path)
print(fmt_module(design.modules[0], FormatStyle.allman()))

# Or with a VerilogFormatter instance
from veriforge.codegen import VerilogFormatter
formatter = VerilogFormatter(FormatStyle(indent_width=2, begin_end_style="knr"))
print(formatter.format_module(design.modules[0]))
```

Style presets: `FormatStyle.knr()`, `FormatStyle.allman()`, `FormatStyle.gnu()`.

## 7) Build RTL using the Python DSL

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

mod = m.build()
print(emit_module(mod))
```

### DSL syntax at a glance

| Python DSL | Verilog | Notes |
|------------|---------|-------|
| `m.input("clk")` | `input clk` | Also `m.output`, `m.output_reg`, `m.inout` |
| `m.wire("w", width=8)` | `wire [7:0] w` | Also `m.reg`, `m.integer` |
| `m.parameter("W", default=8)` | `parameter W = 8` | Also `m.localparam` |
| `m.reg("mem", width=8, depth=256)` | `reg [7:0] mem [0:255]` | Memory arrays |
| `signal <<= expr` | `signal <= expr;` | Non-blocking (sequential) |
| `signal @= expr` | `signal = expr;` | Blocking (combinational) |
| `m.assign(lhs, rhs)` | `assign lhs = rhs;` | Continuous assignment |
| `with m.always(posedge(clk)):` | `always @(posedge clk)` | Sequential block |
| `with m.always():` | `always @(*)` | Combinational block |
| `with m.if_(cond):` | `if (cond)` | Also `m.elif_`, `m.else_` |
| `with m.case(sel) as c:` | `case (sel)` | Also `m.casex`, `m.casez` |
| `cat(a, b)` | `{a, b}` | Also `rep`, `mux`, `land`, `lor`, `lnot` |
| `a[7:4]` | `a[7:4]` | Range select; `a[3]` for bit select |
| `m.instance("mod", "u0", ports={...})` | `mod u0(.port(sig));` | Module instantiation |

Comparison operators (`==`, `!=`, `<`, etc.) return `Expr`, not `bool` — use
`m.if_(expr)` instead of Python `if`.

For the full DSL syntax reference (comments, attributes, interfaces, delays,
system tasks, error handling, and more), see [dsl_guide.md](dsl/dsl_guide.md).

### DSL standard library (`veriforge.dsl.lib`)

`dsl/lib/` provides ready-made parameterized components built on top of the DSL:

| Module | Contents |
|--------|----------|
| `dsl.lib.fifo` | `sync_fifo`, `async_fifo` |
| `dsl.lib.cdc` | `synchronizer`, `edge_detect` |
| `dsl.lib.codec` | `priority_encoder`, `binary_decoder` |
| `dsl.lib.axi_stream` | AXI-Stream source and sink helpers |
| `dsl.lib.axi` | AXI4-Lite register-file helpers |
| `dsl.lib.dsp` | MAC, pipelined multiplier, FIR filter |
| `dsl.lib.xilinx` | SRL16/32, LUTRAM inference wrappers |

Usage example:

```python
from veriforge.dsl.lib.fifo import sync_fifo
from veriforge.codegen import emit_module

mod = sync_fifo(width=8, depth=16)
print(emit_module(mod))
```

Runnable examples are in `examples/`.

## 8) Generate a Python testbench

`veriforge` can inspect a DUT and emit a ready-to-run Python testbench that
wires up clocks, resets, and every detected AXI-Stream / AXI-Lite / AXI4 / MemBus
interface.  There are two styles and two entry points (CLI and Python API).

### Recommended workflow

**Step 1 — inspect what the planner will infer (no code generated):**

```bash
uv run veriforge generate-python-testbench \
    --file rtl/my_dut.v --explain-plan
```

This prints the `TestbenchPlan` summary: detected clocks, reset polarities,
inferred domains, and every interface bundle that was found.  Read it before
generating so you know whether any overrides are needed.

**Step 2 — generate the scaffold:**

```bash
uv run veriforge generate-python-testbench \
    --file rtl/my_dut.v \
    --enhanced --style=bench \
    --auto-deps \
    --output tb/test_my_dut.py
```

The generated file is a runnable pytest module with:
- one `with bench.run():` block per clock domain
- `bench.reset_all()` sequence
- `bench.iface("prefix")` stub calls for every detected interface
- `bench.drive()` / `bench.signal()` examples for other ports
- `--vcd` argparse flag wired to `bench.run(vcd=...)`

### Key flags

| Flag | Description |
|------|-------------|
| `--file PATH` | Single DUT source file |
| `--directory DIR` | Multi-file project root (use with `--module`) |
| `--module NAME` | Target module name (auto-detected if only one top module) |
| `--output PATH` | Write generated text to file (prints to stdout if omitted) |
| `--enhanced` | Use `TestbenchPlan` (multi-domain, inferred interfaces) — **always use this** |
| `--style bench` | Emit `Testbench`/`bench.iface()` scaffold — the modern high-level approach |
| `--style legacy` | Emit raw `Simulator` + `step_drive` code — simple designs or low-level work |
| `--auto-deps` | Scan sibling `.v`/`.sv` files to find child modules and embed them in `DEPS` |
| `--explain-plan` | Print the inferred plan and exit without generating code |
| `--engine vm\|compiled` | Emit a `compile_native()` scaffold for engine-speed simulation |
| `--no-strict` | Pick the first candidate domain when inference is ambiguous (instead of failing) |
| `--json` | Machine-readable output envelope |

### Override flags (when auto-detection is wrong)

| Flag | Example | Effect |
|------|---------|--------|
| `--clock-override NAME=PERIOD` | `--clock-override aclk=8` | Force clock period |
| `--reset-override NAME=POLARITY` | `--reset-override aresetn=active_low` | Force reset polarity |
| `--iface-domain PREFIX=DOMAIN` | `--iface-domain s_axi=aclk` | Pin interface to a clock domain |
| `--domain-alias CLOCK=ALIAS` | `--domain-alias aclk=axis_domain` | Rename a clock's domain label |

### Multi-file project

```bash
uv run veriforge generate-python-testbench \
    --directory rtl/ --module my_top \
    --enhanced --style=bench --auto-deps \
    --output tb/test_my_top.py
```

### Generate a compiled-engine scaffold

When all detected interfaces are engine-natively lowerable (AXI-Stream, AXI-Lite,
AXI4, MemBus), `--engine compiled` emits a `compile_native()` scaffold that runs
at compiled-engine speed instead of Python-stepped speed:

```bash
uv run veriforge generate-python-testbench \
    --file rtl/my_dut.v \
    --enhanced --style=bench \
    --engine compiled \
    --output tb/test_my_dut_fast.py
```

### Python API

The CLI delegates to `generate_python_testbench_skeleton` from
`veriforge.scaffold` (also re-exported from `veriforge.project` for
backward compatibility):

```python
from veriforge.scaffold import build_testbench, generate_python_testbench_skeleton
from veriforge.project import parse_file

design = parse_file("rtl/my_dut.v")

# Print the inferred plan:
from veriforge.scaffold import build_testbench_plan
plan = build_testbench_plan(design)
print(plan)

# Generate enhanced bench-style scaffold:
text = generate_python_testbench_skeleton(
    design,
    enhanced=True,
    style="bench",
    dut_source_path="rtl/my_dut.v",
)
print(text)

# Or jump straight to a live Testbench object:
bench = build_testbench("rtl/my_dut.v")
with bench.run():
    bench.reset_all()
    src = bench.iface("s_axis")   # AXIStreamProxy
    src.put([0xDE, 0xAD])
    bench.step(20)
```

See [bench_usage.md](simulation/bench_usage.md) for the full `Testbench` proxy API.

### Worked example: AXI-Stream skid buffer

`examples/axis_skid_buffer/` shows the full workflow end-to-end.  The DUT
(`axis_skid_buf.v`) is a single-register AXI-Stream pipeline stage with one
slave port (`s_axis`) and one master port (`m_axis`).

**Inspect the plan:**

```bash
uv run veriforge generate-python-testbench \
    --file examples/axis_skid_buffer/axis_skid_buf.v \
    --explain-plan
```

**Generate the scaffold:**

```bash
uv run veriforge generate-python-testbench \
    --file examples/axis_skid_buffer/axis_skid_buf.v \
    --enhanced --style=bench \
    --output /tmp/test_skid_scaffold.py
```

**The filled-in testbench** (`examples/axis_skid_buffer/test_axis_skid_buf.py`)
demonstrates two patterns:

```python
# Pre-load all frames first (no clock steps), then drain independently.
for frame_data in FRAMES:
    s_axis.put(frame_data)          # queues frame; tlast=1 set automatically on last beat
for expected_data in FRAMES:
    m_axis.expect(expected_data, timeout=200)  # steps clock until frame arrives

# Back-pressure: hold tready low ~33% of cycles to stress the DUT.
m_axis.pause = PauseGenerator(1, 3, seed=42)
```

**Run it:**

```bash
uv run python examples/axis_skid_buffer/test_axis_skid_buf.py
uv run python examples/axis_skid_buffer/test_axis_skid_buf.py --vcd build/skid.vcd
```

For a step-by-step explanation of each part see
`examples/axis_skid_buffer/README.md` and
[user_guide.md §11](user_guide.md#11-testbench-generation).

## 9) Convert parsed Verilog into DSL code

```python
from veriforge.convert.to_dsl import design_to_dsl

dsl_text = design_to_dsl(design)
print(dsl_text[:1000])
```

For file export:

```python
from veriforge.scaffold import export_dsl_project

export_dsl_project(design, "out_dsl")
```

## 10) Simulate designs

```python
from veriforge.sim import Simulator, Clock

sim = Simulator(mod)                          # default engine="reference"
sim.fork(Clock(sim.signal("clk"), period=10))  # auto-toggling clock

def test(s):
    s.drive("rst", 1)       # drive signal by name
    # test_fn runs before event loop — set up initial state here

sim.run(test, max_time=200)
print(sim.read("count"))    # read signal by name
print(sim.time)             # current sim time
print(sim.display_output)   # collected $display strings
```

Engine options: `"reference"` (tree-walking), `"vm"` (bytecode, pure Python), `"vm-fast"` (bytecode, Cython-accelerated), `"compiled"` (design-specific Cython).

Simulation supports:
- 4-state logic values
- event scheduling and delta cycles
- blocking/non-blocking semantics
- memory arrays and `$readmemh`/`$readmemb`
- VCD waveform dumping (`VcdWriter`)
- generate elaboration and hierarchy flattening
- SystemVerilog constructs (enum, struct, package imports)

### Run AXI simulations and capture VCDs

The PULP AXI regressions under `tests/test_sim/test_pulp_axi_examples.py` support
writing VCD waveforms directly from pytest with `--vcd-dir`.

From the repository root, run one AXI-Lite regs simulation on the reference engine:

```powershell
uv run pytest tests/test_sim/test_pulp_axi_examples.py::test_axi_lite_regs_cross_engine[reference] --vcd-dir .\vcd_out --tb=no -q
```

This writes:

```text
.\vcd_out\axi_lite_regs_basic_reference.vcd
.\vcd_out\axi_lite_regs_prot_reference.vcd
```

Run the same regs simulation on the VM engine:

```powershell
uv run pytest tests/test_sim/test_pulp_axi_examples.py::test_axi_lite_regs_cross_engine[vm] --vcd-dir .\vcd_out --tb=no -q
```

Run the AXI-Lite DW converter regression on the reference engine:

```powershell
uv run pytest tests/test_sim/test_pulp_axi_examples.py::test_axi_lite_dw_converter_cross_engine[reference] --vcd-dir .\vcd_out --tb=no -q
```

That writes files such as:

```text
.\vcd_out\axi_lite_dw_down_manual_reference.vcd
.\vcd_out\axi_lite_dw_up_reference.vcd
.\vcd_out\axi_lite_dw_same_reference.vcd
```

List the generated waveforms:

```powershell
Get-ChildItem .\vcd_out
```

Open one in GTKWave:

```powershell
gtkwave .\vcd_out\axi_lite_regs_basic_reference.vcd
```

Notes:
- On Windows, this test file currently runs `reference` and `vm`.
- `--vcd-dir` is optional; without it, the tests still run but no waveforms are written.
- The regs test produces two VCDs because it exercises both the normal path and the protected-access path.

## 11) Use the preprocessor standalone

```python
from veriforge.preprocessor import preprocess, preprocess_file

# Preprocess a string
output = preprocess(source_text, defines={"SIMULATION": ""})

# Preprocess a file (resolves `include relative to file directory)
output = preprocess_file("rtl/top.v", defines={"__ICARUS__": ""})

# Get final defines back for chaining
output, final_defs = preprocess_file("rtl/top.v", return_defines=True)
```

Supported directives: `` `define ``, `` `undef ``, `` `ifdef ``/`` `ifndef ``/`` `elsif ``/`` `else ``/`` `endif ``, `` `include ``, `` `timescale ``, `` `resetall ``, `` `default_nettype ``, and more.

## 12) Verify your environment

Run focused tests while learning:

```bash
uv run pytest tests/test_dsl/test_examples.py --tb=no -q
uv run pytest tests/test_sim/test_testbench.py --tb=no -q
uv run pytest tests/test_project/test_project.py --tb=no -q
```

## 13) Use the Language Server (LSP)

A Language Server Protocol server provides editor diagnostics, navigation, hover,
symbol search, and hierarchy/trace commands.

**Quick start:**

```bash
uv sync --group lsp
uv run veriforge-lsp
```

The server auto-connects on ``localhost:9999`` and can be registered with any
LSP client (VS Code, Neovim, Helix, etc.).

**Diagnostic pipeline** (three tiers):

| Tier | Trigger | Tool | Purpose |
|---|---|---|---|
| 1 — Syntax | keystrokes | Verible (when installed), Lark fallback otherwise | fast diagnostics between saves |
| 2 — File | save | veriforge parser/model | semantic model for saved files |
| 3 — Full | startup / interface change | veriforge parser/model | cross-file resolution, hierarchy |

**Verible dependency (optional but recommended):** Install
[Verible](https://github.com/chipsalliance/verible) for fast, sub-second
syntax diagnostics while typing.  When Verible is absent the server falls back
to a debounced Lark parse so you still receive diagnostics between saves.

For architecture details, see [veriforge_lsp.md](veriforge_lsp.md).

## Where to go next

- Detailed guide: [user_guide.md](user_guide.md)
- DSL deep dive: [dsl_guide.md](dsl/dsl_guide.md)
- Architecture index: [python_overview.md](python_overview.md)
- Example designs: ../examples/
