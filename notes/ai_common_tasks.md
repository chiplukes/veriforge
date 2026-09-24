# AI Common Tasks — Starting Point

Pointer doc for an AI agent (or human) about to start a new veriforge task.
Read this top to bottom first, then jump to the linked deep-dive notes for
whatever you actually need. Two use cases cover the vast majority of work:

1. **Simulate** an existing / in-progress Verilog/SV project.
2. **Design** new hardware with the Python DSL.

---

## Project layout convention

New work is its own Python project, kept separate from the veriforge
checkout:

```
work/                       # your project root (outside the veriforge repo)
├── pysim/                  # uv project: testbench files, pytest, stimulus
│   ├── pyproject.toml
│   └── test_*.py
└── src/                    # (sibling) Verilog/SV source under test
    └── my_dut.v
```

- `pysim/` holds all Python (testbench + tests); veriforge is a dependency.
- Verilog/SV lives in a sibling folder (`rtl/`, `src/`, …) — not inside `pysim/`.
- Name testbenches `test_*.py` so pytest picks them up.

---

## Use case 1 — Simulate an existing / in-progress Verilog/SV design

### 1.1 Bootstrap the project with uv

```bash
uv init pysim
cd pysim

# Add veriforge. Local checkout (editable) is the common case:
uv add --editable /path/to/veriforge
#   …or pin a git revision:
uv add "veriforge @ git+https://github.com/chiplukes/veriforge"

# Test tooling (pytest preferred; xdist when there are many/long tests):
uv add --dev pytest pytest-xdist
```

The `veriforge` console script is then available as `uv run veriforge …`.
Engines are swapped with a single `engine=` keyword — see
[simulation_overview.md](simulation_overview.md) for the four options
(`"reference"`, `"vm"`, `"vm-fast"`, `"compiled"`).

### 1.2 Generate the testbench starting point from the CLI

The CLI has a testbench generator — **always** start from it rather than
hand-writing the harness. Two-step flow:

```bash
# 1. Inspect what the planner will infer (no code written):
uv run veriforge generate-python-testbench \
    --file ../rtl/my_dut.v --explain-plan

# 2. Generate the scaffold:
uv run veriforge generate-python-testbench \
    --file ../rtl/my_dut.v \
    --enhanced --style=bench --auto-deps \
    --output test_my_dut.py
```

- `--enhanced --style=bench` emits the modern `Testbench` scaffold — the
  high-level path. (`--style=legacy` is the raw `Simulator` fallback.)
- `--auto-deps` scans sibling files for child modules so a multi-file DUT
  parses without extra wiring.
- Multi-file project root: use `--directory ../rtl --module my_top` instead
  of `--file`.
- If auto-detection of clocks/resets/domains is wrong, pin it with
  `--clock-override`, `--reset-override`, `--iface-domain`, `--domain-alias`
  (see [getting_started.md](getting_started.md) §8 for the full flag table).
- Persist the inferred plan across RTL changes with `--emit-plan`
  (+ `--force-plan` to accept a diffed re-inference).

The generator is also callable from Python: `build_testbench()` /
`generate_python_testbench_skeleton()` in `veriforge.scaffold`.

### 1.3 Use the high-level interfaces whenever possible

The scaffold wires `bench.iface(prefix)` proxies. Prefer them over raw
`drive()`/`read()` — they auto-detect the protocol, handle handshakes, and
step the right clock domain for you:

```python
from veriforge.sim.bench import Testbench
from veriforge.project import parse_file

design = parse_file("../rtl/my_dut.v")
bench = Testbench(design.modules[0], design=design)

with bench.run(vcd="build/dump.vcd"):
    bench.reset_all()

    src = bench.iface("s_axis")      # DUT slave  -> bench drives frames
    dst = bench.iface("m_axis")      # DUT master -> bench receives

    src.put([0x11, 0x22, 0x33])      # tlast=1 auto on last beat
    dst.expect([0x11, 0x22, 0x33], timeout=200)

    regs = bench.iface("s_axi")      # AXI4-Lite slave -> bench is master
    regs.write(0x00, 0xDEADBEEF)
    val = regs.read(0x00)

    mem = bench.iface("m_axi")       # AXI4 master -> bench is a responder
    print(mem.write_log)             # [(addr, data, strb), ...] per beat
```

- **AXI-Stream**: `put` / `get` / `expect` / `wait_drain`, plus `pause`
  (back-pressure via `PauseGenerator`).
- **AXI4-Lite**: `write(addr, data)` / `read(addr)`; DUT-master role
  auto-returns a responder with `.memory`, `.write_log`, `.read_log`.
- **AXI4**: full-burst `.write` / `.read` for a DUT slave; a DUT master gets
  an `AXI4Responder` (memory + latency/bandwidth knobs).
- **MemBus**: `.write` / `.read` (SRAM/BRAM-style ports).

Full proxy reference: [simulation/bench_usage.md](simulation/bench_usage.md).

### 1.4 Large filesets / long sims — design in the lowering from the start

If the DUT is a big file set or simulation will run many cycles, skip the
Python-stepped bench and use **engine-native lowering** so stimulus runs
inside the compiled C loop (no per-cycle Python overhead):

```python
from veriforge.sim.bench import (
    Testbench, compile_native,
    AXIStreamSourceLowering, AXIStreamSinkLowering,
)

bench = Testbench(dut)
lowered = compile_native(bench, lowerings={
    "s_axis": AXIStreamSourceLowering(beats=[0x01, 0x02, 0x03], data_width=8),
    "m_axis": AXIStreamSinkLowering(n_beats=3, data_width=8),
})
results = lowered.run("compiled", vcd="build/trace.vcd")   # clocks/reset/VCD automatic
# results: dict of <prefix>_cap_<i> etc.; fastest path is lowered.batch_run(...)
```

