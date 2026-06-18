# Multi-Interface Project Example

Demonstrates the `build_testbench()` API from `veriforge.project` on a
multi-file Verilog design with three distinct AXI/AXI-Stream interface types.

---

## Project Structure

```
multi_iface_project/
├── rtl/
│   ├── axis_loopback.v     — AXI-Stream combinational loopback (m_axis → s_axis)
│   ├── axil_regfile.v      — AXI-Lite 4 × 32-bit register file (slave)
│   ├── axi4_ram.v          — AXI4  8 × 32-bit single-beat RAM  (slave)
│   └── multi_iface_top.v   — Structural top: instantiates all three sub-modules
└── tb/
    └── multi_iface_tb.py   — Python testbench (this file)
```

---

## Auto-Detected Interfaces

`build_testbench()` loads all `.v` files in `rtl/`, selects `multi_iface_top`
as the DUT, and auto-detects four interface bundles from the top-level ports:

| Prefix | Protocol  | Role (bench) | Proxy returned by `bench.iface()`      |
|--------|-----------|--------------|----------------------------------------|
| `m_axis` | AXI-Stream slave  | source | `AXIStreamProxy` — `.put()`, `.wait_drain()`, `.pause` |
| `s_axis` | AXI-Stream master | sink   | `AXIStreamProxy` — `.get()`, `.pause` |
| `axil`   | AXI-Lite  slave   | master | `AXILiteProxy`   — `.write()`, `.read()` |
| `ram`    | AXI4      slave   | master | `AXI4Proxy`      — `.write()`, `.read()` |

Detection logic:
- `awlen` **present** in the prefix group → AXI4 (`ram_*`)
- `awlen` **absent**, `awaddr` present → AXI-Lite (`axil_*`)
- `tvalid`/`tdata`/`tlast` group → AXI-Stream (`m_axis_*`, `s_axis_*`)

---

## Quick Start

```bash
# Run all demos
uv run python examples/multi_iface_project/tb/multi_iface_tb.py

# Run one demo and capture VCD waveforms
uv run python examples/multi_iface_project/tb/multi_iface_tb.py --demo 1 --vcd /tmp/waves/

# Explain what the planner detects (no simulation)
uv run veriforge generate-python-testbench \
    --directory examples/multi_iface_project/rtl/ \
    --module multi_iface_top \
    --explain-plan

# Generate a bench-style scaffold (edit it into a real testbench)
uv run veriforge generate-python-testbench \
    --directory examples/multi_iface_project/rtl/ \
    --module multi_iface_top \
    --style bench
```

---

## Demos

### Demo 1 — AXI-Stream loopback with pause

Sends a 32-byte payload through the loopback under three bandwidth conditions:
full speed, 1/3 source pause (bench holds `tvalid` low), and 1/3 sink pause
(bench holds `tready` low).

`PauseGenerator(n, m, seed)` asserts a pause approximately n-out-of-m cycles
using a seeded PRNG, giving reproducible bandwidth reduction.

```python
src = bench.iface("m_axis")   # AXIStreamProxy: bench is source
snk = bench.iface("s_axis")   # AXIStreamProxy: bench is sink
src.pause = PauseGenerator(1, 3, seed=42)
src.put(payload)
src.wait_drain()
pkt = snk.get()
```

### Demo 2 — AXI-Lite register file

Writes four 32-bit patterns, reads them back, then performs a WSTRB
partial-byte write to verify byte-enable masking.

```python
axil = bench.iface("axil")    # AXILiteProxy: bench is master
axil.write(0x0, 0xDEADBEEF)
value = axil.read(0x0)        # returns int
axil.write(0x0, 0x000000AB, strb=0b0001)  # update low byte only
```

### Demo 3 — AXI4 RAM

Writes 8 words in a single-beat sweep, reads them back, and verifies WSTRB
byte-enables.

```python
ram = bench.iface("ram")      # AXI4Proxy: bench is master
ram.write(0x00, 0xDEAD0000)
words = ram.read(0x00, length=1)   # returns list[int]
ram.write(0x00, 0x000000AB, strb=0b0001)
```

### Demo 4 — All interfaces in one session

Drives AXI-Stream, AXI-Lite, and AXI4 within a single `bench.run()` context
to show that all three proxies share the same simulation time axis.

### Demo 5 — compile_native (AXI-Stream fast path)

Lowers the AXIS source+sink FSMs into the hardware DSL and runs the combined
wrapper in the compiled C engine via `batch_run()` — zero Python per-cycle
overhead.

Targets `axis_loopback.v` directly because `multi_iface_top` also has an
AXI4 slave port for which no `AXI4MasterLowering` is currently provided.
`PauseGenerator` is **not** supported in compile_native / batch_run.

```python
bench = build_testbench(RTL_DIR / "axis_loopback.v")
lowered = compile_native(bench, lowerings={
    "m_axis": AXIStreamSourceLowering(beats=data, data_width=8),
    "s_axis": AXIStreamSinkLowering(n_beats=len(data), data_width=8),
})
results = lowered.batch_run(cycles=256, reset_cycles=4)
```

### Demo 6 — compile_native (AXI-Lite master lowering)

Encodes a write/read script as a ROM and replays it via a native FSM.
Targets `axil_regfile.v` directly (same AXI4 slave reason as Demo 5).

```python
bench = build_testbench(RTL_DIR / "axil_regfile.v")
ops = [AXILiteOp.write(0x0, 0xDEADBEEF), ..., AXILiteOp.read(0x0), ...]
lowered = compile_native(bench, lowerings={
    "s_axi": AXILiteMasterLowering(operations=ops, addr_width=4),
})
results = lowered.batch_run(cycles=512, reset_cycles=4)
```

---

## build_testbench() API

```python
from veriforge.project import build_testbench

# From a directory — auto-selects single top module or raises ValueError
bench = build_testbench("path/to/rtl/")

# Specify the top when multiple modules exist
bench = build_testbench("path/to/rtl/", top="multi_iface_top")

# From a single file
bench = build_testbench("path/to/rtl/axis_loopback.v")

# From a list of files
bench = build_testbench(["path/to/rtl/axis_loopback.v", "path/to/rtl/..."])

# Pass overrides (e.g., force a specific clock port)
from veriforge.sim.bench import PlannerOverrides
bench = build_testbench("path/to/rtl/", top="dut",
                        overrides=PlannerOverrides(clock="my_clk"))
```

---

## Notes

- **Clock domain**: `multi_iface_top` is a purely structural module (no
  `always` blocks). The planner uses `extract_clocks_resets_hier()` which
  traces instance port connections upward to promote `clk`/`rst_n` from
  `axis_loopback`'s always block. The fallback name heuristic (`clk` / `rst_n`)
  resolves these signals as well.

- **Interface naming**: The top-level prefix is what you pass to `bench.iface()`.
  Top port `axil_awaddr` → prefix `axil` → `bench.iface("axil")`.

- **WSTRB**: Both AXI-Lite and AXI4 proxies accept an optional `strb=` keyword
  argument on `.write()` for byte-enable masking.

- **compile_native limitations**: Only interfaces with a provided lowering class
  can be used. Currently available: `AXIStreamSourceLowering`,
  `AXIStreamSinkLowering`, `AXILiteMasterLowering`, `AXILiteSlaveLowering`,
  `AXI4SlaveLowering` (DUT-as-master). An `AXI4MasterLowering` for DUT-as-slave
  scenarios is not yet available.
