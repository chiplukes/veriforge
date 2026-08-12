# AXI-Stream full register slice / skid buffer

A full register slice registers data in **both** directions -- `m_axis`
going out, and `s_axis_tready` coming back -- so no combinational path runs
through the module in either direction. It needs a one-deep "skid" register
to avoid dropping a beat that was already accepted the cycle
`m_axis_tready` drops mid-transfer. Compare with `examples/axis_skid_buffer`
(despite its name, that one is a half slice: `s_axis_tready` is driven
combinationally, no skid register).

Use a full slice at a boundary where you don't want the timing on one side
of the interface to reach, combinationally, into the other -- e.g. right
before deriving a clock-enable/stall signal for a deep or BRAM-heavy
pipeline from `m_axis_tready`: put the slice on that pipeline's output and
gate the pipeline from the slice's own *registered* `s_axis_tready`, not
from the raw downstream `m_axis_tready`. That decouples the pipeline's
internal timing from whatever is on the other side of the slice.

Originally written for gfwx-fpga (`stream/axi_stream_slice/`); pulled in
here so it's tested independently of that project and reusable elsewhere.

## Files

| File | What it is |
|---|---|
| `axi_stream_slice.py` | The component: `build_axi_stream_slice(data_width, tuser_width, has_tlast, name)`, raw-port **imperative** DSL builder. |
| `axi_stream_slice_iface.py` | The same module rebuilt with `m.interface()` / `axi_stream()` instead of individual `m.input()`/`m.output_reg()` calls -- see "Interface-based variant" below. |
| `axi_stream_slice_declarative.py` | The same module again, rebuilt in the **declarative** `ModuleSpec` style (`In()`/`OutReg()`/`Reg()`/`Wire()` class attributes) -- see "Declarative (ModuleSpec) variant" below. |
| `axi_stream_slice_declarative_iface.py` | Declarative `ModuleSpec` **and** interface-bound buses combined -- see "Declarative + interface-bound variant" below. |
| `test_axi_stream_slice.py` | Testbench-framework test suite (this is the "thoroughly tested" part). |

Four builder styles along two independent axes -- imperative vs. declarative,
and raw ports vs. an interface-bound bus:

| | raw ports | interface-bound bus |
|---|---|---|
| **imperative** | `axi_stream_slice.py` | `axi_stream_slice_iface.py` |
| **declarative (`ModuleSpec`)** | `axi_stream_slice_declarative.py` | `axi_stream_slice_declarative_iface.py` |

Each column emits byte-for-byte identical Verilog top to bottom (imperative
vs. declarative is purely a source-code style choice); all four are checked
against each other in `test_axi_stream_slice.py`.

## Parameters

- `data_width` -- width of `tdata`.
- `tuser_width` -- width of `tuser`; `0` omits the port entirely.
- `has_tlast` -- `True` (default) includes `tlast`; `False` omits it.
- `tdata`/`tuser`/`tlast` all skid together as one combined payload.

## Step 1 -- inspect the inferred plan

```python
from axi_stream_slice import build_axi_stream_slice
from veriforge.sim.bench import Testbench

dut = build_axi_stream_slice(data_width=16, tuser_width=2, has_tlast=True)
bench = Testbench(dut)
print(bench.plan.summary())
```

Unlike `examples/axis_skid_buffer` (which starts from a `.v` file via
`parse_file()`), `Module.build()` already returns the model object
`Testbench` wants -- no round-trip through Verilog text needed to test a
DSL-authored module.

## Step 2 -- run the test suite

```bash
uv run python examples/axis/slice/test_axi_stream_slice.py
uv run python examples/axis/slice/test_axi_stream_slice.py --vcd build/slice.vcd
```

What's covered:

- **`test_basic`** -- multiple frames, per-beat `tuser`, `tlast` framing.
- **`test_backpressure`** -- `PauseGenerator` stalling `s_axis_tvalid` *and*
  `m_axis_tready` at the same time (~50% each) -- the scenario the skid
  register exists for: a beat gets accepted on the exact cycle the output
  register is still full and undrained.
- **`test_parameter_variants`** -- `tuser_width=0` and a wider
  data/tuser combination, same stress pattern.
- **`test_no_tlast_variant_lowlevel`** -- `has_tlast=False`, driven through
  `veriforge.sim.Simulator` directly rather than `bench.iface()` (see
  below).
- **`test_iface_variant_matches_raw`** -- `axi_stream_slice_iface.py`
  produces identical behavior to the raw-port module for the same stimulus.
- **`test_declarative_variant_matches_raw`** -- `axi_stream_slice_declarative.py`
  produces identical behavior to the raw-port module for the same stimulus.
- **`test_declarative_iface_variant_matches_raw`** -- `axi_stream_slice_declarative_iface.py`
  produces identical behavior to the raw-port module for the same stimulus.

