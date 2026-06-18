"""Pytest tests for the taxi/axis_broadcast example (Wave F-4).

Covers three scenarios for the ``axis_broadcast`` DUT wrapped with
``axis_broadcast_wrap`` (M_COUNT=2, flat-port):

1. Interface auto-detection — verifies the parser finds three AXI-Stream
   bundles: ``s_axis`` slave, ``m_axis_0`` master, ``m_axis_1`` master.
2. Python-stepped simulation — drives 4 frames into s_axis and asserts each
   frame is received identically on both m_axis_0 and m_axis_1.
3. compile_native — drives 8 beats via ``AXIStreamSourceLowering`` and
   two ``AXIStreamSinkLowering`` captures; validates both outputs match.
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
VENDOR_DIR = REPO_ROOT / "examples" / "taxi" / "vendor" / "verilog-axis"
WRAP_DIR = REPO_ROOT / "examples" / "taxi" / "wrappers"
BC_PATH = VENDOR_DIR / "axis_broadcast.v"
WRAP_PATH = WRAP_DIR / "axis_broadcast_wrap.v"

_FILES_EXIST = BC_PATH.exists() and WRAP_PATH.exists()


@pytest.mark.skipif(not _FILES_EXIST, reason="axis_broadcast vendor/wrapper files not present")
def test_broadcast_interfaces_detected():
    """Auto-detection finds s_axis (slave) plus m_axis_0 and m_axis_1 (master)."""
    design = parse_file(WRAP_PATH)
    module = design.get_module("axis_broadcast_wrap")
    assert module is not None

    bundles = detect_axi_stream_interfaces(module)
    prefixes = {b.prefix: b.role for b in bundles}
    assert "s_axis" in prefixes, f"missing s_axis; found {list(prefixes)}"
    assert "m_axis_0" in prefixes, f"missing m_axis_0; found {list(prefixes)}"
    assert "m_axis_1" in prefixes, f"missing m_axis_1; found {list(prefixes)}"
    assert prefixes["s_axis"] == "slave"
    assert prefixes["m_axis_0"] == "master"
    assert prefixes["m_axis_1"] == "master"


@pytest.mark.skipif(not _FILES_EXIST, reason="axis_broadcast vendor/wrapper files not present")
def test_broadcast_python_stepped():
    """4-frame Python-stepped run: both outputs receive identical payloads."""
    frames = [list(range(0x20 + i, 0x20 + i + (i + 2))) for i in range(4)]

    bench = build_testbench([BC_PATH, WRAP_PATH], top="axis_broadcast_wrap")
    with bench.run():
        bench.reset_all()
        src = bench.iface("s_axis")
        snk0 = bench.iface("m_axis_0")
        snk1 = bench.iface("m_axis_1")
        for frame in frames:
            src.put(frame)
        src.wait_drain()
        rx0 = [snk0.get(timeout=2000) for _ in frames]
        rx1 = [snk1.get(timeout=2000) for _ in frames]

    for i, (frame, p0, p1) in enumerate(zip(frames, rx0, rx1, strict=True)):
        assert list(p0.data) == frame, f"output0 frame {i}: got {list(p0.data)}, expected {frame}"
        assert list(p1.data) == frame, f"output1 frame {i}: got {list(p1.data)}, expected {frame}"


@pytest.mark.skipif(not _FILES_EXIST, reason="axis_broadcast vendor/wrapper files not present")
def test_broadcast_compile_native():
    """8-beat compile_native batch_run: both sink captures match source beats."""
    beats = list(range(0x30, 0x38))  # 8 bytes

    bench = build_testbench([BC_PATH, WRAP_PATH], top="axis_broadcast_wrap")
    lowered = compile_native(
        bench,
        lowerings={
            "s_axis": AXIStreamSourceLowering(beats=beats, data_width=8),
            "m_axis_0": AXIStreamSinkLowering(n_beats=len(beats), data_width=8),
            "m_axis_1": AXIStreamSinkLowering(n_beats=len(beats), data_width=8),
        },
    )
    results = lowered.batch_run(cycles=512, reset_cycles=4)

    assert results["m_axis_0_snk_done"] == 1, "sink-0 FSM never flagged done"
    assert results["m_axis_1_snk_done"] == 1, "sink-1 FSM never flagged done"
    cap0 = [results[f"m_axis_0_cap_{i}"] for i in range(len(beats))]
    cap1 = [results[f"m_axis_1_cap_{i}"] for i in range(len(beats))]
    assert cap0 == beats, f"output-0 mismatch: {cap0}"
    assert cap1 == beats, f"output-1 mismatch: {cap1}"
