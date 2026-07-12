# Public API Guide

This document defines the recommended public import surfaces for user code.
Prefer these imports in documentation, examples, and downstream projects.

The root package (`veriforge`) exports the most common entry points
directly, so the following one-liner imports work:

```python
from veriforge import parse_file, Testbench, AXIStreamSource, AXILiteMaster
```

Full root-package exports: `parse_file`, `parse_files`, `parse_directory`,
`Simulator`, `Clock`, `Value`, `Testbench`, `build_testbench`, `compile_native`,
`AXIStreamSource`, `AXIStreamSink`, `AXILiteMaster`, `AXI4Master`,
`MemBusMaster`, `PauseGenerator`, `detect_interfaces`.

For less common names and internal helpers, use the subpackage imports listed
below.

## Project parsing

Use `veriforge.project` for file, file-list, and directory parsing.

```python
from veriforge.project import parse_directory, parse_file, parse_files

design = parse_file("rtl/top.v", preprocess=True)
```

Recommended public functions:

| Import | Purpose |
| --- | --- |
| `parse_file` | Parse one Verilog/SystemVerilog file into a `Design`. |
| `parse_files` | Parse and merge an explicit file list. |
| `parse_directory` | Parse a directory tree by extension. |
| `export_dsl_project` | Export a parsed `Design` to Python DSL files. |
| `generate_python_testbench_skeleton` | Generate a Python simulator testbench skeleton. |

## Preprocessing

Use `veriforge.preprocessor` when preprocessing source text or files
directly.

```python
from veriforge.preprocessor import preprocess, preprocess_file
```

Recommended public functions:

| Import | Purpose |
| --- | --- |
| `preprocess` | Preprocess source text. |
| `preprocess_file` | Preprocess a source file. |

## Semantic model

Use `veriforge.model` for semantic model classes.

```python
from veriforge.model import Design, Module, Port, Net, Variable, Parameter
from veriforge.model import Identifier, Literal, BinaryOp, Range
```

Recommended public classes include:

| Group | Imports |
| --- | --- |
| Containers | `Design`, `Module`, `Package`, `Interface` |
| Declarations | `Port`, `Net`, `Variable`, `Parameter`, `FunctionDecl`, `TaskDecl` |
| Instances | `Instance`, `PortConnection`, `ParameterBinding` |
| Expressions | `Expression`, `Identifier`, `Literal`, `UnaryOp`, `BinaryOp`, `TernaryOp`, `Range`, `BitSelect`, `RangeSelect`, `PartSelect`, `Concatenation`, `Replication` |
| Statements | `Statement`, `BlockingAssign`, `NonblockingAssign`, `IfStatement`, `CaseStatement`, `ForLoop`, `WhileLoop`, `SeqBlock`, `ParBlock`, `SystemTaskCall` |
| Metadata | `VerilogNode`, `SourceLocation`, `Comment` |

## Code generation and formatting

Use `veriforge.codegen` for model-to-Verilog emission and formatting.

```python
from veriforge.codegen import emit_design, emit_module, FormatStyle, VerilogFormatter

print(emit_module(design.modules[0]))
```

Recommended public imports:

| Import | Purpose |
| --- | --- |
| `emit_module` | Emit one module. |
| `emit_design` | Emit a whole design. |
| `emit_interface` | Emit one SystemVerilog interface. |
| `emit_package` | Emit one SystemVerilog package. |
| `emit_expression` | Emit one expression node. |
| `FormatStyle` | Formatting style configuration. |
| `VerilogFormatter` | Formatter class. |
| `fmt_module`, `fmt_design` | Convenience formatting helpers. |

## Analysis

Use `veriforge.analysis` for semantic analysis passes.

```python
from veriforge.analysis import analyze_design, lint_design, infer_widths

analyze_design(design)
warnings = lint_design(design)
```

Recommended public imports:

| Import | Purpose |
| --- | --- |
| `analyze_design` | Run name resolution, linking, and connectivity analysis. |
| `link_instances` | Link instances to module definitions. |
| `resolve_names` | Resolve identifiers. |
| `infer_widths`, `infer_expr_width` | Infer widths for modules and expressions. |
| `fold_constants`, `const_fold`, `const_int` | Evaluate constant expressions. |
| `lint_design`, `lint_module` | Run lint checks. |
| `extract_clocks_resets` | Extract clock/reset information. |

