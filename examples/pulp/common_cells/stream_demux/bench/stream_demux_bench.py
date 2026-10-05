"""Route ready/valid beats to each selected demux output.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_demux/bench/stream_demux_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RTL_FILES = [EXAMPLE_DIR / "rtl" / "stream_demux.sv", Path(__file__).with_name("stream_demux_bench_top.sv")]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_demux_bench_top")
    if dut is None:
        raise RuntimeError("Top module 'stream_demux_bench_top' not found")
    return Testbench(dut, engine=engine, design=design)


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        source = bench.iface("in")
        sinks = [bench.iface(f"out{lane}") for lane in range(3)]

        bench.sim.drive("select_i", 1)
        sinks[1].pause = True
        source.put(0x22)
        bench.step()
        if int(bench.sim.read("out1_valid_o")) != 1 or int(bench.sim.read("in_ready_o")) != 0:
            raise AssertionError("selected output did not stall the input")
        if any(sink.pending() for sink in sinks):
            raise AssertionError("stalled beat reached an output")
        sinks[1].pause = False
        sinks[1].expect(0x22, timeout=10)
        source.wait_drain(timeout=10)

        for lane, payload in ((2, 0x33), (0, 0x11)):
            bench.sim.drive("select_i", lane)
            source.put(payload)
            sinks[lane].expect(payload, timeout=10)
            source.wait_drain(timeout=10)
        bench.step(2)
        if any(sink.pending() for sink in sinks):
            raise AssertionError("demux delivered a beat to an unselected output")

    print(f"stream_demux passed: three selected lanes and backpressure ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
