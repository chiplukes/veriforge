# DarkRISCV Feasibility Study

## Project Overview

**Repository:** https://github.com/darklife/darkriscv
**License:** BSD-3-Clause
**Stars:** 2.5k | **Language:** 63% Verilog, 16% SystemVerilog, 14% VHDL
**Author:** Marcelo Samsoniuk

DarkRISCV is a small, fast, open-source RISC-V (RV32I/RV32E) processor core
written in ~600 lines of Verilog, with a surrounding SoC (DarkSoCV) for
simulation and FPGA deployment. It was famously written in a single night and
has grown into a well-tested, versatile soft processor.

## Architecture

- **ISA:** RV32I (32 registers) or RV32E (16 registers, smaller/faster)
- **Pipeline:** Configurable 2-stage or 3-stage
- **Harvard architecture** with optional von Neumann bridge + caches
- **Optional features** (all compile-time `ifdef`): interrupts, CSRs, ebreak/debug,
  multi-threading, MAC instruction, coprocessor interface, big-endian, SDRAM

### RTL Hierarchy

```
darksocv (SoC top)
├── darkpll        (clock generation)
├── darkbridge     (Harvard/von Neumann bridge)
│   ├── darkriscv  (CPU core)
│   └── darkcache  (2x instruction cache)
├── darkram        (block RAM)
├── darkio         (peripherals)
│   ├── darkuart   (UART)
│   └── darkspi    (SPI, optional)
└── sdram          (SDRAM controller, optional)
```

### Key Files for Simulation

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `rtl/config.vh` | ~200 | Configuration defines — ALL features controlled here |
| `rtl/darkriscv.v` | ~600 | CPU core — instruction decode, ALU, pipeline, register file |
| `rtl/darksocv.v` | ~400 | SoC top — memory, IO, interconnect |
| `rtl/darkuart.v` | ~200 | UART peripheral |
| `rtl/darkbridge.v` | ~200 | Bus bridge (Harvard ↔ von Neumann) |
| `rtl/darkram.v` | ~150 | Block RAM with RMW support |
| `rtl/darkio.v` | ~200 | IO subsystem (timer, GPIO, board ID) |
| `sim/darksimv.v` | ~60 | Simulation testbench (clock gen, reset, instantiation) |

**Total for minimum simulation:** ~5-7 files, ~1500-2000 lines

## Verilog Features Used

### Fully Used (must support)
- **Preprocessor directives:** `define, `ifdef, `ifndef, `else, `endif, `include, `timescale, `undef
  — **Used EVERYWHERE**. Every feature toggle is a `ifdef`. This is the #1 requirement.
