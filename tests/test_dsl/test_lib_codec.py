"""Tests for encoder and decoder library components."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl.lib import binary_decoder, priority_encoder
from veriforge.sim import Simulator


# ===================================================================
# Priority encoder
# ===================================================================


class TestPriorityEncoder:
    """Test priority_encoder factory function."""

    def test_default_ports(self):
        """Default 8-bit encoder has din, out, valid."""
        v = emit_module(priority_encoder().build())
        assert "module priority_encoder" in v
        assert "input [7:0] din" in v
        assert "output reg [2:0] out" in v
        assert "output reg valid" in v

    def test_4bit_output_width(self):
        """width=4 → 2-bit output."""
        v = emit_module(priority_encoder(width=4).build())
        assert "[3:0] din" in v
        assert "reg [1:0] out" in v

    def test_combinational_logic(self):
        """Uses always @(*) with blocking assigns."""
        v = emit_module(priority_encoder(width=4).build())
        assert "always @(*)" in v
        assert "out = 0" in v
        assert "valid = 0" in v

    def test_if_chain(self):
        """Generates individual if-blocks for each input bit."""
        v = emit_module(priority_encoder(width=4).build())
        assert "if (din[0])" in v
        assert "if (din[3])" in v

    def test_custom_name(self):
        v = emit_module(priority_encoder(name="pri_enc").build())
        assert "module pri_enc" in v

    def test_width_validation(self):
        with pytest.raises(ValueError, match="width"):
            priority_encoder(width=1)

    def test_width_zero(self):
        with pytest.raises(ValueError, match="width"):
            priority_encoder(width=0)

    def test_simulate_single_bit(self):
        """Single bit set → output is that bit's index."""
        m = priority_encoder(width=4)
        for bit in range(4):
            sim = Simulator(m.build())
            sim.drive("din", 1 << bit)
            sim.run(max_time=10)
            assert sim.read("out") == bit
            assert sim.read("valid") == 1

    def test_simulate_msb_priority(self):
        """When multiple bits set, highest index wins."""
        m = priority_encoder(width=8)
        sim = Simulator(m.build())
        sim.drive("din", 0b01010101)  # bits 0, 2, 4, 6
        sim.run(max_time=10)
        assert sim.read("out") == 6  # MSB priority
        assert sim.read("valid") == 1

    def test_simulate_all_set(self):
        """All bits set → output is width-1."""
        m = priority_encoder(width=8)
        sim = Simulator(m.build())
        sim.drive("din", 0xFF)
        sim.run(max_time=10)
        assert sim.read("out") == 7
        assert sim.read("valid") == 1

    def test_simulate_no_bits_set(self):
        """No bits set → valid=0, out=0."""
        m = priority_encoder(width=4)
        sim = Simulator(m.build())
        sim.drive("din", 0)
        sim.run(max_time=10)
        assert sim.read("out") == 0
        assert sim.read("valid") == 0

    def test_simulate_lsb_only(self):
        """Only bit 0 set → out=0, valid=1."""
        m = priority_encoder(width=8)
        sim = Simulator(m.build())
        sim.drive("din", 1)
        sim.run(max_time=10)
        assert sim.read("out") == 0
        assert sim.read("valid") == 1


# ===================================================================
# Binary decoder
# ===================================================================


class TestBinaryDecoder:
    """Test binary_decoder factory function."""

    def test_default_ports(self):
        """Default 3-bit decoder has din, en, out."""
        v = emit_module(binary_decoder().build())
        assert "module binary_decoder" in v
        assert "input [2:0] din" in v
        assert "input en" in v
        assert "output reg [7:0] out" in v  # 2^3 = 8

    def test_2bit_decoder(self):
        """width=2 → 4-bit output."""
        v = emit_module(binary_decoder(width=2).build())
        assert "[1:0] din" in v
        assert "reg [3:0] out" in v

    def test_combinational_case(self):
        """Uses always @(*) with case statement."""
        v = emit_module(binary_decoder(width=2).build())
        assert "always @(*)" in v
        assert "case (din)" in v
        assert "endcase" in v

    def test_enable_guard(self):
        """Output is zero when enable is low."""
        v = emit_module(binary_decoder().build())
        assert "out = 0" in v
        assert "if (en)" in v

    def test_custom_name(self):
        v = emit_module(binary_decoder(name="dec").build())
        assert "module dec" in v

    def test_width_validation(self):
        with pytest.raises(ValueError, match="width"):
            binary_decoder(width=0)

    def test_simulate_all_values(self):
        """Each binary input produces correct one-hot output."""
        m = binary_decoder(width=3)
        for i in range(8):
            sim = Simulator(m.build())
            sim.drive("din", i)
            sim.drive("en", 1)
            sim.run(max_time=10)
            assert sim.read("out") == (1 << i)

    def test_simulate_disabled(self):
        """When en=0, output is all zeros."""
        m = binary_decoder(width=3)
        sim = Simulator(m.build())
        sim.drive("din", 5)
        sim.drive("en", 0)
        sim.run(max_time=10)
        assert sim.read("out") == 0

    def test_simulate_2bit(self):
        """2-bit decoder: 4 one-hot outputs."""
        m = binary_decoder(width=2)
        for i in range(4):
            sim = Simulator(m.build())
            sim.drive("din", i)
            sim.drive("en", 1)
            sim.run(max_time=10)
            assert sim.read("out") == (1 << i)

    def test_simulate_1bit(self):
        """1-bit decoder: 2 one-hot outputs."""
        m = binary_decoder(width=1)
        for i in range(2):
            sim = Simulator(m.build())
            sim.drive("din", i)
            sim.drive("en", 1)
            sim.run(max_time=10)
            assert sim.read("out") == (1 << i)
