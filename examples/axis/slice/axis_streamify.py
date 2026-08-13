"""axis_streamify: a decorator that turns a plain ce-gated pipeline into a full AXI-Stream module.

This is the composability idea from the slice examples taken one step
further: instead of hand-wiring an ``axi_stream_slice`` onto the front and
back of every deep/BRAM-heavy pipeline (notes/hdl_guide.md §3.3 -- "the key
trick"), write the pipeline once as an ordinary function that knows nothing
about AXI-Stream at all, and let a decorator supply the slices, the
backpressure, and the ``s_axis``/``m_axis`` ports around it::

    @axis_streamify(data_width=16, tuser_width=4, sof_tuser_bit=0)
    def build_my_pipeline(m, clk, rst, ce, tdata, tvalid, tlast, sideband, sof):
        ...  # plain `if (ce) begin ... end` register stages
        return final_tdata, final_tvalid, final_tlast, final_sideband

    top, design = build_my_pipeline(name="my_pipeline_streamified")

This is specifically the kind of thing that has no equivalent in plain
Verilog: a module definition isn't a value there, so nothing can take one
"pipeline body" as an argument and hand back a different, larger module
wrapped around it. Here it's just a higher-order function.

What the decorator builds, structurally (notes/hdl_guide.md §3.3's diagram):

    s_axis --[axi_stream_slice]--> ce-gated pipeline --[axi_stream_slice]--> m_axis

Two ``axi_stream_slice_iface`` instances (see ``axi_stream_slice_iface.py``
-- this file imports and reuses it directly, unmodified) are wired in as
real sub-module instances, not reimplemented; the interface-based slice is
used specifically because it supports an arbitrary combination of optional
sideband fields (``tuser``/``tid``/``tdest``) uniformly, which the raw-port
slice does not. ``ce`` is the *output* slice's own registered
``s_axis_tready`` -- a clean flop fanned out to every pipeline stage,
decoupling the pipeline's internal timing from both the upstream producer
and the downstream consumer, exactly per §3.3. The input slice's
``m_axis_tready`` is driven by that same ``ce``: the pipeline (and
everything upstream of it) only ever advances when the output slice has
room.

Sideband propagation (``tuser``/``tid``/``tdest``): pass e.g.
``tuser_width=4`` and the decorated function receives a ``sideband`` dict
(``{"tuser": Signal, ...}``, keyed only by whichever fields have width > 0)
holding that beat's registered sideband values, and must return a matching
dict of its own final-stage signals. Each field just rides alongside
tdata/tvalid/tlast through whatever registers the pipeline body builds --
the decorator doesn't interpret the values, only wires them in and out.

Start-of-frame-driven local reset (``sof_tuser_bit``): gfwx-fpga's own
AGENTS.md convention is "prefer local, implicit resets from the pixel
stream itself -- deassert on tuser == 1 (SOF) with tvalid" rather than
relying solely on the global synchronous ``rst`` for per-frame pipeline
state (running sums, line buffers, FSMs that must restart every frame).
Set ``sof_tuser_bit=N`` and the decorated function additionally receives
``sof`` -- a 1-bit signal, true exactly when the beat currently being
presented to the pipeline (``tvalid``, qualified by ``ce`` implicitly since
``sof`` is only meaningful where the pipeline body reads it, inside its own
``if (ce)``) has bit ``N`` of ``tuser`` set. A typical use is
``with m.if_(rst | sof): accumulator <<= 0``. When ``sof_tuser_bit`` is
``None`` (the default), ``sof`` is just the Python literal ``0`` -- the
decorated function can reference it unconditionally either way.

Limitation kept deliberately simple rather than fully general: the
decorated function's pipeline is assumed fixed-latency (tvalid/tlast/
sideband all just ride along under the same ``ce``, per §3's "plain
pipeline inside" pattern -- no per-stage backpressure, no variable
latency). Good enough to prove the composition works.
"""

from __future__ import annotations

from axi_stream_slice_iface import build_axi_stream_slice_iface

from veriforge.dsl import Module
from veriforge.dsl.lib.axi_stream import axi_stream
from veriforge.model.design import Design
from veriforge.model.design import Module as ModelModule

_SIDEBAND_FIELDS = ("tuser", "tid", "tdest")


