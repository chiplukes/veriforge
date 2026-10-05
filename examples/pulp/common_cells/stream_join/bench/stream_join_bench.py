"""Require three input handshakes before each joined output handshake.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_join/bench/stream_join_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RTL_FILES = [
    EXAMPLE_DIR / "rtl" / "stream_join_dynamic.sv",
    EXAMPLE_DIR / "rtl" / "stream_join.sv",
    Path(__file__).with_name("stream_join_bench_top.sv"),
]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_join_bench_top")
    if dut is None:
        raise RuntimeError("Top module 'stream_join_bench_top' not found")
    return Testbench(dut, engine=engine, design=design)


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        sources = [bench.iface(f"in{lane}") for lane in range(3)]
        sink = bench.iface("out")

        sources[0].put()
        sources[1].put()
        bench.step()
        if int(bench.sim.read("out_valid_o")) != 0:
            raise AssertionError("join accepted an incomplete input set")
        sources[2].put()
        sink.pause = True
        bench.step()
        if int(bench.sim.read("out_valid_o")) != 1 or any(
            int(bench.sim.read(f"in{lane}_ready_o")) != 0 for lane in range(3)
        ):
            raise AssertionError("join did not hold all inputs while the output was stalled")
        if sink.pending():
            raise AssertionError("stalled joined output accepted a beat")

        sink.pause = False
        sink.expect(0, timeout=10)
        for source in sources:
            source.wait_drain(timeout=10)

        for source in sources:
            source.put()
        sink.expect(0, timeout=10)
        for source in sources:
            source.wait_drain(timeout=10)
        bench.step(2)
        if sink.pending():
            raise AssertionError("join produced an extra handshake")

    print(f"stream_join passed: two complete three-way handshakes ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
