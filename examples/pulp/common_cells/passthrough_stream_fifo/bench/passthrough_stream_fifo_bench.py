"""Exercise a full passthrough stream FIFO through ready/valid transactions.

Run from the repository root:

    uv run python examples/pulp/common_cells/passthrough_stream_fifo/bench/passthrough_stream_fifo_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

RTL_FILE = Path(__file__).resolve().parents[1] / "rtl" / "passthrough_stream_fifo.sv"
PAYLOADS = [0x11, 0x22, 0x33, 0x44]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(RTL_FILE)], preprocess=True)
    dut = design.get_module("passthrough_stream_fifo")
    if dut is None:
        raise RuntimeError("Top module 'passthrough_stream_fifo' not found")
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
        source.write(PAYLOADS[:3])
        source.wait_drain(timeout=30)
        if sink.pending() or int(bench.sim.read("ready_o")) != 0:
            raise AssertionError("FIFO should hold three beats and backpressure the source")

        source.put(PAYLOADS[3])
        bench.step(2)
        if int(bench.sim.read("ready_o")) != 0:
            raise AssertionError("FIFO accepted a fourth beat while the sink was stalled")

        sink.pause = False
        sink.expect_sequence(PAYLOADS, timeout=30)
        source.wait_drain(timeout=30)

    print(f"passthrough_stream_fifo passed: full stall and {len(PAYLOADS)} beats in order ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
