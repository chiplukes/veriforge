"""Tests for testbench wrapper generation.

Tests the ``generate_testbench()`` function that auto-generates Verilog
testbenches for DUT modules.
"""

from veriforge.dsl import Module
from veriforge.dsl.lib import axi4_lite, axi_stream
from veriforge.dsl.testbench import (
    generate_testbench,
    generate_python_testbench,
    _is_clock,
    _is_reset,
    _is_active_low_reset,
    _port_width_int,
)
from veriforge.codegen import emit_module
from veriforge.model.ports import Port, PortDirection
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _build_dut(*ports):
    """Build a minimal DUT with the given port specs: (name, dir, width)."""
    m = Module("dut")
    for name, direction, width in ports:
        if direction == "input":
            m.input(name, width=width)
        elif direction == "output":
            m.output(name, width=width)
        elif direction == "output_reg":
            m.output_reg(name, width=width)
        elif direction == "inout":
            m.inout(name, width=width)
    return m.build()


def _emit_tb(dut, **kwargs):
    """Generate testbench and emit as Verilog string."""
    tb = generate_testbench(dut, **kwargs)
    return emit_module(tb)


# ===== Heuristic detection tests =====


class TestClockDetection:
    """Tests for clock signal name pattern matching."""

    def test_clk(self):
        assert _is_clock("clk")

    def test_clock(self):
        assert _is_clock("clock")

    def test_sys_clk(self):
        assert _is_clock("sys_clk")

    def test_pclk(self):
        assert _is_clock("pclk")

    def test_aclk(self):
        assert _is_clock("aclk")

    def test_not_clock_data(self):
        assert not _is_clock("data")

    def test_not_clock_enable(self):
        assert not _is_clock("en")

    def test_case_insensitive(self):
        assert _is_clock("CLK")


class TestResetDetection:
    """Tests for reset signal name pattern matching."""

    def test_rst(self):
        assert _is_reset("rst")

    def test_reset(self):
        assert _is_reset("reset")

    def test_rst_n(self):
        assert _is_reset("rst_n")

    def test_arst(self):
        assert _is_reset("arst")

    def test_not_reset_enable(self):
        assert not _is_reset("en")

    def test_active_low_rst_n(self):
        assert _is_active_low_reset("rst_n")

    def test_active_low_rstn(self):
        assert _is_active_low_reset("rstn")

    def test_active_high_rst(self):
        assert not _is_active_low_reset("rst")


# ===== Port width extraction =====


class TestPortWidthExtraction:
    """Tests for extracting integer width from Port declarations."""

    def test_scalar_port(self):
        port = Port("x", PortDirection.INPUT)
        assert _port_width_int(port) == 1

    def test_8bit_port(self):
        from veriforge.model.expressions import Literal, Range

        port = Port("x", PortDirection.INPUT, width=Range(Literal(7), Literal(0)))
        assert _port_width_int(port) == 8

    def test_16bit_port(self):
        from veriforge.model.expressions import Literal, Range

        port = Port("x", PortDirection.OUTPUT, width=Range(Literal(15), Literal(0)))
        assert _port_width_int(port) == 16


# ===== Basic generation tests =====


class TestBasicGeneration:
    """Tests for basic testbench structure."""

    def test_module_name_default(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut)
        assert "module tb_dut" in v

    def test_module_name_custom(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut, tb_name="my_tb")
        assert "module my_tb" in v

    def test_endmodule(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut)
        assert "endmodule" in v

    def test_no_ports_on_testbench(self):
        """Testbench should have no ports (top-level)."""
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut)
        assert "module tb_dut;" in v  # semicolon, not parentheses


# ===== Signal declarations =====


class TestSignalDeclarations:
    """Tests for reg/wire declarations in generated testbench."""

    def test_input_becomes_reg(self):
        dut = _build_dut(("data_in", "input", 8))
        v = _emit_tb(dut)
        assert "reg [7:0] data_in" in v

    def test_output_becomes_wire(self):
        dut = _build_dut(("data_out", "output", 8))
        v = _emit_tb(dut)
        assert "wire [7:0] data_out" in v

    def test_scalar_input(self):
        dut = _build_dut(("en", "input", 1))
        v = _emit_tb(dut)
        assert "reg en" in v

    def test_scalar_output(self):
        dut = _build_dut(("valid", "output", 1))
        v = _emit_tb(dut)
        assert "wire valid" in v

    def test_inout_becomes_wire(self):
        dut = _build_dut(("sda", "inout", 1))
        v = _emit_tb(dut)
        assert "wire sda" in v


