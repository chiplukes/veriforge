"""Check credit exhaustion and runtime credit reduction with queued requests.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_throttle/bench/stream_throttle_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RTL_FILES = [EXAMPLE_DIR / "rtl" / "stream_throttle.sv", Path(__file__).with_name("stream_throttle_bench_top.sv")]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_throttle_bench_top")
    if dut is None:
        raise RuntimeError("Top module 'stream_throttle_bench_top' not found")
    return Testbench(dut, engine=engine, design=design)


def _complete_response(bench: Testbench) -> None:
    bench.sim.drive("rsp_valid_i", 1)
    bench.sim.drive("rsp_ready_i", 1)
    bench.step(domain="clk_i")
    bench.sim.drive("rsp_valid_i", 0)
    bench.sim.settle()


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.sim.drive("credit_i", 2)
        bench.sim.drive("rsp_valid_i", 0)
        bench.sim.drive("rsp_ready_i", 1)
        bench.reset_all()
        source = bench.iface("in")
        sink = bench.iface("out")

        sink.pause = True
        source.put(0x11)
        bench.step(2)
        if int(bench.sim.read("out_valid_o")) != 1 or int(bench.sim.read("in_ready_o")) != 0:
            raise AssertionError("stalled output did not backpressure the request")
        sink.pause = False
        source.write([0x22, 0x33])
        sink.expect_sequence([0x11, 0x22], timeout=20)
        bench.step(2)
        if int(bench.sim.read("out_valid_o")) != 0 or sink.pending():
            raise AssertionError("third request passed the two-credit limit")

        _complete_response(bench)
        sink.expect(0x33, timeout=20)
        source.wait_drain(timeout=20)

        bench.sim.drive("credit_i", 1)
        source.put(0x44)
        bench.step(2)
        if int(bench.sim.read("out_valid_o")) != 0:
            raise AssertionError("lowered runtime credit did not block a new request")
        _complete_response(bench)
        bench.step(2)
        if int(bench.sim.read("out_valid_o")) != 0:
            raise AssertionError("request reopened with one transaction still outstanding")
        _complete_response(bench)
        sink.expect(0x44, timeout=20)
        source.wait_drain(timeout=20)
        bench.step(2)
        if sink.pending():
            raise AssertionError("throttle produced an extra request")

    print(f"stream_throttle passed: credit exhaustion and reduction ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
