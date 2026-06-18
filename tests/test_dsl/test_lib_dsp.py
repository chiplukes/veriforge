"""Tests for DSP inference library components — MAC, pipelined multiplier, FIR."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl.lib import fir_filter, mac, pipelined_mult
from veriforge.sim import Simulator


# ===================================================================
# MAC (Multiply-Accumulate)
# ===================================================================


class TestMAC:
    """Test mac() factory function."""

    def test_default_ports(self):
        v = emit_module(mac().build())
        assert "module mac" in v
        assert "input clk" in v
        assert "input rst" in v
        assert "input en" in v
        assert "input clr" in v
        assert "input [17:0] a" in v
        assert "input [17:0] b" in v
        assert "output reg [36:0] p" in v

    def test_default_acc_width(self):
        """Default accumulator width = a + b + 1."""
        v = emit_module(mac(a_width=18, b_width=18).build())
        assert "[36:0] p" in v  # 18 + 18 + 1 = 37 bits

    def test_custom_acc_width(self):
        v = emit_module(mac(a_width=18, b_width=18, acc_width=48).build())
        assert "[47:0] p" in v

    def test_use_dsp_attribute(self):
        v = emit_module(mac().build())
        assert '(* use_dsp = "yes" *)' in v

    def test_no_dsp_attribute(self):
        v = emit_module(mac(use_dsp=None).build())
        assert "use_dsp" not in v

    def test_dsp_no(self):
        v = emit_module(mac(use_dsp="no").build())
        assert '(* use_dsp = "no" *)' in v

    def test_mac_logic(self):
        """Accumulate pattern: p <= p + a * b."""
        v = emit_module(mac().build())
        assert "p <= p + a * b" in v
        assert "always @(posedge clk)" in v

    def test_reset_and_clear(self):
        v = emit_module(mac().build())
        assert v.count("p <= 0") == 2  # rst and clr

    def test_custom_name(self):
        v = emit_module(mac(name="my_mac").build())
        assert "module my_mac" in v

    def test_custom_widths(self):
        v = emit_module(mac(a_width=25, b_width=18).build())
        assert "[24:0] a" in v
        assert "[17:0] b" in v
        assert "[43:0] p" in v  # 25 + 18 + 1 = 44

    def test_width_validation_a(self):
        with pytest.raises(ValueError, match="a_width"):
            mac(a_width=0)

    def test_width_validation_b(self):
        with pytest.raises(ValueError, match="b_width"):
            mac(b_width=0)

    def test_simulate_reset_clears(self):
        """After reset, accumulator is 0."""
        from veriforge.sim import Clock

        m = mac(a_width=8, b_width=8, acc_width=24, use_dsp=None)
        sim = Simulator(m.build())
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("rst", 1)
        sim.drive("clr", 0)
        sim.drive("en", 0)
        sim.drive("a", 0)
        sim.drive("b", 0)
        sim.run(max_time=15)
        assert sim.read("p") == 0


# ===================================================================
# Pipelined multiplier
# ===================================================================


class TestPipelinedMult:
    """Test pipelined_mult() factory function."""

    def test_default_ports(self):
        v = emit_module(pipelined_mult().build())
        assert "module pipelined_mult" in v
        assert "input clk" in v
        assert "input [17:0] a" in v
        assert "input [17:0] b" in v
        assert "output reg [35:0] p" in v

    def test_product_width(self):
        """Product width = a + b."""
        v = emit_module(pipelined_mult(a_width=25, b_width=18).build())
        assert "[42:0] p" in v  # 25 + 18 = 43

    def test_input_registers(self):
        v = emit_module(pipelined_mult().build())
        assert "reg [17:0] a_r" in v
        assert "reg [17:0] b_r" in v
        assert "a_r <= a" in v
        assert "b_r <= b" in v

    def test_3_stage_pipeline(self):
        """3 stages: input reg, multiply, output reg."""
        v = emit_module(pipelined_mult(stages=3).build())
        assert "p_stage0" in v
        assert "p_stage0 <= a_r * b_r" in v
        assert "p <= p_stage0" in v

    def test_2_stage_pipeline(self):
        """2 stages: input reg, direct output."""
        v = emit_module(pipelined_mult(stages=2).build())
        assert "p_stage" not in v
        assert "p <= a_r * b_r" in v

    def test_4_stage_pipeline(self):
        """4 stages: input reg, multiply, 2 output regs."""
        v = emit_module(pipelined_mult(stages=4).build())
        assert "p_stage0" in v
        assert "p_stage1" in v
        assert "p <= p_stage1" in v

    def test_use_dsp_attribute(self):
        v = emit_module(pipelined_mult().build())
        assert '(* use_dsp = "yes" *)' in v

    def test_custom_name(self):
        v = emit_module(pipelined_mult(name="mult_pipe").build())
        assert "module mult_pipe" in v

    def test_stages_validation(self):
        with pytest.raises(ValueError, match="stages"):
            pipelined_mult(stages=1)

    def test_width_validation(self):
        with pytest.raises(ValueError, match="a_width"):
            pipelined_mult(a_width=0)


# ===================================================================
# FIR filter
# ===================================================================


class TestFIRFilter:
    """Test fir_filter() factory function."""

    def test_default_ports(self):
        v = emit_module(fir_filter().build())
        assert "module fir_filter" in v
        assert "input clk" in v
        assert "input rst" in v
        assert "input [15:0] din" in v
        assert "output reg" in v
        assert "input [15:0] coeff_in" in v
        assert "input coeff_we" in v

    def test_4_tap_structure(self):
        """4-tap FIR has 4 coefficient regs and 4 accumulators."""
        v = emit_module(fir_filter(num_taps=4).build())
        assert "reg [15:0] coeff0" in v
        assert "reg [15:0] coeff3" in v
        assert "acc0" in v
        assert "acc3" in v

    def test_transposed_form(self):
        """Transposed FIR: last tap is just multiply, others add previous acc."""
        v = emit_module(fir_filter(num_taps=4).build())
        assert "acc3 <= din * coeff3" in v  # last tap: just multiply
        assert "acc0 <= din * coeff0 + acc1" in v  # chain: add next acc

    def test_output_assignment(self):
        """Output is the first accumulator."""
        v = emit_module(fir_filter(num_taps=4).build())
        assert "dout <= acc0" in v

    def test_coeff_write(self):
        """Coefficients are written via case statement."""
        v = emit_module(fir_filter(num_taps=4).build())
        assert "case (coeff_addr)" in v
        assert "coeff0 <= coeff_in" in v
        assert "coeff3 <= coeff_in" in v

    def test_output_width(self):
        """Output width = product_width + ceil(log2(num_taps))."""
        v = emit_module(fir_filter(data_width=16, coeff_width=16, num_taps=4).build())
        # product = 32, log2(4) = 2, output = 34
        assert "[33:0] dout" in v

    def test_use_dsp_attribute(self):
        v = emit_module(fir_filter().build())
        assert '(* use_dsp = "yes" *)' in v

    def test_custom_name(self):
        v = emit_module(fir_filter(name="my_fir").build())
        assert "module my_fir" in v

    def test_2_tap_minimum(self):
        """Minimum 2 taps."""
        v = emit_module(fir_filter(num_taps=2).build())
        assert "coeff0" in v
        assert "coeff1" in v
        assert "acc0" in v
        assert "acc1" in v

    def test_8_taps(self):
        """8 taps produce 8 coefficients and accumulators."""
        v = emit_module(fir_filter(num_taps=8).build())
        assert "coeff7" in v
        assert "acc7" in v
        assert "[2:0] coeff_addr" in v  # 3-bit for 8 taps

    def test_validation_taps(self):
        with pytest.raises(ValueError, match="num_taps"):
            fir_filter(num_taps=1)

    def test_validation_data_width(self):
        with pytest.raises(ValueError, match="data_width"):
            fir_filter(data_width=0)

    def test_validation_coeff_width(self):
        with pytest.raises(ValueError, match="coeff_width"):
            fir_filter(coeff_width=0)

    def test_reset_clears_accumulators(self):
        """Reset zeros all accumulators and output."""
        v = emit_module(fir_filter(num_taps=4).build())
        assert v.count("acc0 <= 0") >= 1
        assert v.count("acc3 <= 0") >= 1
        assert "dout <= 0" in v
