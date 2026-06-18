# `common_cells/stream_join`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_join` and `stream_join_dynamic`
handshake structure while removing the assertion include that is not needed for
this deterministic regression.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT files: `src/stream_join.sv`, `src/stream_join_dynamic.sv`
- Related upstream support bench: `test/stream_test.sv`

## Why This Is Useful

This example adds the small stream-side primitive that gates one output
handshake on a set of input valid signals. It exercises:

- no output valid until all selected inputs are valid
- no per-input ready until the joined output is both valid and accepted
- simultaneous ready fanout to all inputs when the joined handshake fires
- immediate deassertion again when any joined input drops

## Local Layout

```text
examples/pulp/common_cells/stream_join/
├── README.md
├── rtl/
│   ├── stream_join.sv
│   └── stream_join_dynamic.sv
├── tb/
│   └── stream_join_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the join to three inputs and checks the handshake
behavior directly with a Python-driven combinational harness.

The checks cover:

- idle state with no valid inputs
- partial-valid inputs staying blocked
- all-valid with downstream stalled
- all-valid with downstream ready, including simultaneous input ready fanout
- output deassertion after one input drops again

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_join/run_sim.py
```

Success is indicated by:

```text
PASS stream_join python reference checks
PASS stream_join python vm checks
PASS stream_join python compiled checks
```
