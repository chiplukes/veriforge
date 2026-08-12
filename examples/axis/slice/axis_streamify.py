"""axis_streamify: a decorator that turns a plain ce-gated pipeline into a full AXI-Stream module.

This is the composability idea from the slice examples taken one step
further: instead of hand-wiring an ``axi_stream_slice`` onto the front and
back of every deep/BRAM-heavy pipeline (notes/hdl_guide.md §3.3 -- "the key
trick"), write the pipeline once as an ordinary function that knows nothing
about AXI-Stream at all, and let a decorator supply the slices, the
backpressure, and the ``s_axis``/``m_axis`` ports around it::

    @axis_streamify(data_width=16)
    def build_my_pipeline(m, clk, rst, ce, tdata, tvalid, tlast):
        ...  # plain `if (ce) begin ... end` register stages
        return final_tdata, final_tvalid, final_tlast

    top, design = build_my_pipeline(name="my_pipeline_streamified")

This is specifically the kind of thing that has no equivalent in plain
Verilog: a module definition isn't a value there, so nothing can take one
"pipeline body" as an argument and hand back a different, larger module
wrapped around it. Here it's just a higher-order function.

What the decorator builds, structurally (notes/hdl_guide.md §3.3's diagram):

    s_axis --[axi_stream_slice]--> ce-gated pipeline --[axi_stream_slice]--> m_axis

Two ``axi_stream_slice`` instances (see ``axi_stream_slice.py`` -- this file
imports and reuses it directly, unmodified) are wired in as real
sub-module instances, not reimplemented. ``ce`` is the *output* slice's own
registered ``s_axis_tready`` -- a clean flop fanned out to every pipeline
stage, decoupling the pipeline's internal timing from both the upstream
producer and the downstream consumer, exactly per §3.3. The input slice's
``m_axis_tready`` is driven by that same ``ce``: the pipeline (and
everything upstream of it) only ever advances when the output slice has
room.

Limitations of this first version, kept deliberately simple rather than
fully general: fixed ``has_tlast=True``, no ``tuser`` support, and the
decorated function's pipeline is assumed fixed-latency (tvalid/tlast just
ride along under the same ``ce``, per §3's "plain pipeline inside"
pattern -- no per-stage backpressure). Good enough to prove the composition
works; a production version would want to thread tuser through and support
variable-latency cores.
"""

from __future__ import annotations

from axi_stream_slice import build_axi_stream_slice

from veriforge.dsl import Module
from veriforge.model.design import Design
from veriforge.model.design import Module as ModelModule


def axis_streamify(*, data_width: int):
    """Decorator factory: wrap a plain ce-gated pipeline body as an AXI-Stream module.

    Args:
        data_width: Width of ``tdata`` on both ``s_axis`` and ``m_axis``.

    The decorated function must be shaped::

        def core(m, clk, rst, ce, tdata, tvalid, tlast) -> (tdata, tvalid, tlast)

    It builds its pipeline stages directly into ``m`` -- ordinary registers
    gated by ``if (ce)``, no AXI-Stream handshaking at all -- and returns its
    final stage's data/valid/last signals. It never sees ``m_axis_tready``
    or an ``s_axis_tready`` it has to drive; the decorator's slices own all
    of that.

    Returns:
        A ``build(name=None) -> (ModelModule, Design)`` function. The
        ``Design`` bundles the streamified top module together with the
        (single, twice-instantiated) ``axi_stream_slice`` module it depends
        on -- pass it to ``Testbench(top, design=design)`` /
        ``Simulator(top, design=design)`` for simulation, or emit both
        modules from ``design.modules`` for synthesizable output.
    """

    def decorator(core):
        def build(name: str | None = None) -> tuple[ModelModule, Design]:
            mod_name = name or f"{core.__name__}_streamified"
            slice_name = f"{mod_name}_axi_stream_slice"
            slice_module = build_axi_stream_slice(data_width=data_width, tuser_width=0, has_tlast=True, name=slice_name)

            m = Module(mod_name)
            clk = m.input("clk")
            rst = m.input("rst")

            s_tvalid = m.input("s_axis_tvalid")
            s_tready = m.output("s_axis_tready")
            s_tdata = m.input("s_axis_tdata", width=data_width)
            s_tlast = m.input("s_axis_tlast")

            m_tvalid = m.output("m_axis_tvalid")
            m_tready = m.input("m_axis_tready")
            m_tdata = m.output("m_axis_tdata", width=data_width)
            m_tlast = m.output("m_axis_tlast")

            # ce is the output slice's own registered s_axis_tready (§3.3) --
            # a single clean flop fanned out to the whole pipeline, and also
            # what feeds the input slice's m_axis_tready.
            ce = m.wire("ce")

            # --- input slice: isolates upstream (producer) timing ---
            in_tvalid = m.wire("in_tvalid")
            in_tdata = m.wire("in_tdata", width=data_width)
            in_tlast = m.wire("in_tlast")
            m.instance(
                slice_name,
                "in_slice",
                ports={
                    "clk": clk,
                    "rst": rst,
                    "s_axis_tvalid": s_tvalid,
                    "s_axis_tready": s_tready,
                    "s_axis_tdata": s_tdata,
                    "s_axis_tlast": s_tlast,
                    "m_axis_tvalid": in_tvalid,
                    "m_axis_tready": ce,
                    "m_axis_tdata": in_tdata,
                    "m_axis_tlast": in_tlast,
                },
            )

            # --- the plain pipeline: no AXI-Stream in sight ---
            out_tdata, out_tvalid, out_tlast = core(m, clk, rst, ce, in_tdata, in_tvalid, in_tlast)

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
                    "m_axis_tvalid": m_tvalid,
                    "m_axis_tready": m_tready,
                    "m_axis_tdata": m_tdata,
                    "m_axis_tlast": m_tlast,
                },
            )

            top = m.build()
            return top, Design(modules=[slice_module, top])

        build.__name__ = f"build_{core.__name__}"
        return build

    return decorator
