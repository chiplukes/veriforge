"""Multi-domain AXI-Stream example with full sideband (TUSER/TDEST/TID/TKEEP).

This example demonstrates the *file-driven* end-to-end flow of the
veriforge Python testbench framework:

1. Parse a Verilog file from disk
   (``examples/python_testbench/dut/multi_domain_axis_dut.v``).
2. Let :class:`Testbench` auto-discover clock domains, resets, and
   AXI-Stream interfaces. Print the inferred plan.
3. Use :class:`PlannerOverrides.iface_layouts` to declare the per-stream
   element layout — important for designs whose TDATA carries non-byte
   elements (12-bit pixels in this case).
4. Drive both clock domains independently and concurrently:
   * Domain **P** ("pixel") sends a frame of 12-bit pixel ELEMENTS packed
     four-per-beat into a 48-bit TDATA, big-endian. ``last_user`` flags
     the trailing beat as "good frame" via TUSER.
   * Domain **R** ("router") sends multiple single-byte frames, each
     with its own TDEST and TID, validating the sideband round-trip.
5. ``expect(...)`` asserts both payload and sideband.

A note on terminology
---------------------
The AXI4-Stream specification only defines "byte lane" (one byte of
TDATA, one bit of TKEEP). Real designs often pack non-byte units --
12-bit pixels, 10b8b symbols, custom fields -- into a wider TDATA. The
spec has no standard term for that sub-beat unit; veriforge (and
veri-quickbench before it) uses **element**:

    elements_per_beat  -- how many elements occupy one TDATA beat
    element_size_bits  -- width of a single element in bits
    endian             -- packing order (little = element 0 in LSBs)

So a "lane" in the AXI4-Stream sense is the special case
``element_size_bits == 8``.

Run with::

    uv run python examples/python_testbench/multi_domain_axis.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.sim.bench import BenchTimeoutError, PlannerOverrides, Testbench
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

DUT_PATH = Path(__file__).parent / "dut" / "multi_domain_axis_dut.v"


def parse_dut_from_file(path: Path):
    """Parse a Verilog DUT from disk into a model module."""
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=path.read_text())
    design = tree_to_design(tree)
    return design.modules[0]


def build_bench() -> Testbench:
    """Parse the DUT and construct the multi-domain testbench."""
    dut = parse_dut_from_file(DUT_PATH)

    # Auto-discovery infers two domains, but we tell it explicitly which
    # interface lives on which clock so name collisions can never sneak
    # in. We also describe the Pixel stream's element layout: 4 elements
    # of 12 bits each, big-endian (element 0 in MSBs of TDATA).
    overrides = PlannerOverrides(
        iface_domains={
            "pix_in": "pclk",
            "pix_out": "pclk",
            "rtr_in": "rclk",
            "rtr_out": "rclk",
        },
        iface_layouts={
            "pix_in": {"elements_per_beat": 4, "element_size_bits": 12, "endian": "big"},
            "pix_out": {"elements_per_beat": 4, "element_size_bits": 12, "endian": "big"},
            # rtr_* are 8b-per-beat with no TKEEP, so the auto-inferred
            # "1 element of 8 bits, little endian" is correct -- no
            # iface_layouts entry needed for them.
        },
    )
    return Testbench(dut, overrides=overrides, engine="reference")


def drive_pixel_frames(bench: Testbench) -> None:
    """Two pixel frames on Domain P -- one good (tuser=1), one corrupt (tuser=0)."""
    pix_in = bench.iface("pix_in")
    pix_out = bench.iface("pix_out")

    print(
        f"  pix layout: {pix_in.elements_per_beat} elements/beat * "
        f"{pix_in.element_size_bits} bits, endian={pix_in.endian}"
    )

    # 8 pixels = 2 beats of 4 pixels each. Use a clear sentinel pattern
    # so packing/unpacking errors stand out in the printout.
    good_pixels = [0x111, 0x222, 0x333, 0x444, 0x555, 0x666, 0x777, 0x888]
    corrupt_pixels = [0xAAA, 0xBBB, 0xCCC, 0xDDD]

    # last_user=1 sets TUSER on every element of the trailing beat to 1
    # (the proxy enforces "all elements of a beat must agree on TUSER",
    # which the AXI-Stream spec also requires when TUSER is per-beat).
    pix_in.put(good_pixels, last_user=1)
    pix_in.put(corrupt_pixels, last_user=0)

    pix_out.expect(good_pixels, last_user=1, timeout=200)
    print("  good frame round-tripped (8 x 12-bit pixels, TUSER@TLAST=1)")
    pix_out.expect(corrupt_pixels, last_user=0, timeout=200)
    print("  corrupt frame round-tripped (4 x 12-bit pixels, TUSER@TLAST=0)")


def drive_router_frames(bench: Testbench) -> None:
    """Three router packets on Domain R with distinct TDEST and TID."""
    rtr_in = bench.iface("rtr_in")
    rtr_out = bench.iface("rtr_out")

    packets = [
        # (payload_bytes, tdest, tid)
        ([0x10, 0x11, 0x12], 3, 7),
        ([0xA0, 0xA1, 0xA2, 0xA3], 5, 2),
        ([0xC0], 1, 11),
    ]
    for payload, dest, tid in packets:
        rtr_in.put(payload, dest=dest, tid=tid)

    for payload, dest, tid in packets:
        frame = rtr_out.expect(payload, dest=dest, tid=tid, timeout=200)
        print(f"  router pkt OK: data={list(frame.data)} dest={frame.dest[0]} tid={frame.tid[0]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--vcd",
        type=Path,
        default=None,
        help="Optional VCD output path. Captures every top-level DUT signal.",
    )
    args = parser.parse_args()

    bench = build_bench()

    print("Discovered testbench plan:\n")
    print(bench.plan.summary())
    print()

    with bench.run(vcd=args.vcd):
        if args.vcd is not None:
            print(f"VCD tracing -> {args.vcd}\n")
        bench.reset_all()

        print("Domain P (pixel stream):")
        drive_pixel_frames(bench)

        print("\nDomain R (router stream):")
        drive_router_frames(bench)

        # Demonstrate that timeouts are domain-local: a sink with no
        # pending traffic should raise BenchTimeoutError after the
        # configured cycle budget elapses on its OWN clock.
        print("\nDemonstrating BenchTimeoutError (no further traffic queued):")
        try:
            bench.iface("pix_out").get(timeout=20)
        except BenchTimeoutError as exc:
            print(f"  caught (as expected): {exc}")


if __name__ == "__main__":
    main()
