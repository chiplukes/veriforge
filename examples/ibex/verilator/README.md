# Ibex Verilator Reference Simulation

Runs ibex_core with the same memory/firmware testbench as `../sim/testbench.v` using
Verilator, producing a VCD file for cross-simulation validation against our simulator
engines.

## Purpose

Generate a reference VCD trace that can be used for step-by-step signal comparison
against the veriforge simulator (reference/VM/compiled engines). Same approach
as picorv32 + Icarus Verilog, but uses Verilator since ibex is SystemVerilog.

## Prerequisites (WSL / Linux)

```bash
sudo apt install verilator build-essential
```

## Quick Start

```bash
# Generate firmware first (if not already done):
cd /path/to/veriforge
python examples/ibex/gen_firmware.py

# Then build and run:
cd examples/ibex/verilator
make
```

This will:
1. Copy `firmware.hex` from `../sim/`
2. Verilate the ibex design + testbench
3. Build the C++ simulation
4. Run it and produce `ibex_trace.vcd`

## Output

- **`ibex_trace.vcd`** — Full signal dump of the ibex core running RV32I instruction
  tests. Covers reset (10 cycles), execution (~300-500 cycles), and halt.
  Trace depth is 99 levels so all internal signals are captured.

## Usage for Cross-Simulation

```python
from veriforge.sim.vcd_compare import compare_vcd

# Compare our simulator output against the Verilator reference
results = compare_vcd(
    "examples/ibex/verilator/ibex_trace.vcd",  # reference
    "our_sim_output.vcd",                       # our simulator
)
```

Or manually inspect in GTKWave:
```bash
gtkwave ibex_trace.vcd
```

## Files

| File | Description |
|------|-------------|
| `tb_verilator.sv` | SystemVerilog testbench (same logic as `../sim/testbench.v`, adapted for Verilator) |
| `sim_main.cpp` | C++ driver: clock, reset, halt detection, VCD dump |
| `Makefile` | Build and run targets |

## Configuration

Edit `sim_main.cpp` constants:

| Constant | Default | Description |
|----------|---------|-------------|
| `RESET_CYCLES` | 10 | Clock cycles with reset asserted |
| `MAX_CYCLES` | 11000 | Timeout (matches testbench.v's 10100 cycle timeout) |
| `CLK_PERIOD_NS` | 10 | Clock period for VCD timestamps |

## Differences from `../sim/testbench.v`

- Clock/reset driven from C++ (no `always #5` / `initial`)
- ICache unpacked array inputs (`ic_tag_rdata_i`, `ic_data_rdata_i`) explicitly tied to zero
- Halt condition exposed as output port instead of `$finish`
- No `$display` statements (results visible from VCD and C++ output)
