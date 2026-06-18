# `common_cells/ring_buffer`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream ring-buffer behavior on a deterministic fixed
subset while replacing the package, assertion, and macro surface with an
explicit 8-bit, depth-4 implementation.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/ring_buffer.sv`

## Why This Is Useful

This example adds a richer buffering primitive outside the ready/valid-focused
wave. It exercises:

- pointer wraparound with full and empty detection
- memory writes plus indexed reads
- restricted random-access read validity
- independent read-pointer advancement by a programmable step

## Local Layout

```text
examples/pulp/common_cells/ring_buffer/
├── README.md
├── rtl/
│   └── ring_buffer.sv
├── tb/
│   └── ring_buffer_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates a deterministic `Depth = 4`, `DataWidth = 8`
subset and checks:

- reset empty state and ready flags
- three writes with immediate restricted reads
- invalid read outside the written range
- one-step pointer advance
- wraparound writes to full
- wrapped valid-range reads after a two-step advance
- final drain to empty

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/ring_buffer/run_sim.py
```

Success is indicated by:

```text
PASS ring_buffer deterministic checks
```
