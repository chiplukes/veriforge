# Python testbench examples

Two end-to-end examples of the high-level transaction-style testbench
framework (`veriforge.sim.bench`):

| Script                       | What it shows                                                              |
|------------------------------|----------------------------------------------------------------------------|
| `axi_stream_loopback.py`     | Two-domain inline DUT, basic `put` / `get` / `expect`, role inversion.     |
| `multi_domain_axis.py`       | File-driven DUT, **TUSER / TDEST / TID / TKEEP**, 12-bit pixel **elements**, multiple frames per domain, optional **VCD** trace. *Hand-written.* |

The `multi_domain_axis.py` example can also be **regenerated from the DUT** as
a starting-point scaffold via the CLI — see *Option B* below.

Run either with `uv`:

```powershell
uv run python examples/python_testbench/axi_stream_loopback.py
uv run python examples/python_testbench/multi_domain_axis.py
uv run python examples/python_testbench/multi_domain_axis.py --vcd build/multi.vcd
```

---

## How the testbench is generated

You have two options.

### Option A: write the harness by hand (no codegen)

The framework is **generator-free at runtime** — `Testbench(parsed_module, ...)`
*infers* clocks, resets, and AXI-Stream / AXI-Lite interfaces from the parsed
Verilog model and materializes a live test harness in memory. The five lines
below are the entire generation flow used by `multi_domain_axis.py`:

### Option B: scaffold a Python testbench file from a Verilog source

Use the CLI to emit a runnable starting-point `.py` file that wires up
`Testbench(...)` with pre-filled `iface_domains` / `iface_layouts` and
per-interface `# TODO` markers:

```powershell
uv run veriforge generate-python-testbench `
    -f examples/python_testbench/dut/multi_domain_axis_dut.v `
    --enhanced --style bench `
    --iface-domain pix_in=pclk --iface-domain pix_out=pclk `
    --iface-domain rtr_in=rclk --iface-domain rtr_out=rclk `
    -o examples/python_testbench/multi_domain_axis_generated.py
```

The generated file is immediately runnable (`uv run python <file>` will
parse the DUT, build the bench, drive default zeros, observe outputs, and
demonstrate `BenchTimeoutError`). It accepts `--vcd PATH` out of the box.
Edit the `# TODO` markers to inject real stimulus and replace the `get`
calls with `expect(...)`.

`--style legacy` (the default) emits the original raw-`Simulator` +
`step_drive` skeleton instead. `--style bench` requires `--enhanced`.

```python
from veriforge.verilog_parser import verilog_parser
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.sim.bench import Testbench

parser  = verilog_parser(start="module_declaration")             # 1. build a Lark parser
tree    = parser.build_tree(text=open("path/to/dut.v").read())   # 2. parse the Verilog file
design  = tree_to_design(tree)                                   # 3. lift to model.Module
dut     = design.modules[0]
bench   = Testbench(dut, engine="reference")                     # 4. discover + materialize
print(bench.plan.summary())                                      # 5. inspect what was found
```

`bench.plan` is the discovered structure — clock domains, resets, and
the interface bundles bound to each domain. Print it before running any
stimulus to catch misinference early.

### Steering the planner with `PlannerOverrides`

Auto-discovery is heuristic; when it picks the wrong thing, supply a
`PlannerOverrides` to pin it down. Every override is optional and
additive:

```python
from veriforge.sim.bench import PlannerOverrides

overrides = PlannerOverrides(
    # Force interface-to-clock-domain bindings.
    iface_domains={
        "pix_in":  "pclk",
        "pix_out": "pclk",
        "rtr_in":  "rclk",
        "rtr_out": "rclk",
    },
    # Per-interface element layout (matters for non-byte data,
    # designs without TKEEP, or non-default endianness).
    iface_layouts={
        "pix_in":  {"elements_per_beat": 4, "element_size_bits": 12, "endian": "big"},
        "pix_out": {"elements_per_beat": 4, "element_size_bits": 12, "endian": "big"},
    },
    # Other knobs available:
    # clock_periods    = {"pclk": 8, "rclk": 5},
    # domain_aliases   = {"pclk": "pix"},
    # reset_polarities = {"presetn": "active_low"},
)
bench = Testbench(dut, overrides=overrides)
```

### Driving stimulus

