"""Auto-generated Python testbench scaffold (bench framework).

Edit the TODO markers below with stimulus and expectations.

Plan summary:
  TestbenchPlan(top='stream_register')
    domains:
      - clk_i: clock=clk_i (posedge, period=?); reset=rst_ni (active-low, async)
    interfaces:
      - in (stream, role=slave) -> domain=clk_i [structural]
      - out (stream, role=master) -> domain=clk_i [structural]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.sim.bench import BenchTimeoutError, PlannerOverrides, Testbench
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

DUT_PATH = Path(__file__).resolve().parents[1] / "rtl" / "stream_register.sv"


def parse_dut():
    """Parse the DUT module from disk."""
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=DUT_PATH.read_text())
    design = tree_to_design(tree)
    return design.get_module("stream_register")


def build_bench() -> Testbench:
    """Construct the multi-domain Testbench from the parsed DUT."""
    dut = parse_dut()
    overrides = PlannerOverrides(
        iface_domains={
            "in": "clk_i",
            "out": "clk_i",
        },
    )
    return Testbench(dut, overrides=overrides, engine="reference")


def drive_in(bench: Testbench) -> None:
    """Drive a known sequence into the 'in' ready/valid input.

    Domain: 'clk_i'
    """
    iface = bench.iface("in")
    iface.write([0x11, 0x22, 0x33, 0x44])


def expect_out(bench: Testbench) -> None:
    """Read 4 beats from the 'out' ready/valid output and assert payload.

    Domain: 'clk_i'
    """
    iface = bench.iface("out")
    iface.expect_sequence([0x11, 0x22, 0x33, 0x44], timeout=200)
    print("stream_register passed: 4-beat round-trip [0x11, 0x22, 0x33, 0x44]")


def run_smoke_test() -> None:
    """Auto-generated entry point for the 'stream_register' testbench."""
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

        drive_in(bench)
        expect_out(bench)


if __name__ == "__main__":
    run_smoke_test()
