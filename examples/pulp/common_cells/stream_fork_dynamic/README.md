# `common_cells/stream_fork_dynamic`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_fork_dynamic` selector-mask handshake
structure while fixing the import to a small 3-output subset, bundling the
supporting `stream_fork` RTL locally, and removing the assertion include that is
not needed for the deterministic local regression.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT files:
  - `src/stream_fork_dynamic.sv`
  - `src/stream_fork.sv`

## Why This Is Useful

This example extends the new standalone `stream_fork` import with a dynamic
selector stream. It exercises:

- selector-valid gating of both the input and output handshakes
- valid fanout only to the selected outputs
- partial completion tracked only across the selected subset
- selector-stream ready returning when the selected subset finally completes

## Local Layout

```text
examples/pulp/common_cells/stream_fork_dynamic/
├── README.md
├── rtl/
│   ├── stream_fork.sv
│   └── stream_fork_dynamic.sv
├── tb/
│   └── stream_fork_dynamic_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes the design to three outputs and checks the sequential
ready/valid behavior with a Python-driven stepped clock harness.

The checks cover:

- selector-invalid gating while the input valid is high
- partial completion for a two-output mask
- final acceptance only when the last selected output becomes ready
- immediate acceptance for a one-output mask

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_fork_dynamic/run_sim.py
```

Success is indicated by:

```text
PASS stream_fork_dynamic python reference checks
PASS stream_fork_dynamic python vm checks
PASS stream_fork_dynamic python compiled checks
```
