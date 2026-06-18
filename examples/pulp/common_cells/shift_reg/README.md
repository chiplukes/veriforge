# `common_cells/shift_reg`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream shift-register behavior on a small
deterministic subset while replacing the macro- and type-parameter surface with
an explicit 8-bit sequential implementation.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT files:
  - `src/shift_reg.sv`
  - `src/shift_reg_gated.sv`

## Why This Is Useful

This example adds a compact clocked data-path primitive that still exercises:

- generated pass-through versus registered behavior
- valid-gated pipeline advance
- held payload state while validity is low
- wrapper reuse of a gated helper inside an always-valid helper

## Local Layout

```text
examples/pulp/common_cells/shift_reg/
├── README.md
├── rtl/
│   ├── shift_reg.sv
│   └── shift_reg_gated.sv
├── tb/
│   └── shift_reg_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates:

- a depth-0 `shift_reg_gated` pass-through slice
- a depth-3 `shift_reg_gated` slice with explicit valid gaps
- a depth-3 `shift_reg` slice with always-valid shifting

The checks cover:

- depth-0 combinational pass-through for valid and data
- reset-cleared registered outputs
- depth-3 gated latency and ordered delivery of two valid entries
- output invalidation once the gated pipe drains
- depth-3 always-valid delayed shifting through the plain wrapper

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/shift_reg/run_sim.py
```

Success is indicated by:

```text
PASS shift_reg deterministic checks
```
