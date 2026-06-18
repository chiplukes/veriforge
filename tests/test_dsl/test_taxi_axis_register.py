"""Pytest tests for the taxi/axis_register example (Wave F).

Covers three scenarios for the ``axis_register`` DUT (verilog-axis fallback):

1. Interface auto-detection — verifies the parser finds exactly two AXI-Stream
   bundles (``s_axis`` slave, ``m_axis`` master) on the flat-port module.
2. Python-stepped simulation — drives 5 frames through the skid buffer and
   asserts the received payload is identical.
3. compile_native — drives 10 beats through ``AXIStreamSourceLowering`` /
   ``AXIStreamSinkLowering`` and validates the batch_run results.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veriforge.project import build_testbench, parse_file
from veriforge.sim.bench import (
    AXIStreamSinkLowering,
    AXIStreamSourceLowering,
    compile_native,
)
from veriforge.sim.endpoints import detect_axi_stream_interfaces

REPO_ROOT = Path(__file__).resolve().parents[2]
DUT_PATH = REPO_ROOT / "examples" / "taxi" / "vendor" / "verilog-axis" / "axis_register.v"


@pytest.mark.skipif(not DUT_PATH.exists(), reason="axis_register.v vendor file not present")
def test_axis_register_interfaces_detected():
    """Auto-detection finds s_axis (slave) and m_axis (master)."""
    design = parse_file(DUT_PATH)
    module = design.get_module("axis_register")
    assert module is not None

    bundles = detect_axi_stream_interfaces(module)
    prefixes = {b.prefix: b.role for b in bundles}
    assert "s_axis" in prefixes, f"missing s_axis; found {list(prefixes)}"
    assert "m_axis" in prefixes, f"missing m_axis; found {list(prefixes)}"
    assert prefixes["s_axis"] == "slave"
    assert prefixes["m_axis"] == "master"


@pytest.mark.skipif(not DUT_PATH.exists(), reason="axis_register.v vendor file not present")
def test_axis_register_python_stepped():
    """5-frame Python-stepped run: received payload matches sent payload."""
    frames = [list(range(0x10 + i, 0x10 + i + 4)) for i in range(5)]

    bench = build_testbench(DUT_PATH)
    with bench.run():
        bench.reset_all()
        src = bench.iface("s_axis")
        snk = bench.iface("m_axis")
        for frame in frames:
            src.put(frame)
        src.wait_drain()
        received = [snk.get() for _ in frames]

    for i, (frame, pkt) in enumerate(zip(frames, received, strict=True)):
        assert list(pkt.data) == frame, f"frame {i}: got {list(pkt.data)}, expected {frame}"


@pytest.mark.skipif(not DUT_PATH.exists(), reason="axis_register.v vendor file not present")
def test_axis_register_compile_native():
    """10-beat compile_native batch_run: all captured beats match source."""
    beats = list(range(0xA0, 0xAA))  # 10 bytes

    bench = build_testbench(DUT_PATH)
    lowered = compile_native(
        bench,
        lowerings={
            "s_axis": AXIStreamSourceLowering(beats=beats, data_width=8),
            "m_axis": AXIStreamSinkLowering(n_beats=len(beats), data_width=8),
        },
    )
    results = lowered.batch_run(cycles=512, reset_cycles=4)


@pytest.mark.skipif(not DUT_PATH.exists(), reason="axis_register.v vendor file not present")
def test_axis_register_prng_stress():
    """10 000-beat PRNG stress test: end-to-end data integrity via compile_native.

    Both source and sink use the same PRNG seed so the sink can verify every
    beat without per-beat capture regs.  Both pause generators are active.
    """
    n_beats = 10_000
    seed = 0xDEAD_BEEF

    bench = build_testbench(DUT_PATH)
    lowered = compile_native(
        bench,
        lowerings={
            "s_axis": AXIStreamSourceLowering(
                n_prng_beats=n_beats,
                data_prng_seed=seed,
                data_width=8,
                prng_bits=4,
                pause_threshold=5,
                prng_seed=0x1234,
            ),
            "m_axis": AXIStreamSinkLowering(
                n_beats=n_beats,
                data_width=8,
                data_prng_seed=seed,
                prng_bits=4,
                pause_threshold=5,
                prng_seed=0x5678,
            ),
        },
    )
    results = lowered.batch_run(cycles=60_000, reset_cycles=4)

    assert results["m_axis_snk_done"] == 1, "sink FSM never flagged done"
    assert results["m_axis_snk_err_flag"] == 0, f"PRNG data mismatch: err_cnt={results['m_axis_snk_err_cnt']}"
