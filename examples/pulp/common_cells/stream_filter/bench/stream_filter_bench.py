"""Transaction-level ready/valid test for the combinational stream filter.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_filter/bench/stream_filter_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

RTL_FILE = Path(__file__).resolve().parents[1] / "rtl" / "stream_filter.sv"


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(RTL_FILE)], preprocess=True)
    dut = design.get_module("stream_filter")
    if dut is None:
        raise RuntimeError("Top module 'stream_filter' not found")
    return Testbench(dut, engine=engine, design=design)


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        source = bench.iface("in")
        sink = bench.iface("out")

        source.put(sideband={"drop": 0})
        sink.get(timeout=5)

        source.put(sideband={"drop": 1})
        source.wait_drain(timeout=5)
        if sink.pending():
            raise AssertionError("stream_filter forwarded a dropped transfer")

        source.put(sideband={"drop": 0})
        sink.get(timeout=5)
        bench.step(2)
        if sink.pending():
            raise AssertionError("stream_filter produced an extra transfer")

    print(f"stream_filter passed: 2 forwarded, 1 dropped ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