## DSL construction

Use `veriforge.dsl` for Python hardware construction.

```python
from veriforge.dsl import Module, posedge, cat, mux
from veriforge.codegen import emit_module

with Module("counter") as m:
    clk = m.input("clk")
    q = m.output_reg("q", width=8)
    with m.always(posedge(clk)):
        q <<= q + 1

print(emit_module(m.build()))
```

Recommended public imports:

| Import | Purpose |
| --- | --- |
| `Module` | Main DSL module builder. |
| `Signal`, `Expr` | DSL expression objects. |
| `posedge`, `negedge` | Edge sensitivity helpers. |
| `cat`, `rep`, `mux` | Concatenation, replication, and ternary helpers. |
| `land`, `lor`, `lnot` | Logical helpers. |
| `reduce_and`, `reduce_or`, `reduce_xor` | Reduction helpers. |
| `Interface`, `BoundInterface` | DSL interface helpers. |

## Verilog-to-DSL conversion

Use `veriforge.convert` for model-to-DSL translation.

```python
from veriforge.convert import design_to_dsl, module_to_dsl
```

Recommended public imports:

| Import | Purpose |
| --- | --- |
| `module_to_dsl` | Convert one module to Python DSL source. |
| `design_to_dsl` | Convert a whole design to Python DSL source. |

## Simulation

Use `veriforge.sim` for the public simulator API and validation helpers.

```python
from veriforge.sim import Simulator, Clock, Value
```

Recommended public imports:

| Import | Purpose |
| --- | --- |
| `Simulator` | Main simulation entry point. |
| `Clock` | Testbench clock helper. |
| `Value` | Four-state value representation. |
| `VcdWriter`, `attach_vcd` | VCD output helpers. |
| `IcarusCosim`, `record_vcd`, `find_icarus` | External validation helpers. |

`Simulator` compiled-engine-only methods: `load_memory(name, data)` to bulk-write
a DSL memory before `batch_run`, `dump_memory(name, count)` to read it back, and
`memory_names` property to list available memories.  All three raise
`NotImplementedError` on non-compiled engines.

Compiled-simulator details and limitations are owned by
`notes\plans\plan_review_20260425_sim.md`.

## Simulation endpoints

Use `veriforge.sim.endpoints` for low-level protocol drivers and monitors.

```python
from veriforge.sim.endpoints import (
    AXIStreamSource, AXIStreamSink, AXIStreamFrame,
    AXILiteMaster, AXILiteResponder,
    AXI4Master, AXI4Responder,
    MemBusMaster, MemBusResponder,
    StreamSource, StreamSink,
    EndpointCoordinator, DomainCoordinator, MultiDomainRunner,
    PauseGenerator,
    detect_interfaces, detect_axi4_interfaces, detect_membus_interfaces,
)
```

| Import | Purpose |
| --- | --- |
| `AXIStreamSource` | Drives AXIS tvalid/tdata/tlast to a DUT slave port. |
| `AXIStreamSink` | Captures AXIS beats from a DUT master port. |
| `AXIStreamFrame` | Multi-beat AXIS frame container. |
| `AXILiteMaster` | Drives AXI-Lite AW/W/B/AR/R to a DUT slave port. |
| `AXILiteResponder` | Responds to a DUT AXI-Lite master; auto-ticks. |
| `AXI4Master` | Burst read/write to a DUT AXI4 slave (INCR). |
| `AXI4Responder` | Responds to a DUT AXI4 master; `.memory` dict. |
| `MemBusMaster` | SRAM/BRAM-style master; `.write()`, `.read()`. |
| `MemBusResponder` | SRAM/BRAM-style responder; auto-ticks; `.memory` dict. |
| `StreamSource` / `StreamSink` | Ready/valid stream (Pulp-style). |
| `PauseGenerator` | Random or duty-cycle backpressure; assign to `endpoint.pause`. |
| `EndpointCoordinator` | Ticks a set of endpoints in lock-step. |
| `MultiDomainRunner` | Coordinates multiple clock domains. |
| `detect_interfaces` | Infer AXIS/AXI-Lite/AXI4/MemBus/stream bundles from flat port names. |

