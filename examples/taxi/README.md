# taxi Examples

Examples using Alex Forencich's [taxi](https://github.com/alexforencich/taxi)
and [verilog-axis](https://github.com/alexforencich/verilog-axis) /
[verilog-axi](https://github.com/alexforencich/verilog-axi) IPs with the
veriforge simulation framework.

## Directory layout

```
examples/taxi/
├── ATTRIBUTION.md          — License information for vendor IP
├── README.md               — This file
├── vendor/
│   ├── taxi/               — taxi SystemVerilog IP (CERN-OHL-S-2.0 / MIT)
│   │   ├── axis/rtl/       — AXI-Stream modules + interface
│   │   ├── axi/rtl/        — AXI-Lite modules + interface
│   │   └── lib/sync/rtl/   — Reset/signal synchronizers
│   ├── verilog-axis/       — Flat Verilog 2001 AXI-Stream fallbacks (MIT)
│   └── verilog-axi/        — Flat Verilog 2001 AXI-Lite fallbacks (MIT)
├── wrappers/               — Flat-port SV wrappers around taxi interfaces
│   ├── axis_register_wrap.sv
│   └── axil_ram_wrap.sv
└── tb/                     — Python testbenches
    ├── test_axis_register.py
    └── test_axil_ram.py
```

## IP descriptions

### taxi_axis_register / axis_register
AXI-Stream skid buffer register (REG_TYPE=2). Provides full-throughput
pipelining with no bubble cycles. Useful for timing closure on high-speed
AXIS paths.

### taxi_axil_ram / axil_ram
AXI-Lite slave RAM. Supports arbitrary data width (default 32-bit) and
optional output pipeline register. WSTRB byte-enable on writes.

### taxi_axis_async_fifo / axis_async_fifo
AXI-Stream asynchronous FIFO for clock-domain crossing. Supports frame FIFO
mode, bad-frame dropping, and overflow marking.

### taxi_axis_broadcast / axis_broadcast
AXI-Stream broadcaster — replicates one input to N outputs with proper
backpressure handling across all outputs.

### taxi_axis_arb_mux / axis_arb_mux
AXI-Stream arbitrated multiplexer — M inputs to one output, with
configurable round-robin or priority arbitration.

## Running testbenches

```bash
# AXI-Stream register (taxi SV wrapper preferred, verilog-axis fallback)
uv run python examples/taxi/tb/test_axis_register.py

# AXI-Lite RAM (verilog-axi flat Verilog 2001)
uv run python examples/taxi/tb/test_axil_ram.py
```

## Running pytest tests

```bash
uv run pytest tests/test_dsl/test_taxi_axis_register.py tests/test_dsl/test_taxi_axil_ram.py -v
```

## Regenerating scaffolds with CLI

```bash
# Explain the planner's decisions for axis_register_wrap
uv run veriforge generate-python-testbench \
    examples/taxi/wrappers/axis_register_wrap.sv \
    --explain-plan

# Generate bench-style scaffold
uv run veriforge generate-python-testbench \
    examples/taxi/wrappers/axis_register_wrap.sv \
    --style bench

# Same for the verilog-axis fallback (flat Verilog 2001)
uv run veriforge generate-python-testbench \
    examples/taxi/vendor/verilog-axis/axis_register.v \
    --style bench

# AXI-Lite RAM (flat fallback)
uv run veriforge generate-python-testbench \
    examples/taxi/vendor/verilog-axi/axil_ram.v \
    --style bench
```

## SV wrapper approach vs verilog-axis fallback

The taxi IP uses SystemVerilog interfaces (`taxi_axis_if`, `taxi_axil_if`).
veriforge' auto-detection works on flat Verilog 2001 ports, so the
`wrappers/` directory contains thin wrappers that:

1. Instantiate the SV interface object
2. Connect flat `s_axis_*` / `m_axis_*` / `s_axil_*` ports to the interface
3. Instantiate the taxi module with the interface ports

This gives veriforge a normal flat-port DUT to detect and simulate,
while preserving the taxi interface-based IP unchanged.

If the SV wrapper cannot be parsed (e.g. unsupported SV constructs),
the testbenches automatically fall back to the equivalent Verilog 2001
module from verilog-axis / verilog-axi.
