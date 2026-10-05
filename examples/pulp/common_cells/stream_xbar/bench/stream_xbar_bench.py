"""Check simultaneous routing and contested grants through the 3x2 crossbar.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_xbar/bench/stream_xbar_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RTL_FILES = [
    EXAMPLE_DIR / "rtl" / "spill_register_flushable.sv",
    EXAMPLE_DIR / "rtl" / "spill_register.sv",
    EXAMPLE_DIR / "rtl" / "stream_xbar.sv",
    Path(__file__).with_name("stream_xbar_bench_top.sv"),
]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_xbar_bench_top")
    if dut is None:
        raise RuntimeError("Top module 'stream_xbar_bench_top' not found")
    return Testbench(dut, engine=engine, design=design)


def _expect_route(sink, payload: int, index: int) -> None:
    data, sideband = sink.get(timeout=30)
    if (data, sideband.get("idx")) != (payload, index):
        raise AssertionError(f"crossbar route mismatch: got data={data:#x}, sideband={sideband}")


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.sim.drive("flush_i", 0)
        bench.reset_all()
        sources = [bench.iface(f"in{lane}") for lane in range(3)]
        out0 = bench.iface("out0")
        out1 = bench.iface("out1")

        sources[0].put(0x10, sideband={"sel": 0})
        sources[2].put(0x30, sideband={"sel": 1})
        _expect_route(out0, 0x10, 0)
        _expect_route(out1, 0x30, 2)
        sources[0].wait_drain(timeout=30)
        sources[2].wait_drain(timeout=30)

        out0.pause = True
        sources[0].put(0x11, sideband={"sel": 0})
        sources[1].put(0x21, sideband={"sel": 0})
        bench.step(2)
        if int(bench.sim.read("out0_valid_o")) != 1 or int(bench.sim.read("out0_data_o")) != 0x11:
            raise AssertionError("crossbar did not hold the first contended grant")
        if out0.pending():
            raise AssertionError("stalled crossbar output accepted a beat")
        out0.pause = False
        _expect_route(out0, 0x11, 0)
        _expect_route(out0, 0x21, 1)
        sources[0].wait_drain(timeout=30)
        sources[1].wait_drain(timeout=30)
        bench.step(2)
        if out0.pending() or out1.pending():
            raise AssertionError("crossbar produced an extra beat")

    print(f"stream_xbar passed: simultaneous routes and contended grants ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
