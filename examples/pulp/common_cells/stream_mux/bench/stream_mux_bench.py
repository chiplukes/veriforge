"""Route queued ready/valid beats through each stream mux input.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_mux/bench/stream_mux_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RTL_FILES = [EXAMPLE_DIR / "rtl" / "stream_mux.sv", Path(__file__).with_name("stream_mux_bench_top.sv")]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_mux_bench_top")
    if dut is None:
        raise RuntimeError("Top module 'stream_mux_bench_top' not found")
    return Testbench(dut, engine=engine, design=design)


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        sources = [bench.iface(f"in{lane}") for lane in range(3)]
        sink = bench.iface("out")
        sink.pause = True
        for source, payload in zip(sources, (0x11, 0x22, 0x33), strict=True):
            source.put(payload)

        bench.sim.drive("select_i", 1)
        bench.step()
        if int(bench.sim.read("out_valid_o")) != 1 or int(bench.sim.read("out_data_o")) != 0x22:
            raise AssertionError("selected input was not visible while the output was stalled")
        if sink.pending():
            raise AssertionError("stalled output accepted a beat")

        sink.pause = False
        for lane, payload in ((1, 0x22), (0, 0x11), (2, 0x33)):
            bench.sim.drive("select_i", lane)
            sink.expect(payload, timeout=10)
            sources[lane].wait_drain(timeout=10)
        bench.step(2)
        if sink.pending():
            raise AssertionError("mux produced an extra beat")

    print(f"stream_mux passed: three queued lanes routed in select order ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
