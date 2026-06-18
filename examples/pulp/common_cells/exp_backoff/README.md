# `common_cells/exp_backoff`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream exponential-backoff behavior while removing the
assertion include and fixing the checkpoint around a small deterministic
`MaxExp = 4` validation slice.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/exp_backoff.sv`

## Why This Is Useful

This example adds a richer sequential helper than the recent tiny
combinational cells. It exercises:

- pulse-driven state reload
- an internal LFSR-based randomized backoff source
- exponentially growing mask state
- countdown-to-zero behavior
- clear priority over an active backoff interval

## Local Layout

```text
examples/pulp/common_cells/exp_backoff/
├── README.md
├── rtl/
│   └── exp_backoff.sv
├── tb/
│   └── exp_backoff_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a deterministic `Seed = 16'hBEEF`,
`MaxExp = 4` subset and checks:

- reset-to-zero state
- the first `set_i` warmup cycle keeping the counter at zero
- a one-cycle backoff interval after the second set
- a three-cycle backoff interval after the third set
- clear resetting an active interval immediately
- the first set after clear returning to the warmup-zero state

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/exp_backoff/run_sim.py
```

Success is indicated by:

```text
PASS exp_backoff deterministic checks
```
