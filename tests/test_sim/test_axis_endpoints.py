from __future__ import annotations

import shutil

import pytest

from veriforge.dsl import Module
from veriforge.dsl.lib import axi_stream
from veriforge.sim.endpoints import AXIStreamFrame, AXIStreamSink, AXIStreamSource, EndpointCoordinator, PauseGenerator
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator


_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")


def _engines() -> list[str]:
    engines = ["reference", "vm", "vm-fast"]
    if _has_compiler:
        engines.append("compiled")
    return engines


ENGINES = _engines()


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


def _read_int(sim: Simulator, signal_name: str) -> int:
    return int(sim.read(signal_name))


def _settle_drives(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=0)
    else:
        step_eval_now(sim)


def _make_sim(engine: str) -> Simulator:
    sim = Simulator(_axis_passthrough_module(), engine=engine)
    sim.run(max_time=0)
    for signal_name in ["clk", "rst", "s_axis_tvalid", "s_axis_tdata", "s_axis_tlast", "m_axis_tready"]:
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
