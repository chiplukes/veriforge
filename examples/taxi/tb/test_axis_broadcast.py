"""AXI-Stream broadcast testbench — flat-port wrapper (Verilog 2001).

DUT: axis_broadcast from verilog-axis, wrapped with axis_broadcast_wrap.v.
The wrapper unpacks the M_COUNT=2 packed output buses into separate
m_axis_0_* and m_axis_1_* ports for single-domain testbench use.

Demo 1 — Python-stepped
-----------------------
Sends 5 frames into s_axis and verifies each frame arrives on both
m_axis_0 (output 0) and m_axis_1 (output 1) with identical payload.

Demo 2 — compile_native (fast path)
-------------------------------------
Drives 8 single-beat frames via AXIStreamSourceLowering and validates
that both AXIStreamSinkLowering captures produce identical results.

Run::

    uv run python examples/taxi/tb/test_axis_broadcast.py
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

VENDOR_DIR = Path(__file__).parent.parent / "vendor" / "verilog-axis"
WRAP_DIR = Path(__file__).parent.parent / "wrappers"

_BC_FILES = [VENDOR_DIR / "axis_broadcast.v", WRAP_DIR / "axis_broadcast_wrap.v"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _banner(title: str) -> None:
    width = 72
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


# ---------------------------------------------------------------------------
# Demo 1 — Python-stepped: 5 frames, both outputs receive identical copies
# ---------------------------------------------------------------------------


def demo_broadcast_python(vcd_dir: Path | None = None) -> None:
    """Send 5 frames; assert both broadcast outputs deliver identical payloads.

    Variant A: no pause.
    Variant B: 1-in-3 source backpressure.
    Variant C: 1-in-3 back-pressure on sink 0 (demonstrates credit stalling).
    """
    _banner("Demo 1 — axis_broadcast Python-stepped (5 frames, both outputs)")

    frames = [list(range(0x20 + i, 0x20 + i + (i + 2))) for i in range(5)]

    variants = [
        ("no pause       (100% BW)", False, False),
        ("1/3 src pause  ( 67% BW)", PauseGenerator(1, 3, seed=42), False),
        ("1/3 snk0 pause ( 67% BW)", False, PauseGenerator(1, 3, seed=55)),
    ]

    for label, src_pause, snk_pause in variants:
        vcd = vcd_dir / f"broadcast_{label.split()[0]}.vcd" if vcd_dir else None
        bench = build_testbench(_BC_FILES, top="axis_broadcast_wrap")
        with bench.run(vcd=vcd):
            bench.reset_all()
            src = bench.iface("s_axis")
            snk0 = bench.iface("m_axis_0")
            snk1 = bench.iface("m_axis_1")
            if src_pause:
                src.pause = src_pause
            if snk_pause:
                snk0.pause = snk_pause

            t0 = time.perf_counter()
            for frame in frames:
                src.put(frame)
            src.wait_drain()
            rx0 = [snk0.get(timeout=2000) for _ in frames]
            rx1 = [snk1.get(timeout=2000) for _ in frames]
            elapsed_ms = (time.perf_counter() - t0) * 1000

        for i, (frame, p0, p1) in enumerate(zip(frames, rx0, rx1, strict=True)):
            assert list(p0.data) == frame, f"output0 frame {i}: got {list(p0.data)}, expected {frame}"
            assert list(p1.data) == frame, f"output1 frame {i}: got {list(p1.data)}, expected {frame}"
        vcd_note = f"  → VCD: {vcd}" if vcd else ""
        print(f"  {label}: {elapsed_ms:>6.1f} ms for {sum(len(f) for f in frames)} beats{vcd_note}")


# ---------------------------------------------------------------------------
# Demo 2 — compile_native: 8 single-beat frames, both outputs captured
# ---------------------------------------------------------------------------


def demo_broadcast_native() -> None:
    """Drive 8 single-beat frames; verify both captured outputs are identical."""
    _banner("Demo 2 — axis_broadcast compile_native (8 beats, both sinks)")

    beats = list(range(0x30, 0x38))  # 8 bytes: 0x30..0x37

    t0 = time.perf_counter()
    bench = build_testbench(_BC_FILES, top="axis_broadcast_wrap")
    lowered = compile_native(
        bench,
        lowerings={
            "s_axis": AXIStreamSourceLowering(beats=beats, data_width=8),
            "m_axis_0": AXIStreamSinkLowering(n_beats=len(beats), data_width=8),
            "m_axis_1": AXIStreamSinkLowering(n_beats=len(beats), data_width=8),
        },
    )
    results = lowered.batch_run(cycles=1024, reset_cycles=4)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert results["m_axis_0_snk_done"] == 1, "sink-0 FSM never flagged done"
    assert results["m_axis_1_snk_done"] == 1, "sink-1 FSM never flagged done"
    cap0 = [results[f"m_axis_0_cap_{i}"] for i in range(len(beats))]
    cap1 = [results[f"m_axis_1_cap_{i}"] for i in range(len(beats))]
    assert cap0 == beats, f"output-0 mismatch: {cap0}"
    assert cap1 == beats, f"output-1 mismatch: {cap1}"

    print(f"  8-beat broadcast via compile_native: {elapsed_ms:>6.1f} ms  PASSED")
    print(f"  Output-0: {[hex(b) for b in cap0]}")
    print(f"  Output-1: {[hex(b) for b in cap1]}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="axis_broadcast testbench demos")
    parser.add_argument("--vcd", metavar="DIR", help="write VCD traces to DIR")
    args = parser.parse_args()

    vcd_dir = Path(args.vcd) if args.vcd else None
    if vcd_dir:
        vcd_dir.mkdir(parents=True, exist_ok=True)

    demo_broadcast_python(vcd_dir)
    demo_broadcast_native()
    print("\nAll axis_broadcast demos passed.")
