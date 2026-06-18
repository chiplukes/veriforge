"""AXI-Stream register testbench — verilog-axis fallback (Verilog 2001).

DUT: axis_register from verilog-axis (flat Verilog 2001 module).

Note: The taxi project provides a SystemVerilog version (taxi_axis_register.sv)
that uses SV interface syntax (``taxi_axis_if``). veriforge parses Verilog
2005 only, so the taxi SV module cannot be simulated here.  This testbench
uses the equivalent flat-port Verilog 2001 module from the verilog-axis library.

Demo 1 — Python-stepped
-----------------------
Sends 10 frames of 1–10 beats each through the axis_register (skid buffer,
REG_TYPE=2).  Verifies each frame is received in order with no corruption.
Optionally applies source/sink backpressure via PauseGenerator.

Demo 2 — compile_native (fast path)
------------------------------------
Drives 20 single-beat frames via AXIStreamSourceLowering + AXIStreamSinkLowering
and validates all 20 captured beats using batch_run().

Run::

    uv run python examples/taxi/tb/test_axis_register.py
"""

from __future__ import annotations

import time
from pathlib import Path

from veriforge.project import build_testbench
from veriforge.sim.bench import (
    AXIStreamSinkLowering,
    AXIStreamSourceLowering,
    compile_native,
)
from veriforge.sim.endpoints import PauseGenerator

VENDOR_DIR = Path(__file__).parent.parent / "vendor"
DUT_PATH = VENDOR_DIR / "verilog-axis" / "axis_register.v"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _banner(title: str) -> None:
    width = 72
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


# ---------------------------------------------------------------------------
# Demo 1 — Python-stepped: 10 frames, optional backpressure
# ---------------------------------------------------------------------------


def demo_axis_register_python(vcd_dir: Path | None = None) -> None:
    """Send 10 frames (1–10 beats each) through the skid-buffer register.

    Variant A: no pause.
    Variant B: 1-in-3 source backpressure.
    Variant C: 1-in-3 sink backpressure.
    """
    _banner("Demo 1 — axis_register Python-stepped (10 frames, optional backpressure)")

    # 10 frames: frame i has beats [i, i+1, ..., i+i] (1 beat for frame 1, 10 for frame 10)
    frames = [list(range(i, i + i)) for i in range(1, 11)]

    variants = [
        ("no pause       (100% BW)", False, False),
        ("1/3 src pause  ( 67% BW)", PauseGenerator(1, 3, seed=42), False),
        ("1/3 snk pause  ( 67% BW)", False, PauseGenerator(1, 3, seed=99)),
    ]

    for label, src_pause, snk_pause in variants:
        vcd = vcd_dir / f"axis_reg_{label.split()[0]}.vcd" if vcd_dir else None
        bench = build_testbench(DUT_PATH)
        with bench.run(vcd=vcd):
            bench.reset_all()
            src = bench.iface("s_axis")
            snk = bench.iface("m_axis")
            if src_pause:
                src.pause = src_pause
            if snk_pause:
                snk.pause = snk_pause

            t0 = time.perf_counter()
            for frame in frames:
                src.put(frame)
            src.wait_drain()
            received = [snk.get() for _ in frames]
            elapsed_ms = (time.perf_counter() - t0) * 1000

        for i, (frame, pkt) in enumerate(zip(frames, received, strict=True)):
            assert list(pkt.data) == frame, f"frame {i}: got {list(pkt.data)}, expected {frame}"
        vcd_note = f"  → VCD: {vcd}" if vcd else ""
        print(f"  {label}: {elapsed_ms:>6.1f} ms for {sum(len(f) for f in frames)} beats{vcd_note}")


# ---------------------------------------------------------------------------
# Demo 2 — compile_native: 20 single-beat frames
# ---------------------------------------------------------------------------


