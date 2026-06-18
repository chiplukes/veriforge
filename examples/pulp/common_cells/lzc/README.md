# `common_cells/lzc`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream leading-zero / trailing-zero semantics while
removing the assertion include, package dependency, and larger generate-tree
surface by fixing the subset to an 8-bit input and 3-bit count output.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/lzc.sv`

## Why This Is Useful

This example adds a slightly richer control-style combinational helper outside
the stream and counter families. It exercises:

- trailing-zero count mode
- leading-zero count mode
- empty input detection
- stable maximum-count reporting on an all-zero input

## Local Layout

```text
examples/pulp/common_cells/lzc/
├── README.md
├── rtl/
│   └── lzc.sv
├── tb/
│   └── lzc_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The local wrapper instantiates two fixed 8-bit variants:

- one in trailing-zero mode
- one in leading-zero mode

The checks cover:

- all-zero empty detection with maximum count
- least-significant one detection in trailing mode
- most-significant one detection in leading mode
- representative mid-range positions in both modes

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_common_cells_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/lzc/run_sim.py
```

Success is indicated by:

```text
PASS lzc deterministic checks
```