def axis_streamify(
    *,
    data_width: int,
    tuser_width: int = 0,
    tid_width: int = 0,
    tdest_width: int = 0,
    sof_tuser_bit: int | None = None,
):
    """Decorator factory: wrap a plain ce-gated pipeline body as an AXI-Stream module.

    Args:
        data_width: Width of ``tdata`` on both ``s_axis`` and ``m_axis``.
        tuser_width: Width of ``tuser`` to propagate; ``0`` omits it.
        tid_width: Width of ``tid`` to propagate; ``0`` omits it.
        tdest_width: Width of ``tdest`` to propagate; ``0`` omits it.
        sof_tuser_bit: If set, the decorated function also receives ``sof``
            -- see the module docstring. Requires ``tuser_width > 0`` and
            ``0 <= sof_tuser_bit < tuser_width``.

    The decorated function must be shaped::

        def core(m, clk, rst, ce, tdata, tvalid, tlast, sideband, sof) \\
                -> (tdata, tvalid, tlast, sideband)

    It builds its pipeline stages directly into ``m`` -- ordinary registers
    gated by ``if (ce)``, no AXI-Stream handshaking at all -- and returns its
    final stage's data/valid/last/sideband signals. It never sees
    ``m_axis_tready`` or an ``s_axis_tready`` it has to drive; the
    decorator's slices own all of that. ``sideband`` (in and out) is a dict
    keyed by whichever of ``tuser``/``tid``/``tdest`` have width > 0 --
    empty if none do.

    Returns:
        A ``build(name=None) -> (ModelModule, Design)`` function. The
        ``Design`` bundles the streamified top module together with the
        (single, twice-instantiated) ``axi_stream_slice_iface`` module it
        depends on -- pass it to ``Testbench(top, design=design)`` /
        ``Simulator(top, design=design)`` for simulation, or emit both
        modules from ``design.modules`` for synthesizable output.
    """
    if sof_tuser_bit is not None:
        if tuser_width <= 0:
            raise ValueError("sof_tuser_bit requires tuser_width > 0")
        if not (0 <= sof_tuser_bit < tuser_width):
            raise ValueError(f"sof_tuser_bit={sof_tuser_bit} out of range for tuser_width={tuser_width}")

    widths = {"tuser": tuser_width, "tid": tid_width, "tdest": tdest_width}
    sideband_fields = [f for f in _SIDEBAND_FIELDS if widths[f]]
    intf = axi_stream(data_width, tuser_width=tuser_width, tid_width=tid_width, tdest_width=tdest_width)

    def decorator(core):
        def build(name: str | None = None) -> tuple[ModelModule, Design]:
            mod_name = name or f"{core.__name__}_streamified"
            slice_name = f"{mod_name}_axi_stream_slice"
            slice_module = build_axi_stream_slice_iface(
                data_width=data_width,
                tuser_width=tuser_width,
                tid_width=tid_width,
                tdest_width=tdest_width,
                name=slice_name,
            )

            m = Module(mod_name)
            clk = m.input("clk")
            rst = m.input("rst")

            s = m.interface("s_axis", intf, role="slave")
            mo = m.interface("m_axis", intf, role="master")

            # --- input slice: isolates upstream (producer) timing ---
            in_ = m.wire_interface("in_axis", intf)
            # ce is the output slice's own registered s_axis_tready (§3.3) --
            # a single clean flop fanned out to the whole pipeline. It's
            # literally the same wire as the input slice's m_axis_tready
            # (aliased here for readability), not a separate signal.
            ce = in_.tready
            m.instance(
                slice_name,
                "in_slice",
                ports={"clk": clk, "rst": rst, **s.port_map("s_axis"), **in_.port_map("m_axis")},
            )

            in_sideband = {f: getattr(in_, f) for f in sideband_fields}
            if sof_tuser_bit is not None:
                sof = m.wire("sof")
                sof.assign = in_.tvalid & in_.tuser[sof_tuser_bit]
            else:
                sof = 0

            # --- the plain pipeline: no AXI-Stream in sight ---
            out_tdata, out_tvalid, out_tlast, out_sideband = core(
                m, clk, rst, ce, in_.tdata, in_.tvalid, in_.tlast, in_sideband, sof
            )

            # --- output slice: isolates downstream (consumer) timing, and
            #     its registered s_axis_tready IS ce ---
            m.instance(
                slice_name,
                "out_slice",
                ports={
                    "clk": clk,
                    "rst": rst,
                    "s_axis_tvalid": out_tvalid,
                    "s_axis_tready": ce,
                    "s_axis_tdata": out_tdata,
                    "s_axis_tlast": out_tlast,
                    **{f"s_axis_{f}": out_sideband[f] for f in sideband_fields},
                    **mo.port_map("m_axis"),
                },
            )

            top = m.build()
            return top, Design(modules=[slice_module, top])

        build.__name__ = f"build_{core.__name__}"
        return build

    return decorator
