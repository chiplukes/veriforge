"""Check that flush resets a stalled arbiter grant to initial priority.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_arbiter_flushable/bench/stream_arbiter_flushable_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RTL_FILES = [
    EXAMPLE_DIR / "rtl" / "rr_arb_tree.sv",
    EXAMPLE_DIR / "rtl" / "stream_arbiter_flushable.sv",
    Path(__file__).with_name("stream_arbiter_flushable_bench_top.sv"),
]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_arbiter_flushable_bench_top")
    if dut is None:
        raise RuntimeError("Top module 'stream_arbiter_flushable_bench_top' not found")
    return Testbench(dut, engine=engine, design=design)


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.sim.drive("flush_i", 0)
        bench.reset_all()
        source0 = bench.iface("in0")
        source1 = bench.iface("in1")
        sink = bench.iface("out")

        source0.put(0x10)
        source1.put(0x20)
        sink.expect(0x10, timeout=20)
        source0.wait_drain(timeout=20)
        sink.pause = True
        source0.put(0x11)
        bench.step(2)
        if int(bench.sim.read("out_data_o")) != 0x20 or int(bench.sim.read("out_valid_o")) != 1:
            raise AssertionError("arbiter did not hold the second request while stalled")
        if sink.pending():
            raise AssertionError("stalled arbiter output accepted a request")

        bench.sim.drive("flush_i", 1)
        bench.step()
        bench.sim.drive("flush_i", 0)
        bench.sim.settle()
        if int(bench.sim.read("out_data_o")) != 0x11:
            raise AssertionError("flush did not restore first-lane priority")
        sink.pause = False
        sink.expect_sequence([0x11, 0x20], timeout=40)
        source0.wait_drain(timeout=40)
        source1.wait_drain(timeout=40)
        bench.step(2)
        if sink.pending():
            raise AssertionError("arbiter produced an extra request after flush")

    print(f"stream_arbiter_flushable passed: stalled grant reset by flush ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
