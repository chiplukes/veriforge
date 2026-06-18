"""Tests for AXI-Stream and AXI4-Lite library components."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.dsl import Module, posedge
from veriforge.dsl.lib import axi4_lite, axi_stream, axis_register


def _sig(intf, name):
    """Look up an InterfaceSignal by name from an Interface's signal list."""
    for s in intf._signals:
        if s.name == name:
            return s
    raise KeyError(f"signal {name!r} not found in {intf.name}")


def _sig_names(intf):
    """Return set of signal names in an Interface."""
    return {s.name for s in intf._signals}


# ===================================================================
# AXI-Stream interface
# ===================================================================


class TestAxiStreamInterface:
    """Test axi_stream() interface factory."""

    def test_default_signals(self):
        """Default interface has 4 standard signals."""
        intf = axi_stream()
        assert len(intf._signals) == 4
        names = _sig_names(intf)
        assert "tvalid" in names
        assert "tready" in names
        assert "tdata" in names
        assert "tlast" in names

    def test_signal_directions(self):
        """tvalid/tdata/tlast from master, tready from slave."""
        intf = axi_stream()
        assert _sig(intf, "tvalid").src == "master"
        assert _sig(intf, "tready").src == "slave"
        assert _sig(intf, "tdata").src == "master"
        assert _sig(intf, "tlast").src == "master"

    def test_data_width(self):
        """tdata width matches parameter."""
        intf = axi_stream(data_width=32)
        assert _sig(intf, "tdata").width == 32

    def test_optional_tid(self):
        """tid is included when tid_width > 0."""
        intf = axi_stream(tid_width=4)
        assert "tid" in _sig_names(intf)
        assert _sig(intf, "tid").width == 4
        assert _sig(intf, "tid").src == "master"

    def test_optional_tdest(self):
        """tdest is included when tdest_width > 0."""
        intf = axi_stream(tdest_width=3)
        assert "tdest" in _sig_names(intf)
        assert _sig(intf, "tdest").width == 3

    def test_optional_tuser(self):
        """tuser is included when tuser_width > 0."""
        intf = axi_stream(tuser_width=8)
        assert "tuser" in _sig_names(intf)
        assert _sig(intf, "tuser").width == 8

    def test_all_optional_signals(self):
        """All optional signals present when specified."""
        intf = axi_stream(data_width=16, tid_width=4, tdest_width=3, tuser_width=8)
        assert len(intf._signals) == 7  # 4 standard + 3 optional

    def test_no_optional_by_default(self):
        """Optional signals absent when width=0."""
        intf = axi_stream()
        names = _sig_names(intf)
        assert "tid" not in names
        assert "tdest" not in names
        assert "tuser" not in names

    def test_bind_as_master(self):
        """Master role makes tvalid/tdata/tlast outputs, tready input."""
        m = Module("test_master")
        m.input("clk")
        m.interface("m_axis", axi_stream(data_width=8), role="master")
        v = emit_module(m.build())
        assert "output m_axis_tvalid" in v
        assert "input m_axis_tready" in v
        assert "output [7:0] m_axis_tdata" in v
        assert "output m_axis_tlast" in v

    def test_bind_as_slave(self):
        """Slave role makes tvalid/tdata/tlast inputs, tready output."""
        m = Module("test_slave")
        m.input("clk")
        m.interface("s_axis", axi_stream(data_width=8), role="slave")
        v = emit_module(m.build())
        assert "input s_axis_tvalid" in v
        assert "output s_axis_tready" in v
        assert "input [7:0] s_axis_tdata" in v
        assert "input s_axis_tlast" in v

    def test_bind_as_master_reg(self):
        """Master with reg=True makes output signals use output_reg."""
        m = Module("test_reg")
        m.input("clk")
        m.interface("m_axis", axi_stream(data_width=8), role="master", reg=True)
        v = emit_module(m.build())
        assert "output reg m_axis_tvalid" in v
        assert "output reg [7:0] m_axis_tdata" in v
        assert "output reg m_axis_tlast" in v
        assert "input m_axis_tready" in v  # inputs stay as inputs


# ===================================================================
# AXI-Stream pipeline register
# ===================================================================


class TestAxisRegister:
    """Test axis_register factory function."""

    def test_port_names(self):
        """Has clock, reset, slave, and master interface ports."""
        v = emit_module(axis_register().build())
        assert "module axis_register" in v
        assert "input clk" in v
        assert "input rst" in v
        # Slave side
        assert "input s_axis_tvalid" in v
        assert "output s_axis_tready" in v
        assert "input [7:0] s_axis_tdata" in v
        assert "input s_axis_tlast" in v
        # Master side
        assert "output reg m_axis_tvalid" in v
        assert "input m_axis_tready" in v
        assert "output reg [7:0] m_axis_tdata" in v
        assert "output reg m_axis_tlast" in v

    def test_forward_register_logic(self):
        """Latches data when output ready or empty."""
        v = emit_module(axis_register().build())
        assert "m_axis_tready | ~m_axis_tvalid" in v
        assert "m_axis_tvalid <= s_axis_tvalid" in v
        assert "m_axis_tdata <= s_axis_tdata" in v
        assert "m_axis_tlast <= s_axis_tlast" in v

    def test_backpressure_assign(self):
        """Slave ready is combinational assign."""
        v = emit_module(axis_register().build())
        assert "assign s_axis_tready = m_axis_tready | ~m_axis_tvalid" in v

    def test_reset_clears_valid(self):
        """Reset deasserts master tvalid."""
        v = emit_module(axis_register().build())
        assert "m_axis_tvalid <= 0" in v

    def test_custom_name(self):
        v = emit_module(axis_register(name="pipe_reg").build())
        assert "module pipe_reg" in v

    def test_custom_data_width(self):
        """32-bit data width."""
        v = emit_module(axis_register(data_width=32).build())
        assert "[31:0] s_axis_tdata" in v
        assert "[31:0] m_axis_tdata" in v

    def test_posedge_clk(self):
        """Always block is edge-triggered on clk."""
        v = emit_module(axis_register().build())
        assert "always @(posedge clk)" in v


