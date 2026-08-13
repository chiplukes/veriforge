"""AXI-Stream full register slice / skid buffer -- testbench-framework walkthrough.

Demonstrates the veriforge Testbench workflow driven directly off a
DSL-built module (no intermediate ``.v`` file / ``parse_file`` round-trip
needed -- ``Module.build()`` already returns the model object ``Testbench``
wants):

  * ``test_basic()``            -- multi-frame, tuser+tlast, no stalls.
  * ``test_backpressure()``     -- PauseGenerator stalls on both sides at once.
  * ``test_parameter_variants()`` -- tuser_width=0 and a wider data/tuser
    combination, each run through the same stress pattern.
  * ``test_no_tlast_variant()`` -- has_tlast=False, driven through the same
    high-level ``bench.iface()`` API as every other test here, via
    ``relaxed_iface_signals``. ``AXIStreamSource``/``AXIStreamSink`` used to
    hard-require ``tlast``; both now treat it as optional (every accepted
    beat becomes its own one-beat frame when it's absent).
  * ``test_iface_variant_matches_raw()`` -- the ``m.interface()``-built
    module (axi_stream_slice_iface.py) behaves identically to the raw-port
    one for the same stimulus.
  * ``test_declarative_variant_matches_raw()`` -- the declarative ``ModuleSpec``-built
    module (axi_stream_slice_declarative.py) behaves identically to the raw-port
    one for the same stimulus.
  * ``test_declarative_iface_variant_matches_raw()`` -- the declarative AND
    interface-bound module (axi_stream_slice_declarative_iface.py) behaves
    identically too.

Run from the repository root:

    uv run python examples/axis/slice/test_axi_stream_slice.py
    uv run python examples/axis/slice/test_axi_stream_slice.py --vcd build/slice.vcd
"""

from __future__ import annotations

import argparse
from pathlib import Path

from axi_stream_slice import build_axi_stream_slice
from axi_stream_slice_iface import build_axi_stream_slice_iface
from axi_stream_slice_declarative import build_axi_stream_slice_declarative
from axi_stream_slice_declarative_iface import build_axi_stream_slice_declarative_iface

from veriforge.sim.bench import Testbench
from veriforge.sim.endpoints import PauseGenerator

# ---------------------------------------------------------------------------
# Step 1: inspect the inferred plan
# ---------------------------------------------------------------------------


def show_plan() -> None:
    """Print the TestbenchPlan veriforge infers for the DSL-built module."""
    dut = build_axi_stream_slice(data_width=16, tuser_width=2, has_tlast=True)
    bench = Testbench(dut)
    print("=== Inferred TestbenchPlan ===")
    print(bench.plan.summary())
    print()


# ---------------------------------------------------------------------------
# Step 2: basic multi-frame test, tuser + tlast both in play
# ---------------------------------------------------------------------------


def test_basic(vcd: Path | None = None) -> None:
    """Send frames with per-beat tuser and verify data + tuser arrive intact."""
    FRAMES = [
        ([0x10, 0x11, 0x12, 0x13], [1, 0, 2, 3]),
        ([0xA0, 0xA1, 0xA2], [0, 1, 1]),
        ([0xFF], [3]),
    ]

    dut = build_axi_stream_slice(data_width=16, tuser_width=2, has_tlast=True)
    bench = Testbench(dut)

    with bench.run(vcd=vcd):
        bench.reset_all()

        s_axis = bench.iface("s_axis")
        m_axis = bench.iface("m_axis")

        for data, user in FRAMES:
            s_axis.put(data, user=user)

        for data, user in FRAMES:
            m_axis.expect(data, user=user, timeout=200)
            print(f"  received {data} user={user}")

    print("test_basic: PASSED\n")


# ---------------------------------------------------------------------------
# Step 3: back-pressure stress test, both directions at once
# ---------------------------------------------------------------------------


