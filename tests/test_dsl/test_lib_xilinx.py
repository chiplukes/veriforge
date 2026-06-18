"""Tests for Xilinx inference library components — SRL shift register, LUTRAM."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl.lib import lutram, shift_register_srl


# ===================================================================
# Shift Register (SRL inference)
# ===================================================================


class TestShiftRegisterSRL:
    """Test shift_register_srl() factory function."""

    def test_default_ports(self):
        v = emit_module(shift_register_srl().build())
        assert "module srl_shift_reg" in v
        assert "input clk" in v
        assert "input ce" in v
        assert "input din" in v
        assert "output dout" in v

    def test_shift_chain(self):
        """Shift chain: shreg[0] <= din, shreg[i] <= shreg[i-1]."""
        v = emit_module(shift_register_srl(depth=4).build())
        assert "shreg[0] <= din" in v
        assert "shreg[1] <= shreg[0]" in v
        assert "shreg[2] <= shreg[1]" in v
        assert "shreg[3] <= shreg[2]" in v

    def test_output_from_last(self):
        """Output is assigned from last register."""
        v = emit_module(shift_register_srl(depth=4).build())
        assert "assign dout = shreg[3]" in v

    def test_clock_enable(self):
        """Shift only on clock enable."""
        v = emit_module(shift_register_srl().build())
        assert "if (ce)" in v

    def test_depth_16(self):
        v = emit_module(shift_register_srl(depth=16).build())
        assert "reg shreg [0:15]" in v
        assert "assign dout = shreg[15]" in v

    def test_depth_32(self):
        v = emit_module(shift_register_srl(depth=32).build())
        assert "reg shreg [0:31]" in v
        assert "assign dout = shreg[31]" in v

    def test_wide_data(self):
        v = emit_module(shift_register_srl(width=8, depth=4).build())
        assert "[7:0] din" in v
        assert "[7:0] dout" in v
        assert "reg [7:0] shreg [0:3]" in v

    def test_shreg_extract_attribute_yes(self):
        v = emit_module(shift_register_srl(style="yes").build())
        assert '(* shreg_extract = "yes" *)' in v

    def test_shreg_extract_attribute_no(self):
        v = emit_module(shift_register_srl(style="no").build())
        assert '(* shreg_extract = "no" *)' in v

    def test_no_attribute_by_default(self):
        v = emit_module(shift_register_srl().build())
        assert "shreg_extract" not in v

    def test_custom_name(self):
        v = emit_module(shift_register_srl(name="my_srl").build())
        assert "module my_srl" in v

    def test_depth_validation(self):
        with pytest.raises(ValueError, match="depth"):
            shift_register_srl(depth=1)

    def test_width_validation(self):
        with pytest.raises(ValueError, match="width"):
            shift_register_srl(width=0)

    def test_always_posedge_clk(self):
        v = emit_module(shift_register_srl().build())
        assert "always @(posedge clk)" in v

    def test_endmodule(self):
        """Module terminates correctly."""
        v = emit_module(shift_register_srl(depth=4).build())
        assert v.strip().endswith("endmodule")


# ===================================================================
# LUTRAM (Distributed RAM)
# ===================================================================


class TestLUTRAM:
    """Test lutram() factory function."""

    def test_default_ports(self):
        v = emit_module(lutram().build())
        assert "module lutram" in v
        assert "input clk" in v
        assert "input we" in v
        assert "input [4:0] waddr" in v  # log2(32) = 5
        assert "input [4:0] raddr" in v
        assert "input [7:0] din" in v
        assert "output [7:0] dout" in v  # wire, not reg (async read)

    def test_async_read(self):
        """Async read: continuous assignment from memory."""
        v = emit_module(lutram().build())
        assert "assign dout = mem[raddr]" in v

    def test_sync_write(self):
        """Sync write: guarded by clock edge and write enable."""
        v = emit_module(lutram().build())
        assert "always @(posedge clk)" in v
        assert "mem[waddr] <= din" in v
        assert "if (we)" in v

    def test_ram_style_attribute(self):
        v = emit_module(lutram().build())
        assert '(* ram_style = "distributed" *)' in v

    def test_custom_widths(self):
        v = emit_module(lutram(data_width=16, depth=64).build())
        assert "[15:0] din" in v
        assert "[15:0] dout" in v
        assert "[5:0] waddr" in v  # log2(64) = 6
        assert "[5:0] raddr" in v

    def test_small_depth(self):
        v = emit_module(lutram(depth=2).build())
        # 1-bit addresses: emitted without range for width=1
        assert "input waddr" in v
        assert "input raddr" in v

    def test_power_of_two_depth(self):
        v = emit_module(lutram(depth=16).build())
        assert "[3:0] waddr" in v
        assert "[3:0] raddr" in v

    def test_non_power_of_two_depth(self):
        """Non-power-of-two depth rounds up address width."""
        v = emit_module(lutram(depth=5).build())
        assert "[2:0] waddr" in v  # ceil(log2(5)) = 3

    def test_custom_name(self):
        v = emit_module(lutram(name="my_ram").build())
        assert "module my_ram" in v

    def test_depth_validation(self):
        with pytest.raises(ValueError, match="depth"):
            lutram(depth=1)

    def test_width_validation(self):
        with pytest.raises(ValueError, match="data_width"):
            lutram(data_width=0)

    def test_memory_declaration(self):
        v = emit_module(lutram(data_width=8, depth=32).build())
        assert "reg [7:0] mem [0:31]" in v

    def test_endmodule(self):
        """Module terminates correctly."""
        v = emit_module(lutram().build())
        assert v.strip().endswith("endmodule")