# ===================================================================
# AXI4-Lite interface
# ===================================================================


class TestAxi4LiteInterface:
    """Test axi4_lite() interface factory."""

    def test_signal_count(self):
        """AXI4-Lite has 19 signals across 5 channels."""
        intf = axi4_lite()
        assert len(intf._signals) == 19

    def test_write_address_channel(self):
        """AW channel: awaddr, awprot, awvalid, awready."""
        intf = axi4_lite()
        assert _sig(intf, "awaddr").src == "master"
        assert _sig(intf, "awprot").src == "master"
        assert _sig(intf, "awvalid").src == "master"
        assert _sig(intf, "awready").src == "slave"

    def test_write_data_channel(self):
        """W channel: wdata, wstrb, wvalid, wready."""
        intf = axi4_lite()
        assert _sig(intf, "wdata").src == "master"
        assert _sig(intf, "wstrb").src == "master"
        assert _sig(intf, "wvalid").src == "master"
        assert _sig(intf, "wready").src == "slave"

    def test_write_response_channel(self):
        """B channel: bresp, bvalid, bready."""
        intf = axi4_lite()
        assert _sig(intf, "bresp").src == "slave"
        assert _sig(intf, "bvalid").src == "slave"
        assert _sig(intf, "bready").src == "master"

    def test_read_address_channel(self):
        """AR channel: araddr, arprot, arvalid, arready."""
        intf = axi4_lite()
        assert _sig(intf, "araddr").src == "master"
        assert _sig(intf, "arprot").src == "master"
        assert _sig(intf, "arvalid").src == "master"
        assert _sig(intf, "arready").src == "slave"

    def test_read_data_channel(self):
        """R channel: rdata, rresp, rvalid, rready."""
        intf = axi4_lite()
        assert _sig(intf, "rdata").src == "slave"
        assert _sig(intf, "rresp").src == "slave"
        assert _sig(intf, "rvalid").src == "slave"
        assert _sig(intf, "rready").src == "master"

    def test_default_widths(self):
        """Default 32-bit data, 32-bit address."""
        intf = axi4_lite()
        assert _sig(intf, "awaddr").width == 32
        assert _sig(intf, "araddr").width == 32
        assert _sig(intf, "wdata").width == 32
        assert _sig(intf, "rdata").width == 32
        assert _sig(intf, "wstrb").width == 4  # 32/8

    def test_custom_widths(self):
        """Custom data and address widths."""
        intf = axi4_lite(data_width=64, addr_width=16)
        assert _sig(intf, "awaddr").width == 16
        assert _sig(intf, "araddr").width == 16
        assert _sig(intf, "wdata").width == 64
        assert _sig(intf, "rdata").width == 64
        assert _sig(intf, "wstrb").width == 8  # 64/8

    def test_prot_width(self):
        """Protection signals are 3 bits."""
        intf = axi4_lite()
        assert _sig(intf, "awprot").width == 3
        assert _sig(intf, "arprot").width == 3

    def test_resp_width(self):
        """Response signals are 2 bits."""
        intf = axi4_lite()
        assert _sig(intf, "bresp").width == 2
        assert _sig(intf, "rresp").width == 2

    def test_bind_as_slave(self):
        """Slave binding makes master-src signals inputs and slave-src outputs."""
        m = Module("periph")
        m.input("clk")
        m.input("rst")
        m.interface("s_axi", axi4_lite(data_width=32, addr_width=8), role="slave")
        v = emit_module(m.build())
        # Master-driven signals → inputs for the slave
        assert "input [7:0] s_axi_awaddr" in v
        assert "input s_axi_awvalid" in v
        # Slave-driven signals → outputs for the slave
        assert "output s_axi_awready" in v
        assert "output s_axi_bvalid" in v
        assert "output [31:0] s_axi_rdata" in v

    def test_bind_as_master(self):
        """Master binding makes master-src signals outputs."""
        m = Module("initiator")
        m.input("clk")
        m.interface("m_axi", axi4_lite(data_width=32, addr_width=16), role="master")
        v = emit_module(m.build())
        assert "output [15:0] m_axi_awaddr" in v
        assert "output m_axi_awvalid" in v
        assert "input m_axi_awready" in v
        assert "input m_axi_bvalid" in v
        assert "input [31:0] m_axi_rdata" in v


class TestLibReexports:
    """Test that all components are importable via the lib package."""

    def test_import_all(self):
        """All library components are accessible from veriforge.dsl.lib."""
        from veriforge.dsl.lib import (
            axi4_lite,
            axi_stream,
            axis_register,
            binary_decoder,
            edge_detector,
            priority_encoder,
            rom,
            simple_dual_port_ram,
            single_port_ram,
            sync_fifo,
            synchronizer,
            true_dual_port_ram,
        )

        # Verify all are callable
        assert callable(sync_fifo)
        assert callable(synchronizer)
        assert callable(edge_detector)
        assert callable(priority_encoder)
        assert callable(binary_decoder)
        assert callable(axi_stream)
        assert callable(axis_register)
        assert callable(axi4_lite)
        # RAM re-exports
        assert callable(single_port_ram)
        assert callable(simple_dual_port_ram)
        assert callable(true_dual_port_ram)
        assert callable(rom)