def test_backpressure(vcd: Path | None = None) -> None:
    """Random stalls on BOTH s_axis_tvalid and m_axis_tready simultaneously.

    This is the scenario the skid register exists for: a beat can be
    accepted (s_axis_tvalid & s_axis_tready) on the very cycle the output
    register is still full and un-drained (m_axis_tvalid & ~m_axis_tready).
    All frames must still arrive in order with exact content.
    """
    FRAMES = [list(range(16)), list(range(16, 32)), list(range(32, 48))]

    dut = build_axi_stream_slice(data_width=16, tuser_width=0, has_tlast=True)
    bench = Testbench(dut)

    with bench.run(vcd=vcd):
        bench.reset_all()

        s_axis = bench.iface("s_axis")
        m_axis = bench.iface("m_axis")

        s_axis.pause = PauseGenerator(1, 2, seed=7)  # tvalid low ~50% of cycles
        m_axis.pause = PauseGenerator(1, 2, seed=13)  # tready low ~50% of cycles

        for frame_data in FRAMES:
            s_axis.put(frame_data)

        for expected_data in FRAMES:
            frame = m_axis.get(timeout=2000)
            got = list(frame.data)
            assert got == expected_data, f"mismatch: got {got}, expected {expected_data}"
            print(f"  received {got} (heavy back-pressure both directions)")

    print("test_backpressure: PASSED\n")


# ---------------------------------------------------------------------------
# Step 4: parameter variants -- tuser_width=0, wide data
# ---------------------------------------------------------------------------


def test_parameter_variants() -> None:
    """Exercise the optional-field combinations under the same stall pattern.

    ``has_tlast=False`` is deliberately NOT included here: veriforge's
    high-level ``AXIStreamProxy``/``AXIStreamSource`` endpoints currently
    hard-require a ``tlast`` signal on the DUT (see
    ``src/veriforge/sim/endpoints/axis_source.py::_required_signal``), even
    though the planner itself supports relaxed ("tlast-less AXIS")
    detection via ``relaxed_iface_signals``. So ``build_axi_stream_slice(...,
    has_tlast=False)`` is a real, working module -- it's the framework's
    ``bench.iface()`` convenience layer that can't drive it yet, not this
    component. Worth a separate follow-up in veriforge itself if tlast-less
    streams need first-class high-level testbench support; out of scope
    here.
    """
    variants = [
        dict(data_width=8, tuser_width=0, has_tlast=True, name="slice_no_tuser"),
        dict(data_width=32, tuser_width=4, has_tlast=True, name="slice_wide"),
    ]
    for kwargs in variants:
        dut = build_axi_stream_slice(**kwargs)
        bench = Testbench(dut)

        with bench.run():
            bench.reset_all()
            s_axis = bench.iface("s_axis")
            m_axis = bench.iface("m_axis")
            s_axis.pause = PauseGenerator(1, 3, seed=1)
            m_axis.pause = PauseGenerator(1, 3, seed=2)

            frame = list(range(20))
            s_axis.put(frame)
            got = m_axis.expect(frame, timeout=500)
            print(f"  {kwargs['name']}: OK ({len(got.data)} beats)")

    print("test_parameter_variants: PASSED\n")


# ---------------------------------------------------------------------------
# Step 4b: has_tlast=False, via the same high-level bench.iface() API
# ---------------------------------------------------------------------------


def test_no_tlast_variant() -> None:
    """has_tlast=False, driven through the same high-level bench.iface() API as everything else.

    Needs relaxed_iface_signals since tlast is a required signal for
    strict AXI-Stream auto-detection; the DUT itself is otherwise a normal
    module. With no tlast, AXIStreamSink treats every accepted beat as its
    own one-beat frame (see axis_sink.py's docstring), so this drains with
    n_beats individual get() calls rather than one multi-beat expect().
    """
    n_beats = 60
    dut = build_axi_stream_slice(data_width=8, tuser_width=0, has_tlast=False, name="slice_no_tlast")
    bench = Testbench(dut, overrides={"relaxed_iface_signals": {"axi_stream": ["tlast"]}})

    with bench.run():
        bench.reset_all()
        s_axis = bench.iface("s_axis")
        m_axis = bench.iface("m_axis")
        s_axis.pause = PauseGenerator(2, 5, seed=99)
        m_axis.pause = PauseGenerator(2, 5, seed=41)

        data = list(range(n_beats))
        s_axis.put(data)
        received = [m_axis.get(timeout=200).data[0] for _ in range(n_beats)]

    assert received == data, f"has_tlast=False mismatch: got {received}, expected {data}"
    print(f"  received {len(received)} beats, has_tlast=False, no mismatches")
    print("test_no_tlast_variant: PASSED\n")


