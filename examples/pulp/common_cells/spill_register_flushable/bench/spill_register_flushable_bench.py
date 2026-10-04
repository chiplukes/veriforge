"""Fill, flush, and refill a PULP spill register through stream transactions.

Run from the repository root:

    uv run python examples/pulp/common_cells/spill_register_flushable/bench/spill_register_flushable_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

RTL_FILE = Path(__file__).resolve().parents[1] / "rtl" / "spill_register_flushable.sv"


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(RTL_FILE)], preprocess=True)
    dut = design.get_module("spill_register_flushable")
    if dut is None:
        raise RuntimeError("Top module 'spill_register_flushable' not found")
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
        source.write([0x12, 0x34])
        source.wait_drain(timeout=20)
        if sink.pending() or int(bench.sim.read("ready_o")) != 0:
            raise AssertionError("spill register should hold two beats and backpressure the source")

        bench.sim.drive("flush_i", 1)
        bench.step()
        bench.sim.drive("flush_i", 0)
        bench.sim.settle()
        if int(bench.sim.read("valid_o")) != 0 or sink.pending():
            raise AssertionError("flush should discard both buffered beats")

        source.write([0x56, 0x78])
        sink.pause = False
        sink.expect_sequence([0x56, 0x78], timeout=20)
        source.wait_drain(timeout=20)

    print(f"spill_register_flushable passed: full, flush, and refill ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
