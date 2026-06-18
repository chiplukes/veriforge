# `common_cells/heaviside`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream mask-generation behavior while removing the
package-derived type surface by fixing the subset to a 3-bit index and 8-bit
mask output.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/heaviside.sv`

## Why This Is Useful

This example adds another small self-contained combinational helper outside the
stream and counter families. It exercises:

- low-end mask generation
- mid-range interval masks
- the full-width mask case

## Local Layout

```text
examples/pulp/common_cells/heaviside/
├── README.md
├── rtl/
│   └── heaviside.sv
├── tb/
│   └── heaviside_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a fixed 8-bit mask generator and checks a
deterministic sequence of index inputs against explicit mask outputs.

The checks cover:

- `x_i = 0` producing the least-significant bit mask
- small adjacent values
- a mid-range value
- the full-width value producing all ones

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/heaviside/run_sim.py
```

Success is indicated by:

```text
PASS heaviside deterministic checks
```
