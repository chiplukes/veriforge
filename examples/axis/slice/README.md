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
| `axi_stream_slice_iface.py` | The same module rebuilt with `m.interface()` / `axi_stream()` instead of individual `m.input()`/`m.output_reg()` calls -- see "Interface-based variant" below. Also the only variant supporting `tid_width`/`tdest_width` alongside `tuser_width`. |
| `axi_stream_slice_declarative.py` | The same module again, rebuilt in the **declarative** `ModuleSpec` style (`In()`/`OutReg()`/`Reg()`/`Wire()` class attributes) -- see "Declarative (ModuleSpec) variant" below. |
| `axi_stream_slice_declarative_iface.py` | Declarative `ModuleSpec` **and** interface-bound buses combined -- see "Declarative + interface-bound variant" below. |
| `test_axi_stream_slice.py` | Testbench-framework test suite (this is the "thoroughly tested" part). |
| `axis_streamify.py` | A decorator that wraps a plain ce-gated pipeline as a full `s_axis`/`m_axis` module, instantiating `axi_stream_slice` on the front and back -- see "The `axis_streamify` decorator" below. |
| `test_axis_streamify.py` | A 3-stage demo pipeline decorated with `@axis_streamify`, tested the same way as the slice itself (including under heavy back-pressure). |

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

It also takes `tid_width`/`tdest_width` alongside `tuser_width` (the
raw-port version only has the latter) -- since every optional sideband
field is handled through `axi_stream()`/`BoundInterface` uniformly, adding
two more is a `_SIDEBAND` list and a loop, not tripling every
`if tuser_width:` guard by hand. `axis_streamify.py`'s decorator uses this
variant specifically for that reason.

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

## The `axis_streamify` decorator

The four variants above all demonstrate the same module written different
ways. `axis_streamify.py` demonstrates something Verilog has no equivalent
of at all: a module definition as a Python value, passed into a function
that builds a bigger module around it.

```python
@axis_streamify(data_width=16, tuser_width=4, sof_tuser_bit=0)
def build_my_pipeline(m, clk, rst, ce, tdata, tvalid, tlast, sideband, sof):
    # ordinary if (ce) begin ... end register stages -- no AXI-Stream here
    ...
    return final_tdata, final_tvalid, final_tlast, final_sideband

top, design = build_my_pipeline(name="my_pipeline_streamified")
```

The decorated function is a plain pipeline -- exactly `notes/hdl_guide.md`
§3's "AXI-Stream at the edges, plain pipeline inside" shape, written with
zero knowledge that AXI-Stream exists: no `tready`, no slices, no
backpressure, just registers gated by `ce`. `axis_streamify` builds a
wrapper module around it that:

1. Instantiates `axi_stream_slice_iface` (this directory's own interface-
   bound component -- reused as a real sub-module instance, not
   reimplemented; interface-bound specifically because it's the one that
   supports an arbitrary combination of `tuser`/`tid`/`tdest`) on the input,
   isolating the pipeline from the upstream producer's timing.
2. Calls the decorated function to fill in the pipeline body, wired to the
   input slice's registered output.
3. Instantiates a second `axi_stream_slice_iface` on the output, and
   derives `ce` from *that* slice's own registered `s_axis_tready` --
   notes/hdl_guide.md §3.3, "the key trick": a single clean flop fanned out
   to the whole pipeline, decoupling its internal timing from the
   downstream consumer.

Because this produces a real multi-module hierarchy (the wrapper, plus one
`axi_stream_slice_iface` module instantiated twice), `build(...)` returns
`(top, design)` -- a `veriforge.model.design.Design` bundling both modules.
Pass `design=design` to `Testbench`/`Simulator` for simulation, or emit
`design.modules` for synthesizable Verilog output.

### Sideband propagation (`tuser`/`tid`/`tdest`)

Pass e.g. `tuser_width=4` and the decorated function additionally receives
a `sideband` dict (`{"tuser": Signal, ...}`, keyed only by whichever of
`tuser`/`tid`/`tdest` have width > 0 -- empty if none do) holding that
beat's registered value, and must return a matching dict of its own
final-stage signals. The decorator doesn't interpret these values at all,
just wires them into and out of the pipeline alongside `tdata`/`tvalid`/
`tlast`.

### Start-of-frame-driven local reset (`sof_tuser_bit`)

gfwx-fpga's own AGENTS.md convention: "prefer local, implicit resets from
the pixel stream itself -- deassert on tuser == 1 (SOF) with tvalid",
rather than relying solely on the global synchronous `rst` for per-frame
pipeline state (running sums, line buffers, anything that must restart
every frame). Set `sof_tuser_bit=N` and the decorated function additionally
receives `sof` -- 1-bit, true when the beat currently presented to the
pipeline has bit `N` of `tuser` set. Typical use:
`with m.if_(rst | sof): accumulator <<= 0`. When `sof_tuser_bit` is not
given, `sof` is just the Python literal `0`, so a decorated function can
always reference it unconditionally.

**A pitfall this feature makes easy to hit, worth knowing before writing a
stateful pipeline stage**: `ce` means "the pipeline may advance if it has a
beat," *not* "there is a beat this cycle" -- it's derived from the output
slice's own readiness, unrelated to whether the input side currently has
valid data. Under source-side back-pressure, `ce` is frequently high on a
cycle where `tvalid` is low (a bubble). A stateless per-beat transform
doesn't care -- garbage computed on a bubble is discarded once `tvalid_pN`
correctly propagates to 0. A *stateful* stage (an accumulator, in
particular) does care: update on a bubble and its state is now corrupted
by an accepted-looking operation on stale/held data, corruption that
carries into every subsequent real beat. Gate stateful updates on
`tvalid` in addition to `ce` -- `test_sideband_and_sof_backpressure` in
`test_axis_streamify.py` exists specifically because this bug slipped
through on the first pass, undetected until tested under back-pressure;
see `build_running_sum_pipeline`'s docstring there for the concrete fix.

```bash
uv run python examples/axis/slice/test_axis_streamify.py
```

`test_axis_streamify.py` has two decorated demo pipelines, each tested
under both no-stall and heavy-`PauseGenerator`-on-both-sides conditions:

- `build_demo_pipeline` -- `y = ((x + 1) ^ 0x0F0F) + 5` over three plain
  register stages, no sideband/sof. The point of `test_basic`/
  `test_backpressure`: the pipeline body has zero backpressure logic of
  its own; both slices absorb it, for free, just from the decorator.
- `build_running_sum_pipeline` -- a per-frame running sum (`tuser_width=3`,
  `sof_tuser_bit=0`), with a "channel" tag riding through unchanged in the
  other two `tuser` bits. `test_sideband_and_sof`/
  `test_sideband_and_sof_backpressure` verify the accumulator restarts at
  each frame boundary (driven by `sof`, not `tlast`) and `tuser` arrives at
  `m_axis` byte-for-byte identical to what was sent.

Limitation kept deliberately simple rather than fully general: the
pipeline is assumed fixed-latency (`tvalid`/`tlast`/sideband all ride along
under the same `ce`, no per-stage stalling, no variable latency). See the
module docstring in `axis_streamify.py` for the exact contract.

## Using this component from another project

This directory is self-contained: `axi_stream_slice.py` only imports from
`veriforge.dsl`. Another project with `veriforge` as a dependency can
import `build_axi_stream_slice` directly, or call
`emit_module(build_axi_stream_slice(...))` (see
`veriforge.codegen.emit_module`) to generate a standalone `.v` file to
vendor in.
