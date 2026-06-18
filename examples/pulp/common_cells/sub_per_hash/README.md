# `common_cells/sub_per_hash`

Second imported PULP validation target.

## Status

Imported and locally runnable with the reference, VM, and compiled engines.

The local RTL copy is syntax-normalized where needed for the currently supported
parser subset.

## Upstream Source

- Repository: `pulp-platform/common_cells`
- DUT file: `src/sub_per_hash.sv`
- Upstream testbench: `test/sub_per_hash_tb.sv`

## Why This Is Next

`sub_per_hash` is still combinational, but it exercises a different part of the
front-end and elaboration pipeline than `popcount`:

- typedefs for unpacked arrays
- functions returning unpacked arrays
- elaboration-time pseudo-random table generation
- nested generate loops
- indexed selects through generated permutation and xor tables

## Local Layout

```text
examples/pulp/common_cells/sub_per_hash/
├── README.md
├── oracle_vectors.py
├── rtl/
│   └── sub_per_hash.sv
├── tb/
│   └── sub_per_hash_tb_local.sv
└── run_sim.py
```

## Local Validation Approach

The upstream testbench depends on `cb_filter_pkg` seed typedefs and a clock/reset
generator helper. The local wrapper keeps the same seed values but instantiates
the DUT directly with fixed parameters and deterministic inputs.

The current golden vectors were generated from a small Python oracle that mirrors
the imported permutation and xor-table generation algorithm for the chosen seeds.
That oracle is checked in as `oracle_vectors.py` so the wrapper expectations are
reproducible instead of being one-off derived values.

Current checks focus on:

- basic elaboration of the generated permutation and xor tables
- exact `hash_o` values for fixed inputs and seed pairs
- exact `hash_onehot_o` values for the same fixed inputs

Current local RTL adaptations:

- typedef-return helper functions replaced with parser-friendly module arrays
- elaboration-time table generation moved into a deterministic `initial` block
- integer table declarations flattened to 1D arrays so the imported design stays within the current compiled memory model

## Reproducing Golden Vectors

Run:

```text
uv run python examples/pulp/common_cells/sub_per_hash/oracle_vectors.py
```

The script recomputes the checked-in hash and onehot values for the local test
vectors and exits non-zero if they drift from the wrapper expectations.

## Expected Next Step

Tighten the wrapper with stronger golden-value checks derived from an external
oracle, then decide whether `stream_to_mem` or `rr_arb_tree` should be the next
import.
