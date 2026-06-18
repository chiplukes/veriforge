"""Truly-async axis_async_fifo test (Wave G — multi-domain stress).

This test exercises the dual-clock ``axis_async_fifo_dualclk_wrap`` with
independent clocks on the write (``s_clk``) and read (``m_clk``) sides.
Unlike the single-clock ``axis_async_fifo_wrap`` tests (which tie both
clocks together and therefore only exercise the gray-code pointer
arithmetic), this test actually crosses the CDC boundary on every
pointer update.

The goal is to validate the bench framework's
:class:`MultiDomainRunner` + per-domain endpoint scheme against a real
CDC DUT and surface any ordering bugs in the
``tick_pre`` / ``sample_pre`` / ``tick_post`` lifecycle when two clocks
fire at unrelated rates.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veriforge.project import parse_files
from veriforge.sim.endpoints import (
    AXIStreamFrame,
    AXIStreamSink,
    AXIStreamSource,
    DomainCoordinator,
    MultiDomainRunner,
)
from veriforge.sim.step_harness import step_drive, step_run_until
from veriforge.sim.testbench import Clock, Simulator

REPO_ROOT = Path(__file__).resolve().parents[2]
VENDOR_DIR = REPO_ROOT / "examples" / "taxi" / "vendor" / "verilog-axis"
WRAP_DIR = REPO_ROOT / "examples" / "taxi" / "wrappers"
WRAP_PATH = WRAP_DIR / "axis_async_fifo_dualclk_wrap.v"
FIFO_PATH = VENDOR_DIR / "axis_async_fifo.v"

_FILES_EXIST = FIFO_PATH.exists() and WRAP_PATH.exists()


def _make_sim(s_period: int = 10, m_period: int = 17) -> Simulator:
    """Build a reference-engine simulator with two independent clocks."""
    design = parse_files([str(FIFO_PATH), str(WRAP_PATH)], preprocess=True)
    top = design.get_module("axis_async_fifo_dualclk_wrap")
    sim = Simulator(top, engine="reference", design=design)
    sim.run(max_time=0)

    # Idle all bench-driven inputs.
    for name in [
        "s_clk",
        "s_rst",
        "m_clk",
        "m_rst",
        "s_axis_tvalid",
        "s_axis_tdata",
        "s_axis_tlast",
        "m_axis_tready",
    ]:
        step_drive(sim, "reference", name, 0)
    sim.run(max_time=0)

    # Schedule both clocks at different periods so edges never align.
    sim._schedule_clock_events(Clock(sim.signal("s_clk"), period=s_period), 10000)
    sim._schedule_clock_events(Clock(sim.signal("m_clk"), period=m_period), 10000)
    sim.run(max_time=0)

    # Apply reset, hold for a comfortable margin in both domains, then
    # release. Doing this *before* creating endpoints avoids the
    # endpoints' constructors driving stale idle values mid-reset.
    step_run_until(sim, 5)
    step_drive(sim, "reference", "s_rst", 1)
    step_drive(sim, "reference", "m_rst", 1)
    sim.run(max_time=0)
    # Hold reset ~10 of each clock — plenty for the synchronizer chains.
    step_run_until(sim, sim.time + max(s_period, m_period) * 10)
    step_drive(sim, "reference", "s_rst", 0)
    step_drive(sim, "reference", "m_rst", 0)
    sim.run(max_time=0)
    return sim


@pytest.mark.skipif(not _FILES_EXIST, reason="dualclk wrapper or vendor file missing")
def test_async_fifo_dualclk_single_frame_fast_to_slow():
    """Write @100 MHz (10 ns) -> Read @ ~59 MHz (17 ns). Single 3-beat frame."""
    sim = _make_sim(s_period=10, m_period=17)
    src = AXIStreamSource(sim, "s_axis")
    snk = AXIStreamSink(sim, "m_axis")
    runner = MultiDomainRunner(
        sim,
        [
            DomainCoordinator(sim, [src], clock_name="s_clk", name="s_clk"),
            DomainCoordinator(sim, [snk], clock_name="m_clk", name="m_clk"),
        ],
    )

    payload = [0xA1, 0xB2, 0xC3]
    src.send(AXIStreamFrame(data=payload))

    runner.run_until(
        lambda: snk.count() == 1,
        max_steps=400,
        message="frame received on m_clk side",
    )

    received = snk.recv()
    assert received is not None, "sink endpoint returned None"
    assert list(received.data) == payload, f"frame mismatch: got {list(received.data)}, expected {payload}"


@pytest.mark.skipif(not _FILES_EXIST, reason="dualclk wrapper or vendor file missing")
def test_async_fifo_dualclk_single_frame_slow_to_fast():
    """Write @ ~59 MHz (17 ns) -> Read @100 MHz (10 ns). Reverse skew."""
    sim = _make_sim(s_period=17, m_period=10)
    src = AXIStreamSource(sim, "s_axis")
    snk = AXIStreamSink(sim, "m_axis")
    runner = MultiDomainRunner(
        sim,
        [
            DomainCoordinator(sim, [src], clock_name="s_clk", name="s_clk"),
            DomainCoordinator(sim, [snk], clock_name="m_clk", name="m_clk"),
        ],
    )

    payload = [0x11, 0x22, 0x33, 0x44]
    src.send(AXIStreamFrame(data=payload))

    runner.run_until(
        lambda: snk.count() == 1,
        max_steps=400,
        message="frame received on m_clk side",
    )

    received = snk.recv()
    assert received is not None, "sink endpoint returned None"
    assert list(received.data) == payload


@pytest.mark.skipif(not _FILES_EXIST, reason="dualclk wrapper or vendor file missing")
def test_async_fifo_dualclk_multiple_frames_ordered():
    """4 back-to-back frames must arrive in order across the CDC."""
    sim = _make_sim(s_period=10, m_period=13)
    src = AXIStreamSource(sim, "s_axis")
    snk = AXIStreamSink(sim, "m_axis")
    runner = MultiDomainRunner(
        sim,
        [
            DomainCoordinator(sim, [src], clock_name="s_clk", name="s_clk"),
            DomainCoordinator(sim, [snk], clock_name="m_clk", name="m_clk"),
        ],
    )

    frames = [
        [0x10, 0x11, 0x12],
        [0x20, 0x21],
        [0x30],
        [0x40, 0x41, 0x42, 0x43, 0x44],
    ]
    for f in frames:
        src.send(AXIStreamFrame(data=f))

    runner.run_until(
        lambda: snk.count() == len(frames),
        max_steps=1500,
        message="all frames received on m_clk side",
    )

    for i, expected in enumerate(frames):
        got = snk.recv()
        assert got is not None, f"frame {i} missing"
        assert list(got.data) == expected, f"frame {i} mismatch: got {list(got.data)}, expected {expected}"


@pytest.mark.skipif(not _FILES_EXIST, reason="dualclk wrapper or vendor file missing")
def test_async_fifo_dualclk_backpressure_stress_strict():
    """Stress: backpressure on both sides, strict-mode sink monitor.

    The source pauses ~30% of cycles and the sink pauses ~40% of cycles,
    using independent PRNG sequences. With a DEPTH=32 FIFO and a
    256-beat single frame, the FIFO fills and drains many times. The
    sink runs in strict mode, so any TVALID drop or TDATA change
    mid-handshake (a sign of the multi-domain runner mis-sequencing
    edges between domains) will raise AXIStreamProtocolError.
    """
    import random

    sim = _make_sim(s_period=10, m_period=17)
    src = AXIStreamSource(sim, "s_axis")
    snk = AXIStreamSink(sim, "m_axis", strict=True)

    rng_src = random.Random(0xC0FFEE)
    rng_snk = random.Random(0xBADBEEF)
    src.pause = lambda: rng_src.random() < 0.30
    snk.pause = lambda: rng_snk.random() < 0.40

    runner = MultiDomainRunner(
        sim,
        [
            DomainCoordinator(sim, [src], clock_name="s_clk", name="s_clk"),
            DomainCoordinator(sim, [snk], clock_name="m_clk", name="m_clk"),
        ],
    )

    payload = list(range(256))
    payload = [b & 0xFF for b in payload]
    src.send(AXIStreamFrame(data=payload))

    runner.run_until(
        lambda: snk.count() == 1,
        max_steps=20_000,
        message="256-beat frame received on m_clk side",
    )

    received = snk.recv()
    assert received is not None
    assert list(received.data) == payload, (
        f"backpressure stress mismatch: len(got)={len(received.data)}, "
        f"len(expected)={len(payload)}; first differing index = "
        f"{next((i for i, (g, e) in enumerate(zip(received.data, payload)) if g != e), None)}"
    )