- **Module declarations** with parameters (#(parameter CPTR = 0))
- **Port declarations:** input, output, wire, reg with bit ranges [31:0]
- **Always blocks:** `always@(posedge CLK)` — all sequential logic, single clock edge
- **Continuous assign:** `assign` statements with complex combinational expressions
- **Ternary operators:** Nested ternary chains for muxing (very heavy use)
- **Bit slicing:** `IDATA[6:0]`, `IDATA[31:25]`, etc.
- **Concatenation:** `{A, B, C}`
- **Register arrays:** `reg [31:0] REGS [0:31]` — the register file
- **Case/endcase:** Instruction decode and CSR handling
- **$readmemh:** Firmware loading from hex files into BRAM
- **$display/$finish:** Simulation reporting and termination
- **$signed/>>>:** Arithmetic shift right for signed operations
- **Integer/for loops:** In `initial` blocks for register initialization
- **Module instantiation:** Named port connections `.CLK(CLK)`

### Not Used (good news)
- **No generate blocks** in the core (simplifies things)
- **No SystemVerilog** constructs in core RTL (despite repo language stats)
- **No vendor primitives** — all logic inferred from behavioral Verilog
- **No tri-state** in core (only in testbench SDRAM model, which is optional)
- **No tasks/functions** in the core
- **No real-number types**

## Gap Analysis vs Our Simulator

### Preprocessor Support

| Directive | Used in DarkRISCV | Our Support |
|-----------|-------------------|-------------|
| `` `define `` | Yes — opcode definitions, config | ✅ |
| `` `ifdef / `ifndef `` | Yes — EVERY feature toggle | ✅ |
| `` `else / `endif `` | Yes — paired with ifdef | ✅ |
| `` `include `` | Yes — config.vh included everywhere | ✅ |
| `` `timescale `` | Yes — `1ns / 1ps` | ✅ |
| `` `undef `` | Yes — in config.vh | ✅ |

### Supported Features

| Feature | Reference Engine | VM Engine | Compiled Engine |
|---------|-----------------|-----------|-----------------|
| Module hierarchy | ✅ | ✅ | ✅ |
| always @(posedge) | ✅ | ✅ | ✅ |
| Continuous assign | ✅ | ✅ | ✅ |
| Ternary operator | ✅ | ✅ | ✅ |
| Bit slicing | ✅ | ✅ | ✅ |
| Concatenation | ✅ | ✅ | ✅ |
| Case statements | ✅ | ✅ | ✅ |
| $signed / >>> | ✅ | ✅ | ✅ |
| $display / $finish | ✅ | ✅ | ✅ (fallback) |
| Module instantiation | ✅ | ✅ | ✅ |
| Parameters | ✅ | ✅ | ✅ |
| Multi-file projects | ✅ (parse_directory) | ✅ | ✅ |
| **Preprocessor** | ✅ | ✅ | ✅ |
| **$readmemh** | ✅ | ✅ | ✅ |
| **$display format strings** | ✅ | ✅ | ✅ |

## What Was Done

All three implementation phases were completed:

**Preprocessor (`src/veriforge/preprocessor.py`)**: Full text-level preprocessor
built before the Lark parser. Supports `` `define `` / `` `undef ``, `` `ifdef `` /
`` `ifndef `` / `` `elsif `` / `` `else `` / `` `endif ``, `` `include `` with search
path, and `` `timescale `` stripping. Accepts a `defines` dict simulating `-D` flags.

**Feature gaps closed**: `$readmemh` wired in all three engines. `$display` format
specifiers (%h, %d, %b, %x, %0d) supported. Memory array patterns (`reg [31:0] REGS [0:31]`)
validated. `$dumpfile`/`$dumpvars` wired to existing VCD writer.

**DarkRISCV simulated**: Working simulation under `examples/darkriscv/` with
`run_sim.py` (reference/vm), `run_fast.py` (vm-fast), and `profile_compiled.py`
(compiled). `diag_compare.py` cross-validates engines. Firmware loaded via `$readmemh`
from hex files; simulation produces correct boot messages and instruction execution stats.

## Why DarkRISCV is a Good Target

1. **Small but real** — ~1500 lines of Verilog for a complete working CPU+SoC
2. **Pure behavioral Verilog** — no vendor primitives, no generate blocks
3. **Well-tested** — used in academic papers, runs on 16+ FPGA boards
4. **Reference available** — Icarus Verilog simulation already works, so we can compare
5. **Incrementally testable** — can start with just the core, add SoC pieces later
6. **Interesting complexity** — pipelines, register files, instruction decode, memory
7. **BSD license** — no restrictions on use
8. **You've used it before** — familiarity with the design reduces debugging time

## Risks

1. **Preprocessor scope creep** — could grow into a large sub-project
2. **Simulation speed** — a CPU executing firmware may need many cycles (millions);
   compiled engine would be important for practical simulation times
3. **Debug difficulty** — if the simulation produces wrong output, debugging a CPU
   pipeline at the Verilog level through our simulator adds a layer of indirection
4. **Memory initialization** — `$readmemh` with relative paths and hex format needs
   to work correctly

## Outcome

DarkRISCV was a successful target. The preprocessor was the key enabler — once built,
the existing simulator handled the rest of DarkRISCV's Verilog with minimal gaps.
The working simulation is in `examples/darkriscv/`. Each infrastructure piece
(preprocessor, $readmemh, format strings) benefited the broader project beyond
just this one example.
