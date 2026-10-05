"""Check pass-through and stalled buffering in a PULP fall-through register.

Run from the repository root:

    uv run python examples/pulp/common_cells/fall_through_register/bench/fall_through_register_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

RTL_DIR = Path(__file__).resolve().parents[1] / "rtl"
RTL_FILES = [RTL_DIR / "fifo_v3.sv", RTL_DIR / "fall_through_register.sv"]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("fall_through_register")
    if dut is None:
        raise RuntimeError("Top module 'fall_through_register' not found")
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

        source.put(0x11)
        sink.expect(0x11, timeout=20)

        sink.pause = True
        source.put(0x22)
        source.wait_drain(timeout=20)
        if sink.pending() or int(bench.sim.read("ready_o")) != 0:
            raise AssertionError("fall-through register should buffer one beat and backpressure the source")

        source.put(0x33)
        bench.step(2)
        if int(bench.sim.read("ready_o")) != 0:
            raise AssertionError("fall-through register accepted a second stalled beat")

        sink.pause = False
        sink.expect_sequence([0x22, 0x33], timeout=20)
        source.wait_drain(timeout=20)

    print(f"fall_through_register passed: pass-through and one-slot stall ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
