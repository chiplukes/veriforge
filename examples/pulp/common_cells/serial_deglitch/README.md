# `common_cells/serial_deglitch`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream deglitching behavior on a small deterministic
slice while making the low-side saturation behavior explicit so the filter can
return low after a stable run of zero samples.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/serial_deglitch.sv`

## Why This Is Useful

This example adds another compact but stateful helper outside the counter
family. It exercises:

- sampled serial input filtering over multiple cycles
- enable-gated state updates
- saturating up/down count behavior
- output hold between threshold crossings
- rejection of short glitches

## Local Layout

```text
examples/pulp/common_cells/serial_deglitch/
├── README.md
├── rtl/
│   └── serial_deglitch.sv
├── tb/
│   └── serial_deglitch_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a deterministic `SIZE = 3` subset and checks:

- reset-low output
- disabled hold with a high input
- three enabled high samples required before the output rises
- disabled hold while the output is high
- three enabled low samples required before the output falls
- rejection of a two-cycle high glitch

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/serial_deglitch/run_sim.py
```

Success is indicated by:

```text
PASS serial_deglitch deterministic checks
```