# ===== DUT instantiation =====


class TestInstantiation:
    """Tests for DUT instantiation in generated testbench."""

    def test_default_instance_name(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut)
        assert "dut uut" in v

    def test_custom_instance_name(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut, instance_name="dut_inst")
        assert "dut dut_inst" in v

    def test_port_connections(self):
        dut = _build_dut(("clk", "input", 1), ("q", "output", 8))
        v = _emit_tb(dut)
        assert ".clk(clk)" in v
        assert ".q(q)" in v

    def test_module_name_in_instance(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut)
        assert "dut uut" in v


# ===== Clock generation =====


class TestClockGeneration:
    """Tests for auto-generated clock logic."""

    def test_clock_initial_zero(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut)
        assert "clk = 0" in v

    def test_clock_toggle(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut, clock_period=10)
        assert "~clk" in v
        assert "#5" in v

    def test_custom_clock_period(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut, clock_period=20)
        assert "#10" in v

    def test_no_clock_no_toggle(self):
        """No clock-named port → no clock generation."""
        dut = _build_dut(("data", "input", 8))
        v = _emit_tb(dut)
        assert "~" not in v or "~data" not in v


# ===== Reset generation =====


class TestResetGeneration:
    """Tests for auto-generated reset sequence."""

    def test_active_high_reset(self):
        dut = _build_dut(("clk", "input", 1), ("rst", "input", 1))
        v = _emit_tb(dut)
        # rst should be asserted (1) then de-asserted (0)
        lines = v.splitlines()
        # Find rst = 1 before rst = 0
        rst_1 = any("rst = 1" in ln for ln in lines)
        rst_0 = any("rst = 0" in ln for ln in lines)
        assert rst_1 and rst_0

    def test_active_low_reset(self):
        dut = _build_dut(("clk", "input", 1), ("rst_n", "input", 1))
        v = _emit_tb(dut)
        # rst_n should be asserted (0) then de-asserted (1)
        lines = v.splitlines()
        rst_0 = any("rst_n = 0" in ln for ln in lines)
        rst_1 = any("rst_n = 1" in ln for ln in lines)
        assert rst_0 and rst_1


class TestPythonTestbenchGeneration:
    """Tests for Python testbench skeleton generation."""

    def test_python_skeleton_includes_clock_and_reset_helpers(self):
        dut = _build_dut(("clk", "input", 1), ("rst", "input", 1), ("q", "output", 8))

        code = generate_python_testbench(dut)

        assert 'def _make_sim(module, *, design=None, engine: str = "reference") -> Simulator:' in code
        assert 'sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 1000)' in code
        assert 'step_drive(sim, engine, "rst", 1)' in code
        assert 'step_drive(sim, engine, "rst", 0)' in code

    def test_python_skeleton_references_axis_endpoints(self):
        m = Module("axis_dut")
        m.input("clk")
        m.input("rst")
        m.interface("s_axis", axi_stream(data_width=8), role="slave")
        m.interface("m_axis", axi_stream(data_width=8), role="master")

        code = generate_python_testbench(m.build())

        assert "AXIStreamFrame" in code
        assert "AXIStreamSink" in code
        assert "AXIStreamSource" in code
        assert "EndpointCoordinator" in code
        assert '"s_axis": AXIStreamSource(sim, "s_axis")' in code
        assert '"m_axis": AXIStreamSink(sim, "m_axis")' in code
        assert "Received AXIS frame:" in code

    def test_python_skeleton_references_axi_lite_master(self):
        m = Module("axi_regs")
        m.input("clk")
        m.input("rst")
        m.interface("s_axi", axi4_lite(data_width=32, addr_width=8), role="slave")

        code = generate_python_testbench(m.build())

        assert "AXILiteMaster" in code
        assert '"s_axi": AXILiteMaster(sim, "s_axi")' in code
        assert 'value = axi_lite_masters["s_axi"].read(0x0)' in code
        assert 'axi_lite_masters["s_axi"].write(0x0, 0x12345678)' in code

    def test_python_skeleton_handles_no_detected_interfaces(self):
        dut = _build_dut(("clk", "input", 1), ("rst", "input", 1), ("data_in", "input", 8), ("data_out", "output", 8))

        code = generate_python_testbench(dut)

        assert "No AXI-Stream or AXI-Lite interfaces were detected." in code

    def test_python_skeleton_can_be_generated_for_parsed_axi_lite_module(self):
        parser = verilog_parser(start="source_text")
        tree = parser.build_tree(
            text="""
module regs(
    input clk,
    input rst,
    input [31:0] s_axi_awaddr,
    input [2:0] s_axi_awprot,
    input s_axi_awvalid,
    output s_axi_awready,
    input [31:0] s_axi_wdata,
    input [3:0] s_axi_wstrb,
    input s_axi_wvalid,
    output s_axi_wready,
    output [1:0] s_axi_bresp,
    output s_axi_bvalid,
    input s_axi_bready,
    input [31:0] s_axi_araddr,
    input [2:0] s_axi_arprot,
    input s_axi_arvalid,
    output s_axi_arready,
    output [31:0] s_axi_rdata,
    output [1:0] s_axi_rresp,
    output s_axi_rvalid,
    input s_axi_rready
);
endmodule
"""
        )
        design = tree_to_design(tree)

        code = generate_python_testbench(design.modules[0])

        assert '"s_axi": AXILiteMaster(sim, "s_axi")' in code

    def test_reset_duration(self):
        dut = _build_dut(("clk", "input", 1), ("rst", "input", 1))
        v = _emit_tb(dut, reset_duration=50)
        assert "#50" in v


