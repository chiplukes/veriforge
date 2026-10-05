"""Check four-way round-robin delivery under output backpressure.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_arbiter/bench/stream_arbiter_bench.py
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
    EXAMPLE_DIR / "rtl" / "stream_arbiter.sv",
    Path(__file__).with_name("stream_arbiter_bench_top.sv"),
]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_arbiter_bench_top")
    if dut is None:
        raise RuntimeError("Top module 'stream_arbiter_bench_top' not found")
    return Testbench(dut, engine=engine, design=design)


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.reset_all()
        sources = [bench.iface(f"in{lane}") for lane in range(4)]
        sink = bench.iface("out")
        sink.pause = True
        for lane, source in enumerate(sources):
            source.write([0x10 * (lane + 1), 0x10 * (lane + 1) + 1])
        bench.step(3)
        if int(bench.sim.read("out_valid_o")) != 1 or int(bench.sim.read("out_data_o")) != 0x10:
            raise AssertionError("arbiter did not hold the initial grant while stalled")
        if sink.pending():
            raise AssertionError("stalled arbiter output accepted a beat")

        sink.pause = False
        sink.expect_sequence([0x10, 0x20, 0x30, 0x40, 0x11, 0x21, 0x31, 0x41], timeout=100)
        for source in sources:
            source.wait_drain(timeout=100)
        bench.step(2)
        if sink.pending():
            raise AssertionError("arbiter produced an extra beat")

    print(f"stream_arbiter passed: two round-robin passes across four lanes ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
