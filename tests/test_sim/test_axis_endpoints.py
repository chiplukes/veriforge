from __future__ import annotations

import pytest

from veriforge.dsl import Module
from veriforge.dsl.lib import axi_stream
from veriforge.sim.bench import Testbench
from veriforge.sim.endpoints import AXIStreamFrame, AXIStreamSink, AXIStreamSource, EndpointCoordinator, PauseGenerator
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator

from .engines import ENGINES


def _axis_passthrough_module():
    module = Module("axis_passthrough_tb")
    module.input("clk")
    module.input("rst")
    s_axis = module.interface("s_axis", axi_stream(data_width=8), role="slave")
    m_axis = module.interface("m_axis", axi_stream(data_width=8), role="master")

    module.assign(m_axis.tvalid, s_axis.tvalid)
    module.assign(m_axis.tdata, s_axis.tdata)
    module.assign(m_axis.tlast, s_axis.tlast)
    module.assign(s_axis.tready, m_axis.tready)
    return module.build()


def _axis_no_tready_passthrough_module():
    """A fixed-latency stream with tvalid/tdata/tlast but no tready at all on either side."""
    module = Module("axis_no_tready_passthrough_tb")
    module.input("clk")
    module.input("rst")
    s_tvalid = module.input("s_axis_tvalid")
    s_tdata = module.input("s_axis_tdata", width=8)
    s_tlast = module.input("s_axis_tlast")
    m_tvalid = module.output("m_axis_tvalid")
    m_tdata = module.output("m_axis_tdata", width=8)
    m_tlast = module.output("m_axis_tlast")

    module.assign(m_tvalid, s_tvalid)
    module.assign(m_tdata, s_tdata)
    module.assign(m_tlast, s_tlast)
    return module.build()


def _axis_no_tlast_passthrough_module():
    """tvalid/tready/tdata but no tlast at all -- every accepted beat is its own frame."""
    module = Module("axis_no_tlast_passthrough_tb")
    module.input("clk")
    module.input("rst")
    s_tvalid = module.input("s_axis_tvalid")
    s_tready = module.output("s_axis_tready")
    s_tdata = module.input("s_axis_tdata", width=8)
    m_tvalid = module.output("m_axis_tvalid")
    m_tready = module.input("m_axis_tready")
    m_tdata = module.output("m_axis_tdata", width=8)

    module.assign(m_tvalid, s_tvalid)
    module.assign(m_tdata, s_tdata)
    module.assign(s_tready, m_tready)
    return module.build()


def _axis_no_tready_no_tlast_passthrough_module():
    """The minimal AXI-Stream: only tvalid/tdata on either side."""
    module = Module("axis_no_tready_no_tlast_passthrough_tb")
    module.input("clk")
    module.input("rst")
    s_tvalid = module.input("s_axis_tvalid")
    s_tdata = module.input("s_axis_tdata", width=8)
    m_tvalid = module.output("m_axis_tvalid")
    m_tdata = module.output("m_axis_tdata", width=8)

    module.assign(m_tvalid, s_tvalid)
    module.assign(m_tdata, s_tdata)
    return module.build()


def _read_int(sim: Simulator, signal_name: str) -> int:
    return int(sim.read(signal_name))


def _settle_drives(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=0)
    else:
        step_eval_now(sim)


