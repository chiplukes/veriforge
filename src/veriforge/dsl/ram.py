"""RAM inference patterns — generate vendor-inferable RAM structures.

Usage::

    from veriforge.dsl import Module, posedge
    from veriforge.dsl.ram import single_port_ram, simple_dual_port_ram, true_dual_port_ram, rom

    # Single-port RAM
    m = single_port_ram(data_width=8, depth=256)

    # Simple dual-port RAM (one write port, one read port)
    m = simple_dual_port_ram(data_width=16, depth=1024)

    # True dual-port RAM (two read/write ports)
    m = true_dual_port_ram(data_width=32, depth=512)

    # ROM with initial values
    m = rom(data_width=8, depth=16, init_file="data.hex")

These produce Verilog that FPGA synthesis tools (Xilinx Vivado, Intel Quartus,
Lattice, etc.) recognize and map to block RAM primitives.

Each function returns a ``Module`` builder. Call ``.build()`` to get the model,
pass it to ``emit_module()`` for Verilog output, or use it for simulation.
"""

from __future__ import annotations

from . import Module, posedge


def single_port_ram(
    data_width: int = 8,
    depth: int = 256,
    *,
    name: str = "single_port_ram",
    sync_read: bool = True,
    style: str | None = None,
) -> Module:
    """Build a single-port RAM with one read/write port.

    Generates the standard inference pattern::

        always @(posedge clk) begin
            if (we)
                mem[addr] <= din;
            if (sync_read)
                dout <= mem[addr];   // synchronous read
            else
                ...                  // async read via assign
        end

    Args:
        data_width: Width of each memory word in bits.
        depth: Number of memory words.
        name: Module name.
        sync_read: If True, read output is registered (block RAM).
                   If False, read is combinational (distributed/LUTRAM).
        style: Optional RAM style attribute (``"block"``, ``"distributed"``,
               ``"ultra"``). Maps to ``(* ram_style = "..." *)``.

    Returns:
        Module builder (call ``.build()`` to finalize).
    """
    addr_width = (depth - 1).bit_length() or 1

    m = Module(name)
    clk = m.input("clk")
    we = m.input("we").comment("Write enable")
    addr = m.input("addr", width=addr_width).comment("Address")
    din = m.input("din", width=data_width).comment("Write data")
    if sync_read:
        dout = m.output_reg("dout", width=data_width).comment("Read data")
    else:
        dout = m.output("dout", width=data_width).comment("Read data (async)")

    mem = m.reg("mem", width=data_width, depth=depth)
    if style:
        mem.attr("ram_style", style)

    with m.always(posedge(clk)):
        with m.if_(we):
            mem[addr] <<= din
        if sync_read:
            dout <<= mem[addr]

    if not sync_read:
        m.assign(dout, mem[addr])

    return m


def simple_dual_port_ram(
    data_width: int = 8,
    depth: int = 256,
    *,
    name: str = "simple_dual_port_ram",
    sync_read: bool = True,
    style: str | None = None,
) -> Module:
    """Build a simple dual-port RAM (one write port, one read port).

    Port A is write-only, Port B is read-only. They share the same clock.
    This is the most common RAM pattern for FIFOs and buffers::

        always @(posedge clk) begin
            if (we)
                mem[waddr] <= din;
            dout <= mem[raddr];   // synchronous read
        end

    Args:
        data_width: Width of each memory word in bits.
        depth: Number of memory words.
        name: Module name.
        sync_read: If True, read output is registered.
        style: Optional RAM style attribute.

    Returns:
        Module builder.
    """
    addr_width = (depth - 1).bit_length() or 1

    m = Module(name)
    clk = m.input("clk")
    we = m.input("we").comment("Write enable")
    waddr = m.input("waddr", width=addr_width).comment("Write address")
    raddr = m.input("raddr", width=addr_width).comment("Read address")
    din = m.input("din", width=data_width).comment("Write data")
    if sync_read:
        dout = m.output_reg("dout", width=data_width).comment("Read data")
    else:
        dout = m.output("dout", width=data_width).comment("Read data (async)")

    mem = m.reg("mem", width=data_width, depth=depth)
    if style:
        mem.attr("ram_style", style)

    with m.always(posedge(clk)):
        with m.if_(we):
            mem[waddr] <<= din
        if sync_read:
            dout <<= mem[raddr]

    if not sync_read:
        m.assign(dout, mem[raddr])

    return m


def true_dual_port_ram(
    data_width: int = 8,
    depth: int = 256,
    *,
    name: str = "true_dual_port_ram",
    style: str | None = None,
) -> Module:
    """Build a true dual-port RAM (two independent read/write ports).

    Both Port A and Port B can read and write independently::

        always @(posedge clk) begin
            if (we_a) mem[addr_a] <= din_a;
            dout_a <= mem[addr_a];
        end
        always @(posedge clk) begin
            if (we_b) mem[addr_b] <= din_b;
            dout_b <= mem[addr_b];
        end

    Args:
        data_width: Width of each memory word in bits.
        depth: Number of memory words.
        name: Module name.
        style: Optional RAM style attribute.

    Returns:
        Module builder.
    """
    addr_width = (depth - 1).bit_length() or 1

    m = Module(name)
    clk = m.input("clk")

    m.comment("Port A")
    we_a = m.input("we_a")
    addr_a = m.input("addr_a", width=addr_width)
    din_a = m.input("din_a", width=data_width)
    dout_a = m.output_reg("dout_a", width=data_width)

    m.comment("Port B")
    we_b = m.input("we_b")
    addr_b = m.input("addr_b", width=addr_width)
    din_b = m.input("din_b", width=data_width)
    dout_b = m.output_reg("dout_b", width=data_width)

    mem = m.reg("mem", width=data_width, depth=depth)
    if style:
        mem.attr("ram_style", style)

    m.comment("Port A logic")
    with m.always(posedge(clk)):
        with m.if_(we_a):
            mem[addr_a] <<= din_a
        dout_a <<= mem[addr_a]

    m.comment("Port B logic")
    with m.always(posedge(clk)):
        with m.if_(we_b):
            mem[addr_b] <<= din_b
        dout_b <<= mem[addr_b]

    return m


def rom(
    data_width: int = 8,
    depth: int = 256,
    *,
    name: str = "rom",
    init_file: str | None = None,
    sync_read: bool = True,
) -> Module:
    """Build a ROM (read-only memory) with optional file initialization.

    If ``init_file`` is given, emits ``$readmemh(file, mem)`` in an initial
    block. The ROM is inferred from a registered read with no write port::

        initial $readmemh("data.hex", mem);
        always @(posedge clk)
            dout <= mem[addr];

    Args:
        data_width: Width of each memory word in bits.
        depth: Number of memory words.
        name: Module name.
        init_file: Path to hex file for ``$readmemh`` initialization.
        sync_read: If True, read is registered (block RAM ROM).

    Returns:
        Module builder.
    """
    addr_width = (depth - 1).bit_length() or 1

    m = Module(name)
    clk = m.input("clk")
    addr = m.input("addr", width=addr_width).comment("Read address")
    if sync_read:
        dout = m.output_reg("dout", width=data_width).comment("Read data")
    else:
        dout = m.output("dout", width=data_width).comment("Read data (async)")

    mem = m.reg("mem", width=data_width, depth=depth).comment("ROM storage")

    if init_file:
        with m.initial():
            m.readmemh(init_file, mem)

    if sync_read:
        with m.always(posedge(clk)):
            dout <<= mem[addr]
    else:
        m.assign(dout, mem[addr])

    return m
