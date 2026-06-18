# `common_cells/stream_to_mem`

Third imported PULP validation target.

## Status

Imported and locally runnable on the reference, VM, and compiled engines.

The reference engine uses a timed Verilog wrapper. The VM and compiled engines
use a Python-driven harness over fixed-parameter wrapper tops so the DUT
semantics can be validated without relying on repeated `Simulator.run()` calls
for clocked testbench control.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/stream_to_mem.sv`
- Upstream testbench: `test/stream_to_mem_tb.sv`

## Why This Example Matters

`stream_to_mem` is a compact ready/valid datapath block that exercises behavior
not covered by the earlier combinational imports:

- request-side backpressure
- bounded outstanding-transaction tracking
- response buffering with fall-through behavior
- the special `BufDepth = 0` no-buffer path

## Local Layout

```text
examples/pulp/common_cells/stream_to_mem/
├── README.md
├── rtl/
│   └── stream_to_mem.sv
├── tb/
│   ├── stream_to_mem_tb_local.sv
│   └── stream_to_mem_tb_vm_local.sv
└── run_sim.py
```

## Local Validation Approach

The upstream RTL depends on common-cells include macros and `stream_fifo`. The
local DUT keeps the same control intent but is rewritten into a single file with
plain procedural logic so it can run as a focused regression target.

The reference wrapper instantiates three configurations:

- `BufDepth = 0` to check the direct pass-through path
- `BufDepth = 1` to check single buffered response behavior and request stalling
- `BufDepth = 2` to check two outstanding requests, buffering, and reopen on drain

The stand-in memory behavior is deterministic:

- `BufDepth = 0`: same-cycle reflected response
- `BufDepth = 1`: one-cycle response latency
- `BufDepth = 2`: two-cycle response latency

For the VM and compiled engines, the example uses a separate wrapper that only
exposes fixed `BufDepth = 0`, `1`, and `2` DUT instances. The runner then drives
clock, reset, requests, and synthetic memory responses from Python instead of
depending on a timed Verilog testbench. This keeps the behavioral checks aligned
while avoiding the current stepped-engine limitation around repeated timed
testbench runs.

Current checks focus on:

- request acceptance and blocking at the outstanding limit
- stable response values while downstream backpressures
- reopening request flow when a buffered response drains
- exact response payloads for all three local scenarios

## Run

```text
uv run python examples/pulp/common_cells/stream_to_mem/run_sim.py
```

Expected pass markers:

```text
PASS stream_to_mem deterministic checks
PASS stream_to_mem python vm checks
PASS stream_to_mem python compiled checks
```
