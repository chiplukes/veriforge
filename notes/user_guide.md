# `veriforge` User Guide

A detailed guide to the main capabilities of this project:

1. Installation and environment
2. Architecture and layers
3. CLI and Python parsing workflows
4. Preprocessor support
5. Semantic model
6. Analysis (linking, widths, constants, lint, clocks/resets)
7. Emission and formatting
8. Python DSL for building RTL
9. Reusable component libraries and RAM patterns
10. Testbench generation
11. Verilog-to-DSL conversion
12. Simulation (reference, VM, compiled engines)
13. VCD waveform output and cross-simulator validation
14. Model introspection and JSON serialization
15. Grammar and language coverage visibility

This guide is intentionally practical and example-driven.

---

## 1. Installation and environment

### Prerequisites
- Python 3.10+
- `uv` (recommended workflow for this repo)

### Setup

```bash
# in repository root
uv venv
# activate venv, then install
uv pip install -e .[dev,test]
```

### Quick sanity check

```bash
uv run python -m veriforge --version
```

---

## 2. Core concepts and architecture

At a high level, `veriforge` has these layers:

- **Parser layer**: grammar + parse tree construction
- **Model layer**: semantic Python objects (`Design`, `Module`, expressions, statements, etc.)
- **Analysis layer**: linking and checks (width, constants, lint, clocks/resets)
- **Codegen layer**: model back to Verilog text
- **DSL layer**: build RTL directly in Python
- **Simulation layer**: execute module behavior, optionally with VM/Cython acceleration

The complete code map is documented in [python_overview.md](python_overview.md).

---

## 3. CLI parsing workflow

The CLI entry point is `python -m veriforge`.

### Print parse tree

```bash
uv run python -m veriforge -f tests/test_verilog_parser/verilog/verilog_all.v -t
```

### Parse tree + reconstructed source

```bash
uv run python -m veriforge -f tests/test_verilog_parser/verilog/verilog_all.v -t -r
```

### Useful flags
- `-f`, `--file`: file to parse
- `-t`, `--tree`: show parse tree
- `-r`, `--reconstruct`: reconstruct source text
- `-d`, `--debug`: parser debug
- `--parser {earley,lalr}`: parser backend
- `-log`: logging level

---

## 4. Python API: parsing source files

### Parse a single file

```python
from veriforge.project import parse_file

design = parse_file(
    "rtl/top.v",
    comments=True,
    preprocess=True,
    defines={"SYNTHESIS": "1"},
    include_paths=["rtl/include"],
)

print(len(design.modules), len(design.interfaces), len(design.packages))
```

### Parse explicit file list

```python
from veriforge.project import parse_files

design = parse_files(
    ["rtl/top.v", "rtl/core.v", "rtl/alu.sv"],
    comments=True,
    analyze=True,
    preprocess=True,
)

print([m.name for m in design.modules])
```

### Parse directory recursively

```python
from veriforge.project import parse_directory

design = parse_directory(
    "rtl",
    recursive=True,
    extensions=(".v", ".sv", ".vh", ".svh"),
    exclude=["*_tb.v", "sim/*"],
    preprocess=True,
    include_paths=["rtl/include"],
)
```

### Notes
- Parsing returns a unified `Design` object.
- With `analyze=True`, instance references are linked after merge.
- Duplicate names are deduplicated (first definition wins).

---

## 5. Preprocessor support

### Via parse APIs

Pass `preprocess=True` to any `parse_*` function:

```python
from veriforge.project import parse_directory

design = parse_directory(
    "rtl",
    preprocess=True,
    defines={"FPGA": "1", "DATA_W": "32"},
    include_paths=["rtl", "rtl/include", "ip/common"],
)
```

Direct `verilog_parser.build_tree()` calls and the default `parse_file()` path
now tolerate parser-blocking directive lines like `` `timescale `` by blanking
those lines before grammar parsing. Full preprocessing is still required for
macro expansion, `` `include ``, and conditional compilation.

### Standalone preprocessor

```python
from veriforge.preprocessor import preprocess, preprocess_file

# Preprocess a string
output = preprocess(source_text, defines={"SIMULATION": ""})

# Preprocess a file (file's directory auto-added to include search path)
output = preprocess_file("rtl/top.v", defines={"__ICARUS__": ""})

# Get final defines back for chaining to the next file
output, final_defs = preprocess_file(
    "rtl/top.v",
    defines={"SYNTH": ""},
    return_defines=True,
)
```

### Supported directives

`` `define ``, `` `undef ``, `` `ifdef ``, `` `ifndef ``, `` `elsif ``, `` `else ``, `` `endif ``,
`` `include ``, `` `timescale ``, `` `resetall ``, `` `default_nettype ``, `` `pragma ``,
`` `line ``, `` `celldefine ``, `` `endcelldefine ``, `` `unconnected_drive ``, `` `nounconnected_drive ``

---

## 6. Working with the semantic model

After parsing, you can inspect the model directly.

```python
design = parse_file("rtl/top.v")

for mod in design.modules:
    print("module:", mod.name)
    print("ports:", [p.name for p in mod.ports])
    print("parameters:", [p.name for p in mod.parameters])
```

The model includes:
- **Declarations**: `Port`, `Net`, `Variable`, `Parameter` (with `is_local` for localparams)
- **Expressions**: `Identifier`, `Literal`, `StringLiteral`, `BinaryOp`, `UnaryOp`, `TernaryOp`, `Concatenation`, `Replication`, `BitSelect`, `RangeSelect`, `PartSelect`, `FunctionCall`, `Mintypmax`, `Range`
- **Statements**: `BlockingAssign`, `NonblockingAssign`, `IfStatement`, `CaseStatement`, `ForLoop`, `WhileLoop`, `ForeverLoop`, `RepeatLoop`, `SeqBlock`, `ParBlock`, `WaitStatement`, `DisableStatement`, `EventTrigger`, `TaskEnable`, `SystemTaskCall`, `DelayControl`, `EventControl`
- **Behavioral**: `AlwaysBlock`, `InitialBlock`, `SensitivityType`
- **Structural**: `Instance`, `PortConnection`, `ParameterBinding`, `ContinuousAssign`
- **Generate**: `GenerateFor`, `GenerateIf`, `GenerateCase`, `GenvarDecl`
- **Functions/Tasks**: `FunctionDecl`, `TaskDecl`
- **SystemVerilog**: `Interface`, `Modport`, `ModportPort`, `Package`, `ImportDecl`, `TypedefDecl`, `EnumType`, `StructType`, `UnionType`
- **Other**: `SpecifyBlock`, `Comment`, `SourceLocation`
- **Containers**: `Design` (top-level), `Module` (with lookup helpers)

---

## 7. Analysis workflow

### Core analysis (4-pass, in-place)

`analyze_design` runs four passes that populate cross-references on model objects **in-place**.
It returns `None`.

```python
from veriforge.analysis import analyze_design

analyze_design(design)  # mutates model objects, returns None

