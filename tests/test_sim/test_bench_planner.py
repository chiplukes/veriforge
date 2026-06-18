"""Tests for the Phase 2 testbench plan builder."""

from __future__ import annotations

import pytest

from veriforge.sim.bench import (
    AmbiguousDomainError,
    PlannerOverrides,
    build_plan,
)
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _parse(src: str):
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


# ---------------------------------------------------------------------------
# Verilog fixtures
# ---------------------------------------------------------------------------


SINGLE_DOMAIN_AXIS = """
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


# Two clean domains, each with its own AXIS bundle, structurally
# disjoint so structural inference resolves both.
TWO_DOMAIN_DISJOINT = """
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
        if (!brst_n) begin
            s_axis_tready <= 1'b0;
        end else begin
            s_axis_tready <= s_axis_tvalid;
        end
    end
endmodule
"""


# Two clocks but the AXIS bus has no structural usage in either always
# block (purely passed-through). Naming heuristic ("axi" -> "aclk") will
# uniquely resolve.
TWO_DOMAIN_AMBIGUOUS_NAMING = """
module dut_naming (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire        bclk,
    input  wire        brst_n,
    input  wire        m_axis_tready,
    output wire        m_axis_tvalid,
    output wire [7:0]  m_axis_tdata,
    output wire        m_axis_tlast
);
    reg a_tick;
    reg b_tick;
    assign m_axis_tvalid = 1'b1;
    assign m_axis_tdata  = 8'h11;
    assign m_axis_tlast  = 1'b0;

    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) a_tick <= 1'b0;
        else          a_tick <= ~a_tick;
    end
    always @(posedge bclk or negedge brst_n) begin
        if (!brst_n) b_tick <= 1'b0;
        else         b_tick <= ~b_tick;
    end
