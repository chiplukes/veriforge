# DarkRISCV — RISC-V Simulation Example

## Overview

This directory contains the [DarkRISCV](https://github.com/darklife/darkriscv)
open-source RISC-V CPU core (BSD 3-Clause license, Copyright (c) 2018 Marcelo
Samsoniuk). The files here are used as a gold-standard integration test for the
veriforge simulator — preprocessing, parsing, and simulating a real-world
RISC-V SoC design.

## Why DarkRISCV?

DarkRISCV is an excellent target for our simulator because:

- **Small but complete**: ~2,000 lines of Verilog implementing a full RV32I CPU,
  SoC wrapper, UART, and memory subsystem
- **Heavy preprocessor usage**: `config.vh` uses `\`define`/`\`ifdef` extensively
  for feature selection — exercises our preprocessor thoroughly
- **$readmemh firmware loading**: Memory initialized via `$readmemh("darksocv.mem")`
  — exercises our memory array and $readmemh support
- **Observable output**: UART transmits characters via `$write`/`$display` during
  simulation — provides a clear pass/fail signal
- **3-stage pipeline**: Non-trivial microarchitecture with pipeline flushes,
  hazard handling, and wait-states
- **Harvard architecture**: Separate instruction and data buses through `darkbridge`

## File Structure

```
examples/darkriscv/
├── LICENSE              # BSD 3-Clause (original DarkRISCV license)
├── README.md            # This file
├── rtl/                 # RTL source files
│   ├── config.vh        # Configuration: defines for pipeline, features, board
│   ├── darkriscv.v      # RISC-V CPU core (RV32I, 3-stage pipeline)
│   ├── darksocv.v       # SoC wrapper (CPU + memory + peripherals)
│   ├── darkbridge.v     # Bus bridge (CPU ↔ memory/IO)
│   ├── darkram.v        # Block RAM with $readmemh initialization
│   ├── darkio.v         # I/O controller (UART, timers, LED, GPIO)
│   ├── darkuart.v       # UART peripheral (TX/RX with baud generator)
│   └── darkpll.v        # PLL/clock generator (pass-through in simulation)
├── sim/
│   └── darksimv.v       # Simulation testbench (clock gen, reset, VCD dump)
└── src/
    └── darksocv.mem     # Firmware hex file (loaded via $readmemh)
```

## Module Hierarchy

```
darksimv (testbench)
└── darksocv (SoC)
    ├── darkpll (clock/reset)
    ├── darkbridge (bus bridge)
    │   └── darkriscv (CPU core)
    ├── darkram (BRAM memory)
    └── darkio (I/O controller)
        └── darkuart (UART)
```

## Default Configuration

With no board-specific defines, `config.vh` selects:
- **`__3STAGE__`**: 3-stage pipeline (fetch → decode → execute)
- **`__HARVARD__`**: Separate instruction and data buses
- **`__PERFMETER__`**: Performance counters enabled
- **`MLEN 13`**: Memory address bits → 32KB (2^13 × 4 bytes)
- **`BOARD_CK 100000000`**: 100 MHz clock
- **`__RESETPC__ 32'd0`**: CPU starts execution at address 0
- **`__BAUD__ 868`**: UART at 115200 bps (100MHz / 115200)

## Preprocessor Defines for Simulation

To preprocess DarkRISCV for our simulator, define:
- **`__ICARUS__`**: Enables `SIMULATION` define (activates `$display`, VCD, etc.)

The preprocessor resolves all conditional compilation and produces clean Verilog
for parsing.

## Simulation Workflow

### Step 1: Preprocess

```python
from veriforge.preprocessor import preprocess_file

rtl_dir = "examples/darkriscv/rtl"
sim_dir = "examples/darkriscv/sim"

# Preprocess each file with __ICARUS__ defined
defines = {"__ICARUS__": "1"}
include_paths = [rtl_dir]

preprocessed = preprocess_file(
    f"{sim_dir}/darksimv.v",
    defines=defines,
    include_paths=include_paths,
)
```

### Step 2: Parse

```python
from veriforge.project import parse_files

design = parse_files(
    [
        f"{sim_dir}/darksimv.v",
        f"{rtl_dir}/darksocv.v",
        f"{rtl_dir}/darkbridge.v",
        f"{rtl_dir}/darkriscv.v",
        f"{rtl_dir}/darkram.v",
        f"{rtl_dir}/darkio.v",
        f"{rtl_dir}/darkuart.v",
        f"{rtl_dir}/darkpll.v",
    ],
    preprocess=True,
    defines=defines,
    include_paths=include_paths,
)
```

### Step 3: Simulate

```python
from veriforge.sim.scheduler import Simulator

sim = Simulator(design.get_module("darksimv"))
sim.run(max_time=100_000)  # run for 100us
```

## Known Challenges

The DarkRISCV design uses several Verilog constructs that push the boundaries
of our parser and simulator:

1. **Arithmetic in preprocessor macros**: `\`define __BAUD__ ((\`BOARD_CK/\`__UARTSPEED__))` —
   our preprocessor does text substitution but not arithmetic evaluation
2. **Complex `\`ifdef` nesting**: Board-specific configs with multiple levels of
   conditional compilation
3. **Memory array part-select writes**: `MEM[addr][31:24] <= data[31:24]` in darkram.v
4. **Simulation-only constructs**: `$dumpfile`, `$dumpvars`, `$display`, `$write`,
   `$fgetc`, `$fflush`, `$finish`, `$stop`
5. **Integer loop variables**: `integer i; for(i=0; ...)` patterns
6. **Wire/reg array of instances**: `NXPC2[TPTR]` — indexed register array access
7. **`casex` usage**: DarkRISCV doesn't use casex, but some Verilog designs do
8. **Hierarchical references**: `soc0.bridge0.core0.REGS[i]` in testbench

## Status

This is a work-in-progress integration test. Progress is tracked in
`notes/plan.md` under the "RISC-V simulation test" item.
