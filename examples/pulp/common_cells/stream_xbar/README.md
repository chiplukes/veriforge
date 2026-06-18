# `common_cells/stream_xbar`

Ninth imported PULP validation target.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL is a focused fixed-configuration adaptation of upstream
`stream_xbar` plus local `spill_register` dependencies. It keeps the essential
selection, arbitration, and optional output spill behavior while removing type
parameters, unpacked-array interfaces, and assertion infrastructure that are not
needed for this deterministic regression.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT files: `src/stream_xbar.sv`, `src/spill_register.sv`, `src/spill_register_flushable.sv`
- Related upstream tests: `test/stream_xbar_tb.sv`

## Why This Is Useful

This example extends the imported ready/valid coverage from single-lane pipeline
primitives to a small routed interconnect with per-output arbitration state.

It exercises:

- simultaneous routing to two different outputs
- contention on one output with round-robin arbitration
- flush resetting arbitration state
- optional output spill buffering under downstream backpressure
- preservation of source index metadata alongside payload routing

## Local Layout

```text
examples/pulp/common_cells/stream_xbar/
├── README.md
├── rtl/
│   ├── spill_register_flushable.sv
│   ├── spill_register.sv
│   └── stream_xbar.sv
├── tb/
│   └── sx_tb.sv
└── run_sim.py
```

## Local Validation Approach

The wrapper exposes two fixed tops over the same 3-input, 2-output crossbar:

- `sx0_tb` with output spill buffering disabled
- `sx1_tb` with output spill buffering enabled

The Python runner checks:

- reset-idle behavior
- independent same-cycle routing to both outputs
- deterministic arbitration order on a contended output
- round-robin rotation only after a contended grant
- flush restoring the initial grant priority
- spill capture, backpressure, and ordered drain on a stalled output

## Running It

```text
uv run python examples/pulp/common_cells/stream_xbar/run_sim.py
```

Success is indicated by:

```text
PASS stream_xbar python reference checks
PASS stream_xbar python vm checks
PASS stream_xbar python compiled checks
```
