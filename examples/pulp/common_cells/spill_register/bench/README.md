# `spill_register` — bench-framework testbench

A Wave B pulp port: drives the Pulp `spill_register` cell using the
high-level `Testbench(...)` framework + `StreamProxy`.

`spill_register.sv` simply instantiates `spill_register_flushable`,
which is where the actual flops live. Same pattern as `stream_fifo`:
the planner's port-name fallback synthesizes a clock domain from
`clk_i`/`rst_ni` since the top has no `always` blocks of its own, and
both stream bundles bind with `confidence='sole-domain'`.

## Run it

```powershell
uv run python examples/pulp/common_cells/spill_register/bench/spill_register_bench.py
uv run python examples/pulp/common_cells/spill_register/bench/spill_register_bench.py --vcd waves.vcd
```

Output (success):

```
spill_register passed: 4-beat round-trip [0xDE, 0xAD, 0xBE, 0xEF]
```

## Regenerate from scratch

```powershell
uv run veriforge generate-python-testbench `
  -f examples/pulp/common_cells/spill_register/rtl/spill_register.sv `
  --module spill_register --enhanced --style bench `
  -o examples/pulp/common_cells/spill_register/bench/spill_register_bench.py
```

Same hand-edits as `stream_fifo`: portable `DUT_PATH`, `DEPS` list for
`spill_register_flushable.sv`, `Testbench(..., design=design)`, and a
real `expect_sequence` assertion.