# ---------------------------------------------------------------------------
# Step 5: interface-built variant behaves identically to the raw-port one
# ---------------------------------------------------------------------------


def test_iface_variant_matches_raw() -> None:
    """axi_stream_slice_iface.py (m.interface()-built) vs. the raw-port module."""
    FRAMES = [list(range(12)), list(range(12, 24))]

    for label, dut in [
        ("raw", build_axi_stream_slice(data_width=16, tuser_width=2, has_tlast=True)),
        ("iface", build_axi_stream_slice_iface(data_width=16, tuser_width=2)),
    ]:
        bench = Testbench(dut)
        with bench.run():
            bench.reset_all()
            s_axis = bench.iface("s_axis")
            m_axis = bench.iface("m_axis")
            s_axis.pause = PauseGenerator(1, 3, seed=5)
            m_axis.pause = PauseGenerator(1, 3, seed=11)

            for frame_data in FRAMES:
                s_axis.put(frame_data)
            for expected_data in FRAMES:
                got = m_axis.expect(expected_data, timeout=500)
                assert list(got.data) == expected_data
        print(f"  {label} variant: OK")

    print("test_iface_variant_matches_raw: PASSED\n")


# ---------------------------------------------------------------------------
# Step 5b: declarative ModuleSpec-built variant behaves identically too
# ---------------------------------------------------------------------------


def test_declarative_variant_matches_raw() -> None:
    """axi_stream_slice_declarative.py (ModuleSpec-built) vs. the raw-port module."""
    FRAMES = [list(range(12)), list(range(12, 24))]

    for label, dut in [
        ("raw", build_axi_stream_slice(data_width=16, tuser_width=2, has_tlast=True)),
        ("declarative", build_axi_stream_slice_declarative(data_width=16, tuser_width=2, has_tlast=True)),
    ]:
        bench = Testbench(dut)
        with bench.run():
            bench.reset_all()
            s_axis = bench.iface("s_axis")
            m_axis = bench.iface("m_axis")
            s_axis.pause = PauseGenerator(1, 3, seed=5)
            m_axis.pause = PauseGenerator(1, 3, seed=11)

            for frame_data in FRAMES:
                s_axis.put(frame_data)
            for expected_data in FRAMES:
                got = m_axis.expect(expected_data, timeout=500)
                assert list(got.data) == expected_data
        print(f"  {label} variant: OK")

    print("test_declarative_variant_matches_raw: PASSED\n")


# ---------------------------------------------------------------------------
# Step 5c: declarative AND interface-bound variant behaves identically too
# ---------------------------------------------------------------------------


def test_declarative_iface_variant_matches_raw() -> None:
    """axi_stream_slice_declarative_iface.py (ModuleSpec + m.interface()) vs. the raw-port module."""
    FRAMES = [list(range(12)), list(range(12, 24))]

    for label, dut in [
        ("raw", build_axi_stream_slice(data_width=16, tuser_width=2, has_tlast=True)),
        ("declarative+iface", build_axi_stream_slice_declarative_iface(data_width=16, tuser_width=2)),
    ]:
        bench = Testbench(dut)
        with bench.run():
            bench.reset_all()
            s_axis = bench.iface("s_axis")
            m_axis = bench.iface("m_axis")
            s_axis.pause = PauseGenerator(1, 3, seed=5)
            m_axis.pause = PauseGenerator(1, 3, seed=11)

            for frame_data in FRAMES:
                s_axis.put(frame_data)
            for expected_data in FRAMES:
                got = m_axis.expect(expected_data, timeout=500)
                assert list(got.data) == expected_data
        print(f"  {label} variant: OK")

    print("test_declarative_iface_variant_matches_raw: PASSED\n")


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
    test_parameter_variants()
    test_no_tlast_variant()
    test_iface_variant_matches_raw()
    test_declarative_variant_matches_raw()
    test_declarative_iface_variant_matches_raw()
    print("All tests passed.")


if __name__ == "__main__":
    main()