### A framework limitation found along the way

`has_tlast=False` is a real, working mode of this module, but veriforge's
high-level `AXIStreamProxy` / `AXIStreamSource` endpoints currently
hard-require a `tlast` port on the DUT, even though the *planner* already
supports relaxed ("tlast-less AXIS") detection via `relaxed_iface_signals`.
So that variant is tested by dropping to `Simulator.drive()/settle()`
directly instead of `bench.iface()`. If tlast-less streams need first-class
high-level testbench support, that's a follow-up in
`src/veriforge/sim/endpoints/axis_source.py` (and the matching sink), not
something to fix in this example.

## Interface-based variant

`axi_stream_slice_iface.py` builds the identical module using
`m.interface("s_axis", axi_stream(...), role="slave", reg=True)` /
`m.interface("m_axis", ..., role="master", reg=True)` instead of
hand-declaring each port. `reg=True` makes every *output* signal for that
role an `output_reg` -- which registers `s_axis_tready` on the slave side
and `tvalid`/`tdata`/`tuser`/`tlast` on the master side, exactly the
registered-both-directions shape a full slice needs, in two calls instead
of nine. See that file's module docstring for the (minor) trade-offs versus
the raw-port version.

## Declarative (ModuleSpec) variant

`axi_stream_slice_declarative.py` builds the identical module again, this time in
veriforge's declarative style -- ports/regs/wires as `ModuleSpec` class
attributes (`In()`, `OutReg()`, `Reg()`, `Wire()`) instead of imperative
`m.input()`/`m.output_reg()` calls, per `notes/dsl/dsl_guide.md`'s
"declarative is the default/recommended style" guidance.

The wrinkle: `tuser`/`tlast` are only present when `tuser_width`/`has_tlast`
say so, so the port list isn't fixed at class-body-*text* time. The fix is
the same one used in gfwx-fpga's `top/gfwx_encode_core/dsl/gfwx_encode_core.py`
-- nest the `class AxiStreamSliceDeclarative(ModuleSpec):` inside the factory
function, so the class body executes once per call with `data_width`/
`tuser_width`/`has_tlast` already bound in the closure, and a plain `if`
guarding a descriptor assignment skips that port entirely:

```python
def build_axi_stream_slice_declarative(data_width=32, tuser_width=0, has_tlast=True, name="axi_stream_slice_declarative"):
    class AxiStreamSliceDeclarative(ModuleSpec):
        module_name = name
        s_axis_tdata = In(data_width)
        if tuser_width:
            s_axis_tuser = In(tuser_width)
        if has_tlast:
            s_axis_tlast = In()
        ...
    return AxiStreamSliceDeclarative().build()
```

Unlike the interface-based variant, `OutReg(init=1)` gives per-signal power-on
defaults directly, so `s_axis_tready`'s `= 1` matches the raw-port version
exactly (`emit_module()` output is byte-for-byte identical between the two).

## Declarative + interface-bound variant

`axi_stream_slice_declarative_iface.py` combines the previous two: `ModuleSpec`
class attributes for the fixed signals (`clk`, `rst`, the skid register),
and `m.interface()` calls inside `body(self, m)` for `s_axis`/`m_axis`.
`notes/dsl/dsl_guide.md` calls out bus-interface expansion as one of the
specific cases where the imperative builder is still the right tool *even
inside an otherwise-declarative module* -- a variable-width, prefix-driven
signal group isn't a fixed, nameable class attribute the way `clk = In()`
is. `body()` on a `ModuleSpec` always receives the live imperative `Module`
builder, so this isn't a workaround, it's the documented way the two styles
compose (see gfwx-fpga's `gfwx_encode_core.py`, which does the same thing
for its own `s_axis`/`m_axis` ports plus several internal buses):

```python
class AxiStreamSliceDeclarativeIface(ModuleSpec):
    clk = In()
    rst = In()
    r_tdata = Reg(data_width, init=0)              # declarative: fixed signals

    def body(self, m):
        s = m.interface("s_axis", intf, role="slave", reg=True)     # procedural: the bus
        out = m.interface("m_axis", intf, role="master", reg=True)
        ...
```

Same trade-off as the plain interface-based variant: `axi_stream()` always
includes `tlast` (no `has_tlast=False` here), and `s_axis_tready`'s
power-on default comes from the reset branch rather than an explicit
`= 1`. `emit_module()` output is byte-for-byte identical to
`axi_stream_slice_iface.py`.

## Using this component from another project

This directory is self-contained: `axi_stream_slice.py` only imports from
`veriforge.dsl`. Another project with `veriforge` as a dependency can
import `build_axi_stream_slice` directly, or call
`emit_module(build_axi_stream_slice(...))` (see
`veriforge.codegen.emit_module`) to generate a standalone `.v` file to
vendor in.
