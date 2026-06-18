"""AXI4-Stream protocol components.

Provides an interface definition for AXI4-Stream and reusable modules
for streaming data pipelines.

Usage::

    from veriforge.dsl import Module, posedge
    from veriforge.dsl.lib import axi_stream, axis_register
    from veriforge.codegen import emit_module

    # Use the interface in a custom module
    m = Module("my_producer")
    clk = m.input("clk")
    m_axis = m.interface("m_axis", axi_stream(data_width=32), role="master")
    with m.always(posedge(clk)):
        m_axis.tvalid <<= 1
        m_axis.tdata  <<= 42
        m_axis.tlast  <<= 0

    # Or use the pre-built pipeline register
    reg = axis_register(data_width=32)
    print(emit_module(reg.build()))
"""

from __future__ import annotations

from .. import Interface, Module, posedge


def axi_stream(
    data_width: int = 8,
    *,
    tid_width: int = 0,
    tdest_width: int = 0,
    tuser_width: int = 0,
) -> Interface:
    """Create an AXI4-Stream interface definition.

    Returns an :class:`Interface` template.  Bind it to a module with
    ``m.interface(prefix, intf, role="master"|"slave")``.

    Standard signals (always present):
        tvalid, tready, tdata [data_width-1:0], tlast

    Optional signals (present when width > 0):
        tid [tid_width-1:0], tdest [tdest_width-1:0], tuser [tuser_width-1:0]

    Args:
        data_width: Width of tdata in bits.
        tid_width: Width of tid (0 = omit).
        tdest_width: Width of tdest (0 = omit).
        tuser_width: Width of tuser (0 = omit).

    Returns:
        Interface template.
    """
    i = Interface("axi_stream")
    i.signal("tvalid", src="master")
    i.signal("tready", src="slave")
    i.signal("tdata", width=data_width, src="master")
    i.signal("tlast", src="master")
    if tid_width > 0:
        i.signal("tid", width=tid_width, src="master")
    if tdest_width > 0:
        i.signal("tdest", width=tdest_width, src="master")
    if tuser_width > 0:
        i.signal("tuser", width=tuser_width, src="master")
    return i


def axis_register(
    data_width: int = 8,
    *,
    name: str = "axis_register",
) -> Module:
    """Build an AXI-Stream pipeline register (forward register slice).

    Adds one cycle of latency for timing closure.  Accepts data from
    the slave interface when the master output is ready or empty.

    Slave interface (input):
        s_axis_tvalid, s_axis_tready, s_axis_tdata, s_axis_tlast

    Master interface (output):
        m_axis_tvalid, m_axis_tready, m_axis_tdata, m_axis_tlast

    Args:
        data_width: Width of tdata in bits.
        name: Module name.

    Returns:
        Module builder.
    """
    intf = axi_stream(data_width)

    m = Module(name)
    clk = m.input("clk")
    rst = m.input("rst")

    # Slave side: we are the slave (data flows in)
    s = m.interface("s_axis", intf, role="slave")
    # Master side: we are the master (data flows out); reg=True for output_reg
    out = m.interface("m_axis", intf, role="master", reg=True)

    m.comment("Forward register: latch data when output is ready or empty")
    with m.always(posedge(clk)):
        with m.if_(rst):
            out.tvalid <<= 0
        with m.else_():
            with m.if_(out.tready | ~out.tvalid):
                out.tvalid <<= s.tvalid
                out.tdata <<= s.tdata
                out.tlast <<= s.tlast

    m.comment("Backpressure: accept when output is ready or empty")
    m.assign(s.tready, out.tready | ~out.tvalid)

    return m