## Bench framework

Use `veriforge.sim.bench` for the high-level transaction-level testbench runtime.

```python
from veriforge.sim.bench import (
    Testbench, Domain, make_bench,
    AXIStreamProxy, AXILiteProxy, AXI4Proxy, MemBusProxy, StreamProxy,
    BenchTimeoutError,
    build_plan, PlannerOverrides, TestbenchPlan,
    compile_native, LoweredDesign,
    AXIStreamSourceLowering, AXIStreamSinkLowering,
    AXILiteMasterLowering, AXILiteOp, AXILiteSlaveLowering,
    AXI4SlaveLowering,
)

bench = Testbench(dut, design=design)
s_axis = bench.iface("s_axis")   # AXIStreamProxy, role=slave (source)
m_axis = bench.iface("m_axis")   # AXIStreamProxy, role=master (sink)

def my_test(bench):
    s_axis.put([0xA1, 0xB2])
    result = m_axis.get(timeout=200)

bench.run(my_test)
```

### Runtime classes

| Import | Purpose |
| --- | --- |
| `Testbench` | Orchestrates clocks, resets, endpoints, and multi-domain runner. |
| `Domain` | One clock + reset + `DomainCoordinator` binding. |
| `make_bench` | Factory: `make_bench(dut, design=, overrides=)` → `Testbench`. |
| `BenchTimeoutError` | Raised when `get()` / `wait_drain()` exceed `timeout` cycles. |

### Proxy classes

| Import | Purpose |
| --- | --- |
| `AXIStreamProxy` | High-level AXIS proxy; `.put(data)`, `.get(timeout=)`, `.expect()`, `.pause=`. |
| `AXILiteProxy` | AXI-Lite proxy; `.read(addr)`, `.write(addr, data)`. Supports `role="slave"` or `role="master"`. |
| `AXI4Proxy` | AXI4 proxy (DUT-slave); `.read(addr, length)`, `.write(addr, data)`. |
| `MemBusProxy` | SRAM/BRAM-style proxy; `.read(addr)`, `.write(addr, data)`. |
| `StreamProxy` | Ready/valid stream proxy; `.put(data)`, `.get(timeout=)`. |

### Planner

| Import | Purpose |
| --- | --- |
| `build_plan` | Infer `TestbenchPlan` from a `Module` (clocks, resets, interfaces, domains). |
| `TestbenchPlan` | Dataclass: clock domains, reset specs, interface bindings, summary(). |
| `PlannerOverrides` | Explicit overrides for clock/reset/interface inference. |

### Engine-native lowering

`compile_native` wraps DUT + bench primitives into a single compiled module for
maximum speed.  See `notes/simulation/bench_native_lowering.md` for full details.

| Import | Purpose |
| --- | --- |
| `compile_native(bench, lowerings, ...)` | Build `LoweredDesign` from a `Testbench` + lowering dict. |
| `LoweredDesign` | `.wrapper`, `.design`, `.run(engine, cycles)`, `.batch_run(cycles)`. |
| `AXIStreamSourceLowering` | Fixed-beat AXIS source in Verilog DSL. |
| `AXIStreamSinkLowering` | Beat-capture AXIS sink in Verilog DSL. |
| `AXILiteMasterLowering` | Scripted AXI-Lite write/read sequence. |
| `AXILiteOp` | Single AXI-Lite operation (write or read). |
| `AXILiteSlaveLowering` | Memory-backed AXI-Lite responder for DUT master. |
| `AXI4SlaveLowering` | Burst-capable AXI4 responder for DUT master. |

## CLI and LSP entry points

| Command | Purpose |
| --- | --- |
| `veriforge` | CLI for parsing, reconstruction, summaries, testbench generation, and DSL export. |
| `python -m veriforge` | Module form of the CLI. |
| `veriforge-lsp` | Language Server Protocol server. |
| `python -m veriforge_lsp` | Module form of the LSP server. |

## Public API maintenance rules

1. Root-package imports (`from veriforge import X`) work for common names;
   use subpackage imports only for less common or internal names.
2. Do not document internal helper modules as public unless they are promoted
   intentionally.
3. Add public import tests before changing `veriforge.__init__`.
4. Keep README and examples aligned with this guide.
