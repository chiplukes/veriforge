"""Pytest tests for the taxi/axis_adapter example (Wave F).

Covers four scenarios for axis_adapter (verilog-axis, MIT):

1. Interface auto-detection — both wrappers are parsed and the detector
   finds s_axis + m_axis on each (different data widths per side).
2. Python-stepped simulation — two frames sent through the 8→16 upscaler,
   verified as correctly packed 16-bit output words.
3. compile_native upsize — four input bytes sent as a ROM through the
   8→16 wrapper; two 16-bit capture regs checked for exact packing.
4. compile_native PRNG stress downsize — 2 000-beat end-to-end integrity
   test using the 16→8 wrapper; input ROM is precomputed from the 8-bit
   PRNG sequence so the sink's LFSR checker stays byte-for-byte in sync
   despite the width conversion; entirely inside the compiled engine.
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

ADAPTER_V = VENDOR_DIR / "axis_adapter.v"
WRAP_8TO16 = WRAP_DIR / "axis_adapter_8to16_wrap.v"
WRAP_16TO8 = WRAP_DIR / "axis_adapter_16to8_wrap.v"

_FILES_EXIST = ADAPTER_V.exists() and WRAP_8TO16.exists() and WRAP_16TO8.exists()

# ---------------------------------------------------------------------------
# Shared PRNG helper — mirrors the logic in lowering.py and test_axis_adapter.py
# ---------------------------------------------------------------------------

_LFSR_POLY_32 = 0xD0000001


def _lfsr_step(state: int) -> int:
    if state == 0:
        return 0xACE1
    if state & 1:
        return ((state >> 1) ^ _LFSR_POLY_32) & 0xFFFF_FFFF
    return (state >> 1) & 0xFFFF_FFFF


def _prng_rom_for_downsize(seed: int, n_output_8bit_beats: int) -> list[int]:
    """Pack pairs of 8-bit PRNG values into 16-bit ROM entries.

    The 16→8 downscaler emits input[7:0] first, then input[15:8], so:
        ROM[k] = lfsr_{2k}[7:0]  |  (lfsr_{2k+1}[7:0] << 8)
    """
    assert n_output_8bit_beats % 2 == 0
    state = seed if seed != 0 else 0xACE1
    rom: list[int] = []
    for _ in range(n_output_8bit_beats // 2):
        lo = state & 0xFF
        state = _lfsr_step(state)
        hi = state & 0xFF
        state = _lfsr_step(state)
        rom.append(lo | (hi << 8))
    return rom


# ---------------------------------------------------------------------------
# Test 1 — interface detection on both wrappers
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FILES_EXIST, reason="axis_adapter vendor/wrapper files not present")
def test_axis_adapter_interfaces_detected():
    """Auto-detection finds s_axis (slave) and m_axis (master) on both wrappers."""
    for wrap_path, mod_name in [
        (WRAP_8TO16, "axis_adapter_8to16_wrap"),
        (WRAP_16TO8, "axis_adapter_16to8_wrap"),
    ]:
        design = parse_file(wrap_path)
        module = design.get_module(mod_name)
        assert module is not None, f"module {mod_name!r} not found"

        bundles = detect_axi_stream_interfaces(module)
        prefixes = {b.prefix: b.role for b in bundles}
        assert "s_axis" in prefixes, f"{mod_name}: missing s_axis; found {list(prefixes)}"
        assert "m_axis" in prefixes, f"{mod_name}: missing m_axis; found {list(prefixes)}"
        assert prefixes["s_axis"] == "slave", f"{mod_name}: s_axis role wrong"
        assert prefixes["m_axis"] == "master", f"{mod_name}: m_axis role wrong"


# ---------------------------------------------------------------------------
# Test 2 — Python-stepped upsize (8→16)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FILES_EXIST, reason="axis_adapter vendor/wrapper files not present")
def test_axis_adapter_8to16_python_stepped():
    """Two frames through the 8→16 upscaler are received as packed 16-bit words."""
    # (first byte → bits [7:0], second byte → bits [15:8])
    frames_in = [
        [0x11, 0x22],
        [0xAA, 0xBB, 0xCC, 0xDD],
    ]
    frames_out_expected = [
        [0x2211],
        [0xBBAA, 0xDDCC],
    ]

    bench = build_testbench([ADAPTER_V, WRAP_8TO16], top="axis_adapter_8to16_wrap")
    with bench.run():
        bench.reset_all()
        src = bench.iface("s_axis")
        snk = bench.iface("m_axis")
        for frame in frames_in:
            src.put(frame)
        src.wait_drain()
        received = [snk.get(timeout=2000) for _ in frames_in]

    for i, (pkt, expected_words) in enumerate(zip(received, frames_out_expected, strict=True)):
        got = list(pkt.data)
        assert got == expected_words, (
            f"upsize frame {i}: got {[hex(x) for x in got]}, expected {[hex(x) for x in expected_words]}"
        )


# ---------------------------------------------------------------------------
# Test 3 — compile_native upsize (8→16), ROM source + 16-bit capture sink
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FILES_EXIST, reason="axis_adapter vendor/wrapper files not present")
def test_axis_adapter_8to16_compile_native():
    """4-input-byte ROM through 8→16 adapter: capture sink holds correct 16-bit words."""
    # first byte → low half, second byte → high half of each output word
    input_bytes = [0xA0, 0xB1, 0xC2, 0xD3]
    expected_words = [0xB1A0, 0xD3C2]

    bench = build_testbench([ADAPTER_V, WRAP_8TO16], top="axis_adapter_8to16_wrap")
    lowered = compile_native(
        bench,
        lowerings={
            "s_axis": AXIStreamSourceLowering(beats=input_bytes, data_width=8),
            "m_axis": AXIStreamSinkLowering(n_beats=len(expected_words), data_width=16),
        },
    )
    results = lowered.batch_run(cycles=512, reset_cycles=4)

    assert results["m_axis_snk_done"] == 1, "sink FSM never flagged done"
    got = [results[f"m_axis_cap_{i}"] for i in range(len(expected_words))]
    assert got == expected_words, (
        f"packing mismatch: got {[hex(x) for x in got]}, expected {[hex(x) for x in expected_words]}"
    )


# ---------------------------------------------------------------------------
# Test 4 — compile_native PRNG stress downsize (16→8)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _FILES_EXIST, reason="axis_adapter vendor/wrapper files not present")
def test_axis_adapter_16to8_prng_stress():
    """2 000-beat PRNG stress test through the 16→8 downscaler — entirely in engine.

    The 16-bit source ROM is precomputed from the 8-bit PRNG seed so that
    after the downscaler splits each word (low byte first), the output byte
    stream exactly matches the sink's shadow LFSR.  This verifies both the
    width-conversion packing and the end-to-end data integrity without any
    per-beat capture registers.
    """
    n_output_beats = 2_000
    seed = 0xCAFE_F00D

    rom = _prng_rom_for_downsize(seed, n_output_beats)

    bench = build_testbench([ADAPTER_V, WRAP_16TO8], top="axis_adapter_16to8_wrap")
    lowered = compile_native(
        bench,
        lowerings={
            "s_axis": AXIStreamSourceLowering(
                beats=rom,
                data_width=16,
                prng_bits=4,
                pause_threshold=6,
                prng_seed=0xABCD,
            ),
            "m_axis": AXIStreamSinkLowering(
                n_beats=n_output_beats,
                data_width=8,
                data_prng_seed=seed,
                prng_bits=4,
                pause_threshold=6,
                prng_seed=0x1234,
            ),
        },
    )
    results = lowered.batch_run(cycles=20_000, reset_cycles=4)

    assert results["m_axis_snk_done"] == 1, "sink FSM never flagged done"
    assert results["m_axis_snk_err_flag"] == 0, (
        f"PRNG data mismatch after 16→8 downsize: err_cnt={results['m_axis_snk_err_cnt']}"
    )