# After analysis, cross-references are populated:
# - Instance.resolved_module → Module
# - Identifier.resolved → Port / Net / Variable / Parameter
# - PortConnection.resolved_port → Port
# - Net.drivers / Net.loads, Variable.drivers / Variable.loads
for mod in design.modules:
    for inst in mod.instances:
        target = inst.resolved_module
        print(f"{inst.name} → {target.name if target else '?'}")
```

### Additional analysis passes

Each can be run independently after `analyze_design`:

```python
from veriforge.analysis import (
    infer_widths,               # IEEE 1364-2005 expression width rules
    fold_constants,             # evaluate constant / parameter expressions
    lint_design, lint_module,   # lint-style checks
    extract_clocks_resets_from_design,  # clock/reset extraction
)

infer_widths(design)              # populates inferred_width on expressions
fold_constants(design)            # resolves parameter-dependent expressions

warnings = lint_design(design)    # returns list[LintWarning]
for w in warnings:
    print(f"[{w.code.name}] {w.message}  signal={w.signal}")

cr_info = extract_clocks_resets_from_design(design)
for mod_name, info in cr_info.items():
    print(mod_name, info.clocks, info.resets)
```

### Lint codes

| Code | Meaning |
|------|-------- |
| `UNDRIVEN` | Signal has no drivers |
| `UNUSED` | Signal has no loads |
| `MULTI_DRIVEN` | Signal driven from multiple sources |
| `LATCH_INFERRED` | Combinational block with incomplete assignments |
| `WIDTH_MISMATCH` | Port connection or assign width differs |
| `MIXED_BLOCKING` | Blocking assign in sequential always block |
| `MIXED_NONBLOCKING` | Non-blocking assign in combinational always block |
| `UNCONNECTED_PORT` | Instance port left open |

### Lower-level pass functions

For fine-grained control:

```python
from veriforge.analysis import (
    link_instances,             # pass 1: resolve Instance.resolved_module
    resolve_names,              # pass 2: build symbol tables
    resolve_port_connections,   # pass 3: resolve PortConnection.resolved_port
    analyze_connectivity,       # pass 4: populate drivers/loads
    infer_widths_in_module,     # per-module width inference
    fold_constants_in_module,   # per-module constant folding
    const_fold, const_int,      # expression-level folding
)
```

---

## 8. Emission and formatting

### Emit model to Verilog text

The emitter converts model objects to Verilog source:

```python
from veriforge.codegen import emit_design, emit_module, emit_package, emit_interface

# Emit an entire design (modules + interfaces + packages)
verilog_text = emit_design(design)

# Emit a single module
print(emit_module(design.modules[0]))

# Emit a single expression (useful for debugging)
from veriforge.codegen import emit_expression
print(emit_expression(some_expr))
```

### Format with configurable style

The formatter works on **model objects** (not raw text strings) and applies
configurable brace placement, indentation, and port alignment:

```python
from veriforge.codegen import FormatStyle, VerilogFormatter, fmt_module, fmt_design

# Convenience functions (shortest path)
print(fmt_module(design.modules[0], FormatStyle.allman()))
print(fmt_design(design, FormatStyle.knr()))

# Or create a formatter instance with custom settings
style = FormatStyle(
    indent_width=2,
    begin_end_style="allman",    # "knr", "allman", or "gnu"
    end_else_same_line=False,
    align_ports=True,
    column_limit=80,
)
formatter = VerilogFormatter(style)
print(formatter.format_module(design.modules[0]))
```

### Style presets

| Preset | `begin` placement | `end else` |
|--------|-------------------|-------------|
| `FormatStyle.knr()` | same line as keyword | same line |
| `FormatStyle.allman()` | next line, indented | separate lines |
| `FormatStyle.gnu()` | next line, keyword indent | separate lines |

---

## 9. DSL workflow (build RTL in Python)

The DSL is useful when you want:
- generator patterns
- parameterized hardware families
- composable blocks
- Python-native metaprogramming

### Minimal sequential example

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

module = m.build()
print(emit_module(module))
```

### Combinational example

```python
from veriforge.dsl import Module

with Module("logic") as m:
    a = m.input("a", width=8)
    b = m.input("b", width=8)
    y = m.output_reg("y", width=8)

    with m.always():
        y @= (a & b) | (a ^ b)
```

### DSL syntax reference

The DSL maps Python constructs to Verilog. The full reference is in
[dsl_guide.md](dsl_guide.md); this section covers the essentials.

#### Declarations

| Python DSL | Verilog | Notes |
|------------|---------|-------|
| `m.input("d", width=8)` | `input [7:0] d` | `m.output`, `m.output_reg`, `m.inout` |
| `m.wire("w", width=8)` | `wire [7:0] w` | Internal wire |
| `m.reg("r", width=4)` | `reg [3:0] r` | Internal register |
| `m.reg("mem", width=8, depth=256)` | `reg [7:0] mem [0:255]` | Memory array |
| `m.integer("i")` | `integer i` | 32-bit integer variable |
| `m.parameter("W", default=8)` | `parameter W = 8` | Module parameter |
| `m.localparam("H", value=4)` | `localparam H = 4` | Local parameter |
| `m.output_reg("q", width=8, init=0)` | `output reg [7:0] q = 0` | Initial value |
| `m.input("d", width=W)` | `input [W-1:0] d` | Parameterized width |

#### Assignments

| Python DSL | Verilog | Context |
|------------|---------|--------|
| `signal <<= expr` | `signal <= expr;` | Non-blocking — sequential `always` |
| `signal @= expr` | `signal = expr;` | Blocking — combinational `always` |
| `m.assign(lhs, rhs)` | `assign lhs = rhs;` | Continuous — outside `always` |
| `m.assign_nb(lhs, rhs)` | `lhs <= rhs;` | Non-blocking (method form) |
| `m.assign_b(lhs, rhs)` | `lhs = rhs;` | Blocking (method form) |

#### Expression operators

```python
# Arithmetic        # Bitwise           # Comparison (return Expr, not bool)
a + b               a & b               a == b    a != b
a - b               a | b               a < b     a <= b
a * b               a ^ b               a > b     a >= b
a // b              ~a                  # Shifts
a % b               -a                  a << 2    a >> 1
a ** b
```

**Important:** Comparisons return `Expr` objects. Using a `Signal` in a Python
`if`/`bool()` raises `TypeError` — always use `m.if_(expr)` instead.

#### Bit / range / part selection

```python
data[3]                     # data[3]       — bit select
data[7:4]                   # data[7:4]     — range select
data.part_select(i, 8)      # data[i +: 8]  — indexed part select up
data.part_select_down(i, 8) # data[i -: 8]  — indexed part select down
```

#### Helper functions

```python
from veriforge.dsl import cat, rep, mux, land, lor, lnot
from veriforge.dsl import reduce_and, reduce_or, reduce_xor
```

| Function | Verilog | Description |
|----------|---------|-------------|
| `cat(a, b, c)` | `{a, b, c}` | Concatenation |
| `rep(4, a)` | `{4{a}}` | Replication |
| `mux(sel, t, f)` | `sel ? t : f` | Ternary mux |
| `land(a, b)` | `a && b` | Logical AND |
| `lor(a, b)` | `a \|\| b` | Logical OR |
| `lnot(a)` | `!a` | Logical NOT |
| `reduce_and(a)` | `&a` | Reduction AND |
| `reduce_or(a)` | `\|a` | Reduction OR |
| `reduce_xor(a)` | `^a` | Reduction XOR |

