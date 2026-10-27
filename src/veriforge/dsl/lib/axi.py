"""AXI interface definitions: AXI4-Lite, and the read channels of AXI4.

`axi4_lite` is the standard AXI4-Lite template for register-based
peripherals; `axi4_read` is the AR/R subset of full AXI4 for a read-only
burst master (a DMA reader, an HBM read port).

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


def axi4_read(
    data_width: int = 32,
    addr_width: int = 32,
    *,
    include_rready: bool = True,
) -> Interface:
    """Create a read-only AXI4 interface definition: the AR and R channels.

    INCR bursts without IDs or the optional AR attributes: ``araddr``,
    ``arlen``, ``arsize``, ``arburst``, ``arvalid``/``arready``, then ``rdata``,
    ``rresp``, ``rlast``, ``rvalid`` and ``rready``, in that order. Without IDs
    the slave returns bursts in request order. Bind it with
    ``m.interface(prefix, axi4_read(...), role="master"|"slave")``.

    Args:
        data_width: Width of rdata in bits.
        addr_width: Width of araddr in bits.
        include_rready: False leaves out ``rready``, for a master that accepts
            every beat (the slave then treats R as always ready).

    Returns:
        Interface template with 11 signals (10 without rready).
    """
    i = Interface("axi4_read")

    # Read address channel (AR)
    i.signal("araddr", width=addr_width, src="master")
    i.signal("arlen", width=8, src="master")
    i.signal("arsize", width=3, src="master")
    i.signal("arburst", width=2, src="master")
    i.signal("arvalid", src="master")
    i.signal("arready", src="slave")

    # Read data channel (R)
    i.signal("rdata", width=data_width, src="slave")
    i.signal("rresp", width=2, src="slave")
    i.signal("rlast", src="slave")
    i.signal("rvalid", src="slave")
    if include_rready:
        i.signal("rready", src="master")

    return i
