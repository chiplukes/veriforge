"""Tests for RAM inference pattern library."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl.ram import (
    rom,
    simple_dual_port_ram,
    single_port_ram,
    true_dual_port_ram,
)


class TestSinglePortRAM:
    """Test single-port RAM pattern."""

    def test_basic_structure(self):
        """Default single-port RAM has correct ports."""
        m = single_port_ram(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "module single_port_ram" in v
        assert "input clk" in v
        assert "input we" in v
        assert "input [7:0] addr" in v
        assert "input [7:0] din" in v
        assert "output reg [7:0] dout" in v

    def test_memory_declaration(self):
        """Memory array is declared."""
        m = single_port_ram(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "reg [7:0] mem [0:255]" in v

    def test_sync_read(self):
        """Synchronous read produces registered output."""
        m = single_port_ram(data_width=8, depth=256, sync_read=True)
        v = emit_module(m.build())
        assert "always @(posedge clk)" in v
        assert "dout <= mem[addr]" in v
        assert "assign" not in v.split("endmodule")[0].split("always")[1]

    def test_async_read(self):
        """Async read uses continuous assignment."""
        m = single_port_ram(data_width=8, depth=256, sync_read=False)
        v = emit_module(m.build())
        assert "assign dout = mem[addr]" in v

    def test_write_enable(self):
        """Write is conditional on we."""
        m = single_port_ram(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "if (we)" in v
        assert "mem[addr] <= din" in v

    def test_custom_name(self):
        """Custom module name."""
        m = single_port_ram(data_width=8, depth=256, name="my_ram")
        v = emit_module(m.build())
        assert "module my_ram" in v

    def test_address_width_calculation(self):
        """Address width is log2(depth)."""
        # depth=256 → 8-bit address
        m = single_port_ram(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "[7:0] addr" in v

        # depth=16 → 4-bit address
        m = single_port_ram(data_width=8, depth=16)
        v = emit_module(m.build())
        assert "[3:0] addr" in v

    def test_small_depth(self):
        """Depth=2 produces 1-bit address."""
        m = single_port_ram(data_width=8, depth=2)
        v = emit_module(m.build())
        # 1-bit address is scalar, no range
        assert "input addr" in v or "input [0:0] addr" in v

    def test_style_attribute(self):
        """RAM style attribute is emitted."""
        m = single_port_ram(data_width=8, depth=256, style="block")
        v = emit_module(m.build())
        assert '(* ram_style = "block" *)' in v

    def test_distributed_style(self):
        """Distributed RAM style."""
        m = single_port_ram(data_width=8, depth=32, style="distributed", sync_read=False)
        v = emit_module(m.build())
        assert '(* ram_style = "distributed" *)' in v
        assert "assign dout" in v

    def test_wide_data(self):
        """32-bit data width."""
        m = single_port_ram(data_width=32, depth=1024)
        v = emit_module(m.build())
        assert "[31:0] din" in v
        assert "[31:0] dout" in v
        assert "[9:0] addr" in v
        assert "reg [31:0] mem [0:1023]" in v


class TestSimpleDualPortRAM:
    """Test simple dual-port RAM (separate read/write ports)."""

    def test_basic_structure(self):
        """Has separate read and write address ports."""
        m = simple_dual_port_ram(data_width=16, depth=512)
        v = emit_module(m.build())
        assert "module simple_dual_port_ram" in v
        assert "input [8:0] waddr" in v
        assert "input [8:0] raddr" in v
        assert "input we" in v
        assert "input [15:0] din" in v
        assert "output reg [15:0] dout" in v

    def test_write_logic(self):
        """Write uses waddr."""
        m = simple_dual_port_ram(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "mem[waddr] <= din" in v

    def test_sync_read(self):
        """Sync read uses raddr."""
        m = simple_dual_port_ram(data_width=8, depth=256, sync_read=True)
        v = emit_module(m.build())
        assert "dout <= mem[raddr]" in v

    def test_async_read(self):
        """Async read assigns from raddr."""
        m = simple_dual_port_ram(data_width=8, depth=256, sync_read=False)
        v = emit_module(m.build())
        assert "assign dout = mem[raddr]" in v

    def test_style(self):
        """Style attribute on memory."""
        m = simple_dual_port_ram(data_width=8, depth=256, style="ultra")
        v = emit_module(m.build())
        assert '(* ram_style = "ultra" *)' in v


class TestTrueDualPortRAM:
    """Test true dual-port RAM (two independent R/W ports)."""

    def test_basic_structure(self):
        """Has ports for both Port A and Port B."""
        m = true_dual_port_ram(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "module true_dual_port_ram" in v
        # Port A
        assert "input we_a" in v
        assert "input [7:0] addr_a" in v
        assert "input [7:0] din_a" in v
        assert "output reg [7:0] dout_a" in v
        # Port B
        assert "input we_b" in v
        assert "input [7:0] addr_b" in v
        assert "input [7:0] din_b" in v
        assert "output reg [7:0] dout_b" in v

    def test_two_always_blocks(self):
        """Two separate always blocks for independent ports."""
        m = true_dual_port_ram(data_width=8, depth=256)
        v = emit_module(m.build())
        assert v.count("always @(posedge clk)") == 2

    def test_port_a_logic(self):
        """Port A has write and read logic."""
        m = true_dual_port_ram(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "mem[addr_a] <= din_a" in v
        assert "dout_a <= mem[addr_a]" in v

    def test_port_b_logic(self):
        """Port B has write and read logic."""
        m = true_dual_port_ram(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "mem[addr_b] <= din_b" in v
        assert "dout_b <= mem[addr_b]" in v

    def test_style(self):
        """Style attribute on shared memory."""
        m = true_dual_port_ram(data_width=8, depth=256, style="block")
        v = emit_module(m.build())
        assert '(* ram_style = "block" *)' in v

    def test_port_comments(self):
        """Port groups have comments."""
        m = true_dual_port_ram(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "Port A" in v
        assert "Port B" in v


class TestROM:
    """Test ROM pattern."""

    def test_basic_structure(self):
        """ROM has clock, addr, and dout."""
        m = rom(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "module rom" in v
        assert "input clk" in v
        assert "input [7:0] addr" in v
        assert "output reg [7:0] dout" in v

    def test_no_write_port(self):
        """ROM has no write enable or data input."""
        m = rom(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "we" not in v
        assert "din" not in v

    def test_sync_read(self):
        """Sync ROM read."""
        m = rom(data_width=8, depth=256, sync_read=True)
        v = emit_module(m.build())
        assert "always @(posedge clk)" in v
        assert "dout <= mem[addr]" in v

    def test_async_read(self):
        """Async ROM read."""
        m = rom(data_width=8, depth=256, sync_read=False)
        v = emit_module(m.build())
        assert "assign dout = mem[addr]" in v

    def test_init_file(self):
        """ROM with $readmemh initialization."""
        m = rom(data_width=8, depth=256, init_file="rom_data.hex")
        v = emit_module(m.build())
        assert '$readmemh("rom_data.hex", mem)' in v
        assert "initial" in v

    def test_no_init_file(self):
        """ROM without init file has no initial block."""
        m = rom(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "initial" not in v
        assert "$readmemh" not in v

    def test_custom_name(self):
        """Custom ROM module name."""
        m = rom(data_width=8, depth=64, name="lookup_table")
        v = emit_module(m.build())
        assert "module lookup_table" in v

    def test_memory_comment(self):
        """ROM storage has a comment."""
        m = rom(data_width=8, depth=256)
        v = emit_module(m.build())
        assert "ROM storage" in v


class TestPowerOfTwoDepths:
    """Test edge cases for address width calculation."""

    def test_depth_1(self):
        """Depth=1 produces 1-bit address (degenerate but valid)."""
        m = single_port_ram(data_width=8, depth=1)
        v = emit_module(m.build())
        assert "mem [0:0]" in v

    def test_depth_power_of_2(self):
        """Powers of 2 produce exact address widths."""
        m = single_port_ram(data_width=8, depth=1024)
        v = emit_module(m.build())
        assert "[9:0] addr" in v

    def test_non_power_of_2(self):
        """Non-power-of-2 depth uses next-higher address width."""
        m = single_port_ram(data_width=8, depth=300)
        v = emit_module(m.build())
        assert "[8:0] addr" in v  # 9 bits needed for 300
        assert "mem [0:299]" in v
