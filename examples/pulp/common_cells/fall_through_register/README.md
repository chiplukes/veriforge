# `common_cells/fall_through_register`

Eighth imported PULP validation target.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL is a focused adaptation of upstream `fall_through_register` plus
its `fifo_v3` dependency. It keeps the default-ready and immediate-forwarding
behavior of the upstream block while removing assertion includes and type
parameters that are not needed for this deterministic regression.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT files: `src/fall_through_register.sv`, `src/fifo_v3.sv`
- Related upstream formal target: `formal/fall_through_register_properties.sv`

## Why This Is Useful

This example complements `spill_register` by validating the opposite tradeoff:
the block preserves combinational forwarding on `valid` and `data` so an empty
register can accept and produce a transfer in the same cycle, while only cutting
the `ready` path.

It exercises:

- immediate pass-through when empty
- default-ready behavior while empty even if downstream stalls
- depth-1 buffering after a stalled input is captured
- backpressure once the single internal slot is full
- ordered drain when downstream resumes
- synchronous clear via `clr_i`

## Local Layout

```text
examples/pulp/common_cells/fall_through_register/
├── README.md
├── rtl/
│   ├── fifo_v3.sv
│   └── fall_through_register.sv
├── tb/
│   └── ft_reg_tb.sv
└── run_sim.py
```

## Local Validation Approach

The wrapper exposes a single fixed top with an 8-bit payload. The Python runner
uses the same stepped single-clock harness pattern as the recent FIFO and spill
register imports.

The checks cover:

- reset state
- immediate same-cycle pass-through into a ready downstream
- stalled capture into the internal depth-1 FIFO
- blocked overwrite while full
- drain behavior once downstream becomes ready again
- synchronous clear of a buffered item

## Running It

```text
uv run python examples/pulp/common_cells/fall_through_register/run_sim.py
```

Success is indicated by:

```text
PASS fall_through_register python reference checks
PASS fall_through_register python vm checks
PASS fall_through_register python compiled checks
```
