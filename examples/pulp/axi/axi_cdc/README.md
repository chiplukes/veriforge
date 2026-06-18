# `axi/axi_cdc`

Focused Wave D-8 AXI import covering a two-asynchronous-clock-domain CDC bridge.

## Status

Wave D-8: migrated to the bench framework. Write and read transfer tests pass.

## Upstream Source

- Repository: `pulp-platform/axi`
- DUT file: `src/axi_cdc.sv`
- Reference bench: `test/tb_axi_cdc.sv`

## Bench

```sh
# Generate scaffold (optional — bench is hand-authored from the scaffold):
uv run veriforge generate-python-testbench \
  -f examples/pulp/axi/axi_cdc/tb/axi_cdc_tb.sv \
  --module axi_cdc_exec_tb \
  --enhanced --style bench --no-strict \
  -o examples/pulp/axi/axi_cdc/bench/axi_cdc_bench.py

# Run:
uv run python examples/pulp/axi/axi_cdc/bench/axi_cdc_bench.py
```

The bench manually drives all `src_*` and `dst_*` signals because the DUT
uses packed struct ports internally and the two clock domains require careful
sequencing. No auto-responding proxy is used; all handshakes are explicit.

## Clock Domains

| Domain     | Signal      | Reset       |
|------------|-------------|-------------|
| Source     | `src_clk_i` | `src_rst_ni` (active-low) |
| Destination| `dst_clk_i` | `dst_rst_ni` (active-low) |

Clock periods are set to 10 (src) and 14 (dst) to exercise non-aligned phases.

## Tests

`tests/test_dsl/test_axi_cdc_pulp_example.py` — 2 tests:
- Two-domain clock detection
- End-to-end write + read transfer

## Notes

- The `axi_cdc_exec_tb.sv` wrapper unpacks flat `src_*`/`dst_*` signals into
  the `axi_req_t`/`axi_resp_t` structs expected by `axi_cdc.sv` via `assign`.
- The scheduler CA-propagation fix (Wave D-8) ensures that callback-driven
  signals like `dst_b_valid` are immediately propagated through struct-packing
  `assign` statements, so the CDC FIFOs see the correct inputs on the next
  clock edge.
