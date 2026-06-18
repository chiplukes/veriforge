"""Pytest tests for the taxi/axis_async_fifo example (Wave F-2).

Covers four scenarios for the ``axis_async_fifo`` DUT wrapped with
the single-clock ``axis_async_fifo_wrap``:

1. Interface auto-detection — verifies the parser finds exactly two AXI-Stream
   bundles (``s_axis`` slave, ``m_axis`` master) on the flat-port wrapper.
2. Python-stepped simulation — drives 5 frames through the FIFO and asserts
   received payloads are identical to sent payloads.
3. compile_native — drives 10 beats via ``AXIStreamSourceLowering`` /
   ``AXIStreamSinkLowering`` and validates the batch_run results.
4. PRNG stress — 10 000-beat end-to-end data integrity test; independent
   pause generators on source and sink cause the FIFO to fill and drain
   repeatedly, stressing the gray-code pointer logic across CDC boundaries.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import os

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
WRAP_PATH = WRAP_DIR / "axis_async_fifo_wrap.v"
FIFO_PATH = VENDOR_DIR / "axis_async_fifo.v"

_FILES_EXIST = FIFO_PATH.exists() and WRAP_PATH.exists()


@pytest.mark.skipif(not _FILES_EXIST, reason="axis_async_fifo vendor/wrapper files not present")
def test_async_fifo_interfaces_detected():
    """Auto-detection finds s_axis (slave) and m_axis (master) on the wrapper."""
    design = parse_file(WRAP_PATH)
    module = design.get_module("axis_async_fifo_wrap")
    assert module is not None

    bundles = detect_axi_stream_interfaces(module)
    prefixes = {b.prefix: b.role for b in bundles}
    assert "s_axis" in prefixes, f"missing s_axis; found {list(prefixes)}"
    assert "m_axis" in prefixes, f"missing m_axis; found {list(prefixes)}"
    assert prefixes["s_axis"] == "slave"
    assert prefixes["m_axis"] == "master"


@pytest.mark.skipif(not _FILES_EXIST, reason="axis_async_fifo vendor/wrapper files not present")
def test_async_fifo_python_stepped():
    """5-frame Python-stepped run: received payloads match sent payloads."""
    frames = [list(range(0x10 + i, 0x10 + i + (i + 1))) for i in range(5)]

    bench = build_testbench([FIFO_PATH, WRAP_PATH], top="axis_async_fifo_wrap")
    with bench.run():
        bench.reset_all()
        src = bench.iface("s_axis")
        snk = bench.iface("m_axis")
        for frame in frames:
            src.put(frame)
        src.wait_drain()
        received = [snk.get(timeout=2000) for _ in frames]

    for i, (frame, pkt) in enumerate(zip(frames, received, strict=True)):
        assert list(pkt.data) == frame, f"frame {i}: got {list(pkt.data)}, expected {frame}"


@pytest.mark.skipif(not _FILES_EXIST, reason="axis_async_fifo vendor/wrapper files not present")
def test_async_fifo_compile_native():
    """10-beat compile_native batch_run: all captured beats match source."""
    print(f"PID:{os.getpid()}")

    beats = list(range(0xA0, 0xAA))  # 10 bytes

    bench = build_testbench([FIFO_PATH, WRAP_PATH], top="axis_async_fifo_wrap")
    lowered = compile_native(
        bench,
        lowerings={
            "s_axis": AXIStreamSourceLowering(beats=beats, data_width=8),
            "m_axis": AXIStreamSinkLowering(n_beats=len(beats), data_width=8),
        },
    )
    results = lowered.batch_run(cycles=512, reset_cycles=4)

    assert results["m_axis_snk_done"] == 1, "sink FSM never flagged done"
    captured = [results[f"m_axis_cap_{i}"] for i in range(len(beats))]
    assert captured == beats, f"captured mismatch: {captured}"


@pytest.mark.skipif(not _FILES_EXIST, reason="axis_async_fifo vendor/wrapper files not present")
def test_async_fifo_prng_stress():
    """10 000-beat PRNG stress test: FIFO fills and drains repeatedly under back-pressure.

    Independent pause generators on source and sink run at different rates,
    causing the FIFO to alternately fill to capacity and drain to empty.
    This exercises the gray-code CDC pointer logic across many fill/drain
    cycles without requiring per-beat capture registers.
    """
    n_beats = 10_000
    seed = 0xDEAD_BEEF

    bench = build_testbench([FIFO_PATH, WRAP_PATH], top="axis_async_fifo_wrap")
    lowered = compile_native(
        bench,
        lowerings={
            "s_axis": AXIStreamSourceLowering(
                n_prng_beats=n_beats,
                data_prng_seed=seed,
                data_width=8,
                prng_bits=4,
                pause_threshold=5,
                prng_seed=0x1111,
            ),
            "m_axis": AXIStreamSinkLowering(
                n_beats=n_beats,
                data_width=8,
                data_prng_seed=seed,
                prng_bits=4,
                pause_threshold=5,
                prng_seed=0x2222,
            ),
        },
    )
    results = lowered.batch_run(cycles=60_000, reset_cycles=4)

    assert results["m_axis_snk_done"] == 1, "sink FSM never flagged done"
    assert results["m_axis_snk_err_flag"] == 0, (
        f"PRNG data mismatch in async FIFO: err_cnt={results['m_axis_snk_err_cnt']}"
    )