#### Behavioral blocks and control flow

```python
# Sequential — posedge/negedge triggers → non-blocking (<<= )
with m.always(posedge(clk)):
    q <<= d

# Combinational — empty or level-sensitive → blocking (@=)
with m.always():
    y @= a & b

# Initial block
with m.initial():
    q @= 0

# Control flow
with m.if_(cond):
    ...
with m.elif_(cond2):
    ...
with m.else_():
    ...

# Case / casex / casez
with m.case(sel) as c:
    with c.when(0):    y @= a
    with c.when(1, 2): y @= b   # multiple values per arm
    with c.default():  y @= 0
```

#### Continuous assignments and instantiation

```python
m.assign(y, a + b)                          # assign y = a + b;

m.instance("counter", "u0",
    ports={"clk": clk, "rst": rst, "count": cnt},
    parameters={"WIDTH": 16})
```

`None` values in the ports dict leave ports unconnected (`.port()`).

#### Delays, events, and system tasks (testbench patterns)

```python
with m.initial():
    m.delay(10)                  # #10;
    m.wait_posedge(clk)          # @(posedge clk);
    m.display("val = %d", sig)   # $display("val = %d", sig);
    m.readmemh("data.hex", mem)  # $readmemh("data.hex", mem);
    m.finish()                   # $finish;
```

#### Comments, attributes, and interfaces

```python
clk = m.input("clk").comment("System clock")
state = m.reg("state", width=3).attr("fsm_encoding", "one_hot")
m.comment("Stage 1: partial sums")     # standalone comment

# Interfaces (expand to flat prefixed ports in Verilog 2005)
from veriforge.dsl import Interface
axis = (Interface("axi_stream")
    .signal("tvalid", src="master")
    .signal("tready", src="slave")
    .signal("tdata", width=8, src="master"))
m_axis = m.interface("m_axis", axis, role="master", reg=True)
```

For the complete DSL reference — including LHS targets, error handling,
`for`/`while`/`forever` loops, `wire_interface`, `port_map`, and simulation
from DSL — see [dsl_guide.md](dsl_guide.md).

### SystemVerilog type declarations

The DSL supports `typedef`, `enum`, `struct`, and `union`:

```python
with Module("sv_types_demo") as m:
    # Enum
    m.typedef_enum("state_t", ["IDLE", "RUN", "DONE"])
    state = m.typed_var("state", "state_t")

    # Struct
    m.typedef_struct("packet_t", [("valid", 1), ("data", 8), ("tag", 4)])
    pkt = m.typed_var("pkt", "packet_t")

    # Union
    m.typedef_union("overlay_t", [("raw", 16), ("hi", 8), ("lo", 8)])

    # Simple type alias
    m.typedef_alias("word_t", "logic", width=32)

    # Package imports
    m.import_pkg("my_pkg")         # import my_pkg::*;
    m.import_pkg("my_pkg", "FOO")  # import my_pkg::FOO;
```

For a full DSL reference and advanced patterns, see [dsl_guide.md](dsl_guide.md).

---

## 10. DSL component libraries

The project includes reusable DSL components in `veriforge.dsl.lib`:

| Category | Functions | Description |
|----------|-----------|-------------|
| **FIFO** | `sync_fifo(data_width, depth)` | Pointer-based synchronous FIFO with full/empty/count |
| **CDC** | `synchronizer(width, stages)`, `edge_detector(...)` | Clock-domain crossing and edge detection |
| **Codec** | `priority_encoder(width)`, `binary_decoder(width)` | Combinational logic encoders/decoders |
| **AXI Stream** | `axi_stream(data_width)`, `axis_register(...)` | Interface template + pipeline register |
| **AXI4-Lite** | `axi4_lite(addr_width, data_width)` | Full 5-channel interface (19 signals) |
| **DSP** | `mac(width)`, `pipelined_mult(width)`, `fir_filter(...)` | DSP inference patterns |
| **Xilinx** | `shift_register_srl(...)`, `lutram(...)` | Xilinx-specific inference |
| **RAM** | `single_port_ram(...)`, `simple_dual_port_ram(...)`, `true_dual_port_ram(...)`, `rom(...)` | Memory inference with style hints |

All factory functions return `Module` builders. Usage:

```python
from veriforge.dsl.lib import sync_fifo, single_port_ram
from veriforge.codegen import emit_module

fifo = sync_fifo(data_width=8, depth=16)
print(emit_module(fifo.build()))

ram = single_port_ram(data_width=32, addr_width=10, style="block")
print(emit_module(ram.build()))
```

Runnable examples:
- `examples/library/*`
- `examples/axi/*`
- `examples/composability/*`

---

## 11. Testbench generation

