# `common_cells/trip_counter`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream trip-on-bound behavior while removing the
assertion include and bundling the already-proven local `delta_counter`
dependency for a fixed 4-bit subset.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/trip_counter.sv`

## Why This Is Useful

This example adds a small sequential helper outside the plain counter family. It
exercises:

- counted progression toward a programmable bound
- combinational `last_o` / `trip_o` assertion at the bound
- automatic reset on the next enabled cycle after tripping

## Local Layout

```text
examples/pulp/common_cells/trip_counter/
├── README.md
├── rtl/
│   ├── delta_counter.sv
│   └── trip_counter.sv
├── tb/
│   └── trip_counter_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a fixed 4-bit counter and checks deterministic
single-step and two-step trip sequences.

The checks cover:

- reset state
- counting up to a bound with `delta_i = 1`
- `last_o` and `trip_o` assertion when the bound is reached
- automatic reset on the next enabled cycle
- a second exact-trip sequence with `delta_i = 2`

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/trip_counter/run_sim.py
```

Success is indicated by:

```text
PASS trip_counter deterministic checks
```
