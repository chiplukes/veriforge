"""Tests for the multi-domain endpoint runner."""

from __future__ import annotations

from veriforge.dsl import Module
from veriforge.dsl.lib import axi_stream
from veriforge.sim.endpoints import (
    AXIStreamFrame,
    AXIStreamSink,
    AXIStreamSource,
    DomainCoordinator,
    EndpointCoordinator,
    MultiDomainRunner,
)
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator


def _two_domain_module():
    module = Module("two_domain_tb")
    module.input("aclk")
    module.input("arst")
    module.input("bclk")
    module.input("brst")
    a_in = module.interface("a_in", axi_stream(data_width=8), role="slave")
    a_out = module.interface("a_out", axi_stream(data_width=8), role="master")
    b_in = module.interface("b_in", axi_stream(data_width=8), role="slave")
    b_out = module.interface("b_out", axi_stream(data_width=8), role="master")

    # Pure combinational passthroughs (sampling happens on per-domain clocks
    # via the endpoint coordinator).
    module.assign(a_out.tvalid, a_in.tvalid)
    module.assign(a_out.tdata, a_in.tdata)
    module.assign(a_out.tlast, a_in.tlast)
    module.assign(a_in.tready, a_out.tready)

    module.assign(b_out.tvalid, b_in.tvalid)
    module.assign(b_out.tdata, b_in.tdata)
    module.assign(b_out.tlast, b_in.tlast)
    module.assign(b_in.tready, b_out.tready)
    return module.build()


def _settle(sim: Simulator) -> None:
    sim.run(max_time=0)


def _make_sim(*, aclk_period: int = 10, bclk_period: int = 14) -> Simulator:
    sim = Simulator(_two_domain_module(), engine="reference")
    sim.run(max_time=0)
    for s in [
        "aclk",
        "arst",
        "bclk",
        "brst",
        "a_in_tvalid",
        "a_in_tdata",
        "a_in_tlast",
        "a_out_tready",
        "b_in_tvalid",
        "b_in_tdata",
        "b_in_tlast",
        "b_out_tready",
    ]:
        step_drive(sim, "reference", s, 0)
    _settle(sim)
    sim._schedule_clock_events(Clock(sim.signal("aclk"), period=aclk_period), 5000)
    sim._schedule_clock_events(Clock(sim.signal("bclk"), period=bclk_period), 5000)
    _settle(sim)
    step_run_until(sim, 12)
    step_drive(sim, "reference", "arst", 1)
    step_drive(sim, "reference", "brst", 1)
    _settle(sim)
    step_run_until(sim, 30)
    step_drive(sim, "reference", "arst", 0)
    step_drive(sim, "reference", "brst", 0)
    _settle(sim)
    return sim


def test_multidomain_runner_delivers_frame_on_each_domain():
    sim = _make_sim()
    a_src = AXIStreamSource(sim, "a_in")
    a_sink = AXIStreamSink(sim, "a_out")
    b_src = AXIStreamSource(sim, "b_in")
    b_sink = AXIStreamSink(sim, "b_out")
    runner = MultiDomainRunner(
        sim,
        [
            DomainCoordinator(sim, [a_src, a_sink], clock_name="aclk", name="aclk"),
            DomainCoordinator(sim, [b_src, b_sink], clock_name="bclk", name="bclk"),
        ],
    )

    a_src.send(AXIStreamFrame(data=[0x11, 0x22, 0x33]))
    b_src.send(AXIStreamFrame(data=[0xA0, 0xB0]))

    runner.run_until(
        lambda: a_sink.count() == 1 and b_sink.count() == 1,
        max_steps=200,
        message="frames received on both domains",
    )

    fa = a_sink.recv()
    fb = b_sink.recv()
    assert fa is not None and fa.data == [0x11, 0x22, 0x33]
    assert fb is not None and fb.data == [0xA0, 0xB0]


def test_multidomain_runner_only_one_domain_active():
    """When only one domain has traffic, the other still ticks but does nothing."""
    sim = _make_sim()
    a_src = AXIStreamSource(sim, "a_in")
    a_sink = AXIStreamSink(sim, "a_out")
    b_src = AXIStreamSource(sim, "b_in")
    b_sink = AXIStreamSink(sim, "b_out")
    runner = MultiDomainRunner(
        sim,
        [
            DomainCoordinator(sim, [a_src, a_sink], clock_name="aclk"),
            DomainCoordinator(sim, [b_src, b_sink], clock_name="bclk"),
        ],
    )

    a_src.send(AXIStreamFrame(data=[0x55]))
    runner.run_until(lambda: a_sink.count() == 1, max_steps=80, message="a-domain frame")

    assert a_sink.recv().data == [0x55]
    assert b_sink.empty()


def test_multidomain_runner_rejects_empty_domain_list():
    sim = _make_sim()
    try:
        MultiDomainRunner(sim, [])
    except ValueError as exc:
        assert "at least one" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_multidomain_runner_rejects_duplicate_domain_names():
    sim = _make_sim()
    src = AXIStreamSource(sim, "a_in")
    try:
        MultiDomainRunner(
            sim,
            [
                DomainCoordinator(sim, [src], clock_name="aclk", name="dup"),
                DomainCoordinator(sim, [], clock_name="bclk", name="dup"),
            ],
        )
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_multidomain_runner_lookup_by_name():
    sim = _make_sim()
    a_src = AXIStreamSource(sim, "a_in")
    runner = MultiDomainRunner(
        sim,
        [DomainCoordinator(sim, [a_src], clock_name="aclk", name="alpha")],
    )
    assert runner.domain("alpha").clock.name == "aclk"


def test_multidomain_runner_run_until_timeout_raises():
    sim = _make_sim()
    a_src = AXIStreamSource(sim, "a_in")
    a_sink = AXIStreamSink(sim, "a_out")
    runner = MultiDomainRunner(
        sim,
        [DomainCoordinator(sim, [a_src, a_sink], clock_name="aclk")],
    )
    try:
        runner.run_until(lambda: a_sink.count() == 99, max_steps=5, message="impossible")
    except TimeoutError as exc:
        assert "impossible" in str(exc)
    else:
        raise AssertionError("expected TimeoutError")


def test_single_domain_endpoint_coordinator_unchanged():
    """EndpointCoordinator (single-domain) still works after the new API lands."""
    module = Module("simple_pass")
    module.input("clk")
    module.input("rst")
    s = module.interface("s_axis", axi_stream(data_width=8), role="slave")
    m = module.interface("m_axis", axi_stream(data_width=8), role="master")
    module.assign(m.tvalid, s.tvalid)
    module.assign(m.tdata, s.tdata)
    module.assign(m.tlast, s.tlast)
    module.assign(s.tready, m.tready)

    sim = Simulator(module.build(), engine="reference")
    sim.run(max_time=0)
    for sig in ["clk", "rst", "s_axis_tvalid", "s_axis_tdata", "s_axis_tlast", "m_axis_tready"]:
        step_drive(sim, "reference", sig, 0)
    sim.run(max_time=0)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 2000)
    sim.run(max_time=0)
    step_run_until(sim, 30)
    step_eval_now(sim)

    src = AXIStreamSource(sim, "s_axis")
    sink = AXIStreamSink(sim, "m_axis")
    coord = EndpointCoordinator(sim, [src, sink])

    src.send(AXIStreamFrame(data=[0x77]))
    coord.run_until(lambda: sink.count() == 1, max_steps=40, message="legacy single-domain")
    assert sink.recv().data == [0x77]