The proxy returned by `bench.iface(prefix)` is the transaction-level
DSL. For AXI-Stream it exposes `put` (source side) and `get` / `expect`
(sink side), with first-class sideband:

```python
pix_in  = bench.iface("pix_in")    # DUT slave -> testbench source
pix_out = bench.iface("pix_out")   # DUT master -> testbench sink

with bench.run(vcd="build/run.vcd"):     # vcd= is optional
    bench.reset_all()

    # 8 pixels in 2 beats, TUSER set on every element of the trailing
    # beat (the "TUSER@TLAST = good frame" convention).
    pix_in.put(
        [0x111, 0x222, 0x333, 0x444, 0x555, 0x666, 0x777, 0x888],
        last_user=1,
    )

    # Per-packet TDEST / TID:
    rtr_in = bench.iface("rtr_in")
    rtr_in.put([0x10, 0x11, 0x12], dest=3, tid=7)

    # Assert payload + sideband in one call:
    pix_out.expect(
        [0x111, 0x222, 0x333, 0x444, 0x555, 0x666, 0x777, 0x888],
        last_user=1, timeout=200,
    )
    rtr_out = bench.iface("rtr_out")
    rtr_out.expect([0x10, 0x11, 0x12], dest=3, tid=7, timeout=200)
```

Sideband kwargs accepted on `put` / `expect`:

| kwarg       | type                  | meaning                                                    |
|-------------|-----------------------|------------------------------------------------------------|
| `dest`      | `int` or `list[int]`  | TDEST (scalar broadcast or per-element).                   |
| `tid`       | `int` or `list[int]`  | TID (scalar broadcast or per-element).                     |
| `user`      | `int` or `list[int]`  | TUSER, per element.                                        |
| `last_user` | `int`                 | Convenience: TUSER applied only to the trailing beat.      |
| `keep`      | `list[int]`           | TKEEP per element (defaults to all-ones).                  |
| `last`      | `list[int]`           | Per-element TLAST (defaults to "1 on the final element").  |

For full control build the frame yourself with `proxy.frame(...)` (which
inherits the proxy's `elements_per_beat` / `element_size_bits` /
`endian`) and pass it to `put_frame(...)`.

### Multi-domain coordination is automatic

Each interface proxy is registered with its planned domain. Calls like
`pix_out.get(timeout=200)` count cycles **on `pclk` only** —
`rtr_*` traffic continues to clock on `rclk` in parallel under the
multi-domain runner. There is no manual `step()` interleaving in user
code.

---

## VCD waveform tracing

`Testbench.run()` accepts a `vcd=` path; the trace is opened on enter
and finalized on exit (even if the body raises):

```python
with bench.run(vcd="build/multi.vcd"):
    bench.reset_all()
    ...
```

Optional kwargs:

| kwarg            | default  | meaning                                            |
|------------------|----------|----------------------------------------------------|
| `vcd`            | `None`   | Output path. `None` disables tracing (no overhead).|
| `vcd_timescale`  | `"1ns"`  | VCD `$timescale` directive.                        |
| `vcd_signals`    | `None`   | Iterable of signal names. `None` records all.      |

The example accepts `--vcd PATH` and threads it through:

```powershell
uv run python examples/python_testbench/multi_domain_axis.py --vcd build/multi.vcd
gtkwave build/multi.vcd
```

---

## A note on terminology: **element**, not **lane**

The AXI4-Stream specification only defines "byte lane" — a fixed
8-bit slice of TDATA with one TKEEP bit. It has no standard term for
sub-beat units of other widths (12-bit pixels, 10b8b symbols, custom
fields). This codebase uses **element** for that concept:

- `elements_per_beat` — how many elements occupy one TDATA beat.
- `element_size_bits` — width of a single element in bits.
- `endian` — packing order (`"little"` puts element 0 in the LSBs).

So an AXI-Stream "byte lane" is the special case `element_size_bits == 8`.

---

## Where to go next

* Full reference: `notes/dsl/dsl_guide.md` ("Writing a Python testbench").
* Plan inference logic: `src/veriforge/sim/bench/planner.py`.
* Runtime: `src/veriforge/sim/bench/runtime.py`.
* Engine-native lowering (subset that compiles back to the Verilog
  simulator for speedup): `src/veriforge/sim/bench/lowering.py`.
