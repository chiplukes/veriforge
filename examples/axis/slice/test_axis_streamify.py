"""axis_streamify demo: a plain pipeline, decorated into a full AXI-Stream module.

``build_demo_pipeline`` below knows nothing about AXI-Stream -- no
``tready``, no slices, no backpressure. It's an ordinary 3-stage
ce-gated register pipeline, exactly the "plain pipeline inside" shape from
notes/hdl_guide.md §3, computing::

    y = ((x + 1) ^ 0x0F0F) + 5      (mod 2**DATA_WIDTH at every stage)

``@axis_streamify(data_width=DATA_WIDTH)`` is what makes it a real
``s_axis``/``m_axis`` module -- see axis_streamify.py for how.

``build_running_sum_pipeline`` below is a second demo exercising the two
optional features: a ``tuser`` "channel" tag that just rides through
unchanged, and a start-of-frame bit (also in ``tuser``) that locally resets
a running-sum accumulator at each frame boundary.

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


def _sof_user_sequence(channel: int, n_beats: int) -> list[int]:
    """tuser per beat for a frame on `channel`: bit 0 sof (beat 0 only), bits [2:1] channel."""
    return [(channel << 1) | (1 if i == 0 else 0) for i in range(n_beats)]


def _running_sum_expected(frame_data: list[int]) -> list[int]:
    """Python-side reference for build_running_sum_pipeline: resets on beat 0 of each frame."""
    expected = []
    acc = 0
    for i, x in enumerate(frame_data):
        acc = x if i == 0 else (acc + x) & _MASK
        expected.append(acc)
    return expected


@axis_streamify(data_width=DATA_WIDTH)
def build_demo_pipeline(m, clk, rst, ce, tdata, tvalid, tlast, sideband, sof):
    """y = ((x + 1) ^ 0x0F0F) + 5, three pipeline stages.

    Ordinary ce-gated registers, stage-tagged ``_p1``/``_p2``/``_p3`` per
    notes/hdl_guide.md §1's naming convention; ``tvalid``/``tlast`` ride
    along under the same ``ce``, each stage folding ``rst`` into its own
    ``tvalid`` per §3's staged-pipeline snippet (a bubble propagates
    through rather than needing every stage cleared at once). No sideband
    fields or ``sof`` configured for this one (``sideband`` is always ``{}``,
    ``sof`` is always the literal ``0``) -- see ``build_running_sum_pipeline``
    below for those.
    """
    del sideband, sof  # unused: this pipeline doesn't propagate sideband or use sof

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

    return data_p3, tvalid_p3, tlast_p3, {}


TUSER_WIDTH = 3  # bit 0: start-of-frame marker; bits [2:1]: arbitrary "channel" tag (0-3)


@axis_streamify(data_width=DATA_WIDTH, tuser_width=TUSER_WIDTH, sof_tuser_bit=0)
def build_running_sum_pipeline(m, clk, rst, ce, tdata, tvalid, tlast, sideband, sof):
    """Per-frame running sum, with tuser (a "channel" tag) passed straight through.

    ``acc`` resets to the current beat's ``tdata`` on ``rst | sof`` --
    exactly the gfwx-fpga AGENTS.md convention this feature exists for:
    "prefer local, implicit resets from the pixel stream itself -- deassert
    on tuser == 1 (SOF) with tvalid" -- rather than needing a sideband
    frame-boundary signal threaded in some other way. ``sof`` here is
    driven by tuser bit 0; the *other* bits of the same tuser value (the
    "channel" tag) still ride through ``sideband`` completely unchanged,
    proving ``sof`` is a read of tuser, not a consuming transform of it.

    Caught by ``test_sideband_and_sof_backpressure`` while writing this
    demo, worth calling out explicitly: ``ce`` means "the pipeline may
    advance if it has a beat," not "there is a beat this cycle" -- it's
    derived from the *output* slice's readiness (§3.3), which has nothing
    to do with whether the *input* side actually has valid data right now.
    Under source-side back-pressure (``s_axis.pause``), ``ce`` is
    frequently high on a cycle where ``tvalid`` is low (a bubble -- the
    input slice simply has nothing new to offer yet). A stateless
    per-beat transform (``build_demo_pipeline`` above) doesn't care:
    whatever it computes on a bubble is discarded downstream once
    ``tvalid_pN`` correctly propagates to 0. A *stateful* stage like this
    accumulator absolutely does care -- update ``acc`` on a bubble and
    it's now been corrupted by an accepted-looking add of stale/held
    ``tdata``, corruption that then carries into every subsequent real
    beat. Hence the explicit ``with m.if_(tvalid):`` gate below, in
    addition to ``ce``.
    """
    acc = m.reg("acc", width=DATA_WIDTH, init=0)
    tvalid_p1 = m.reg("tvalid_p1", init=0)
    tlast_p1 = m.reg("tlast_p1", init=0)
    tuser_p1 = m.reg("tuser_p1", width=TUSER_WIDTH, init=0)

    with m.always(posedge(clk)):
        with m.if_(ce):
            with m.if_(tvalid):
                with m.if_(rst | sof):
                    acc <<= tdata
                with m.else_():
                    acc <<= acc + tdata
            # else: bubble -- no real beat this cycle, hold acc unchanged.
            tvalid_p1 <<= ~rst & tvalid
            tlast_p1 <<= tlast
            tuser_p1 <<= sideband["tuser"]

    return acc, tvalid_p1, tlast_p1, {"tuser": tuser_p1}


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


def test_sideband_and_sof(vcd: Path | None = None) -> None:
    """Running-sum pipeline: sof-driven reset per frame, tuser channel tag propagated.

    tuser bit 0 is the sof marker (set only on each frame's first beat);
    bits [2:1] are an arbitrary per-frame "channel" tag (0-3). Verifies
    both: the accumulator restarts at each frame boundary (driven by sof,
    not by tlast or any explicit frame-length knowledge), and tuser arrives
    at m_axis byte-for-byte identical to what was sent, channel bits
    included.
    """
    # (data, channel) per frame -- sof is derived automatically: beat 0 of
    # every frame gets sof=1, the rest sof=0.
    FRAMES = [
        ([10, 20, 30], 0),
        ([100, 200], 1),
        ([1, 2, 3, 4], 3),
    ]

    top, design = build_running_sum_pipeline(name="demo_running_sum")
    bench = Testbench(top, design=design)

    with bench.run(vcd=vcd):
        bench.reset_all()
        s_axis = bench.iface("s_axis")
        m_axis = bench.iface("m_axis")

        for frame_data, channel in FRAMES:
            s_axis.put(frame_data, user=_sof_user_sequence(channel, len(frame_data)))

        for frame_data, channel in FRAMES:
            user = _sof_user_sequence(channel, len(frame_data))
            expected = _running_sum_expected(frame_data)
            got = m_axis.expect(expected, user=user, timeout=500)
            print(f"  channel={channel}: {frame_data} -> running sum {list(got.data)}, tuser {list(got.user)}")

    print("test_sideband_and_sof: PASSED\n")


def test_sideband_and_sof_backpressure(vcd: Path | None = None) -> None:
    """Same running-sum + sof + tuser pipeline, under heavy back-pressure both directions.

    Proves sof/tuser propagation survives exactly the same stall stress
    test_backpressure puts the plain pipeline through -- the sideband path
    isn't a happy-path-only shortcut.
    """
    FRAMES = [
        ([5, 10, 15, 20], 0),
        ([1, 1, 1, 1, 1, 1], 2),
    ]

    top, design = build_running_sum_pipeline(name="demo_running_sum_backpressure")
    bench = Testbench(top, design=design)

    with bench.run(vcd=vcd):
        bench.reset_all()
        s_axis = bench.iface("s_axis")
        m_axis = bench.iface("m_axis")

        s_axis.pause = PauseGenerator(1, 2, seed=41)
        m_axis.pause = PauseGenerator(1, 2, seed=43)

        for frame_data, channel in FRAMES:
            s_axis.put(frame_data, user=_sof_user_sequence(channel, len(frame_data)))

        for frame_data, channel in FRAMES:
            user = _sof_user_sequence(channel, len(frame_data))
            expected = _running_sum_expected(frame_data)
            got = m_axis.expect(expected, user=user, timeout=2000)
            print(f"  channel={channel}: running sum {list(got.data)} OK (heavy back-pressure both directions)")

    print("test_sideband_and_sof_backpressure: PASSED\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--vcd", type=Path, default=None, help="Optional VCD output path.")
    args = parser.parse_args()

    show_plan()
    test_basic(vcd=args.vcd)
    test_backpressure(vcd=args.vcd)
    test_sideband_and_sof(vcd=args.vcd)
    test_sideband_and_sof_backpressure(vcd=args.vcd)
    print("All tests passed.")


if __name__ == "__main__":
    main()