def demo_axis_register_native() -> None:
    """Drive 20 single-beat frames and capture via engine-native FSMs.

    AXIStreamSourceLowering drives ``s_axis`` (DUT input slave port).
    AXIStreamSinkLowering   captures from ``m_axis`` (DUT output master port).
    """
    _banner("Demo 2 — axis_register compile_native (20 beats, batch_run)")

    beats = list(range(0x40, 0x54))  # 20 bytes: 0x40..0x53

    t0 = time.perf_counter()
    bench = build_testbench(DUT_PATH)
    lowered = compile_native(
        bench,
        lowerings={
            "s_axis": AXIStreamSourceLowering(beats=beats, data_width=8),
            "m_axis": AXIStreamSinkLowering(n_beats=len(beats), data_width=8),
        },
    )
    results = lowered.batch_run(cycles=1024, reset_cycles=4)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert results["m_axis_snk_done"] == 1, "sink FSM never flagged done"
    captured = [results[f"m_axis_cap_{i}"] for i in range(len(beats))]
    assert captured == beats, f"capture mismatch: {captured}"

    print(f"  20-beat loopback via compile_native: {elapsed_ms:>6.1f} ms  PASSED")
    print(f"  Captured: {[hex(b) for b in captured[:8]]} ...")


# ---------------------------------------------------------------------------
# Demo 3 — compile_native PRNG stress: 10 000 beats, both pause generators
# ---------------------------------------------------------------------------


def demo_axis_register_prng_stress() -> None:
    """Drive 10 000 PRNG beats and verify data integrity entirely in the simulator.

    AXIStreamSourceLowering in PRNG mode drives ``tdata`` from a 32-bit Galois
    LFSR.  AXIStreamSinkLowering in PRNG check mode runs a matching shadow LFSR
    and flags any mismatch.  Both pause generators are active (~33 % rate each)
    so the register experiences realistic back-pressure throughout.

    No Python iteration, no per-beat capture array — the entire 10 K beat run
    is executed in one batch_run() call, fully inside the compiled engine.
    """
    _banner("Demo 3 — axis_register compile_native PRNG stress (10 000 beats)")

    n_beats = 10_000
    seed = 0xDEAD_BEEF

    t0 = time.perf_counter()
    bench = build_testbench(DUT_PATH)
    lowered = compile_native(
        bench,
        lowerings={
            "s_axis": AXIStreamSourceLowering(
                n_prng_beats=n_beats,
                data_prng_seed=seed,
                data_width=8,
                prng_bits=4,
                pause_threshold=5,  # ~31 % source pause rate
                prng_seed=0x1234,
            ),
            "m_axis": AXIStreamSinkLowering(
                n_beats=n_beats,
                data_width=8,
                data_prng_seed=seed,
                prng_bits=4,
                pause_threshold=5,  # ~31 % sink back-pressure rate
                prng_seed=0x5678,
            ),
        },
    )
    results = lowered.batch_run(cycles=60_000, reset_cycles=4)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert results["m_axis_snk_done"] == 1, "sink FSM never flagged done after 60 K cycles"
    assert results["m_axis_snk_err_flag"] == 0, f"PRNG data mismatch detected! err_cnt={results['m_axis_snk_err_cnt']}"

    print(f"  {n_beats:,} beats × 8-bit PRNG loopback: {elapsed_ms:>6.1f} ms  PASSED")
    print(f"  (err_cnt={results['m_axis_snk_err_cnt']}, done={results['m_axis_snk_done']})")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="axis_register testbench demos")
    parser.add_argument("--vcd", metavar="DIR", help="write VCD traces to DIR")
    args = parser.parse_args()

    vcd_dir = Path(args.vcd) if args.vcd else None
    if vcd_dir:
        vcd_dir.mkdir(parents=True, exist_ok=True)

    demo_axis_register_python(vcd_dir)
    demo_axis_register_native()
    demo_axis_register_prng_stress()
    print("\nAll axis_register demos passed.")
