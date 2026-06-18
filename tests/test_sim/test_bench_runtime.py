"""Tests for the high-level :class:`Testbench` runtime DSL (Phase 7)."""

from __future__ import annotations

import pytest

from veriforge.sim.bench import (
    AXIStreamProxy,
    BenchTimeoutError,
    Testbench,
    make_bench,
)
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _parse(src: str):
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# Single-domain combinational AXIS loopback (s_axis -> m_axis) plus a tiny
# clocked counter so the planner can detect the clock + reset.
SINGLE_DOMAIN_LOOPBACK = """
module loopback (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire [7:0]  s_axis_tdata,
    input  wire        s_axis_tlast,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output wire [7:0]  m_axis_tdata,
    output wire        m_axis_tlast
);
    reg [7:0] heartbeat;
    assign m_axis_tvalid = s_axis_tvalid;
    assign m_axis_tdata  = s_axis_tdata;
    assign m_axis_tlast  = s_axis_tlast;
    assign s_axis_tready = m_axis_tready;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) heartbeat <= 8'h00;
        else        heartbeat <= heartbeat + 8'd1;
    end
endmodule
"""


# Two independent clock+reset domains, each with its own combinational AXIS
# passthrough plus a clocked tickover counter to anchor clock detection.
TWO_DOMAIN_LOOPBACK = """
module two_dom_loopback (
    input  wire        aclk,
    input  wire        aresetn,
    input  wire        bclk,
    input  wire        bresetn,
    input  wire        a_axis_in_tvalid,
    output wire        a_axis_in_tready,
    input  wire [7:0]  a_axis_in_tdata,
    input  wire        a_axis_in_tlast,
    output wire        a_axis_out_tvalid,
    input  wire        a_axis_out_tready,
    output wire [7:0]  a_axis_out_tdata,
    output wire        a_axis_out_tlast,
    input  wire        b_axis_in_tvalid,
    output wire        b_axis_in_tready,
    input  wire [7:0]  b_axis_in_tdata,
    input  wire        b_axis_in_tlast,
    output wire        b_axis_out_tvalid,
    input  wire        b_axis_out_tready,
    output wire [7:0]  b_axis_out_tdata,
    output wire        b_axis_out_tlast
);
    reg [7:0] a_tick, b_tick;
    assign a_axis_out_tvalid = a_axis_in_tvalid;
    assign a_axis_out_tdata  = a_axis_in_tdata;
    assign a_axis_out_tlast  = a_axis_in_tlast;
    assign a_axis_in_tready  = a_axis_out_tready;
    assign b_axis_out_tvalid = b_axis_in_tvalid;
    assign b_axis_out_tdata  = b_axis_in_tdata;
    assign b_axis_out_tlast  = b_axis_in_tlast;
    assign b_axis_in_tready  = b_axis_out_tready;
    always @(posedge aclk or negedge aresetn) begin
        if (!aresetn) a_tick <= 8'h00;
        else          a_tick <= a_tick + 8'd1;
    end
    always @(posedge bclk or negedge bresetn) begin
        if (!bresetn) b_tick <= 8'h00;
        else          b_tick <= b_tick + 8'd1;
    end
endmodule
"""


def _single_domain_loopback():
    return _parse(SINGLE_DOMAIN_LOOPBACK)


def _two_domain_loopback():
    return _parse(TWO_DOMAIN_LOOPBACK)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_construction_builds_plan_and_domains(self):
        bench = Testbench(_single_domain_loopback())
        assert bench.plan.top == "loopback"
        assert {d.name for d in bench.plan.domains} == {"clk"}
        assert bench.domain("clk").reset_name == "rst_n"
        assert bench.domain("clk").reset_active_low is True

    def test_make_bench_factory_equivalent(self):
        bench = make_bench(_single_domain_loopback())
        assert isinstance(bench, Testbench)
        assert bench.domain("clk").period == 10  # default

    def test_unknown_domain_raises_keyerror(self):
        bench = Testbench(_single_domain_loopback())
        with pytest.raises(KeyError, match="no domain"):
            bench.domain("missing")

    def test_unknown_iface_raises_keyerror(self):
        bench = Testbench(_single_domain_loopback())
        with pytest.raises(KeyError):
            bench.iface("nope")

    def test_clock_period_override_applied(self):
        from veriforge.sim.bench import PlannerOverrides  # noqa: PLC0415

        bench = Testbench(
            _single_domain_loopback(),
            overrides=PlannerOverrides(clock_periods={"clk": 14}),
        )
        assert bench.domain("clk").period == 14


