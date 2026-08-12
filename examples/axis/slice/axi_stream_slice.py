"""axi_stream_slice: full register slice / skid buffer for AXI4-Stream.

Registers data in BOTH directions -- m_axis_tdata/tvalid going out, and
s_axis_tready coming back -- so no combinational path runs through the slice
in either direction. A one-deep skid register catches the beat that was
already accepted the cycle m_axis_tready drops, so no beat is lost and no
throughput is given up once a stall clears.

Use this at a module boundary where you don't want the signal on the OTHER
side of m_axis_tready to reach back, combinationally, into anything on this
side -- in particular right before a clock-enable/stall signal derived from
m_axis_tready fans out to a deep or BRAM-heavy pipeline: put the slice on
that pipeline's output and derive the enable from the SLICE's own registered
s_axis_tready, not from the raw downstream m_axis_tready. That decouples the
pipeline's internal timing from whatever is on the other side of the slice.

tdata/tuser/tlast all skid together as one combined payload; ``tuser_width=0``
omits tuser entirely and ``has_tlast=False`` omits tlast, for streams that
don't carry one.

Interface:
    clk, rst
    s_axis: tdata[data_width-1:0], (tuser[tuser_width-1:0]), (tlast), tvalid, tready
    m_axis: same fields, tdata/tuser/tlast/tvalid registered, tready driven
        by whatever is downstream (this module reads it combinationally --
        that's fine, no path runs THROUGH this module because of it, only
        INTO it, and it terminates in a register here).

See ``axi_stream_slice_iface.py`` in this directory for a second builder that
produces the *identical* module but declares its ports via the DSL's
``Interface``/``m.interface()`` abstraction instead of raw ``m.input()`` /
``m.output_reg()`` calls.
"""

from veriforge.dsl import Module, Signal, posedge


def _nb(sig: Signal | None, val: object) -> None:
    """Non-blocking assign, skipped when `sig` is an omitted optional signal (tuser/tlast).

    Uses `.next =` rather than `<<=`: `<<=` desugars to reassigning the
    local variable to its own `__ilshift__` return value (`Expr`, broader
    than `Signal`), which a type checker rejects for a `Signal | None`
    parameter even after narrowing `sig is not None` inside the `if`.
    `.next =` is a property setter -- it never rebinds `sig` -- so no such
    conflict exists.
    """
    if sig is not None:
        sig.next = val


def build_axi_stream_slice(data_width=32, tuser_width=0, has_tlast=True, name="axi_stream_slice"):
    m = Module(name)

    clk = m.input("clk")
    rst = m.input("rst")

    # Raw ports throughout (both sides): s_axis_tready is registered here,
    # which needs direct control over the always-block logic below rather
    # than just a port declaration -- see axi_stream_slice_iface.py for the
    # equivalent module built from the library's axi_stream() interface
    # template instead (it supports registered outputs too, via
    # m.interface(..., reg=True)).
    s_tvalid = m.input("s_axis_tvalid")
    s_tready = m.output_reg("s_axis_tready", init=1)
    s_tdata = m.input("s_axis_tdata", width=data_width)
    s_tuser = m.input("s_axis_tuser", width=tuser_width) if tuser_width else None
    s_tlast = m.input("s_axis_tlast") if has_tlast else None

    m_tvalid = m.output_reg("m_axis_tvalid", init=0)
    m_tready = m.input("m_axis_tready")
    m_tdata = m.output_reg("m_axis_tdata", width=data_width, init=0)
    m_tuser = m.output_reg("m_axis_tuser", width=tuser_width, init=0) if tuser_width else None
    m_tlast = m.output_reg("m_axis_tlast", init=0) if has_tlast else None

    # Skid register: catches the beat s_axis_tready already accepted the
    # cycle m_axis_tvalid&~m_axis_tready first held the output register full.
    r_tdata = m.reg("r_tdata", width=data_width, init=0)
    r_tuser = m.reg("r_tuser", width=tuser_width, init=0) if tuser_width else None
    r_tlast = m.reg("r_tlast", init=0) if has_tlast else None

    r_tvalid_w = m.wire("r_tvalid_w")
    r_tvalid_w.assign = ~s_tready

    with m.always(posedge(clk)):
        with m.if_(rst):
            m_tvalid <<= 0
            s_tready <<= 1
            r_tdata <<= 0
            _nb(r_tuser, 0)
            _nb(r_tlast, 0)
            m_tdata <<= 0
            _nb(m_tuser, 0)
            _nb(m_tlast, 0)

        with m.else_():
            with m.if_(~m_tvalid | m_tready):
                m_tvalid <<= s_tvalid | r_tvalid_w

            # STOP the moment a beat we just accepted finds the output
            # register still full (not yet drained).
            with m.if_((s_tvalid & s_tready) & (m_tvalid & ~m_tready)):
                s_tready <<= 0
            with m.elif_(m_tready):
                s_tready <<= 1
            # else: implicit hold.

            with m.if_(s_tready):
                r_tdata <<= s_tdata
                _nb(r_tuser, s_tuser)
                _nb(r_tlast, s_tlast)

            with m.if_(m_tready | ~m_tvalid):
                with m.if_(r_tvalid_w):
                    m_tdata <<= r_tdata
                    _nb(m_tuser, r_tuser)
                    _nb(m_tlast, r_tlast)
                with m.elif_(s_tvalid & s_tready):
                    m_tdata <<= s_tdata
                    _nb(m_tuser, s_tuser)
                    _nb(m_tlast, s_tlast)

    return m.build()
