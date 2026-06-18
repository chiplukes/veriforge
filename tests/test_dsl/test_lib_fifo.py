"""Tests for synchronous FIFO library component."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl.lib import sync_fifo


class TestSyncFifo:
    """Test sync_fifo factory function — emission and structure."""

    def test_default_ports(self):
        """Default FIFO has all expected ports."""
        v = emit_module(sync_fifo().build())
        assert "module sync_fifo" in v
        assert "input clk" in v
        assert "input rst" in v
        assert "input wr_en" in v
        assert "input rd_en" in v
        assert "input [7:0] din" in v
        assert "output reg [7:0] dout" in v
        assert "output full" in v
        assert "output empty" in v
        assert "output [4:0] count" in v

    def test_memory_declaration(self):
        """Memory array has correct dimensions."""
        v = emit_module(sync_fifo(data_width=8, depth=16).build())
        assert "reg [7:0] mem [0:15]" in v

    def test_pointer_width(self):
        """Pointers have extra MSB for wrap-around detection."""
        v = emit_module(sync_fifo(data_width=8, depth=16).build())
        assert "reg [4:0] wr_ptr" in v
        assert "reg [4:0] rd_ptr" in v

    def test_full_empty_assigns(self):
        """Full and empty use continuous assigns from pointer comparison."""
        v = emit_module(sync_fifo(data_width=8, depth=16).build())
        assert "assign full" in v
        assert "assign empty = wr_ptr == rd_ptr" in v
        assert "assign count = wr_ptr - rd_ptr" in v

    def test_write_logic(self):
        """Write stores data and advances pointer when enabled and not full."""
        v = emit_module(sync_fifo(data_width=8, depth=16).build())
        assert "wr_en & ~full" in v
        assert "mem[wr_ptr[3:0]] <= din" in v
        assert "wr_ptr <= wr_ptr + 1" in v

    def test_read_logic(self):
        """Read outputs data and advances pointer when enabled and not empty."""
        v = emit_module(sync_fifo(data_width=8, depth=16).build())
        assert "rd_en & ~empty" in v
        assert "dout <= mem[rd_ptr[3:0]]" in v
        assert "rd_ptr <= rd_ptr + 1" in v

    def test_reset_clears_pointers(self):
        """Synchronous reset zeroes both pointers."""
        v = emit_module(sync_fifo().build())
        assert "wr_ptr <= 0" in v
        assert "rd_ptr <= 0" in v

    def test_posedge_clock(self):
        """Always block is edge-triggered."""
        v = emit_module(sync_fifo().build())
        assert "always @(posedge clk)" in v

    def test_custom_width_and_depth(self):
        """Custom data width and depth produce correct declarations."""
        v = emit_module(sync_fifo(data_width=32, depth=64).build())
        assert "[31:0] din" in v
        assert "[31:0] dout" in v
        assert "reg [31:0] mem [0:63]" in v
        assert "reg [6:0] wr_ptr" in v  # depth=64 → addr=6 → ptr=7

    def test_custom_name(self):
        """Custom module name."""
        v = emit_module(sync_fifo(name="my_fifo").build())
        assert "module my_fifo" in v

    def test_ram_style_block(self):
        """Block RAM style attribute."""
        v = emit_module(sync_fifo(style="block").build())
        assert '(* ram_style = "block" *)' in v

    def test_ram_style_distributed(self):
        """Distributed RAM style attribute."""
        v = emit_module(sync_fifo(depth=4, style="distributed").build())
        assert '(* ram_style = "distributed" *)' in v

    def test_no_style_by_default(self):
        """No ram_style attribute when style is None."""
        v = emit_module(sync_fifo().build())
        assert "ram_style" not in v


class TestSyncFifoDepths:
    """Test various FIFO depth configurations."""

    @pytest.mark.parametrize("depth", [2, 4, 8, 16, 32, 64, 128, 256])
    def test_power_of_2_depths(self, depth):
        """All power-of-2 depths produce valid modules."""
        v = emit_module(sync_fifo(depth=depth).build())
        assert f"mem [0:{depth - 1}]" in v

    def test_depth_2_minimum(self):
        """Minimum depth=2: 1-bit address, 2-bit pointer."""
        v = emit_module(sync_fifo(depth=2).build())
        assert "reg [1:0] wr_ptr" in v
        assert "reg [1:0] rd_ptr" in v
        assert "mem [0:1]" in v

    def test_depth_256(self):
        """depth=256: 8-bit address, 9-bit pointer."""
        v = emit_module(sync_fifo(depth=256).build())
        assert "reg [8:0] wr_ptr" in v
        assert "[8:0] count" in v

    def test_count_width_matches_ptr(self):
        """Count output width equals pointer width (addr+1)."""
        for depth in [4, 16, 64]:
            addr_width = (depth - 1).bit_length()
            ptr_width = addr_width + 1
            v = emit_module(sync_fifo(depth=depth).build())
            assert f"[{ptr_width - 1}:0] count" in v


class TestSyncFifoValidation:
    """Test parameter validation and error messages."""

    def test_depth_not_power_of_2(self):
        with pytest.raises(ValueError, match="power of 2"):
            sync_fifo(depth=3)

    def test_depth_too_small(self):
        with pytest.raises(ValueError, match="power of 2"):
            sync_fifo(depth=1)

    def test_depth_zero(self):
        with pytest.raises(ValueError, match="power of 2"):
            sync_fifo(depth=0)

    def test_depth_negative(self):
        with pytest.raises(ValueError):
            sync_fifo(depth=-1)

    def test_odd_depth(self):
        with pytest.raises(ValueError, match="power of 2"):
            sync_fifo(depth=7)

    def test_large_non_power(self):
        with pytest.raises(ValueError, match="power of 2"):
            sync_fifo(depth=100)
