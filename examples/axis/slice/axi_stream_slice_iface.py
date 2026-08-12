"""axi_stream_slice, rebuilt on top of the DSL's ``Interface`` abstraction.

``axi_stream_slice.py`` declares its ports one-by-one with raw ``m.input()``
/ ``m.output_reg()`` calls. This module builds the *same* full register
slice / skid buffer, but declares ``s_axis`` and ``m_axis`` in one call each
via ``m.interface(prefix, axi_stream(...), role=..., reg=True)`` from
``veriforge.dsl.lib.axi_stream`` -- the same helper used by the library's
own ``axis_register`` (a half-slice, no-skid pipeline register).

``reg=True`` makes every *output* signal for that role an ``output_reg``
instead of a plain ``output`` -- so ``role="slave", reg=True`` registers
``s_axis_tready`` (the only slave-side output), and ``role="master",
reg=True`` registers ``tvalid``/``tdata``/``tuser``/``tlast`` (the
master-side outputs). That is exactly the registered-both-directions shape
a full slice needs, expressed without hand-declaring each port.

Trade-off vs. the raw-port version: the library's ``axi_stream()`` interface
always includes ``tlast`` (no ``has_tlast=False`` escape hatch), and
``m.interface(..., reg=True)`` does not expose a per-signal ``init=`` value,
so ``s_axis_tready``'s power-on default comes from the synchronous reset
branch alone rather than also getting an explicit ``= 1`` in the port
declaration. Functionally identical once out of reset -- see
``test_axi_stream_slice.py::test_iface_variant_matches_raw`` -- but worth
knowing before reaching for ``m.interface()`` on a module that must also
behave sanely for the handful of cycles *before* reset is first applied.

Use whichever style reads more naturally for a given module: raw ports when
the exact declaration text matters (custom widths per field, conditional
ports), ``m.interface()`` when you just want a standard bus wired up fast.
"""

from veriforge.dsl import Module, Signal, posedge
from veriforge.dsl.lib.axi_stream import axi_stream


def _nb(sig: Signal | None, val: object) -> None:
    """Non-blocking assign, skipped when `sig` is an omitted optional signal (r_tuser only, here).

    See axi_stream_slice.py's `_nb` docstring for why `.next =` is used
    instead of `<<=` -- `<<=` rebinds the local variable to its
    `__ilshift__` return value, which a type checker rejects for a
    `Signal | None` parameter even after narrowing.
    """
    if sig is not None:
        sig.next = val


def build_axi_stream_slice_iface(data_width=32, tuser_width=0, name="axi_stream_slice_iface"):
    intf = axi_stream(data_width, tuser_width=tuser_width)

    m = Module(name)
    clk = m.input("clk")
    rst = m.input("rst")

    s = m.interface("s_axis", intf, role="slave", reg=True)  # s.tready is output_reg
    out = m.interface("m_axis", intf, role="master", reg=True)  # out.tvalid/tdata/tuser/tlast are output_reg

    r_tdata = m.reg("r_tdata", width=data_width, init=0)
    r_tuser = m.reg("r_tuser", width=tuser_width, init=0) if tuser_width else None
    r_tlast = m.reg("r_tlast", init=0)

    r_tvalid_w = m.wire("r_tvalid_w")
    r_tvalid_w.assign = ~s.tready

    with m.always(posedge(clk)):
        with m.if_(rst):
            out.tvalid <<= 0
            s.tready <<= 1
            r_tdata <<= 0
            _nb(r_tuser, 0)
            r_tlast <<= 0
            out.tdata <<= 0
            if tuser_width:
                out.tuser <<= 0
            out.tlast <<= 0

        with m.else_():
            with m.if_(~out.tvalid | out.tready):
                out.tvalid <<= s.tvalid | r_tvalid_w

            with m.if_((s.tvalid & s.tready) & (out.tvalid & ~out.tready)):
                s.tready <<= 0
            with m.elif_(out.tready):
                s.tready <<= 1

            with m.if_(s.tready):
                r_tdata <<= s.tdata
                _nb(r_tuser, s.tuser)
                r_tlast <<= s.tlast

            with m.if_(out.tready | ~out.tvalid):
                with m.if_(r_tvalid_w):
                    out.tdata <<= r_tdata
                    if tuser_width:
                        out.tuser <<= r_tuser
                    out.tlast <<= r_tlast
                with m.elif_(s.tvalid & s.tready):
                    out.tdata <<= s.tdata
                    if tuser_width:
                        out.tuser <<= s.tuser
                    out.tlast <<= s.tlast

    return m.build()