# ---------------------------------------------------------------------------
# Single-domain AXIS round-trip
# ---------------------------------------------------------------------------


class TestSingleDomainLoopback:
    def test_put_and_get_round_trip(self):
        bench = Testbench(_single_domain_loopback())
        with bench.run():
            bench.reset_all()
            m_axis = bench.iface("s_axis")  # DUT slave -> proxy is source
            s_axis = bench.iface("m_axis")  # DUT master -> proxy is sink
            assert isinstance(m_axis, AXIStreamProxy)
            assert isinstance(s_axis, AXIStreamProxy)

            payload = [0x11, 0x22, 0x33]
            m_axis.put(payload)
            frame = s_axis.get(timeout=200)
            assert list(frame.data) == payload

    def test_proxy_is_cached_per_prefix(self):
        bench = Testbench(_single_domain_loopback())
        first = bench.iface("s_axis")
        second = bench.iface("s_axis")
        assert first is second

    def test_get_raises_bench_timeout_when_no_traffic(self):
        bench = Testbench(_single_domain_loopback())
        with bench.run():
            bench.reset_all()
            sink = bench.iface("m_axis")
            with pytest.raises(BenchTimeoutError, match="no frame after"):
                sink.get(timeout=20)

    def test_put_on_sink_role_raises(self):
        bench = Testbench(_single_domain_loopback())
        sink = bench.iface("m_axis")  # DUT master -> proxy is a sink
        with pytest.raises(RuntimeError, match="sink"):
            sink.put([1, 2, 3])

    def test_get_on_source_role_raises(self):
        bench = Testbench(_single_domain_loopback())
        source = bench.iface("s_axis")
        with pytest.raises(RuntimeError, match="source"):
            source.get()

    def test_expect_matches(self):
        bench = Testbench(_single_domain_loopback())
        with bench.run():
            bench.reset_all()
            bench.iface("s_axis").put([0xAA, 0xBB])
            bench.iface("m_axis").expect([0xAA, 0xBB], timeout=200)

    def test_expect_mismatch_raises_assertion(self):
        bench = Testbench(_single_domain_loopback())
        with bench.run():
            bench.reset_all()
            bench.iface("s_axis").put([1, 2, 3])
            with pytest.raises(AssertionError, match="mismatch"):
                bench.iface("m_axis").expect([9, 9, 9], timeout=200)

    def test_pending_reflects_buffered_frames(self):
        bench = Testbench(_single_domain_loopback())
        with bench.run():
            bench.reset_all()
            bench.iface("s_axis").put([1])
            bench.iface("s_axis").put([2])
            sink = bench.iface("m_axis")
            # Step until both frames have arrived.
            for _ in range(200):
                if sink.pending() >= 2:
                    break
                bench.step()
            assert sink.pending() == 2
            assert list(sink.get().data) == [1]
            assert list(sink.get().data) == [2]

    def test_wait_drain_returns_after_source_empties(self):
        bench = Testbench(_single_domain_loopback())
        with bench.run():
            bench.reset_all()
            src = bench.iface("s_axis")
            bench.iface("m_axis")  # also register sink so tready is driven
            src.put([1, 2, 3, 4])
            src.wait_drain(timeout=200)
            # After drain the source has no more pending beats.
            assert src._source.empty()


# ---------------------------------------------------------------------------
# Multi-domain
# ---------------------------------------------------------------------------


class TestMultiDomain:
    def test_two_domain_loopback_independent(self):
        bench = Testbench(_two_domain_loopback())
        assert {d.name for d in bench.plan.domains} == {"aclk", "bclk"}
        with bench.run():
            bench.reset_all()
            bench.iface("a_axis_in").put([0xA1, 0xA2])
            bench.iface("b_axis_in").put([0xB1, 0xB2, 0xB3])
            a_frame = bench.iface("a_axis_out").get(timeout=300)
            b_frame = bench.iface("b_axis_out").get(timeout=300)
            assert list(a_frame.data) == [0xA1, 0xA2]
            assert list(b_frame.data) == [0xB1, 0xB2, 0xB3]

    def test_step_with_specific_domain(self):
        bench = Testbench(_two_domain_loopback())
        with bench.run():
            bench.reset_all()
            aclk_before = int(bench.domain("aclk").coordinator.clock.value)
            bench.step(2, domain="aclk")
            # We requested 2 rising edges; clock should be high after the
            # second edge or low after a subsequent fall — but the runner
            # always returns at a rising edge so it must be 1.
            aclk_after = int(bench.domain("aclk").coordinator.clock.value)
            assert (aclk_before, aclk_after) == (0, 1) or aclk_after == 1
