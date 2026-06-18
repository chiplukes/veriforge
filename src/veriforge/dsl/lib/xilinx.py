"""Xilinx inference patterns — shift register (SRL) and LUTRAM.

Usage::

    from veriforge.dsl.lib import shift_register_srl, lutram
    from veriforge.codegen import emit_module

    srl = shift_register_srl(width=8, depth=32)
    print(emit_module(srl.build()))

Generates Verilog matching Xilinx UG901 / UG687 coding guidelines so
Vivado synthesis infers SRL16E/SRL32E, LUTRAM, or other primitives.
"""

from __future__ import annotations

from .. import Module, posedge


def shift_register_srl(
    width: int = 1,
    depth: int = 16,
    *,
    name: str = "srl_shift_reg",
    style: str | None = None,
) -> Module:
    """Build a shift register that infers Xilinx SRL primitives.

    Vivado maps shift registers stored in a reg array with fixed
    addressing to SRL16E (depth <= 16) or SRLC32E (depth <= 32)
    primitives::

        always @(posedge clk)
            if (ce) begin
                shreg <= {shreg[DEPTH-2:0], din};
            end
        assign dout = shreg[DEPTH-1];

    For deeper shift registers, use the ``style`` attribute to guide
    synthesis (``"srl"``, ``"srl_reg"``, ``"reg"``).

    Ports:
        clk          — clock
        ce           — clock enable
        din [W-1:0]  — serial input
        dout [W-1:0] — serial output (tapped at end of chain)

    Args:
        width: Data width in bits.
        depth: Shift register depth (>= 2).
        name: Module name.
        style: Synthesis attribute ``(* shreg_extract = "yes" *)`` or
               SRL mapping hint. Use ``"srl"`` to force SRL inference.

    Returns:
        Module builder.

    Raises:
        ValueError: If depth < 2 or width < 1.
    """
    if depth < 2:
        raise ValueError(f"depth must be >= 2, got {depth}")
    if width < 1:
        raise ValueError(f"width must be >= 1, got {width}")

    m = Module(name)
    clk = m.input("clk")
    ce = m.input("ce").comment("Clock enable")
    din = m.input("din", width=width).comment("Serial input")
    dout = m.output("dout", width=width).comment("Serial output")

    shreg = m.reg("shreg", width=width, depth=depth)
    if style:
        shreg.attr("shreg_extract", style)

    m.comment("Shift chain with clock enable")
    with m.always(posedge(clk)):
        with m.if_(ce):
            # Shift: write to position 0, read from position depth-1
            shreg[0] <<= din
            for i in range(1, depth):
                shreg[i] <<= shreg[i - 1]

    m.assign(dout, shreg[depth - 1])

    return m


def lutram(
    data_width: int = 8,
    depth: int = 32,
    *,
    name: str = "lutram",
) -> Module:
    """Build a distributed (LUT-based) RAM — Xilinx LUTRAM inference.

    Uses asynchronous read and synchronous write, which Vivado maps to
    distributed RAM (RAM32X1S, RAM64X1S, etc.)::

        always @(posedge clk)
            if (we) mem[waddr] <= din;
        assign dout = mem[raddr];

    For block RAM inference, use ``single_port_ram(style="block")``
    or ``simple_dual_port_ram(style="block")`` from the RAM library.

    Ports:
        clk              — clock
        we               — write enable
        waddr [A-1:0]    — write address
        raddr [A-1:0]    — read address
        din [D-1:0]      — write data
        dout [D-1:0]     — read data (asynchronous / combinational)

    Args:
        data_width: Width of each memory word.
        depth: Number of entries.
        name: Module name.

    Returns:
        Module builder.

    Raises:
        ValueError: If depth < 2 or data_width < 1.
    """
    if depth < 2:
        raise ValueError(f"depth must be >= 2, got {depth}")
    if data_width < 1:
        raise ValueError(f"data_width must be >= 1, got {data_width}")

    addr_width = (depth - 1).bit_length() or 1

    m = Module(name)
    clk = m.input("clk")
    we = m.input("we").comment("Write enable")
    waddr = m.input("waddr", width=addr_width).comment("Write address")
    raddr = m.input("raddr", width=addr_width).comment("Read address")
    din = m.input("din", width=data_width).comment("Write data")
    dout = m.output("dout", width=data_width).comment("Read data (async)")

    mem = m.reg("mem", width=data_width, depth=depth)
    mem.attr("ram_style", "distributed")

    m.comment("Synchronous write")
    with m.always(posedge(clk)):
        with m.if_(we):
            mem[waddr] <<= din

    m.comment("Asynchronous (combinational) read")
    m.assign(dout, mem[raddr])

    return m
