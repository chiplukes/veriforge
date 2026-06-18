# `common_cells/stream_omega_net`

Tenth imported PULP validation target.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL is a focused fixed-configuration adaptation of upstream
`stream_omega_net` with a non-degenerate `4x4`, `radix=2` topology plus local
spill-register dependencies. It keeps staged routing, arbitration, and optional
per-switch output buffering while replacing type-parameter and unpacked-array
interfaces with flattened buses that are easier to parse and simulate here.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT files: `src/stream_omega_net.sv`, `src/stream_xbar.sv`, `src/spill_register.sv`, `src/spill_register_flushable.sv`
- Related upstream tests: `test/stream_omega_net_tb.sv`

## Why This Is Useful

This example extends the imported interconnect coverage beyond a single-stage
crossbar into a staged network where conflicts can appear at intermediate switch
points even when final outputs differ.

It exercises:

- a real two-stage omega topology rather than a degenerate single-switch case
- conflict-free end-to-end routing through both stages
- first-stage arbitration when paired inputs request the same branch
- second-stage arbitration when different first-stage switches converge on one output
- flush resetting round-robin state in the internal switches
- optional spill buffering across the staged path under downstream stall

## Local Layout

```text
examples/pulp/common_cells/stream_omega_net/
├── README.md
├── rtl/
│   ├── spill_register_flushable.sv
│   ├── spill_register.sv
│   └── stream_omega_net.sv
├── tb/
│   └── so_tb.sv
└── run_sim.py
```

## Local Validation Approach

The wrapper exposes two fixed tops over the same `4x4` network:

- `so0_tb` with spill buffering disabled in each internal switch
- `so1_tb` with spill buffering enabled in each internal switch

The Python runner checks:

- reset-idle behavior
- a conflict-free permutation that exercises all four outputs simultaneously
- contention localized to the first stage
- contention localized to the second stage
- flush restoring the initial winner after round-robin rotation
- staged spill latency and drain behavior under a stalled sink

## Running It

```text
uv run python examples/pulp/common_cells/stream_omega_net/run_sim.py
```

Success is indicated by:

```text
PASS stream_omega_net python reference checks
PASS stream_omega_net python vm checks
PASS stream_omega_net python compiled checks
```
