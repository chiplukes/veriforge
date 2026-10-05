# `common_cells/stream_mux`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_mux` handshake and routing behavior
while fixing the import to a flat 3-input, 8-bit subset. This avoids the
assertion include, package helper, and typed unpacked-array surface that are
already stressed elsewhere by the typed `stream_xbar` import.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/stream_mux.sv`

## Why This Is Useful

This example adds the complementary selector primitive to the newly imported
`stream_join`. It exercises:

- selected-input data routing
- selected-input valid routing
- one-hot ready fanout to only the selected input
- immediate output changes when the select input changes
- ignoring non-selected valid inputs

## Local Layout

```text
examples/pulp/common_cells/stream_mux/
├── README.md
├── rtl/
│   └── stream_mux.sv
├── tb/
│   └── stream_mux_tb_local.sv
├── bench/
│   ├── stream_mux_bench.py
│   └── stream_mux_bench_top.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the mux to three 8-bit inputs and checks the
combinational routing directly with a Python-driven harness.

The transaction-level bench in `bench/stream_mux_bench.py` exposes the three
packed lanes as independent ready/valid sources. It queues one beat on each,
stalls the output, then accepts them in a different select order.

The checks cover:

- idle output valid when the selected input is invalid
- data and valid routing for two different selected inputs
- ready fanout only to the selected input
- immediate reroute when the selected input changes

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_mux/run_sim.py
uv run python examples/pulp/common_cells/stream_mux/bench/stream_mux_bench.py --engine vm-fast
```

Success is indicated by:

```text
PASS stream_mux python reference checks
PASS stream_mux python vm checks
PASS stream_mux python compiled checks
```
