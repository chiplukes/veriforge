# `common_cells/stream_throttle`

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL keeps the upstream `stream_throttle` credit-counter structure
while fixing the import to a flat local subset that removes the macro include
and `cf_math_pkg` dependency surface.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/stream_throttle.sv`

## Why This Is Useful

This example adds the small credit-gated ready/valid wrapper. It exercises:

- request valid/ready pass-through when credit is available
- blocking new requests once the outstanding count reaches the runtime credit
- response-driven reopening of the request path
- simultaneous request and response preserving the outstanding count
- runtime credit reduction immediately throttling further requests

## Local Layout

```text
examples/pulp/common_cells/stream_throttle/
├── README.md
├── rtl/
│   └── stream_throttle.sv
├── tb/
│   └── stream_throttle_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The imported subset fixes `MaxNumPending = 3` with a 2-bit credit input and
uses a Python-driven stepped clock harness.

The checks cover:

- valid passing through even when downstream ready is low
- accepted requests incrementing the outstanding counter
- blocking at the configured runtime credit limit
- simultaneous request/response preserving the outstanding count
- runtime credit reduction blocking new requests until responses drain

Shared pytest coverage for the imported wrapper also lives in
`tests/test_sim/test_pulp_ready_valid_examples.py`.

## Running It

```text
uv run python examples/pulp/common_cells/stream_throttle/run_sim.py
```

Success is indicated by:

```text
PASS stream_throttle python reference checks
PASS stream_throttle python vm checks
PASS stream_throttle python compiled checks
```
