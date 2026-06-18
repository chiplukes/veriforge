# `common_cells/lossy_valid_to_stream`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream two-entry lossy-buffer structure while fixing
the import to a flat 8-bit payload subset and removing the type-parameter
surface.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/lossy_valid_to_stream.sv`

## Why This Is Useful

This example adds a small valid-only source adapter that always accepts new
input data, but may overwrite intermediate buffered values while the sink
stalls. It exercises:

- empty fall-through when the sink is already ready
- stalled capture into the first buffered slot
- filling the second slot under continued backpressure
- overwriting the newest buffered value when the two-entry buffer is full
- ordered drain exposing the oldest value first and the overwritten newest
  value second

## Local Layout

```text
examples/pulp/common_cells/lossy_valid_to_stream/
├── README.md
├── rtl/
│   └── lossy_valid_to_stream.sv
├── tb/
│   └── lossy_valid_to_stream_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the payload to 8 bits and uses a Python-driven
stepped clock harness.

The checks cover:

- pass-through with no buffered transactions
- `busy_o` staying low for immediate pass-through
- buffering a stalled value and asserting `busy_o`
- overwriting the newest buffered value while the sink remains stalled
- draining the oldest value first and the latest overwritten value second

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/lossy_valid_to_stream/run_sim.py
```

Success is indicated by:

```text
PASS lossy_valid_to_stream python reference checks
PASS lossy_valid_to_stream python vm checks
PASS lossy_valid_to_stream python compiled checks
```
