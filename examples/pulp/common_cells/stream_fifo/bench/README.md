# `stream_fifo` — bench-framework testbench

A Wave B pulp port: drives the Pulp `stream_fifo` cell using the
high-level `Testbench(...)` framework + `StreamProxy`.

`stream_fifo.sv` is purely structural — it instantiates `fifo_v3` to
implement the actual flop-based FIFO. With no `always_ff` block on the
boundary, structural clock detection finds nothing, so the planner's
**naming fallback** (Wave B) recognizes the canonical Pulp port names
`clk_i`/`rst_ni` and synthesizes the clock domain. The planner
classifies the bound interfaces as `confidence='sole-domain'`.

## Run it

```powershell
uv run python examples/pulp/common_cells/stream_fifo/bench/stream_fifo_bench.py
uv run python examples/pulp/common_cells/stream_fifo/bench/stream_fifo_bench.py --vcd waves.vcd
```

Output (success):

```
stream_fifo passed: 8-beat in-order replay [0x11..0x88]
```

## Regenerate from scratch (Wave C: `--auto-deps`)

The CLI now auto-discovers child SV files in the same directory and
emits them as a `DEPS = [...]` list, so a hand-tweaked `parse_dut()`
is no longer required:

```powershell
uv run veriforge generate-python-testbench `
  -f examples/pulp/common_cells/stream_fifo/rtl/stream_fifo.sv `
  --module stream_fifo --enhanced --style bench --auto-deps `
  --no-strict --iface-domain in=clk_i --iface-domain out=clk_i `
  -o examples/pulp/common_cells/stream_fifo/bench/stream_fifo_bench.py
```

Pass `--include-dir <DIR>` (repeatable) to widen the search beyond the
DUT's parent directory. The generated scaffold uses
`parse_files([*DEPS, DUT_PATH])` and forwards `design=` to `Testbench`.

The remaining hand edits after regeneration are stimulus-only:

1. `DUT_PATH` rewritten to a `__file__`-relative path (CLI emits an
   absolute/relative literal — for portable examples, prefer
   `Path(__file__).resolve().parents[1] / "rtl" / "stream_fifo.sv"`).
2. Real stimulus: 8-beat in-order replay with `iface.expect_sequence`.
