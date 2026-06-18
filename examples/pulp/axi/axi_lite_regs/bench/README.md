# `axi_lite_regs` — Wave C-2 bench-framework testbench

This directory ports the spirit of the pulp **`axi_lite_regs`** cell into
the high-level `Testbench(...)` framework. It exercises the existing
`AXILiteProxy` / `AXILiteMaster` end-to-end through an auto-detected
flat-port AXI-Lite slave bundle.

## Why a flat-port wrapper?

The pulp original (`../rtl/axi_lite_regs.sv`) takes parametric struct
typedefs:

```sv
parameter type req_lite_t  = logic,
parameter type resp_lite_t = logic,
input  req_lite_t  axi_req_i,
output resp_lite_t axi_resp_o,
```

Neither the veriforge Lark grammar nor the reference simulator
expand parametric struct typedefs today. The flat port detector keys on
canonical AXI-Lite suffixes (`awvalid`, `wready`, `rdata`, …), so a
struct-bundled module is invisible to it.

`../rtl_flat/axi_lite_regs_flat.sv` is a synthesizable equivalent with:

* the same protocol (single-outstanding, RESP=OKAY),
* WSTRB byte enables honored,
* 4 × 32-bit registers, word-aligned at 0x0/0x4/0x8/0xC,
* canonical `s_axi_*` flat ports — so the auto-detector picks it up as
  `interface 's_axi' (axi_lite, role=slave)` with no overrides needed.

## Run it

```powershell
uv run python examples/pulp/axi/axi_lite_regs/bench/axi_lite_regs_bench.py
uv run python examples/pulp/axi/axi_lite_regs/bench/axi_lite_regs_bench.py --vcd waves.vcd
```

Output (success):

```
axi_lite_regs_flat passed: 4-reg sweep + WSTRB partial write
```

## Regenerate from scratch (Wave C: `--auto-deps`)

```powershell
uv run veriforge generate-python-testbench `
  -f examples/pulp/axi/axi_lite_regs/rtl_flat/axi_lite_regs_flat.sv `
  --module axi_lite_regs_flat --enhanced --style bench --auto-deps `
  --no-strict `
  -o examples/pulp/axi/axi_lite_regs/bench/axi_lite_regs_bench.py
```

The DUT is single-file, so `DEPS = []` and the generated scaffold loads
just `parse_file(DUT_PATH)`. The auto-detector finds the AXI-Lite slave
bundle on its own; no `--iface-domain` overrides are required (the
planner's naming-fallback recognizes `clk_i`/`rst_ni`).

The remaining hand edits after regeneration are stimulus-only:

1. `DUT_PATH` rewritten to a `__file__`-relative path (the CLI emits an
   absolute/relative literal — for portable examples, prefer
   `Path(__file__).resolve().parents[1] / "rtl_flat" / "axi_lite_regs_flat.sv"`).
2. Real stimulus: replace the single `iface.write(0x0, 0xDEADBEEF)` /
   `iface.read(0x0)` stub with a 4-register sweep + WSTRB partial write.

## Proxy reference

```python
iface = bench.iface("s_axi")
iface.write(addr, data, strb=0b1111, prot=0)   # → BRESP
value = iface.read(addr, prot=0)               # → RDATA
iface.write_then_read(addr, data)              # convenience
```
