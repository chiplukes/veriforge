"""AXI-Stream skid buffer — scaffold-to-simulation walkthrough.

Demonstrates the full veriforge testbench-generation workflow for a DUT with
one AXI-Stream input (s_axis) and one AXI-Stream output (m_axis):

  Step 1 — inspect the inferred TestbenchPlan (``show_plan()``).
  Step 2 — send multiple frames with independent stimulus/checking (``test_basic()``).
  Step 3 — stress back-pressure handling with PauseGenerator (``test_backpressure()``).

The two test functions show the key patterns that the scaffold generator emits:

  * ``put()`` pre-loads frames into the source queue — no clock steps yet.
  * ``get()`` / ``expect()`` drain frames from the sink, stepping the clock
    internally until each frame's ``tlast`` beat is consumed.
  * ``tlast=1`` is set automatically on the last beat of every ``put()`` call.
  * ``iface.pause = PauseGenerator(num, denom)`` adds random flow-control
    events: on a source it gates ``tvalid``; on a sink it gates ``tready``.

Run from the repository root:

    uv run python examples/axis_skid_buffer/test_axis_skid_buf.py
    uv run python examples/axis_skid_buffer/test_axis_skid_buf.py --vcd build/skid.vcd

See ``examples/axis_skid_buffer/README.md`` for the step-by-step walkthrough.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_file
from veriforge.sim.bench import Testbench
from veriforge.sim.endpoints import PauseGenerator

DUT_PATH = Path(__file__).parent / "axis_skid_buf.v"


# ---------------------------------------------------------------------------
# Step 1: inspect the inferred plan
# ---------------------------------------------------------------------------


def show_plan() -> None:
    """Print the TestbenchPlan that veriforge infers for the skid buffer."""
    design = parse_file(DUT_PATH)
    bench = Testbench(design.modules[0], design=design)
    print("=== Inferred TestbenchPlan ===")
    print(bench.plan.summary())
    print()


# ---------------------------------------------------------------------------
# Step 2: basic multi-frame test (pre-load / drain pattern)
# ---------------------------------------------------------------------------


def test_basic(vcd: Path | None = None) -> None:
    """Send 3 frames and verify each one arrives in order.

    The pre-load / drain pattern:
      1. Call put() for ALL input frames first — no clock steps happen.
      2. Call get() / expect() for each output frame — the source feeds beats
         automatically as get() steps the simulation clock.

    This separates stimulus generation from output checking, which is the
    correct structure for a reusable testbench.
    """
    FRAMES = [
        [0x10, 0x11, 0x12, 0x13],  # 4-byte frame
        [0xA0, 0xA1, 0xA2],        # 3-byte frame
        [0xFF],                     # 1-byte frame
    ]

    design = parse_file(DUT_PATH)
    bench = Testbench(design.modules[0], design=design)

    with bench.run(vcd=vcd):
        bench.reset_all()

        s_axis = bench.iface("s_axis")
        m_axis = bench.iface("m_axis")

        # Queue all input frames — tlast=1 is set on the last beat automatically.
        for frame_data in FRAMES:
            s_axis.put(frame_data)

        # Drain output independently; each expect() steps the clock until the
        # frame's tlast beat is consumed and the frame is complete.
        for expected_data in FRAMES:
            m_axis.expect(expected_data, timeout=200)
            print(f"  received {expected_data}")

    print("test_basic: PASSED\n")


# ---------------------------------------------------------------------------
# Step 3: back-pressure stress test
# ---------------------------------------------------------------------------


def test_backpressure(vcd: Path | None = None) -> None:
    """Same frames with random tready stalls on the output.

    PauseGenerator(1, 3) drives tready low ~33% of cycles, stressing the
    DUT's back-pressure path. All frames must still arrive in the same order
    with the same content — just requiring more clock cycles.
    """
    FRAMES = [
        list(range(8)),      # 0x00–0x07
        list(range(8, 16)),  # 0x08–0x0F
        list(range(16, 24)), # 0x10–0x17
    ]

    design = parse_file(DUT_PATH)
    bench = Testbench(design.modules[0], design=design)

    with bench.run(vcd=vcd):
        bench.reset_all()

        s_axis = bench.iface("s_axis")
        m_axis = bench.iface("m_axis")

        # Random back-pressure on the output: tready goes low ~33% of cycles.
        m_axis.pause = PauseGenerator(1, 3, seed=42)

        for frame_data in FRAMES:
            s_axis.put(frame_data)

        for expected_data in FRAMES:
            frame = m_axis.get(timeout=500)
            got = list(frame.data)
            assert got == expected_data, f"mismatch: got {got}, expected {expected_data}"
            print(f"  received {got} (with back-pressure)")

    print("test_backpressure: PASSED\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--vcd", type=Path, default=None, help="Optional VCD output path.")
    args = parser.parse_args()

    show_plan()
    test_basic(vcd=args.vcd)
    test_backpressure(vcd=args.vcd)
    print("All tests passed.")


if __name__ == "__main__":
    main()
