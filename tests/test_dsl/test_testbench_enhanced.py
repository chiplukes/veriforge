"""Tests for the enhanced multi-domain Python testbench generator."""

from __future__ import annotations

import pytest

from veriforge.dsl.testbench import generate_python_testbench
from veriforge.sim.bench import AmbiguousDomainError, PlannerOverrides
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _parse(src: str):
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


SINGLE_DOMAIN = """
module dut_single (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        m_axis_tready,
    output reg         m_axis_tvalid,
    output reg  [7:0]  m_axis_tdata,
    output reg         m_axis_tlast
);
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            m_axis_tvalid <= 1'b0;
            m_axis_tdata  <= 8'h00;
            m_axis_tlast  <= 1'b0;
        end else begin
            m_axis_tvalid <= 1'b1;
            m_axis_tdata  <= 8'hAA;
            m_axis_tlast  <= 1'b0;
        end
    end
endmodule
"""


TWO_DOMAIN = """
module dut_two (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire        m_axis_tready,
    output reg         m_axis_tvalid,
    output reg  [7:0]  m_axis_tdata,
    output reg         m_axis_tlast,

    input  wire        bclk,
    input  wire        brst_n,
    output reg         s_axis_tready,
    input  wire        s_axis_tvalid,
    input  wire [7:0]  s_axis_tdata,
    input  wire        s_axis_tlast
);
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) begin
            m_axis_tvalid <= 1'b0;
            m_axis_tdata  <= 8'h00;
            m_axis_tlast  <= 1'b0;
        end else begin
            m_axis_tvalid <= 1'b1;
            m_axis_tdata  <= 8'hAA;
            m_axis_tlast  <= 1'b0;
        end
    end

    always @(posedge bclk or negedge brst_n) begin
        if (!brst_n) s_axis_tready <= 1'b0;
        else         s_axis_tready <= s_axis_tvalid;
    end
endmodule
"""


COMB_ONLY = """
module dut_comb (
    input  wire [7:0] a,
    input  wire [7:0] b,
    output wire [7:0] y
);
    assign y = a + b;
endmodule
"""


class TestEnhancedSingleDomain:
    def test_includes_plan_summary_docstring(self):
        text = generate_python_testbench(_parse(SINGLE_DOMAIN), enhanced=True)
        assert text.startswith('"""Auto-generated Python testbench skeleton (enhanced multi-domain).')
        assert "Plan summary:" in text

    def test_schedules_single_clock(self):
        text = generate_python_testbench(_parse(SINGLE_DOMAIN), enhanced=True)
        assert text.count("_schedule_clock_events(Clock(sim.signal(") == 1
        assert 'sim.signal("clk")' in text

    def test_drives_active_low_reset(self):
        text = generate_python_testbench(_parse(SINGLE_DOMAIN), enhanced=True)
        assert 'step_drive(sim, engine, "rst_n", 0)' in text
        assert 'step_drive(sim, engine, "rst_n", 1)' in text

    def test_emits_axis_endpoint_grouped_by_domain(self):
        text = generate_python_testbench(_parse(SINGLE_DOMAIN), enhanced=True)
        assert "axis_sinks_clk" in text
        assert "coord_clk = EndpointCoordinator(" in text


class TestEnhancedTwoDomain:
    def test_schedules_each_clock(self):
        text = generate_python_testbench(_parse(TWO_DOMAIN), enhanced=True)
        assert 'sim.signal("aclk")' in text
        assert 'sim.signal("bclk")' in text
        assert text.count("_schedule_clock_events(Clock(") == 2

    def test_releases_each_reset(self):
        text = generate_python_testbench(_parse(TWO_DOMAIN), enhanced=True)
        assert 'step_drive(sim, engine, "aresetn", 0)' in text
        assert 'step_drive(sim, engine, "aresetn", 1)' in text
        assert 'step_drive(sim, engine, "brst_n", 0)' in text
        assert 'step_drive(sim, engine, "brst_n", 1)' in text

    def test_groups_endpoints_per_domain(self):
        text = generate_python_testbench(_parse(TWO_DOMAIN), enhanced=True)
        # Each domain owns its own coordinator + endpoint dict.
        assert "axis_sinks_aclk" in text
        assert "axis_sources_bclk" in text or "axis_sinks_bclk" in text
        assert "coord_aclk" in text
        assert "coord_bclk" in text


class TestEnhancedCombinational:
    def test_no_clock_no_reset_no_endpoints(self):
        text = generate_python_testbench(_parse(COMB_ONLY), enhanced=True)
        assert "_schedule_clock_events" not in text
        assert 'step_drive(sim, engine, "rst' not in text
        assert "No AXI-Stream or AXI-Lite interfaces were detected" in text


class TestEnhancedOverrides:
    def test_clock_period_override_via_kwarg(self):
        ov = PlannerOverrides(clock_periods={"clk": 7})
        text = generate_python_testbench(_parse(SINGLE_DOMAIN), enhanced=True, overrides=ov)
        assert "period=7" in text

    def test_strict_default_raises_on_ambiguity(self):
        ambiguous = """
module dut_amb (
    input  wire        clk_a,
    input  wire        rst_a_n,
    input  wire        clk_b,
    input  wire        rst_b_n,
    input  wire        bus_tready,
    output wire        bus_tvalid,
    output wire [7:0]  bus_tdata,
    output wire        bus_tlast
);
    reg ta, tb;
    assign bus_tvalid = 1'b1;
    assign bus_tdata  = 8'h22;
    assign bus_tlast  = 1'b0;
    always @(posedge clk_a or negedge rst_a_n) begin
        if (!rst_a_n) ta <= 1'b0; else ta <= ~ta;
    end
    always @(posedge clk_b or negedge rst_b_n) begin
        if (!rst_b_n) tb <= 1'b0; else tb <= ~tb;
    end
endmodule
"""
        with pytest.raises(AmbiguousDomainError):
            generate_python_testbench(_parse(ambiguous), enhanced=True)


class TestLegacyPathUnchanged:
    def test_legacy_default_does_not_mention_enhanced(self):
        text = generate_python_testbench(_parse(SINGLE_DOMAIN))
        assert "enhanced multi-domain" not in text
        assert text.startswith('"""Auto-generated Python testbench skeleton."""')
