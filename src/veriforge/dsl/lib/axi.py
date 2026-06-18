"""AXI4-Lite protocol interface definition.

Provides the standard AXI4-Lite interface template for register-based
peripherals.

Usage::

    from veriforge.dsl import Module
    from veriforge.dsl.lib import axi4_lite

    m = Module("my_slave")
    clk = m.input("clk")
    rst = m.input("rst")
    s_axi = m.interface("s_axi", axi4_lite(data_width=32, addr_width=8), role="slave")

    # s_axi.awaddr, s_axi.awvalid, s_axi.awready, etc.
"""

from __future__ import annotations

from .. import Interface


def axi4_lite(
    data_width: int = 32,
    addr_width: int = 32,
) -> Interface:
    """Create an AXI4-Lite interface definition.

    Includes all five AXI4-Lite channels: write address (AW), write data (W),
    write response (B), read address (AR), and read data (R).

    Returns an :class:`Interface` template.  Bind it to a module with
    ``m.interface(prefix, intf, role="master"|"slave")``.

    Args:
        data_width: Width of wdata / rdata in bits (typically 32 or 64).
        addr_width: Width of awaddr / araddr in bits.

    Returns:
        Interface template with 20 signals across 5 channels.
    """
    strb_width = data_width // 8

    i = Interface("axi4_lite")

    # Write address channel (AW)
    i.signal("awaddr", width=addr_width, src="master")
    i.signal("awprot", width=3, src="master")
    i.signal("awvalid", src="master")
    i.signal("awready", src="slave")

    # Write data channel (W)
    i.signal("wdata", width=data_width, src="master")
    i.signal("wstrb", width=strb_width, src="master")
    i.signal("wvalid", src="master")
    i.signal("wready", src="slave")

    # Write response channel (B)
    i.signal("bresp", width=2, src="slave")
    i.signal("bvalid", src="slave")
    i.signal("bready", src="master")

    # Read address channel (AR)
    i.signal("araddr", width=addr_width, src="master")
    i.signal("arprot", width=3, src="master")
    i.signal("arvalid", src="master")
    i.signal("arready", src="slave")

    # Read data channel (R)
    i.signal("rdata", width=data_width, src="slave")
    i.signal("rresp", width=2, src="slave")
    i.signal("rvalid", src="slave")
    i.signal("rready", src="master")

    return i
