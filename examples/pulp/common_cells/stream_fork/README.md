# `common_cells/stream_fork`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_fork` handshake structure directly
while fixing the import to a small 3-output subset and dropping the assertion
include that is not needed for the deterministic local regression.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/stream_fork.sv`

## Why This Is Useful

This example complements the standalone `stream_join`, `stream_mux`, and
`stream_demux` imports by checking the stateful one-to-many handshake case. It
exercises:

- fanout of one input valid to all outputs at the start of a transaction
- remembering which outputs have already handshaked under backpressure
- only asserting input ready once every output has handshaked exactly once
- returning to the idle state for the next transaction

## Local Layout

```text
examples/pulp/common_cells/stream_fork/
├── README.md
├── rtl/
│   └── stream_fork.sv
├── tb/
│   └── stream_fork_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the fork to three outputs and checks the sequential
ready/valid behavior with a Python-driven stepped clock harness.

The checks cover:

- idle state after reset
- partial handshakes that leave only the remaining outputs pending
- final input acceptance when the last pending output becomes ready
- clean restart for a fully-ready follow-on transaction

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_fork/run_sim.py
```

Success is indicated by:

```text
PASS stream_fork python reference checks
PASS stream_fork python vm checks
PASS stream_fork python compiled checks
```
