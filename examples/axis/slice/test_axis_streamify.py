"""axis_streamify demo: a plain pipeline, decorated into a full AXI-Stream module.

``build_demo_pipeline`` below knows nothing about AXI-Stream -- no
``tready``, no slices, no backpressure. It's an ordinary 3-stage
ce-gated register pipeline, exactly the "plain pipeline inside" shape from
notes/hdl_guide.md §3, computing::

    y = ((x + 1) ^ 0x0F0F) + 5      (mod 2**DATA_WIDTH at every stage)

``@axis_streamify(data_width=DATA_WIDTH)`` is what makes it a real
``s_axis``/``m_axis`` module -- see axis_streamify.py for how.

Run from the repository root:

    uv run python examples/axis/slice/test_axis_streamify.py
    uv run python examples/axis/slice/test_axis_streamify.py --vcd build/streamify.vcd
"""

from __future__ import annotations

import argparse
from pathlib import Path

from axis_streamify import axis_streamify

from veriforge.dsl import posedge
from veriforge.sim.bench import Testbench
from veriforge.sim.endpoints import PauseGenerator

DATA_WIDTH = 16
_MASK = (1 << DATA_WIDTH) - 1


def _expected(x: int) -> int:
    """Python-side reference for the pipeline's transform, stage by stage."""
    p1 = (x + 1) & _MASK
    p2 = p1 ^ 0x0F0F
    p3 = (p2 + 5) & _MASK
    return p3


@axis_streamify(data_width=DATA_WIDTH)
def build_demo_pipeline(m, clk, rst, ce, tdata, tvalid, tlast):
    """y = ((x + 1) ^ 0x0F0F) + 5, three pipeline stages.

    Ordinary ce-gated registers, stage-tagged ``_p1``/``_p2``/``_p3`` per
    notes/hdl_guide.md §1's naming convention; ``tvalid``/``tlast`` ride
    along under the same ``ce``, each stage folding ``rst`` into its own
    ``tvalid`` per §3's staged-pipeline snippet (a bubble propagates
    through rather than needing every stage cleared at once).
    """
    data_p1 = m.reg("data_p1", width=DATA_WIDTH, init=0)
    tvalid_p1 = m.reg("tvalid_p1", init=0)
    tlast_p1 = m.reg("tlast_p1", init=0)

    data_p2 = m.reg("data_p2", width=DATA_WIDTH, init=0)
    tvalid_p2 = m.reg("tvalid_p2", init=0)
    tlast_p2 = m.reg("tlast_p2", init=0)

    data_p3 = m.reg("data_p3", width=DATA_WIDTH, init=0)
    tvalid_p3 = m.reg("tvalid_p3", init=0)
    tlast_p3 = m.reg("tlast_p3", init=0)

    with m.always(posedge(clk)):
        with m.if_(ce):
            data_p1 <<= tdata + 1
            tvalid_p1 <<= ~rst & tvalid
            tlast_p1 <<= tlast

            data_p2 <<= data_p1 ^ 0x0F0F
            tvalid_p2 <<= ~rst & tvalid_p1
            tlast_p2 <<= tlast_p1

            data_p3 <<= data_p2 + 5
            tvalid_p3 <<= ~rst & tvalid_p2
            tlast_p3 <<= tlast_p2

    return data_p3, tvalid_p3, tlast_p3


def show_plan() -> None:
    """Print the TestbenchPlan for the streamified module."""
    top, design = build_demo_pipeline()
    bench = Testbench(top, design=design)
    print("=== Inferred TestbenchPlan ===")
    print(bench.plan.summary())
    print()


def test_basic(vcd: Path | None = None) -> None:
    """Send frames through the streamified pipeline, no stalls."""
    FRAMES = [
        [0x0000, 0x0001, 0x0002, 0x0003],
        [0xBEEF, 0xCAFE, 0x1234],
        [0xFFFF],
    ]

    top, design = build_demo_pipeline(name="demo_pipeline_basic")
    bench = Testbench(top, design=design)

    with bench.run(vcd=vcd):
        bench.reset_all()
        s_axis = bench.iface("s_axis")
        m_axis = bench.iface("m_axis")

        for frame_data in FRAMES:
            s_axis.put(frame_data)

        for frame_data in FRAMES:
            expected = [_expected(x) for x in frame_data]
            got = m_axis.expect(expected, timeout=200)
            print(f"  {[hex(x) for x in frame_data]} -> {[hex(y) for y in got.data]}")

    print("test_basic: PASSED\n")


def test_backpressure(vcd: Path | None = None) -> None:
    """Same pipeline, heavy stalls on both sides -- both slices must absorb it.

    This is the property the decorator is supposed to buy for free: the
    pipeline body above has zero backpressure logic of its own.
    """
    FRAMES = [list(range(20)), list(range(20, 40))]

    top, design = build_demo_pipeline(name="demo_pipeline_backpressure")
    bench = Testbench(top, design=design)

    with bench.run(vcd=vcd):
        bench.reset_all()
        s_axis = bench.iface("s_axis")
        m_axis = bench.iface("m_axis")

        s_axis.pause = PauseGenerator(1, 2, seed=3)
        m_axis.pause = PauseGenerator(1, 2, seed=17)

        for frame_data in FRAMES:
            s_axis.put(frame_data)

        for frame_data in FRAMES:
            expected = [_expected(x) for x in frame_data]
            got = m_axis.expect(expected, timeout=2000)
            print(f"  frame of {len(frame_data)} beats: OK (heavy back-pressure both directions)")

    print("test_backpressure: PASSED\n")


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