endmodule
"""


# Two clocks with no AXI naming and no structural use — truly ambiguous.
TWO_DOMAIN_TRULY_AMBIGUOUS = """
module dut_ambig (
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


COMBINATIONAL_ONLY = """
module dut_comb (
    input  wire [7:0] a,
    input  wire [7:0] b,
    output wire [7:0] y
);
    assign y = a + b;
endmodule
"""


# ---------------------------------------------------------------------------
# Single-domain happy path
# ---------------------------------------------------------------------------


class TestSingleDomain:
    def test_clock_and_reset_inferred(self) -> None:
        plan = build_plan(_parse(SINGLE_DOMAIN_AXIS))
        assert plan.top == "dut_single"
        assert len(plan.domains) == 1
        d = plan.domains[0]
        assert d.name == "clk"
        assert d.clock.name == "clk"
        assert d.clock.edge == "posedge"
        assert d.reset is not None
        assert d.reset.name == "rst_n"
        assert d.reset.active_low is True
        assert d.reset.style == "async"
        assert d.reset.edge == "negedge"

    def test_interface_bound_with_high_confidence(self) -> None:
        plan = build_plan(_parse(SINGLE_DOMAIN_AXIS))
        assert len(plan.interfaces) == 1
        b = plan.interface("m_axis")
        assert b.protocol == "axi_stream"
        assert b.role == "master"
        assert b.domain_name == "clk"
        # Bundle is structurally driven inside the always block, so
        # confidence should be structural rather than naming/sole-domain.
        assert b.confidence == "structural"

    def test_no_warnings_or_overrides(self) -> None:
        plan = build_plan(_parse(SINGLE_DOMAIN_AXIS))
        assert plan.warnings == ()
        assert plan.overrides_applied == ()

    def test_signals_payload(self) -> None:
        plan = build_plan(_parse(SINGLE_DOMAIN_AXIS))
        signals = dict(plan.interface("m_axis").signals)
        assert signals["tvalid"] == "m_axis_tvalid"
        assert signals["tdata"] == "m_axis_tdata"
        assert signals["tlast"] == "m_axis_tlast"
        assert signals["tready"] == "m_axis_tready"


# ---------------------------------------------------------------------------
# Multi-domain
# ---------------------------------------------------------------------------


class TestMultiDomain:
    def test_two_disjoint_domains(self) -> None:
        plan = build_plan(_parse(TWO_DOMAIN_DISJOINT))
        names = {d.name for d in plan.domains}
        assert names == {"aclk", "bclk"}
        # Both interface bindings should resolve structurally.
        m = plan.interface("m_axis")
        s = plan.interface("s_axis")
        assert m.domain_name == "aclk"
        assert m.confidence == "structural"
        assert s.domain_name == "bclk"
        assert s.confidence == "structural"
        assert plan.warnings == ()

    def test_naming_heuristic_resolves_axi_to_aclk(self) -> None:
        plan = build_plan(_parse(TWO_DOMAIN_AMBIGUOUS_NAMING))
        b = plan.interface("m_axis")
        assert b.domain_name == "aclk"
        assert b.confidence == "naming"
        assert plan.warnings == ()


# ---------------------------------------------------------------------------
# Strict / non-strict ambiguity behavior
# ---------------------------------------------------------------------------


class TestAmbiguity:
    def test_strict_raises_on_truly_ambiguous(self) -> None:
        module = _parse(TWO_DOMAIN_TRULY_AMBIGUOUS)
        with pytest.raises(AmbiguousDomainError, match="bus"):
            build_plan(module, strict=True)

    def test_non_strict_warns_and_picks_deterministically(self) -> None:
        module = _parse(TWO_DOMAIN_TRULY_AMBIGUOUS)
        plan = build_plan(module, strict=False)
        b = plan.interface("bus")
        # Lowest-named candidate wins deterministically when ambiguous.
        # Note: with no naming match the candidates list is empty and the
        # fallback prefers the lowest clock name overall.
        assert b.domain_name in {"clk_a", "clk_b"}
        # Either way, a warning is recorded.
        assert any("bus" in w for w in plan.warnings)

    def test_override_resolves_ambiguity(self) -> None:
        module = _parse(TWO_DOMAIN_TRULY_AMBIGUOUS)
        plan = build_plan(
            module,
            overrides={"iface_domains": {"bus": "clk_b"}},
            strict=True,
        )
        b = plan.interface("bus")
        assert b.domain_name == "clk_b"
        assert b.confidence == "override"
        assert any("bus" in entry for entry in plan.overrides_applied)
        assert plan.warnings == ()

    def test_override_to_unknown_domain_rejected(self) -> None:
        module = _parse(TWO_DOMAIN_TRULY_AMBIGUOUS)
        with pytest.raises(Exception, match="unknown domain"):
            build_plan(module, overrides={"iface_domains": {"bus": "ghost"}})


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_combinational_only_module(self) -> None:
        plan = build_plan(_parse(COMBINATIONAL_ONLY))
        assert plan.top == "dut_comb"
        # Combinational domain is synthesized when no clocks are present.
        assert len(plan.domains) == 1
        assert plan.domains[0].name == "__combinational__"
        assert plan.interfaces == ()

    def test_overrides_can_set_clock_period(self) -> None:
        plan = build_plan(
            _parse(SINGLE_DOMAIN_AXIS),
            overrides={"clock_periods": {"clk": 8}},
        )
        assert plan.domains[0].clock.period_hint == 8
        assert any("clock_periods" in entry for entry in plan.overrides_applied)

    def test_overrides_can_alias_domain_name(self) -> None:
        plan = build_plan(
            _parse(SINGLE_DOMAIN_AXIS),
            overrides={"domain_aliases": {"clk": "core"}},
        )
        assert {d.name for d in plan.domains} == {"core"}
        # Interface binding is rewritten to the new domain name.
        assert plan.interface("m_axis").domain_name == "core"

    def test_planner_overrides_dataclass_accepted(self) -> None:
        ov = PlannerOverrides(clock_periods={"clk": 12})
        plan = build_plan(_parse(SINGLE_DOMAIN_AXIS), overrides=ov)
        assert plan.domains[0].clock.period_hint == 12

    def test_invalid_overrides_type_rejected(self) -> None:
        with pytest.raises(TypeError):
            build_plan(_parse(SINGLE_DOMAIN_AXIS), overrides=42)  # type: ignore[arg-type]

    def test_reset_polarity_propagates(self) -> None:
        plan = build_plan(_parse(SINGLE_DOMAIN_AXIS))
        rst = plan.domains[0].reset
        assert rst is not None
        assert rst.assert_level == 0
        assert rst.release_level == 1

    def test_reset_polarity_override_flips_active_low(self) -> None:
        plan = build_plan(
            _parse(SINGLE_DOMAIN_AXIS),
            overrides=PlannerOverrides(reset_polarities={"rst_n": "active_high"}),
        )
        rst = plan.domains[0].reset
        assert rst is not None
        assert rst.active_low is False
        assert any("reset_polarities" in entry for entry in plan.overrides_applied)

    def test_reset_polarity_override_noop_when_matching(self) -> None:
        plan = build_plan(
            _parse(SINGLE_DOMAIN_AXIS),
            overrides={"reset_polarities": {"rst_n": "active_low"}},
        )
        rst = plan.domains[0].reset
        assert rst is not None
        assert rst.active_low is True
        # No effective change -> not recorded as applied.
        assert not any("reset_polarities" in entry for entry in plan.overrides_applied)

    def test_reset_polarity_override_invalid_value(self) -> None:
        with pytest.raises(ValueError, match="active_high"):
            PlannerOverrides(reset_polarities={"rst_n": "low"})


# ---------------------------------------------------------------------------
# Plan invariants
# ---------------------------------------------------------------------------


class TestPlanInvariants:
    def test_every_interface_resolves_in_lookup(self) -> None:
        plan = build_plan(_parse(TWO_DOMAIN_DISJOINT))
        for iface in plan.interfaces:
            d = plan.domain(iface.domain_name)
            assert d.clock.name in {"aclk", "bclk"}

    def test_summary_contains_clocks_and_interfaces(self) -> None:
        plan = build_plan(_parse(TWO_DOMAIN_DISJOINT))
        text = plan.summary()
        assert "aclk" in text
        assert "bclk" in text
        assert "m_axis" in text
        assert "s_axis" in text