`veriforge` can inspect a DUT and emit a ready-to-run Python testbench.  The
most common use case is a DUT with one or more AXI-Stream interfaces; the
walkthrough below uses an AXI-Stream register slice (skid buffer) as the
concrete example.  For the CLI flag reference see
[getting_started.md §8](getting_started.md#8-generate-a-python-testbench).

A complete, runnable example lives in `examples/axis_skid_buffer/`.

### Full workflow

#### Step 1 — inspect the inferred plan

```bash
uv run veriforge generate-python-testbench \
    --file rtl/my_dut.v --explain-plan
```

This prints the `TestbenchPlan` — detected clocks, reset polarities, inferred
domains, and every interface bundle — without writing any code.  Read it
first so you can catch incorrect inferences before generating.

#### Step 2 — generate the scaffold

```bash
uv run veriforge generate-python-testbench \
    --file rtl/my_dut.v --enhanced --style=bench \
    --auto-deps \
    --output tb/test_my_dut.py
```

The generated file is a runnable Python script with stub functions for every
detected interface and a `run_smoke_test()` entry point that you fill in with
real stimulus and assertions.

#### Step 3 — understand what was generated

For a DUT with one slave AXI-Stream port (`s_axis`) and one master AXI-Stream
port (`m_axis`), the scaffold looks like this after generation:

```python
from veriforge.sim.endpoints import PauseGenerator

def drive_s_axis(bench: Testbench) -> None:
    iface = bench.iface("s_axis")
    # Optional: add random source gaps (hold tvalid low ~25% of cycles).
    # iface.pause = PauseGenerator(1, 4)
    # TODO: replace with real stimulus.
    # tlast=1 is set on the last beat automatically (override with last=[...] if needed).
    # Other sideband kwargs: dest=..., tid=..., user=..., last_user=..., keep=...
    iface.put([0x00, 0x01, 0x02, 0x03])

def expect_m_axis(bench: Testbench) -> None:
    iface = bench.iface("m_axis")
    # Optional: add random back-pressure (hold tready low ~25% of cycles).
    # iface.pause = PauseGenerator(1, 4)
    frame = iface.get(timeout=200)
    print("received m_axis:", list(frame.data))

def run_smoke_test(bench: Testbench) -> None:
    bench.reset_all()
    # TODO: set NUM_FRAMES to the number of input packets to send.
    NUM_FRAMES = 1
    for _i in range(NUM_FRAMES):
        drive_s_axis(bench)
    for _i in range(NUM_FRAMES):
        expect_m_axis(bench)
```

Key points:

- **`put()` queues a frame — no clock steps happen.**  The frame is buffered
  in the source endpoint and will be driven beat-by-beat as the simulation
  clock is stepped.
- **`tlast=1` is automatic.**  `iface.put([0x10, 0x11, 0x12])` drives
  `tlast=0` on the first two beats and `tlast=1` on the last beat.  You do
  not need to set it explicitly.  Override with `last=[0, 0, 1]` only if you
  need a custom `tlast` pattern within the frame.
- **`get()` / `expect()` step the clock.**  Each call steps the simulation
  internally until the sink sees a `tlast=1` beat and the frame is complete.
- **`expect()` is the assertion form of `get()`.**  It raises `AssertionError`
  on mismatch, which is more useful than a manual `assert list(frame.data) == ...`.

#### Step 4 — fill in real stimulus

Replace the placeholder `put([0x00, 0x01, 0x02, 0x03])` call with your
actual test vectors and add assertions to the `expect_m_axis` stub:

```python
FRAMES = [
    [0x10, 0x11, 0x12, 0x13],
    [0xA0, 0xA1, 0xA2],
    [0xFF],
]

def drive_s_axis(bench: Testbench) -> None:
    iface = bench.iface("s_axis")
    for frame_data in FRAMES:
        iface.put(frame_data)  # all queued before any clock steps

def expect_m_axis(bench: Testbench) -> None:
    iface = bench.iface("m_axis")
    for expected in FRAMES:
        iface.expect(expected, timeout=200)
```

The pre-load / drain pattern — queue all sources first, then drain all outputs
— is important.  It decouples stimulus generation from output checking,
matching how real hardware behaves.  A tight send-one / receive-one loop
forces an artificial single-packet cadence that hides pipeline bugs.

In `run_smoke_test`, call `drive_s_axis` once and `expect_m_axis` once
(the loops are inside those functions now):

```python
def run_smoke_test(bench: Testbench) -> None:
    bench.reset_all()
    drive_s_axis(bench)   # queues all frames; no clock steps yet
    expect_m_axis(bench)  # drains all output; steps clock internally
```

#### Step 5 — add back-pressure

`PauseGenerator` adds randomized flow-control events.  On a source it gates
`tvalid`; on a sink it gates `tready`.

```python
from veriforge.sim.endpoints import PauseGenerator

def expect_m_axis(bench: Testbench) -> None:
    iface = bench.iface("m_axis")
    # Hold tready low ~33% of cycles, stressing back-pressure handling.
    iface.pause = PauseGenerator(1, 3, seed=42)
    for expected in FRAMES:
        iface.expect(expected, timeout=400)  # higher timeout for stalled cycles
```

`PauseGenerator(num, denom)` pauses with probability `num / denom`.  Common
values: `(1, 4)` ≈ 25%, `(1, 3)` ≈ 33%, `(1, 2)` ≈ 50%.  Pass `seed=N` for
reproducible sequences.

`PauseGenerator` is exported from `veriforge.sim.endpoints`, not from
`veriforge.sim.bench`.

#### Step 6 — run and capture a waveform

```bash
uv run python tb/test_my_dut.py
uv run python tb/test_my_dut.py --vcd build/my_dut.vcd
```

The scaffold's `main()` wires `--vcd` to `bench.run(vcd=...)` automatically.

### Python API

```bash
# Inspect plan:
uv run veriforge generate-python-testbench --file rtl/my_dut.v --explain-plan

# Generate scaffold:
uv run veriforge generate-python-testbench \
    --file rtl/my_dut.v --enhanced --style=bench --auto-deps \
    --output tb/test_my_dut.py
```

```python
from veriforge.scaffold import generate_python_testbench_skeleton
from veriforge.project import parse_file

design = parse_file("rtl/my_dut.v")
text = generate_python_testbench_skeleton(
    design, enhanced=True, style="bench", dut_source_path="rtl/my_dut.v"
)
print(text)
```

To generate a Verilog-language testbench wrapper (DUT instantiation + clock +
reset + VCD dump) instead of a Python scaffold:

```python
from veriforge.dsl.testbench import generate_testbench
from veriforge.codegen import emit_module

tb = generate_testbench(mod)   # accepts a built model Module
print(emit_module(tb))
```

---

## 12. Convert Verilog to DSL

This project can translate parsed model objects back into DSL source.

### Convert a whole design to a Python string

```python
from veriforge.project import parse_directory
from veriforge.convert.to_dsl import design_to_dsl

design = parse_directory("rtl")
py_text = design_to_dsl(design)
```

### Export one file per module/interface/package

```python
from veriforge.project import export_dsl_project

written = export_dsl_project(design, "out_dsl", one_file_per_module=True)
print([str(p) for p in written])
```

This is useful for migration workflows and design introspection.

---

## 13. Simulation workflow

### Basic usage

```python
from veriforge.sim import Simulator, Clock

sim = Simulator(module)  # default engine="reference"
sim.fork(Clock(sim.signal("clk"), period=10))  # auto-toggling clock

def test(s):
    s.drive("rst", 1)           # drive signal by name
    # test_fn is called before the event loop runs

sim.run(test, max_time=200)
print(sim.read("count"))        # read signal value
print(sim.time)                 # current simulation time
print(sim.display_output)       # collected $display strings
```

### Signal handles

```python
clk_h = sim.signal("clk")      # returns SignalHandle
print(clk_h.value)              # current Value
clk_h.value = 1                 # drive from testbench

# List all signals (optional prefix filter)
all_sigs = sim.signals()        # sorted list of all names
clk_sigs = sim.signals("clk")   # only names starting with "clk"
```

### Engine options

```python
sim_ref  = Simulator(module, engine="reference")   # tree-walking (default)
sim_vm   = Simulator(module, engine="vm")           # bytecode VM, pure Python
sim_fast = Simulator(module, engine="vm-fast")      # bytecode VM, Cython (falls back to "vm")
sim_cyc  = Simulator(module, engine="compiled")     # design-specific Cython codegen
```

- `"reference"`: easiest to debug, slowest
- `"vm"`: ~3–5x faster via bytecode compiler/interpreter
- `"vm-fast"`: same bytecode as `"vm"` with Cython interpreter; falls back to pure Python if extension not built
- `"compiled"`: fastest; generates design-specific Cython extension

The compiled engine also supports batch mode:

```python
sim_cyc.batch_run(cycles=1000, clock_name="clk", clock_period=10)
```

### Multi-module / hierarchical simulation

Pass `design=` to resolve instances across modules:

```python
from veriforge.project import parse_directory

design = parse_directory("rtl")
top = design.get_top_modules()[0]
sim = Simulator(top, design=design)

# Inspect hierarchy
print(sim.hierarchy())  # {"u1": "inverter", "u_mid.u_leaf": "leaf", ...}
```

### Simulation capabilities
- 4-state value representation (`Value` type with val/mask int-pair encoding)
- event queue + delta cycle scheduling
- generate elaboration and hierarchy flattening
- blocking and non-blocking assignment semantics
- memory arrays and `$readmemh`/`$readmemb`
- SystemVerilog constructs (enum, struct, package imports)
- `$display`, `$write`, `$monitor`, `$finish`, `$stop` system tasks
- VCD waveform dumping

### Simulator API summary

| Method / Property | Description |
|-------------------|-------------|
| `Simulator(module, engine=..., design=...)` | Create and elaborate |
| `sim.signal(name)` | Get `SignalHandle` |
| `sim.signals(prefix)` | List signal names |
| `sim.drive(name, value)` | Drive signal |
| `sim.read(name)` | Read signal |
| `sim.fork(Clock(...))` | Start clock generator |
| `sim.run(test_fn, max_time=N)` | Run simulation |
| `sim.run_step()` | Advance one time step |
| `sim.batch_run(cycles, clock_name, clock_period, events)` | Compiled engine batch |
| `sim.time` | Current simulation time |
| `sim.display_output` | Collected `$display` output |
| `sim.hierarchy()` | Instance path → module name |
| `IcarusCosim(...)` | Cross-check against Icarus Verilog (see §14) |

### Performance: compiled engine batch mode

The compiled engine's `batch_run()` runs the entire clock toggle + delta-loop
cycle in a C loop with `nogil`. This is **500x faster** than the event-loop
path for long simulations. However, the speedup depends heavily on how the
testbench is structured.

**Key principle**: every `initial` block with timing controls (e.g. `#delay`,
`@(posedge clk)`) runs as a Python coroutine. Each coroutine resume requires
a full Python→C signal sync round-trip. A clock generator written as
`initial while(1) #5 CLK = !CLK` forces **every clock edge** through the
Python event loop — defeating the entire purpose of compiled simulation.

#### Slow pattern (avoid)

```verilog
// This runs as a Python coroutine — every edge goes through Python!
initial while(1) #5 CLK = !CLK;

initial begin
    #1000 RES = 0;    // Also a coroutine, but only fires once
end
```

This forces the simulator into step mode (~170K cycles/s) even with the
compiled engine, because the clock generator is an infinite coroutine.

#### Fast pattern (recommended)

Drive clock and reset from Python using `batch_run()`:

```python
sim = Simulator(top, engine="compiled", design=design)
sim.run(max_time=0)  # execute $readmemh, settle combinational logic

# Schedule reset at cycle 100 (= 1000 time units)
events = [(100, "RES", 0)]
sim.batch_run(50000, "CLK", clock_period=10, events=events)
```

This runs the **entire simulation in C** (~10M cycles/s). The Verilog
testbench should have no `initial` blocks with timing — just wire declarations
and module instantiation:

```verilog
module testbench;
    reg CLK = 0;
    reg RES = 1;
    // No initial blocks with timing!
    // Clock and reset driven by batch_run() from Python.
    my_design dut(.clk(CLK), .rst(RES));
endmodule
```

#### Performance comparison (DarkRISCV, 500K time units)

| Approach | Time | Speedup |
|---|---|---|
| Event loop (original testbench) | 163s | 1x |
| Event loop + VCD fast path | 5.7s | 29x |
| **batch_run (no Verilog timing)** | **0.3s** | **512x** |

#### When to use each approach

| Approach | Use when |
|---|---|
| `sim.run()` with Verilog `initial` | Need VCD, complex stimulus timing, `$monitor` |
| `sim.batch_run()` no events | Free-running design, external stimulus from Python |
| `sim.batch_run()` with events | Clock + scheduled signal changes (reset, interrupts) |

### Engine-native bench lowering

For AXI-Stream, AXI-Lite, and AXI4 testbenches with fixed (known-at-test-time)
stimulus, `compile_native` wraps the DUT **and** the bench logic together into
a single compiled module. This gives compiled-engine speeds without the
coroutine overhead of the Python `Testbench`.

```python
from veriforge.sim.bench import (
    Testbench, compile_native,
    AXIStreamSourceLowering, AXIStreamSinkLowering,
    AXILiteMasterLowering, AXILiteOp,
    AXI4SlaveLowering,
)

bench = Testbench(dut)
lowered = compile_native(
    bench,
    lowerings={
        # Drive 4 AXIS beats into DUT's slave port
        "s_axis": AXIStreamSourceLowering(beats=[0xA1, 0xB2, 0xC3, 0xD4], data_width=8),
        # Capture 4 beats from DUT's master port
        "m_axis": AXIStreamSinkLowering(n_beats=4, data_width=8),
    },
)

sim = Simulator(lowered.wrapper, design=lowered.design, engine="compiled")
sim.fork(Clock(sim.signal("clk"), period=10))
sim.signal("rst_n").value = 0
sim.run(max_time=40)
sim.signal("rst_n").value = 1
sim.run(max_time=10 * 60)

# Read captured beats
for i in range(4):
    print(f"cap[{i}] = {int(sim.signal(f'm_axis_cap_{i}').value):#04x}")
assert int(sim.signal("m_axis_snk_done").value) == 1
```

Supported lowerings:

| Class | DUT-side role | Purpose |
|---|---|---|
| `AXIStreamSourceLowering(beats, data_width)` | AXI-Stream slave | Fixed-beat source |
| `AXIStreamSinkLowering(n_beats, data_width)` | AXI-Stream master | Beat capture |
| `AXILiteMasterLowering(operations, ...)` | AXI-Lite slave | Scripted write/read |
| `AXILiteSlaveLowering(memory_depth, ...)` | AXI-Lite master | Memory-backed slave responder |
| `AXI4SlaveLowering(memory_depth, ...)` | AXI4 master | Memory-backed INCR-burst responder |
| `AXI4MasterLowering(operations, ...)` | AXI4 slave | Scripted single-beat write/read master |
| `MemBusMasterLowering(operations, ...)` | MemBus slave | Scripted synchronous-bus write/read master |
| `MemBusResponderLowering(memory_depth, ...)` | MemBus master | Memory-backed synchronous-bus slave responder |

See [bench_native_lowering.md](simulation/bench_native_lowering.md) for full
API, examples, signal naming, and performance guidance.

For the full Python `Testbench` proxy API reference (all proxy types,
backpressure, multi-domain, overrides, error handling) see
[bench_usage.md](simulation/bench_usage.md).

---

## 13b. Backpressure / bandwidth throttling (PauseGenerator)

Any endpoint's `pause` attribute accepts either a plain `bool` or a callable
`PauseGenerator`. When callable, the generator is invoked **exactly once per
clock cycle** in `tick_pre`, so the RNG state advances at the correct rate
regardless of how many tick phases run per cycle.

### What gets gated

| Endpoint | What `pause=True` asserts |
|---|---|
| `AXIStreamSource` / `StreamSource` | `tvalid` / `valid` held low |
| `AXIStreamSink` / `StreamSink` | `tready` / `ready` held low |
| `AXILiteResponder` (always_ready) | `awready` + `wready` + `arready` held low |
| `AXI4Responder` (always_ready) | `awready` + `wready` + `arready` held low |

### PauseGenerator

```python
from veriforge.sim.endpoints import PauseGenerator

# Pause 1 in every 4 cycles — ~75% throughput, random.
gen = PauseGenerator(1, 4)

# Same bandwidth with a fixed seed (reproducible sequences).
gen = PauseGenerator(1, 4, seed=42)

# Factory shortcuts.
gen = PauseGenerator.never()           # always False — full bandwidth
gen = PauseGenerator.always()          # always True  — zero bandwidth
gen = PauseGenerator.duty(0.3)         # ~30% pause rate
gen = PauseGenerator.duty(0.3, seed=7) # seeded
```

### Direct endpoint usage

```python
from veriforge.sim.endpoints import (
    AXIStreamSource, AXIStreamSink, EndpointCoordinator, PauseGenerator,
)

source = AXIStreamSource(sim, "s_axis")
sink   = AXIStreamSink(sim, "m_axis")
coord  = EndpointCoordinator(sim, [source, sink])

# Throttle source to ~50% bandwidth.
source.pause = PauseGenerator(1, 2, seed=0)

# Apply backpressure on the sink side instead.
sink.pause = PauseGenerator.duty(0.25)

# Plain bool still works as before.
source.pause = True   # stall permanently
source.pause = False  # clear (default)
```

### Via proxy (Testbench / bench-style)

All four proxy classes (`AXIStreamProxy`, `AXILiteProxy`, `AXI4Proxy`,
`StreamProxy`) expose a `pause` property that forwards to the underlying
endpoint:

```python
bench = Testbench(dut)
# ...
m_axis = bench.iface("m_axis")  # AXIStreamProxy, role="master" (sink)
s_axis = bench.iface("s_axis")  # AXIStreamProxy, role="slave" (source)

# Throttle the source during a burst.
s_axis.pause = PauseGenerator(1, 3, seed=1)
s_axis.put([1, 2, 3, 4, 5, 6])
bench.step(20)
s_axis.pause = False

# Backpressure on the sink while draining.
m_axis.pause = PauseGenerator.duty(0.4)
bench.step(30)
```

### AXI-Lite / AXI4 responder throttle

For DUT-master paths, pausing the responder randomly withholds the
`awready`/`wready`/`arready` handshake, stressing the DUT's ability to handle
delayed acknowledgements:

```python
axi_lite = bench.iface("m_axi_lite")  # AXILiteProxy, role="master"
axi_lite.pause = PauseGenerator(1, 4, seed=99)
bench.step(200)
axi_lite.pause = False
```

---

## 13c. Testbench access levels

Every testbench interaction falls into one of three access levels. Understanding
which level applies to a given port determines both the API you use and the timing
rules that apply.

### Level 1 — Proxy API (recognized interfaces)

`build_testbench` auto-detects AXI-Stream, AXI-Lite, and AXI4 interface bundles by
scanning port names. Each detected bundle gets a **proxy object** with a high-level
API. All timing is managed internally; you never call tick_pre/sample_pre/tick_post.

```python
bench = build_testbench(DUT_PATH)
with bench.run():
    bench.reset_all()
    src  = bench.iface("s_axis")   # AXIStreamProxy — put/wait_drain/get
    axil = bench.iface("axil")     # AXILiteProxy   — read/write
    ram  = bench.iface("ram")      # AXI4Proxy      — read/write

    src.put([0x10, 0x20, 0x30])
    axil.write(0x00, 0xDEAD_BEEF)
    src.wait_drain()
    pkt = src.get()               # blocks (advances clock) until frame arrives
```

Recognized interface types:

| Port pattern | Proxy class | Role |
|---|---|---|
| `<prefix>_tdata`, `_tvalid`, `_tready`, `_tlast` | `AXIStreamProxy` | source / sink |
| `<prefix>_awaddr`, `_awvalid`, … | `AXILiteProxy` | master / slave |
| `<prefix>_awaddr`, `_awid`, `_awlen`, … | `AXI4Proxy` | master / slave |
| `<prefix>_valid`, `<prefix>_ready` (no `_t`-prefix) | `StreamProxy` | plain handshake source / sink |
| `<prefix>_addr`, `_wen`/`_we`, `_wdata`, `_rdata` | `MemBusProxy` | synchronous SRAM-style master / slave |

### Level 2 — Raw signal access (non-interface ports)

Any port that does not match a recognized interface pattern — status flags, FIFO
depth counters, interrupt lines, custom config registers, enable bits — must be
accessed directly via `bench.sim.signal()` / `bench.sim.drive()` / `bench.step()`.

```python
with bench.run():
    bench.reset_all()

    # Configure DUT before traffic
    bench.sim.drive("cfg_threshold", 8)
    bench.sim.drive("enable", 1)
    bench.step(2)                          # settle the config

    # Run normal proxy traffic
    src = bench.iface("s_axis")
    src.put(list(range(16)))
    src.wait_drain()

    # Read non-interface status signals after proxy work has finished
    overflow = int(bench.sim.signal("s_status_overflow").value)
    depth    = int(bench.sim.signal("s_status_depth").value)
    assert overflow == 0, f"FIFO overflowed: depth={depth}"
```

**Timing rule for Level 2**: reading a signal after `wait_drain()`, `bench.step()`,
or any proxy method that advances the clock is safe — you are reading between clock
cycles, after all NBA updates have settled. What is unsafe is reading a registered
signal in the same moment a clock edge fires (i.e. inside a `tick_post()` callback).
Since Level 2 code runs between proxy calls, not inside callbacks, this hazard
normally does not arise.

`bench.step(n)` advances exactly `n` clock cycles without driving any interfaces —
useful for adding gaps, settling config signals, or waiting for a DUT pipeline to
flush.

### Level 3 — Custom endpoint class (new reusable protocol drivers)

Only needed when implementing a **new protocol** that the auto-detector does not
recognise (SPI, I2C, custom memory bus, etc.) and you want it to participate in the
`EndpointCoordinator` tick lifecycle.

Implement three hooks and register the class with the coordinator:

```python
class MySPIMaster:
    def tick_pre(self) -> None:
        """Drive output signals for this clock cycle (before clock edge)."""
        self.sim.drive("sck", self._next_sck)
        self.sim.drive("mosi", self._next_mosi)

    def sample_pre(self) -> None:
        """Snapshot DUT outputs — stable pre-edge values (D-input state)."""
        self._sampled_miso = int(self.sim.signal("miso").value)

    def tick_post(self) -> None:
        """Act on the snapshot taken in sample_pre (NOT on live signal values)."""
        if self._sampled_miso:
            self._rx_buffer.append(self._sampled_miso)

coord = EndpointCoordinator(sim, [MySPIMaster(sim)], clock_name="clk")
coord.run_until(lambda: done, max_steps=1000, message="SPI transfer timeout")
```

**Critical rule**: read registered DUT outputs in `sample_pre()`, never in
`tick_post()`. `tick_post()` runs after `run_step()` has applied all Non-Blocking
Assignments — signal values there reflect the *next* cycle's state, not the clock
edge just observed. See
[endpoint_timing_model.md](simulation/endpoint_timing_model.md) for a full
explanation and the concrete bug example that motivated this rule.

### Summary

| Situation | Level | API |
|---|---|---|
| AXI-Stream, AXI-Lite, AXI4 ports | 1 | `bench.iface("prefix")` → proxy |
| Status flags, config regs, custom ports | 2 | `bench.sim.signal()` / `bench.sim.drive()` / `bench.step()` |
| Multi-domain CDC (separate clock pins) | 2 | `Simulator` + `MultiDomainRunner` directly |
| New reusable protocol driver | 3 | Implement tick_pre / sample_pre / tick_post |

---

## 14. VCD waveform output

The `VcdWriter` generates IEEE 1364-2001 compliant VCD files for GTKWave or similar viewers.

For simulator-driven tracing, the shared helper is `attach_vcd(...)` from
`veriforge.sim`. This is the reusable API behind the current PULP AXI pytest
waveform flow.

### Standalone VCD writing

```python
from veriforge.sim import VcdWriter, Value

with VcdWriter("output.vcd", timescale="1ns") as vcd:
    vcd.add_signal("clk", width=1)
    vcd.add_signal("count", width=8, scope="counter")
    vcd.write_header()

    vcd.set_time(0)
    vcd.change("clk", Value(0, width=1))
    vcd.change("count", Value(0, width=8))

    vcd.set_time(5)
    vcd.change("clk", Value(1, width=1))
    vcd.change("count", Value(1, width=8))
```

### VCD API

| Method | Description |
|--------|-------------|
| `add_signal(name, width, scope)` | Register signal for tracing |
| `write_header()` | Emit VCD header (call after all `add_signal`) |
| `set_time(t)` | Advance VCD time |
| `change(name, value)` | Record value change (auto-deduplicates) |
| `dump_all(time, signals_dict)` | Dump all signals at once |
| `write_initial(signals_dict)` | Write `$dumpvars` section |
| `finalize()` | Flush and close |

Supports context manager (`with VcdWriter(...) as vcd:`).

### Capturing VCDs from AXI pytest regressions

The PULP AXI regression file `tests/test_sim/test_pulp_axi_examples.py` supports
copy-paste waveform capture through the pytest option `--vcd-dir`.

#### AXI-Lite regs example

Reference engine:

```powershell
uv run pytest tests/test_sim/test_pulp_axi_examples.py::test_axi_lite_regs_cross_engine[reference] --vcd-dir .\vcd_out --tb=no -q
```

VM engine:

```powershell
uv run pytest tests/test_sim/test_pulp_axi_examples.py::test_axi_lite_regs_cross_engine[vm] --vcd-dir .\vcd_out --tb=no -q
```

Generated files:

```text
.\vcd_out\axi_lite_regs_basic_reference.vcd
.\vcd_out\axi_lite_regs_prot_reference.vcd
```

The `basic` waveform covers the normal read/write path. The `prot` waveform covers
the protected-access checks.

#### AXI-Lite DW converter example

Reference engine:

```powershell
uv run pytest tests/test_sim/test_pulp_axi_examples.py::test_axi_lite_dw_converter_cross_engine[reference] --vcd-dir .\vcd_out --tb=no -q
```

VM engine:

```powershell
uv run pytest tests/test_sim/test_pulp_axi_examples.py::test_axi_lite_dw_converter_cross_engine[vm] --vcd-dir .\vcd_out --tb=no -q
```

Generated files include:

```text
.\vcd_out\axi_lite_dw_down_manual_reference.vcd
.\vcd_out\axi_lite_dw_up_reference.vcd
.\vcd_out\axi_lite_dw_same_reference.vcd
```

#### Inspect the output

List the written files:

```powershell
Get-ChildItem .\vcd_out
```

Open a waveform in GTKWave:

```powershell
gtkwave .\vcd_out\axi_lite_dw_down_manual_reference.vcd
```

#### Notes

- Run commands from the repository root.
- `--vcd-dir` creates the directory if needed.
- Without `--vcd-dir`, the tests run normally but do not emit `.vcd` files.
- On Windows, this test module currently targets the `reference` and `vm` engines.

### Attaching a VCD recorder from Python

If you are driving the simulator directly from Python, use `attach_vcd(...)` as a
context manager around the portion of the simulation you want to trace.

```python
from veriforge.sim import Clock, Simulator, attach_vcd

sim = Simulator(mod, engine="reference")
sim.fork(Clock(sim.signal("clk"), period=10))

with attach_vcd(sim, "waves.vcd"):
    sim.run(max_time=100)
```

This records initial values immediately, appends changes after each time step,
and restores any existing scheduler callback when the context exits.

### Cross-simulator validation

The `vcd_compare` module can parse and diff VCD files for cross-simulator validation.
Validation-oriented tests are in `tests/test_validation/`.

### IcarusCosim — cross-check against Icarus Verilog

The `IcarusCosim` class automates running Icarus Verilog alongside our simulator
and comparing results. It handles finding Icarus, compiling, running, parsing VCD,
and comparing signals — all in one API call.

**Requirements:** Icarus Verilog (`iverilog` + `vvp`) installed and on PATH,
or at `C:\iverilog\bin` on Windows.

#### Single-file usage

```python
from veriforge.sim import IcarusCosim

verilog = r"""
module test;
    reg clk = 0;
    reg [7:0] count = 0;
    always #5 clk = ~clk;
    always @(posedge clk) count <= count + 1;
    initial begin
        $dumpfile("test.vcd");
        $dumpvars(0, test);
        #100 $finish;
    end
endmodule
"""

cosim = IcarusCosim(verilog_src=verilog)
result = cosim.run(engine="reference", max_time=100)
assert not result.diffs, "\n".join(result.diffs)
```

#### Multi-file project

```python
from veriforge.sim import IcarusCosim

cosim = IcarusCosim(
    files=["rtl/top.v", "rtl/sub.v", "sim/testbench.v"],
    top_module="testbench",
    defines={"SIM": "1"},
    work_dir="sim/",  # cwd for $readmemh etc.
)
result = cosim.run(engine="reference", max_time=5000, verbose=True)
for d in result.diffs:
    print(d)
```

#### Cycle-by-cycle comparison

For targeted debugging, `run_cycle_by_cycle()` steps both simulators
one clock at a time and reports the first cycle with signal mismatches:

```python
cosim = IcarusCosim(
    files=["rtl/cpu.v", "sim/testbench.v"],
    top_module="testbench",
    defines={"SIM": "1"},
    work_dir="sim/",
)

mismatch = cosim.run_cycle_by_cycle(
    engine="reference",
    max_cycles=300,
    reset_cycles=10,
    clock_name="clk",
    reset_name="rst",
    verbose=True,
)

if mismatch:
    print(f"First mismatch at cycle {mismatch.cycle}:")
    for sig, icarus_val, our_val in mismatch.signals:
        print(f"  {sig}: icarus={icarus_val} ours={our_val}")
```

#### IcarusCosim API

| Method / Constructor | Description |
|---------------------|-------------|
| `IcarusCosim(verilog_src=..., files=..., top_module=..., defines=..., work_dir=...)` | Set up cosim |
| `cosim.run(engine, max_time, signals, ignore_signals, verbose)` | VCD-based full comparison |
| `cosim.run_icarus()` | Run Icarus only, return VCD text |
| `cosim.run_cycle_by_cycle(engine, max_cycles, reset_cycles, clock_name, ...)` | Cycle-level comparison |
| `find_icarus("iverilog")` | Locate Icarus executables |
| `record_vcd(sim, max_time)` | Run simulator and capture VCD as string |

| Return type | Description |
|-------------|-------------|
| `CosimResult` | `.diffs` (list of strings), `.icarus_signal_count`, `.ref_signal_count`, `.compared_signal_count`, `.icarus_vcd` |
| `CycleMismatch` | `.cycle` (int), `.signals` (list of `(name, icarus_val, our_val)`) |

---

## 15. Model introspection and serialization

Model objects provide lookup methods and JSON serialization:

```python
mod = design.modules[0]

# Lookup by name
port = mod.get_port("clk")
net  = mod.get_net("data_bus")
var  = mod.get_variable("state")
par  = mod.get_parameter("WIDTH")

# Filtered port lists
mod.input_ports()    # all input ports
mod.output_ports()   # all output ports
mod.inout_ports()    # all inout ports
mod.all_signals()    # all nets + variables in declaration order

# Design-level
design.get_module("counter")
design.get_top_modules()     # modules never instantiated by others

# JSON serialization
json_str = design.to_json(indent=2)
print(json_str[:500])
```

---

## 16. Grammar and language support visibility

Use grammar tooling to inspect parser coverage, but treat the generated grammar
docs as **parser metadata**, not as a complete runtime compatibility matrix.

For a cross-surface support overview, see
[`notes/support_matrix.md`](support_matrix.md). That matrix summarizes parser,
model, emitter, simulator, DSL, converter, and LSP support while keeping
compiled-engine details delegated to the active compiled simulation plan.

`docs/grammar_support.md` is auto-generated from `verilog.lark` metadata tags.
It is useful for seeing which grammar rules exist and how they are prioritized,
but it does **not** fully capture the difference between:

1. grammar acceptance,
2. tree-to-model extraction,
3. elaboration / flattening support, and
4. simulation support in the reference, VM, and compiled engines.

### Practical support matrix

This table is the better high-level guide for the current intended subset.

| Area | Parse / model | Elaboration / simulation | Notes |
|------|---------------|--------------------------|-------|
| Verilog 2005 core RTL (`module`, ports, params, assigns, `always`, `case`, `for`) | ✅ | ✅ | Core project surface; shared parser/model/analysis/sim coverage is broad. |
| SV low-hanging RTL syntax (`logic`, `always_comb/ff/latch`, `unique` / `priority case`, SV int types) | ✅ | ✅ | Covered by parser tests and cross-engine simulation tests, including unnamed procedural block locals. |
| Generate + hierarchy + parameter propagation | ✅ | ✅ | Simulators run hierarchical designs through elaboration / flattening first. |
| Declared `signed` nets / variables | ✅ | ✅ | IEEE 1364-2005 §5.5 expression-signedness propagation: signed comparison, arithmetic right-shift, context-determined sign-extension, and signed division/modulus all activate from declarations — not only from explicit `$signed()` wrappers. All four engines. |
| User-defined functions and tasks | ✅ | ✅ | Supported on reference, VM, and compiled engines for the tested scalar input / output patterns. |
| Packages / imports / typedef enums | ✅ | ✅ | Imported params, typedefs, and enum resolution are part of the live simulation subset. |
| Packed structs / unions / assignment patterns | ✅ | ⚠️ | Broad support exists, but compiled codegen still has edge cases around named assignment-pattern layout lookup and wide internals. |
| Interfaces / modports | ✅ | ⚠️ | Parser/model support exists and elaboration has binding support, but this is not yet a broadly documented shared runtime subset. |
| One-dimensional memories / unpacked arrays | ✅ | ✅ / ⚠️ | Reference and VM support is broad; compiled now flattens multi-dimensional unpacked memories for full element accesses, but broader subarray semantics remain limited. |
| Specify blocks | ✅ | ❌ | Parsed and round-tripped opaquely; not executed as timing semantics. |
| Gate / UDP / config-library source text | ⚠️ | ❌ | Some grammar coverage exists, but this is outside the main RTL-oriented execution subset. |
| SV verification features (classes, assertions/SVA, covergroups, constraints/randomize, dynamic/associative arrays, queues, bind/program) | ❌ | ❌ | Explicitly out of scope for the current project subset. |

### Current targeted language gaps

These are the most actionable open support gaps inside the intended RTL-oriented
subset:

| Gap | Current behavior | Good focused repro |
|-----|------------------|--------------------|
| Compiled `>64`-bit internals | Partial support only | Wide compiled regressions in `tests/test_sim/test_compiled.py` |
| Compiled raw-codegen limits | Wide internals and some multi-dimensional subarray semantics remain partial | Focused regressions in `tests/test_sim/test_compiled.py` |

See `notes/known_issues.md` for the maintained issue list and current status.

### Generate grammar dependency tree

```bash
uv run python -m veriforge.lark_file.gen_tree --all --depth 5
```

Also see:
- `docs/grammar_support.md`
- `docs/grammar_deps.json`

---

## 17. Typical end-to-end workflows

### A) Analyze an existing RTL repository
1. Parse with `parse_directory(..., preprocess=True)`
2. Run `analyze_design()`
3. Export diagnostics/reports
4. Emit normalized/formatted Verilog when needed

### B) Build generated RTL from Python
1. Create modules with DSL builders
2. Reuse `dsl.lib` components
3. Emit Verilog
4. Simulate and inspect VCD

### C) Migrate Verilog to DSL
1. Parse source RTL to model
2. Translate with `design_to_dsl()` or `export_dsl_project()`
3. Re-emit and run round-trip checks

---

## 18. Testing guidance

Run focused tests when working in a specific area:

```bash
# Parser/model
uv run pytest tests/test_verilog_parser/ --tb=no -q
uv run pytest tests/test_model/ --tb=no -q

# DSL + conversion
uv run pytest tests/test_dsl/ --tb=no -q

# Simulation
uv run pytest tests/test_sim/ --tb=no -q

# Project-level parsing
uv run pytest tests/test_project/ --tb=no -q
```

Project notes document additional testing conventions.

---

## 19. Pointers to related docs

- Quick path: [getting_started.md](getting_started.md)
- DSL reference: [dsl_guide.md](dsl_guide.md)
- Architecture map: [python_overview.md](python_overview.md)
- Semantic model notes: [semantic_model.md](semantic_model.md)
- Simulation notes: [simulator_python.md](simulation/simulator_python.md),
  [simulator_bytecode_vm.md](simulation/simulator_bytecode_vm.md),
  [simulator_compile_cython.md](simulation/simulator_compile_cython.md)
- Simulator debugging: [debug.md](simulation/debug.md)

---

## 20. Practical tips

- Prefer `uv run ...` commands in this repository.
- Start with focused tests for the area you changed.
- Use DSL examples as known-good templates before building larger generators.
- Keep parse + analysis + simulation scripts small and composable.

If you only need an overview and first steps, use [getting_started.md](getting_started.md).
