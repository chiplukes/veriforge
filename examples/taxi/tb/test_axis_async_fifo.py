"""AXI-Stream async FIFO testbench — single-clock wrapper (Verilog 2001).

DUT: axis_async_fifo from verilog-axis, wrapped with axis_async_fifo_wrap.v.
The wrapper ties both CDC clock domains to a single clk/rst so the design
can be exercised with a single-domain testbench.

Demo 1 — Python-stepped
-----------------------
Sends 5 frames of increasing depth through the FIFO and verifies each is
received intact, with optional source/sink backpressure variants.

Demo 2 — compile_native (fast path)
-------------------------------------
Drives 10 single-beat frames via AXIStreamSourceLowering +
AXIStreamSinkLowering and validates all 10 captured beats using batch_run().

Demo 3 — compile_native PRNG stress
--------------------------------------
10 000-beat end-to-end data integrity test with independent pause generators
on source and sink.  The FIFO fills and drains repeatedly under back-pressure,
stressing the gray-code CDC pointer logic.  Verification runs entirely inside
the compiled C engine — no per-beat capture registers.

Run::

    uv run python examples/taxi/tb/test_axis_async_fifo.py
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

_FIFO_FILES = [VENDOR_DIR / "axis_async_fifo.v", WRAP_DIR / "axis_async_fifo_wrap.v"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _banner(title: str) -> None:
    width = 72
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


# ---------------------------------------------------------------------------
# Demo 1 — Python-stepped: 5 frames, optional backpressure
# ---------------------------------------------------------------------------


def demo_async_fifo_python(vcd_dir: Path | None = None) -> None:
    """Send 5 frames through the FIFO, verify payloads are preserved.

    Variant A: no pause.
    Variant B: 1-in-3 source backpressure.
    Variant C: 1-in-3 sink backpressure.
    """
    _banner("Demo 1 — axis_async_fifo Python-stepped (5 frames, optional backpressure)")

    frames = [list(range(0x10 + i, 0x10 + i + (i + 1))) for i in range(5)]

    variants = [
        ("no pause       (100% BW)", False, False),
        ("1/3 src pause  ( 67% BW)", PauseGenerator(1, 3, seed=42), False),
        ("1/3 snk pause  ( 67% BW)", False, PauseGenerator(1, 3, seed=99)),
    ]

    for label, src_pause, snk_pause in variants:
        vcd = vcd_dir / f"async_fifo_{label.split()[0]}.vcd" if vcd_dir else None
        bench = build_testbench(_FIFO_FILES, top="axis_async_fifo_wrap")
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
# Demo 2 — compile_native: 10 single-beat frames
# ---------------------------------------------------------------------------


def demo_async_fifo_native() -> None:
    """Drive 10 single-beat frames through the FIFO using engine-native FSMs."""
    _banner("Demo 2 — axis_async_fifo compile_native (10 beats, batch_run)")

    beats = list(range(0xA0, 0xAA))  # 10 bytes: 0xA0..0xA9

    t0 = time.perf_counter()
    bench = build_testbench(_FIFO_FILES, top="axis_async_fifo_wrap")
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

    print(f"  10-beat FIFO loopback via compile_native: {elapsed_ms:>6.1f} ms  PASSED")
    print(f"  Captured: {[hex(b) for b in captured[:8]]} ...")


# ---------------------------------------------------------------------------
# Demo 3 — compile_native PRNG stress: fills and drains the FIFO repeatedly
# ---------------------------------------------------------------------------


def demo_async_fifo_prng_stress() -> None:
    """10 000-beat PRNG stress: independent pause generators fill/drain the FIFO.

    Source pause seed (0x1111) and sink pause seed (0x2222) are deliberately
    different so source and sink run at different effective rates.  This causes
    the FIFO to oscillate between nearly-full and nearly-empty, exercising the
    gray-code CDC pointer logic far more thoroughly than a constant-rate test.

    Both source and sink use the same data PRNG seed so the sink's shadow LFSR
    can verify every beat without per-beat capture registers.
    """
    _banner("Demo 3 — axis_async_fifo compile_native PRNG stress (10 000 beats)")

    n_beats = 10_000
    seed = 0xDEAD_BEEF

    t0 = time.perf_counter()
    bench = build_testbench(_FIFO_FILES, top="axis_async_fifo_wrap")
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
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert results["m_axis_snk_done"] == 1, "sink FSM never flagged done"
    assert results["m_axis_snk_err_flag"] == 0, f"PRNG data mismatch: err_cnt={results['m_axis_snk_err_cnt']}"

    print(f"  {n_beats:,} beats with fill/drain stress: {elapsed_ms:>6.1f} ms  PASSED")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="axis_async_fifo testbench demos")
    parser.add_argument("--vcd", metavar="DIR", help="write VCD traces to DIR")
    args = parser.parse_args()

    vcd_dir = Path(args.vcd) if args.vcd else None
    if vcd_dir:
        vcd_dir.mkdir(parents=True, exist_ok=True)

    demo_async_fifo_python(vcd_dir)
    demo_async_fifo_native()
    demo_async_fifo_prng_stress()
    print("\nAll axis_async_fifo demos passed.")
