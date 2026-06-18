"""Testbench demos for the axis_adapter width-conversion IP (verilog-axis).

Demonstrates two adapter configurations:
  * 8→16 upsize  (axis_adapter_8to16_wrap): pairs of input bytes are packed
    into one 16-bit output beat — LSB carries the first byte received.
  * 16→8 downsize (axis_adapter_16to8_wrap): each 16-bit input beat is split
    into two 8-bit output beats — low byte first.

Three runnable demos:
  demo 1 — Python-stepped, 8→16 upsize: frames of 8-bit bytes are sent and
            the packed 16-bit output is verified word-for-word.
  demo 2 — compile_native, 8→16 upsize: small ROM source + capture sink;
            confirms the exact 16-bit output packing in the engine.
  demo 3 — compile_native PRNG stress, 16→8 downsize: 2 000 8-bit output
            beats verified entirely inside the compiled engine.

CLI recipe to regenerate a bench scaffold (adapt as needed):
    veriforge generate-python-testbench \\
        examples/taxi/vendor/verilog-axis/axis_adapter.v \\
        --style bench --enhanced

Data packing convention (axis_adapter.v):
  Upsize  (8→16): output[7:0]  = first input byte received
                  output[15:8] = second input byte received
  Downsize(16→8): first output byte  = input[7:0]   (low byte)
                  second output byte = input[15:8]  (high byte)
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).parent
_VENDOR = _HERE.parent / "vendor" / "verilog-axis"
_WRAP = _HERE.parent / "wrappers"

ADAPTER_V = _VENDOR / "axis_adapter.v"
WRAP_8TO16 = _WRAP / "axis_adapter_8to16_wrap.v"
WRAP_16TO8 = _WRAP / "axis_adapter_16to8_wrap.v"

# ---------------------------------------------------------------------------
# PRNG helper — must match the 32-bit Galois LFSR in lowering.py exactly.
# ---------------------------------------------------------------------------

_LFSR_POLY_32 = 0xD0000001


def _lfsr_step(state: int) -> int:
    """One step of the 32-bit Galois LFSR used by the native lowerings."""
    if state == 0:
        return 0xACE1
    if state & 1:
        return ((state >> 1) ^ _LFSR_POLY_32) & 0xFFFF_FFFF
    return (state >> 1) & 0xFFFF_FFFF


def prng_rom_for_downsize(seed: int, n_output_8bit_beats: int) -> list[int]:
    """Build a 16-bit ROM for the downsize source that produces the 8-bit PRNG sequence.

    The 16→8 downscaler emits input[7:0] first, then input[15:8].
    So to produce PRNG bytes lfsr_0, lfsr_1, lfsr_2, lfsr_3, …:
        ROM[k] = lfsr_{2k}[7:0]  |  (lfsr_{2k+1}[7:0] << 8)

    Args:
        seed: The same seed passed to AXIStreamSinkLowering(data_prng_seed=seed).
        n_output_8bit_beats: Total 8-bit output beats expected (must be even).

    Returns:
        List of 16-bit integers for AXIStreamSourceLowering(beats=..., data_width=16).
    """
    assert n_output_8bit_beats % 2 == 0, "n_output_8bit_beats must be even for 16→8 downsize"
    state = seed if seed != 0 else 0xACE1
    rom: list[int] = []
    for _ in range(n_output_8bit_beats // 2):
        lo = state & 0xFF  # first 8-bit output beat (tdata[7:0] of 16-bit input)
        state = _lfsr_step(state)
        hi = state & 0xFF  # second 8-bit output beat (tdata[15:8] of 16-bit input)
        state = _lfsr_step(state)
        rom.append(lo | (hi << 8))
    return rom


# ---------------------------------------------------------------------------
# Demo 1 — Python-stepped 8→16 upsize
# ---------------------------------------------------------------------------


def demo_axis_adapter_upsize_python():
    """Python-stepped simulation: 8-bit frames packed into 16-bit output words."""
    from veriforge.project import build_testbench

    # Two frames: 2 bytes → 1 output word, 4 bytes → 2 output words
    frames_in = [
        [0x11, 0x22],
        [0xAA, 0xBB, 0xCC, 0xDD],
    ]
    # Expected 16-bit words (first byte → low, second byte → high)
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

    print(f"axis_adapter_8to16 passed: {len(frames_in)} frames packed correctly into 16-bit output")


# ---------------------------------------------------------------------------
# Demo 2 — compile_native 8→16 upsize with ROM source + capture sink
# ---------------------------------------------------------------------------


def demo_axis_adapter_upsize_native():
    """compile_native upsize: verify exact 16-bit packing of 4 input bytes."""
    from veriforge.project import build_testbench
    from veriforge.sim.bench import AXIStreamSinkLowering, AXIStreamSourceLowering, compile_native

    # 4 input bytes → 2 output 16-bit words
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

    print(f"axis_adapter_8to16 compile_native passed: {input_bytes} → {[hex(w) for w in got]}")


# ---------------------------------------------------------------------------
# Demo 3 — compile_native PRNG stress 16→8 downsize
# ---------------------------------------------------------------------------


def demo_axis_adapter_downsize_prng_stress():
    """PRNG stress test (16→8 downsize): 2 000 beats verified inside the engine.

    Strategy:
      1. Precompute N=2 000 8-bit PRNG values from seed S.
      2. Pack pairs into N/2=1 000 16-bit ROM entries for the source.
      3. The downscaler splits each 16-bit input back into two 8-bit beats
         (low byte first), reconstructing the 8-bit PRNG sequence exactly.
      4. AXIStreamSinkLowering with data_prng_seed=S checks every beat
         against the running LFSR — no per-beat capture registers needed.
      5. Both pause generators are active on source and sink to stress the
         handshake logic under back-pressure.

    The entire verification loop runs inside the compiled C simulator;
    Python only inspects the final error flag after batch_run completes.
    """
    from veriforge.project import build_testbench
    from veriforge.sim.bench import AXIStreamSinkLowering, AXIStreamSourceLowering, compile_native

    n_output_beats = 2_000  # total 8-bit beats at the downscaler output
    seed = 0xCAFE_F00D

    rom = prng_rom_for_downsize(seed, n_output_beats)

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
    # Budget: 2 000 beats × 2 cycles avg + generous pause headroom
    results = lowered.batch_run(cycles=20_000, reset_cycles=4)

    assert results["m_axis_snk_done"] == 1, "sink FSM never flagged done"
    assert results["m_axis_snk_err_flag"] == 0, (
        f"PRNG data mismatch after downsize: err_cnt={results['m_axis_snk_err_cnt']}"
    )

    print(f"axis_adapter_16to8 PRNG stress passed: {n_output_beats} beats verified with seed=0x{seed:08X}")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if not ADAPTER_V.exists():
        raise SystemExit(f"Missing vendor file: {ADAPTER_V}")
    if not WRAP_8TO16.exists() or not WRAP_16TO8.exists():
        raise SystemExit("Missing wrapper files in examples/taxi/wrappers/")

    print("=== Demo 1: Python-stepped upsize (8→16) ===")
    demo_axis_adapter_upsize_python()

    print("\n=== Demo 2: compile_native upsize (8→16) ===")
    demo_axis_adapter_upsize_native()

    print("\n=== Demo 3: compile_native PRNG stress downsize (16→8) ===")
    demo_axis_adapter_downsize_prng_stress()