# ===== VCD generation =====


class TestVCDGeneration:
    """Tests for VCD dump code generation."""

    def test_dumpfile_default(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut)
        assert "$dumpfile" in v
        assert "tb_dut.vcd" in v

    def test_dumpvars(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut)
        assert "$dumpvars" in v

    def test_custom_vcd_filename(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut, vcd_filename="test.vcd")
        assert "test.vcd" in v

    def test_vcd_disabled(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut, vcd=False)
        assert "$dumpfile" not in v
        assert "$dumpvars" not in v


# ===== Timeout =====


class TestTimeout:
    """Tests for timeout watchdog."""

    def test_timeout_default(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut)
        assert "#1000" in v
        assert "$finish" in v

    def test_custom_timeout(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut, timeout=5000)
        assert "#5000" in v

    def test_timeout_message(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut)
        assert "timeout" in v.lower()


# ===== Stimulus placeholder =====


class TestStimulusPlaceholder:
    """Tests for stimulus placeholder text."""

    def test_placeholder_message(self):
        dut = _build_dut(("clk", "input", 1), ("rst", "input", 1))
        v = _emit_tb(dut)
        assert "Add your stimulus here" in v

    def test_finish_at_end(self):
        dut = _build_dut(("clk", "input", 1))
        v = _emit_tb(dut)
        # Should have $finish
        assert v.count("$finish") >= 1


# ===== Multi-port complex DUT =====


class TestComplexDUT:
    """End-to-end tests with realistic DUT configurations."""

    def test_counter_dut(self):
        """Counter with clk, rst, en, count[7:0]."""
        m = Module("counter")
        m.input("clk")
        m.input("rst")
        m.input("en")
        m.output_reg("count", width=8)
        dut = m.build()

        v = _emit_tb(dut)
        assert "module tb_counter" in v
        assert "reg clk" in v
        assert "reg rst" in v
        assert "reg en" in v
        assert "wire [7:0] count" in v
        assert "counter uut" in v

    def test_spi_dut(self):
        """SPI master with active-low reset."""
        m = Module("spi")
        m.input("clk")
        m.input("rst_n")
        m.input("start")
        m.input("data_in", width=8)
        m.output("sclk")
        m.output("mosi")
        m.output("data_out", width=8)
        dut = m.build()

        v = _emit_tb(dut)
        assert "rst_n = 0" in v  # Assert active-low
        assert "rst_n = 1" in v  # De-assert

    def test_no_clock_no_reset(self):
        """Purely combinational DUT."""
        dut = _build_dut(("a", "input", 8), ("b", "input", 8), ("y", "output", 8))
        v = _emit_tb(dut)
        assert "module tb_dut" in v
        assert "reg [7:0] a" in v
        assert "reg [7:0] b" in v
        assert "wire [7:0] y" in v
        # No clock gen
        assert "~" not in v or "clk" not in v.split("~")[1] if "~" in v else True