def _make_sim_for(module, engine: str, signal_names: list[str]) -> Simulator:
    sim = Simulator(module, engine=engine)
    sim.run(max_time=0)
    for signal_name in signal_names:
        step_drive(sim, engine, signal_name, 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 1000)
    _settle_drives(sim, engine)
    step_run_until(sim, 12)
    step_drive(sim, engine, "rst", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst", 0)
    _settle_drives(sim, engine)
    return sim


def _make_sim(engine: str) -> Simulator:
    signal_names = ["clk", "rst", "s_axis_tvalid", "s_axis_tdata", "s_axis_tlast", "m_axis_tready"]
    return _make_sim_for(_axis_passthrough_module(), engine, signal_names)


def _make_no_tready_sim(engine: str) -> Simulator:
    signal_names = ["clk", "rst", "s_axis_tvalid", "s_axis_tdata", "s_axis_tlast"]
    return _make_sim_for(_axis_no_tready_passthrough_module(), engine, signal_names)


def _make_no_tlast_sim(engine: str) -> Simulator:
    signal_names = ["clk", "rst", "s_axis_tvalid", "s_axis_tdata", "m_axis_tready"]
    return _make_sim_for(_axis_no_tlast_passthrough_module(), engine, signal_names)


def _make_no_tready_no_tlast_sim(engine: str) -> Simulator:
    signal_names = ["clk", "rst", "s_axis_tvalid", "s_axis_tdata"]
    return _make_sim_for(_axis_no_tready_no_tlast_passthrough_module(), engine, signal_names)


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_source_sink_single_frame(engine: str) -> None:
    sim = _make_sim(engine)
    source = AXIStreamSource(sim, "s_axis")
    sink = AXIStreamSink(sim, "m_axis")
    coordinator = EndpointCoordinator(sim, [source, sink])

    source.send(AXIStreamFrame(data=[0x11, 0x22, 0x33]))
    coordinator.run_until(lambda: sink.count() == 1, max_steps=40, message="single frame receipt")

    frame = sink.recv()
    assert frame is not None
    assert frame.data == [0x11, 0x22, 0x33]
    assert frame.last == [0, 0, 1]
    assert source.empty()


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_sink_pause_backpressures_source(engine: str) -> None:
    sim = _make_sim(engine)
    source = AXIStreamSource(sim, "s_axis")
    sink = AXIStreamSink(sim, "m_axis")
    coordinator = EndpointCoordinator(sim, [source, sink])

    source.send(AXIStreamFrame(data=[0x41, 0x42, 0x43]))
    sink.pause = True

    for _ in range(6):
        assert coordinator.step()

    assert sink.empty()
    assert _read_int(sim, "s_axis_tvalid") == 1
    assert _read_int(sim, "m_axis_tready") == 0

    sink.pause = False
    coordinator.run_until(lambda: sink.count() == 1, max_steps=40, message="paused frame receipt")

    frame = sink.recv()
    assert frame is not None
    assert frame.data == [0x41, 0x42, 0x43]


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_source_write_helper_and_sink_read_helper(engine: str) -> None:
    sim = _make_sim(engine)
    source = AXIStreamSource(sim, "s_axis")
    sink = AXIStreamSink(sim, "m_axis")
    coordinator = EndpointCoordinator(sim, [source, sink])

    source.write([1, 2])
    source.write([3, 4])

    coordinator.run_until(lambda: sink.count() == 2, max_steps=60, message="two frame receipt")

    assert sink.read() == [1, 2, 3, 4]


# ------------------------------------------------------------------ PauseGenerator unit tests


def test_pause_generator_never() -> None:
    gen = PauseGenerator.never()
    assert all(not gen() for _ in range(100))


def test_pause_generator_always() -> None:
    gen = PauseGenerator.always()
    assert all(gen() for _ in range(100))


def test_pause_generator_duty_cycle() -> None:
    gen = PauseGenerator(1, 4, seed=0)
    results = [gen() for _ in range(400)]
    pause_rate = sum(results) / len(results)
    # Expect ~25% pause rate; allow generous tolerance.
    assert 0.15 <= pause_rate <= 0.35


def test_pause_generator_seeded_reproducible() -> None:
    gen_a = PauseGenerator(1, 3, seed=42)
    gen_b = PauseGenerator(1, 3, seed=42)
    sequence_a = [gen_a() for _ in range(50)]
    sequence_b = [gen_b() for _ in range(50)]
    assert sequence_a == sequence_b


def test_pause_generator_duty_factory() -> None:
    gen = PauseGenerator.duty(0.5, seed=1)
    results = [gen() for _ in range(1000)]
    pause_rate = sum(results) / len(results)
    assert 0.4 <= pause_rate <= 0.6


def test_pause_generator_invalid_args() -> None:
    with pytest.raises(ValueError):
        PauseGenerator(-1, 4)
    with pytest.raises(ValueError):
        PauseGenerator(3, 0)
    with pytest.raises(ValueError):
        PauseGenerator(5, 4)
    with pytest.raises(ValueError):
        PauseGenerator.duty(1.5)


# ------------------------------------------------------------------ callable pause on endpoints


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_source_callable_pause_throttles(engine: str) -> None:
    """Source with 50% random pause takes more cycles than unthrottled."""
    sim_fast = _make_sim(engine)
    src_fast = AXIStreamSource(sim_fast, "s_axis")
    snk_fast = AXIStreamSink(sim_fast, "m_axis")
    coord_fast = EndpointCoordinator(sim_fast, [src_fast, snk_fast])
    src_fast.send(AXIStreamFrame(data=list(range(8))))
    steps_fast = 0
    while snk_fast.count() == 0 and steps_fast < 200:
        coord_fast.step()
        steps_fast += 1

    sim_slow = _make_sim(engine)
    src_slow = AXIStreamSource(sim_slow, "s_axis")
    snk_slow = AXIStreamSink(sim_slow, "m_axis")
    coord_slow = EndpointCoordinator(sim_slow, [src_slow, snk_slow])
    src_slow.send(AXIStreamFrame(data=list(range(8))))
    # 50% pause on source — should take roughly 2× as many steps.
    src_slow.pause = PauseGenerator(1, 2, seed=7)
    steps_slow = 0
    while snk_slow.count() == 0 and steps_slow < 400:
        coord_slow.step()
        steps_slow += 1

    assert snk_slow.count() == 1
    assert steps_slow > steps_fast
    frame = snk_slow.recv()
    assert frame is not None
    assert frame.data == list(range(8))


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_sink_callable_pause_throttles(engine: str) -> None:
    """Sink with 50% random pause takes more cycles than unthrottled."""
    sim_fast = _make_sim(engine)
    src_fast = AXIStreamSource(sim_fast, "s_axis")
    snk_fast = AXIStreamSink(sim_fast, "m_axis")
    coord_fast = EndpointCoordinator(sim_fast, [src_fast, snk_fast])
    src_fast.send(AXIStreamFrame(data=[0xAA, 0xBB, 0xCC]))
    steps_fast = 0
    while snk_fast.count() == 0 and steps_fast < 200:
        coord_fast.step()
        steps_fast += 1

    sim_slow = _make_sim(engine)
    src_slow = AXIStreamSource(sim_slow, "s_axis")
    snk_slow = AXIStreamSink(sim_slow, "m_axis")
    coord_slow = EndpointCoordinator(sim_slow, [src_slow, snk_slow])
    src_slow.send(AXIStreamFrame(data=[0xAA, 0xBB, 0xCC]))
    # 50% pause on sink — should take roughly 2× as many steps.
    snk_slow.pause = PauseGenerator(1, 2, seed=13)
    steps_slow = 0
    while snk_slow.count() == 0 and steps_slow < 400:
        coord_slow.step()
        steps_slow += 1

    assert snk_slow.count() == 1
    assert steps_slow > steps_fast
    frame = snk_slow.recv()
    assert frame is not None
    assert frame.data == [0xAA, 0xBB, 0xCC]


# ------------------------------------------------------------------ tready-less AXI-Stream


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_source_sink_no_tready_single_frame(engine: str) -> None:
    """A fixed-latency DUT with no tready at all still works through the endpoints."""
    sim = _make_no_tready_sim(engine)
    source = AXIStreamSource(sim, "s_axis")
    sink = AXIStreamSink(sim, "m_axis")
    assert source.tready is None
    assert sink.tready is None
    coordinator = EndpointCoordinator(sim, [source, sink])

    source.send(AXIStreamFrame(data=[0x11, 0x22, 0x33]))
    coordinator.run_until(lambda: sink.count() == 1, max_steps=40, message="single frame receipt, no tready")

    frame = sink.recv()
    assert frame is not None
    assert frame.data == [0x11, 0x22, 0x33]
    assert frame.last == [0, 0, 1]
    assert source.empty()


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_source_no_tready_accepts_every_beat_immediately(engine: str) -> None:
    """With no tready, the source can't be held -- every offered beat is accepted next cycle."""
    sim = _make_no_tready_sim(engine)
    source = AXIStreamSource(sim, "s_axis")
    sink = AXIStreamSink(sim, "m_axis")
    coordinator = EndpointCoordinator(sim, [source, sink])

    source.send(AXIStreamFrame(data=list(range(8))))
    steps = 0
    while sink.count() == 0 and steps < 40:
        coordinator.step()
        steps += 1

    # 8 beats, no flow control anywhere: this should be about as fast as
    # test_axis_source_sink_single_frame's tready-bearing equivalent, not
    # stuck waiting on a tready that doesn't exist.
    assert sink.count() == 1
    assert steps < 20


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_source_pause_still_works_without_tready(engine: str) -> None:
    """pause on a tready-less SOURCE is still meaningful (producer-side gaps)."""
    sim = _make_no_tready_sim(engine)
    source = AXIStreamSource(sim, "s_axis")
    sink = AXIStreamSink(sim, "m_axis")
    coord = EndpointCoordinator(sim, [source, sink])

    source.send(AXIStreamFrame(data=[0x10, 0x20]))
    source.pause = True
    for _ in range(10):
        coord.step()
    assert sink.empty()

    source.pause = False
    coord.run_until(lambda: sink.count() == 1, max_steps=40, message="tready-less source pause release")
    frame = sink.recv()
    assert frame is not None
    assert frame.data == [0x10, 0x20]


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_sink_pause_false_allowed_without_tready(engine: str) -> None:
    """The default pause=False must not raise even when tready is absent."""
    sim = _make_no_tready_sim(engine)
    sink = AXIStreamSink(sim, "m_axis")
    sink.pause = False  # no-op, must not raise
    assert sink.pause is False


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("value", [True, PauseGenerator(1, 2, seed=1)])
def test_axis_sink_pause_raises_without_tready(engine: str, value) -> None:
    """There's nothing for pause to gate on a tready-less sink -- fail loudly, not silently."""
    sim = _make_no_tready_sim(engine)
    sink = AXIStreamSink(sim, "m_axis")
    with pytest.raises(ValueError, match="no tready signal"):
        sink.pause = value


def test_axis_endpoints_no_tready_via_bench_iface() -> None:
    """End-to-end: bench.iface() can drive/observe a tready-less AXI-Stream via relaxed detection.

    This is the scenario reported as unusable: a fixed-latency pipeline
    (tvalid/tdata/tlast, no tready anywhere) previously couldn't use the
    high-level put()/expect() API at all, even though the planner already
    detects it correctly via relaxed_iface_signals.
    """
    dut = _axis_no_tready_passthrough_module()
    bench = Testbench(dut, overrides={"relaxed_iface_signals": {"axi_stream": ["tready"]}})
    with bench.run():
        bench.reset_all()
        s_axis = bench.iface("s_axis")
        m_axis = bench.iface("m_axis")
        s_axis.put([1, 2, 3, 4, 5])
        got = m_axis.expect([1, 2, 3, 4, 5], timeout=50)
        assert list(got.data) == [1, 2, 3, 4, 5]

        with pytest.raises(ValueError, match="no tready signal"):
            m_axis.pause = True


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_source_pause_bool_unchanged(engine: str) -> None:
    """Existing bool-pause behaviour still works (no regression)."""
    sim = _make_sim(engine)
    source = AXIStreamSource(sim, "s_axis")
    sink = AXIStreamSink(sim, "m_axis")
    coord = EndpointCoordinator(sim, [source, sink])

    source.send(AXIStreamFrame(data=[0x10, 0x20]))
    source.pause = True
    for _ in range(10):
        coord.step()
    assert sink.empty()

    source.pause = False
    coord.run_until(lambda: sink.count() == 1, max_steps=40, message="bool pause release")
    frame = sink.recv()
    assert frame is not None
    assert frame.data == [0x10, 0x20]


# ------------------------------------------------------------------ tlast-less AXI-Stream


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_sink_no_tlast_every_beat_is_own_frame(engine: str) -> None:
    """With no tlast, each accepted beat completes immediately as its own one-beat frame."""
    sim = _make_no_tlast_sim(engine)
    source = AXIStreamSource(sim, "s_axis")
    sink = AXIStreamSink(sim, "m_axis")
    assert source.tlast is None
    assert sink.tlast is None
    coordinator = EndpointCoordinator(sim, [source, sink])

    source.send(AXIStreamFrame(data=[0x11, 0x22, 0x33]))
    coordinator.run_until(lambda: sink.count() == 3, max_steps=40, message="three one-beat frames")

    frames = [sink.recv() for _ in range(3)]
    assert [f.data for f in frames if f is not None] == [[0x11], [0x22], [0x33]]
    assert sink.empty()
    assert source.empty()


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_sink_no_tlast_strict_mode_unaffected(engine: str) -> None:
    """Strict-mode TDATA/TLAST-stability checks must not misfire when tlast doesn't exist."""
    sim = _make_no_tlast_sim(engine)
    source = AXIStreamSource(sim, "s_axis")
    sink = AXIStreamSink(sim, "m_axis", strict=True)
    coordinator = EndpointCoordinator(sim, [source, sink])

    sink.pause = PauseGenerator(1, 2, seed=5)  # exercise the no-handshake-yet path
    source.send(AXIStreamFrame(data=list(range(6))))
    coordinator.run_until(lambda: sink.count() == 6, max_steps=200, message="strict mode, no tlast")

    frames = [sink.recv() for _ in range(6)]
    assert [f.data[0] for f in frames if f is not None] == list(range(6))


@pytest.mark.parametrize("engine", ENGINES)
def test_axis_source_sink_no_tready_no_tlast(engine: str) -> None:
    """The minimal AXI-Stream -- only tvalid/tdata -- still works end to end."""
    sim = _make_no_tready_no_tlast_sim(engine)
    source = AXIStreamSource(sim, "s_axis")
    sink = AXIStreamSink(sim, "m_axis")
    assert source.tready is None and source.tlast is None
    assert sink.tready is None and sink.tlast is None
    coordinator = EndpointCoordinator(sim, [source, sink])

    source.send(AXIStreamFrame(data=[7, 8, 9]))
    coordinator.run_until(lambda: sink.count() == 3, max_steps=40, message="minimal AXI-Stream, three frames")

    frames = [sink.recv() for _ in range(3)]
    assert [f.data for f in frames if f is not None] == [[7], [8], [9]]


def test_axis_endpoints_no_tlast_via_bench_iface() -> None:
    """End-to-end: bench.iface() drives/observes a tlast-less stream via relaxed detection.

    Closes the gap examples/axis/slice's test_axi_stream_slice.py originally
    had to work around by dropping to Simulator.drive()/settle() directly
    for axi_stream_slice(..., has_tlast=False).
    """
    dut = _axis_no_tlast_passthrough_module()
    bench = Testbench(dut, overrides={"relaxed_iface_signals": {"axi_stream": ["tlast"]}})
    with bench.run():
        bench.reset_all()
        s_axis = bench.iface("s_axis")
        m_axis = bench.iface("m_axis")
        s_axis.put([1, 2, 3])
        for expected in [1, 2, 3]:
            got = m_axis.get(timeout=50)
            assert list(got.data) == [expected]
