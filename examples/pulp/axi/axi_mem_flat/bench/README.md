# `axi_mem_flat` — Wave C-3 bench-framework AXI4 testbench

This directory exercises the bench framework's new **`AXI4Proxy`** /
`AXI4Master` against a small flat-port AXI4 (full) memory. It mirrors
the Wave C-2 `axi_lite_regs` example but for full AXI4 with multi-beat
INCR bursts, ID, WLAST/RLAST, and the AW*/AR* sideband signals.

## Why a flat-port wrapper?

Pulp AXI4 cells (`axi_xbar`, `axi_fifo`, `axi_cdc`, `axi_to_mem`, …)
take parametric struct typedefs:

```sv
parameter type axi_req_t  = logic,
parameter type axi_resp_t = logic,
input  axi_req_t  axi_req_i,
output axi_resp_t axi_resp_o,
```

Neither the veriforge Lark grammar nor the reference simulator
expand parametric struct typedefs today. The flat detector keys on
canonical AXI4 suffixes (`awvalid`, `wlast`, `rdata`, …), so a
struct-bundled module is invisible to it.

`../rtl_flat/axi_mem_flat.sv` is a synthesizable equivalent with:

* the same protocol (single-outstanding, RESP=OKAY),
* INCR bursts up to AWLEN=255,
* WSTRB byte enables honored on writes,
* WLAST/RLAST end-of-burst signaling,
* 16 × 32-bit words, word-aligned at `0x00..0x3C`,
* canonical `s_axi_*` flat ports — so the auto-detector picks it up as
  `interface 's_axi' (axi4, role=slave)` with no overrides needed.

## Run it

```powershell
uv run python examples/pulp/axi/axi_mem_flat/bench/axi_mem_flat_bench.py
uv run python examples/pulp/axi/axi_mem_flat/bench/axi_mem_flat_bench.py --vcd waves.vcd
```

Output (success):

```
axi_mem_flat passed: 16-word sweep + 4-beat INCR burst + WSTRB partial write
```

## Regenerate from scratch (Wave C: `--auto-deps`)

```powershell
uv run veriforge generate-python-testbench `
  -f examples/pulp/axi/axi_mem_flat/rtl_flat/axi_mem_flat.sv `
  --module axi_mem_flat --enhanced --style bench --auto-deps `
  --no-strict `
  -o examples/pulp/axi/axi_mem_flat/bench/axi_mem_flat_bench.py
```

The DUT is single-file, so `DEPS = []` and the generated scaffold loads
just `parse_file(DUT_PATH)`. The auto-detector finds the AXI4 slave
bundle on its own (the planner's naming-fallback recognizes
`clk_i`/`rst_ni`).

The remaining hand edits after regeneration are stimulus-only:

1. `DUT_PATH` rewritten to a `__file__`-relative path (the CLI emits an
   absolute/relative literal — for portable examples, prefer
   `Path(__file__).resolve().parents[1] / "rtl_flat" / "axi_mem_flat.sv"`).
2. Real stimulus: replace the single `iface.write(0x0, [0xDEADBEEF])` /
   `iface.read(0x0, length=1)` stub with the 16-word sweep + 4-beat
   INCR burst + WSTRB partial write check.

## Proxy reference

```python
iface = bench.iface("s_axi")

# Single-beat write (data may be int or list).
iface.write(addr, 0xDEAD_BEEF)
iface.write(addr, [0xDEAD_BEEF, 0xCAFEF00D, ...])           # INCR burst
iface.write(addr, value, strb=0b0001)                        # WSTRB partial
iface.write(addr, value, txn_id=3, prot=0, cache=0)          # sideband

# Burst read returns a list of beat data (length=N beats).
beats = iface.read(addr, length=4)

# Convenience: write + read-back the same number of beats.
beats = iface.write_then_read(addr, [a, b, c, d])
```

Limitations of `AXI4Master` in Wave C-3:

* INCR burst only (FIXED/WRAP not implemented).
* Default `awsize`/`arsize` = `log2(DATA_WIDTH/8)` (full beats).
* Single in-flight transaction per channel (no outstanding/reorder).
* DUT slave role only — `AXI4Proxy(role="master")` is not yet supported.
