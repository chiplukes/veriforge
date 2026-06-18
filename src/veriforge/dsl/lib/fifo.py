"""Synchronous FIFO — power-of-2 depth, pointer-based full/empty detection.

Usage::

    from veriforge.dsl.lib import sync_fifo
    from veriforge.codegen import emit_module

    fifo = sync_fifo(data_width=8, depth=16)
    print(emit_module(fifo.build()))

Generates Verilog that FPGA synthesis tools infer as block RAM + control logic.
"""

from __future__ import annotations

from .. import Module, posedge


def sync_fifo(
    data_width: int = 8,
    depth: int = 16,
    *,
    name: str = "sync_fifo",
    style: str | None = None,
) -> Module:
    """Build a synchronous FIFO with full/empty/count outputs.

    Uses a dual-pointer design with an extra MSB for full/empty detection.
    Write and read can occur simultaneously.

    Ports:
        clk, rst       — clock and synchronous reset
        wr_en, rd_en   — write / read enable
        din [W-1:0]    — write data
        dout [W-1:0]   — read data (registered, one-cycle latency)
        full, empty    — status flags
        count [A:0]    — current FIFO occupancy (0 .. depth)

    Args:
        data_width: Width of each FIFO word in bits.
        depth: Number of entries (must be a power of 2, >= 2).
        name: Module name.
        style: Optional RAM style attribute (``"block"``, ``"distributed"``).

    Returns:
        Module builder (call ``.build()`` to finalize).

    Raises:
        ValueError: If *depth* is not a power of 2 or is less than 2.
    """
    if depth < 2 or (depth & (depth - 1)) != 0:
        raise ValueError(f"FIFO depth must be a power of 2 (>= 2), got {depth}")

    addr_width = (depth - 1).bit_length()
    ptr_width = addr_width + 1  # extra MSB for wrap-around detection

    m = Module(name)
    clk = m.input("clk")
    rst = m.input("rst")
    wr_en = m.input("wr_en").comment("Write enable")
    rd_en = m.input("rd_en").comment("Read enable")
    din = m.input("din", width=data_width).comment("Write data")
    dout = m.output_reg("dout", width=data_width).comment("Read data (registered)")
    full = m.output("full")
    empty = m.output("empty")
    count = m.output("count", width=ptr_width).comment("FIFO occupancy")

    mem = m.reg("mem", width=data_width, depth=depth)
    if style:
        mem.attr("ram_style", style)
    wr_ptr = m.reg("wr_ptr", width=ptr_width)
    rd_ptr = m.reg("rd_ptr", width=ptr_width)

    # Full: MSBs differ, lower address bits equal (one full wrap-around ahead)
    m.comment("Status flags")
    m.assign(
        full,
        (wr_ptr[addr_width] != rd_ptr[addr_width]) & (wr_ptr[addr_width - 1 : 0] == rd_ptr[addr_width - 1 : 0]),
    )
    m.assign(empty, wr_ptr == rd_ptr)
    m.assign(count, wr_ptr - rd_ptr)

    m.comment("Read / write logic")
    with m.always(posedge(clk)):
        with m.if_(rst):
            wr_ptr <<= 0
            rd_ptr <<= 0
        with m.else_():
            with m.if_(wr_en & ~full):
                mem[wr_ptr[addr_width - 1 : 0]] <<= din
                wr_ptr <<= wr_ptr + 1
            with m.if_(rd_en & ~empty):
                dout <<= mem[rd_ptr[addr_width - 1 : 0]]
                rd_ptr <<= rd_ptr + 1

    return m
