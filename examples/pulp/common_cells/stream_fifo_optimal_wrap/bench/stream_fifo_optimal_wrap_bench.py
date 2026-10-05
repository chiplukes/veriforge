"""Fill and drain the default depth-eight PULP stream FIFO wrapper.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_fifo_optimal_wrap/bench/stream_fifo_optimal_wrap_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

RTL_DIR = Path(__file__).resolve().parents[1] / "rtl"
RTL_FILES = [
    RTL_DIR / "fifo_v3.sv",
    RTL_DIR / "spill_register_flushable.sv",
    RTL_DIR / "stream_fifo.sv",
    RTL_DIR / "stream_fifo_optimal_wrap.sv",
]
PAYLOADS = [0x10 + index for index in range(9)]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_fifo_optimal_wrap")
    if dut is None:
        raise RuntimeError("Top module 'stream_fifo_optimal_wrap' not found")
    return Testbench(dut, engine=engine, design=design)


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.reset_all()
        source = bench.iface("in")
        sink = bench.iface("out")

        sink.pause = True
        source.write(PAYLOADS[:8])
        source.wait_drain(timeout=50)
        if sink.pending() or int(bench.sim.read("ready_o")) != 0:
            raise AssertionError("depth-eight FIFO should backpressure after eight queued beats")

        source.put(PAYLOADS[8])
        bench.step(2)
        if int(bench.sim.read("ready_o")) != 0:
            raise AssertionError("depth-eight FIFO accepted a ninth beat while full")

        sink.pause = False
        sink.expect_sequence(PAYLOADS, timeout=50)
        source.wait_drain(timeout=50)

    print(f"stream_fifo_optimal_wrap passed: depth-eight full stall and {len(PAYLOADS)} ordered beats ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
