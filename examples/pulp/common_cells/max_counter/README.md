# `common_cells/max_counter`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream max-tracking wrapper structure directly and
bundles the already-imported `delta_counter` dependency locally.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/max_counter.sv`

## Why This Is Useful

This example adds a small stateful wrapper that tracks both the current counter
value and the running maximum. It exercises:

- variable-delta count updates through the wrapped sticky-overflow counter
- visible maximum tracking from the current counter value
- `clear_max_i` resetting only the tracked maximum state
- `overflow_max_o` asserting once a post-overflow value exceeds the previous max

## Local Layout

```text
examples/pulp/common_cells/max_counter/
├── README.md
├── rtl/
│   ├── delta_counter.sv
│   └── max_counter.sv
├── tb/
│   └── max_counter_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper uses width 4 and drives one deterministic sequence.

The checks cover:

- reset state
- load and visible max tracking
- positive delta updates and visible max tracking
- overflow followed by a larger wrapped value
- `overflow_max_o` setting once the new post-overflow maximum is recorded
- `clear_max_i` resetting the tracked max state while the visible output still reflects the current counter value
- combined clear of counter and max state
- down-count behavior preserving the tracked maximum

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/max_counter/run_sim.py
```

Success is indicated by:

```text
PASS max_counter deterministic checks
```
