# `common_cells/stream_demux`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_demux` handshake structure directly.
This import stays on the small selector-only valid/ready primitive rather than a
larger data-carrying stream fabric.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/stream_demux.sv`

## Why This Is Useful

This example complements the standalone `stream_mux` import by checking the
opposite selector primitive. It exercises:

- one-hot valid fanout to only the selected output
- selected-output ready returning to the single input
- immediate reroute when the selector changes
- no valid fanout when the input stream is invalid

## Local Layout

```text
examples/pulp/common_cells/stream_demux/
├── README.md
├── rtl/
│   └── stream_demux.sv
├── tb/
│   └── stream_demux_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the demux to three outputs and checks the
combinational routing directly with a Python-driven harness.

The checks cover:

- idle valid fanout with ready following the selected output
- selected-output valid routing
- selected-output ready returning to the input
- immediate reroute when the selector changes

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_demux/run_sim.py
```

Success is indicated by:

```text
PASS stream_demux python reference checks
PASS stream_demux python vm checks
PASS stream_demux python compiled checks
```
