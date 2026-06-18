# `common_cells/popcount`

First imported PULP validation target.

The local RTL copy is syntax-normalized where needed for the currently supported
parser and simulator subset.

## Status

Imported and locally runnable with the reference engine.
Imported and locally runnable with the VM engine using the reduced-width wrapper.
Imported and locally runnable with the compiled engine using the reduced-width wrapper.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/popcount.sv`
- Upstream testbench: `test/popcount_tb.sv`

## Why This Is First

`popcount` is a good first import because it is small, self-contained, and easy to
validate across multiple widths. It exercises:

- parameter handling
- `$clog2`
- `always_comb`
- `for` loops over parameterized widths
- wide vectors, including a 981-bit case in the upstream bench

## Local Layout

```text
examples/pulp/common_cells/popcount/
├── README.md
├── rtl/
│   └── popcount.sv
├── tb/
│   ├── popcount_tb_local.sv
│   └── popcount_tb_vm_local.sv
└── run_sim.py
```

## Current Local Validation Flow

1. `rtl/popcount.sv` contains the imported DUT.
2. `tb/popcount_tb_local.sv` provides the full deterministic wrapper, including the 981-bit case.
3. `tb/popcount_tb_vm_local.sv` provides a reduced-width deterministic wrapper for the VM.
4. `run_sim.py` parses all local files and runs:
	- the reference engine against the full wrapper
	- the VM engine against the reduced-width wrapper
 	- the compiled engine against the reduced-width wrapper
5. Each wrapper emits an explicit `PASS` marker on success.

Current local RTL adaptations:

- module-parameter `localparam` converted to `parameter`
- elaboration-time `$error` removed
- labeled `endmodule` removed
- `+=` rewritten as `=` plus explicit RHS addition

## Notes About The Upstream Testbench

The upstream `popcount_tb.sv` is simple enough to use as a behavioral reference,
but it uses:

- `randomize(...)`
- `$countones(...)`

That makes it a useful external oracle, but not the best first direct regression
target for the in-project simulator. A smaller local wrapper with deterministic
vectors will be easier to debug and keep stable.

## Suggested Local Wrapper Behavior

Instantiate `popcount` at these widths:

- 1
- 5
- 16
- 32
- 64
- 981

For each width, test at least:

- all zeros
- single one at LSB
- all ones
- a few deterministic mixed patterns

Pass condition:

- no `$error` or `$fatal`
- a final `PASS` message or equivalent explicit completion signal

## Known Limitation

The full wrapper includes a 981-bit DUT instance, and wide signals above 64 bits
are not yet supported in the compiled engine.

The VM and compiled engines therefore use the reduced-width wrapper, while the
reference engine keeps the full-width 981-bit case.

## Immediate Next Step

Use this example as the template for the next `common_cells` import, likely
`sub_per_hash` or `stream_to_mem`.
