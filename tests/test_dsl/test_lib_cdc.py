"""Tests for clock domain crossing and edge detection library components."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl.lib import edge_detector, synchronizer
from veriforge.sim import Clock, Simulator


# ===================================================================
# Synchronizer
# ===================================================================


class TestSynchronizer:
    """Test synchronizer factory function."""

    def test_default_ports(self):
        """Default synchronizer has clk, din, dout (1-bit)."""
        v = emit_module(synchronizer().build())
        assert "module synchronizer" in v
        assert "input clk" in v
        assert "input din" in v
        assert "output dout" in v

    def test_async_reg_attribute(self):
        """Synthesis attributes for FPGA placement."""
        v = emit_module(synchronizer().build())
        assert '(* async_reg = "true" *)' in v
        assert v.count("async_reg") == 2  # 2 stages

    def test_register_chain_2_stages(self):
        """Default 2-stage chain wiring."""
        v = emit_module(synchronizer().build())
        assert "sync_r0 <= din" in v
        assert "sync_r1 <= sync_r0" in v
        assert "assign dout = sync_r1" in v

    def test_3_stages(self):
        """3-stage synchronizer has three registers."""
        v = emit_module(synchronizer(stages=3).build())
        assert "sync_r0" in v
        assert "sync_r1" in v
        assert "sync_r2" in v
        assert v.count("async_reg") == 3
        assert "assign dout = sync_r2" in v

    def test_4_stages(self):
        """4-stage synchronizer."""
        v = emit_module(synchronizer(stages=4).build())
        assert "sync_r3" in v
        assert v.count("async_reg") == 4
        assert "assign dout = sync_r3" in v

    def test_multi_bit_width(self):
        """Multi-bit synchronizer declares correct widths."""
        v = emit_module(synchronizer(width=4).build())
        assert "[3:0] din" in v
        assert "[3:0] dout" in v
        assert "reg [3:0] sync_r0" in v

    def test_custom_name(self):
        v = emit_module(synchronizer(name="cdc_sync").build())
        assert "module cdc_sync" in v

    def test_stages_validation_1(self):
        with pytest.raises(ValueError, match="stages"):
            synchronizer(stages=1)

    def test_stages_validation_0(self):
        with pytest.raises(ValueError, match="stages"):
            synchronizer(stages=0)

    def test_simulate_initial_zero(self):
        """Output starts at zero before input propagates."""
        m = synchronizer(width=1, stages=2)
        sim = Simulator(m.build())
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("din", 0)
        sim.run(max_time=15)
        assert sim.read("dout") == 0

    def test_simulate_not_instant(self):
        """Output does not match input after only the initial settling."""
        m = synchronizer(width=1, stages=2)
        sim = Simulator(m.build())
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("din", 1)
        # Before any posedge, output still holds initial value (X or 0)
        sim.run(max_time=3)
        assert sim.read("dout") != 1  # not yet propagated

    def test_simulate_propagation_complete(self):
        """After enough clocks, output matches input."""
        m = synchronizer(width=1, stages=2)
        sim = Simulator(m.build())
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("din", 1)
        # After 2 posedges: sync_r0=1, sync_r1=1 → dout=1
        sim.run(max_time=25)
        assert sim.read("dout") == 1


# ===================================================================
# Edge detector
# ===================================================================


class TestEdgeDetector:
    """Test edge_detector factory function."""

    def test_rising_emit(self):
        """Rising edge detector emission."""
        v = emit_module(edge_detector("rising").build())
        assert "module rising_edge_det" in v
        assert "input clk" in v
        assert "input din" in v
        assert "output pulse" in v
        assert "din_r <= din" in v
        assert "assign pulse = din & ~din_r" in v

    def test_falling_emit(self):
        """Falling edge detector uses ~din & din_r."""
        v = emit_module(edge_detector("falling").build())
        assert "module falling_edge_det" in v
        assert "assign pulse = ~din & din_r" in v

    def test_any_emit(self):
        """Any-edge detector uses XOR."""
        v = emit_module(edge_detector("any").build())
        assert "module any_edge_det" in v
        assert "assign pulse = din ^ din_r" in v

    def test_custom_name(self):
        v = emit_module(edge_detector("rising", name="my_det").build())
        assert "module my_det" in v

    def test_invalid_type(self):
        with pytest.raises(ValueError, match="edge_type"):
            edge_detector("both")

    def test_invalid_type_empty(self):
        with pytest.raises(ValueError, match="edge_type"):
            edge_detector("")

    def test_simulate_rising_no_edge(self):
        """No pulse when din stays constant."""
        m = edge_detector("rising")
        sim = Simulator(m.build())
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("din", 0)
        sim.run(max_time=25)  # several clocks, din stays 0
        assert sim.read("pulse") == 0

    def test_simulate_rising_steady_high(self):
        """No pulse after din has been high long enough for din_r to catch up."""
        m = edge_detector("rising")
        sim = Simulator(m.build())
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("din", 1)
        sim.run(max_time=25)  # after posedges: din_r=1, pulse = 1 & ~1 = 0
        assert sim.read("pulse") == 0

    def test_simulate_falling_no_edge(self):
        """No falling pulse when din stays high."""
        m = edge_detector("falling")
        sim = Simulator(m.build())
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("din", 1)
        sim.run(max_time=25)
        assert sim.read("pulse") == 0

    def test_simulate_falling_steady_low(self):
        """No falling pulse when din stays low and din_r catches up."""
        m = edge_detector("falling")
        sim = Simulator(m.build())
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("din", 0)
        sim.run(max_time=25)
        assert sim.read("pulse") == 0

    def test_simulate_any_no_edge(self):
        """No any-edge pulse when din is steady."""
        m = edge_detector("any")
        sim = Simulator(m.build())
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("din", 0)
        sim.run(max_time=25)
        assert sim.read("pulse") == 0
