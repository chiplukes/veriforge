# `common_cells/lfsr_8bit`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream 8-bit LFSR behavior on a deterministic
`WIDTH = 8` subset while removing the assertion include surface.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/lfsr_8bit.sv`

## Why This Is Useful

This example adds another compact stateful control primitive that exercises:

- enabled versus held sequential state updates
- feedback-tap driven pseudo-random progression
- derived binary and one-hot outputs from the current state
- `$clog2`-sized output shaping on a fixed-width subset

## Local Layout

```text
examples/pulp/common_cells/lfsr_8bit/
├── README.md
├── rtl/
│   └── lfsr_8bit.sv
├── tb/
│   └── lfsr_8bit_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a deterministic `SEED = 8'hA5`, `WIDTH = 8`
subset and checks:

- reset loading the seed-derived one-hot/bin outputs
- `en_i = 0` holding the outputs constant
- four exact enabled LFSR steps
- one mid-sequence disabled hold cycle
- one-hot output matching the low three state bits on each checked step

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/lfsr_8bit/run_sim.py
```

Success is indicated by:

```text
PASS lfsr_8bit deterministic checks
```