Rule of thumb: fixed, known-at-compile-time stimulus → `compile_native`;
stimulus that must branch on live DUT signals → Python `Testbench`.
See [simulation/bench_native_lowering.md](simulation/bench_native_lowering.md)
for every `*Lowering` type (AXI-Stream, AXI-Lite, AXI4, MemBus, master and
responder roles) and the `batch_run()` fast path.

### 1.5 pytest + xdist

Tests are plain pytest. For many or long-running tests, run in parallel:

```bash
uv run pytest -n auto            # xdist: one worker per core
uv run pytest -n 4 -q
```

Keep each test's VCD in a per-test build dir (or thread `--vcd` via a
fixture) so parallel workers don't collide. Cython compilation is
content-hash cached per module, so parallel workers are safe.

### 1.6 VCD + progress visibility

- `bench.run(vcd="…")` (and `lowered.run(..., vcd="…")`) capture waveforms;
  `vcd_signals=[...]` limits to the signals you care about. View with
  `gtkwave`.
- Add an argparse `--vcd PATH` flag to each `test_*.py` entry point (the
  scaffold already does this) so you can always re-run with a dump.
- Before running, print `bench.plan.summary()` (or use `--explain-plan`) to
  see detected clocks, resets, and interfaces — catches misinference early.
- For live progress on long regressions, print per-test status from pytest
  (`-v`) or use `rich` (already a veriforge dep) for a progress display.

---

## Use case 2 — Design with the DSL

### 2.1 Prefer the imperative style

Use the imperative `Module` builder (`m.input("clk")`, `m.reg(…)`,
`m.interface(…)`). Signal declarations are a bit more verbose, but the
signals are first-class locals afterward and generation/loops are natural:

```python
from veriforge.dsl import Module, posedge
from veriforge.codegen import emit_module

with Module("counter") as m:
    clk = m.input("clk")
    rst = m.input("rst")
    count = m.output_reg("count", width=8)

    with m.always(posedge(clk)):
        with m.if_(rst):
            count <<= 0
        with m.else_():
            count <<= count + 1

mod = m.build()
```

The declarative `ModuleSpec` style (class attributes) is fine for a fixed
interface, but reach for the imperative builder whenever the signal set is
generated or driven by parameters. See
[dsl/dsl_guide.md](dsl/dsl_guide.md) for the full syntax (operators,
`always`/`if`/`case`, delays, system tasks).

### 2.2 Map AXI-Stream / AXI ports as an `Interface`, not per-signal

Bind a protocol interface with `m.interface(prefix, template, role=…)`
instead of declaring each `tvalid`/`tready`/`tdata`/… signal by hand. This
sets the correct directions automatically and gives dotted access
(`iface.tvalid`, `iface.tdata`):

```python
from veriforge.dsl import Module, posedge
from veriforge.dsl.lib import axi_stream, axi4_lite

with Module("producer") as m:
    clk = m.input("clk")
    rst = m.input("rst")
    out = m.interface("m_axis", axi_stream(data_width=32), role="master", reg=True)

    with m.always(posedge(clk)):
        out.tvalid <<= 1
        out.tdata  <<= 0xCAFE
        out.tlast  <<= 0

# AXI4-Lite peripheral (full 5-channel template):
with Module("regs") as m:
    clk = m.input("clk")
    s_axi = m.interface("s_axi", axi4_lite(data_width=32, addr_width=8), role="slave")
    # s_axi.awaddr / .awvalid / .awready / .wdata / .bvalid / .rdata / ...
```

- `axi_stream(data_width, tid_width=, tdest_width=, tuser_width=)` — optional
  sideband signals only appear when their width is > 0.
- `axi4_lite(data_width, addr_width)` — all five channels.
- Wire bus between two instances with `**iface.port_map("prefix")` in
  `m.instance(..., ports={...})`.
- Library components live in `veriforge.dsl.lib` (FIFO, CDC, codec, DSP,
  AXI-Stream, AXI4-Lite, Xilinx inference).
- DSL-built modules simulate directly (`Simulator(mod)`) or emit via
  `emit_module(mod)`.

For interface details (roles, `reg=True`, `port_map()`, asymmetric buses):
[dsl/dsl_guide.md §Interfaces](dsl/dsl_guide.md#interfaces-signal-bus-grouping).

---

## Reference map — what to read next

| Need | Note |
|------|------|
| First steps / install / CLI reference | [getting_started.md](getting_started.md) |
| Testbench generator flags + Python API | [getting_started.md §8](getting_started.md#8-generate-a-python-testbench) |
| `Testbench` + proxy API (put/get/write/read/pause) | [simulation/bench_usage.md](simulation/bench_usage.md) |
| Engine-native lowering (`compile_native`, `batch_run`) | [simulation/bench_native_lowering.md](simulation/bench_native_lowering.md) |
| Engine selection + performance | [simulation_overview.md](simulation_overview.md), [simulation/simulator_engines.md](simulation/simulator_engines.md) |
| DSL full syntax reference | [dsl/dsl_guide.md](dsl/dsl_guide.md) |
| DSL interfaces / port_map | [dsl/dsl_guide.md §Interfaces](dsl/dsl_guide.md#interfaces-signal-bus-grouping) |
| Worked end-to-end example (AXIS skid buffer) | [../examples/axis_skid_buffer/](../examples/axis_skid_buffer/) |
| Multi-domain + sideband example | [../examples/python_testbench/](../examples/python_testbench/) |
| Debugging + VCD | [simulation/debug.md](simulation/debug.md) |
| Cross-validate vs Icarus | [simulation/cosim.md](simulation/cosim.md) |
