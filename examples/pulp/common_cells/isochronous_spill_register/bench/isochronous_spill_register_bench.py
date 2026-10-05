"""Transfer ordered beats across the two clocks of an isochronous spill register.

Run from the repository root:

    uv run python examples/pulp/common_cells/isochronous_spill_register/bench/isochronous_spill_register_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

RTL_FILE = Path(__file__).resolve().parents[1] / "rtl" / "isochronous_spill_register.sv"
PAYLOADS = [0x11, 0x22, 0x33]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(RTL_FILE)], preprocess=True)
    dut = design.get_module("isochronous_spill_register")
    if dut is None:
        raise RuntimeError("Top module 'isochronous_spill_register' not found")
    return Testbench(dut, engine=engine, design=design)


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.reset_all()
        source = bench.iface("src")
        sink = bench.iface("dst")

        sink.pause = True
        source.write(PAYLOADS[:2])
        source.wait_drain(timeout=40)
        if sink.pending() or int(bench.sim.read("src_ready_o")) != 0:
            raise AssertionError("two queued beats should fill the source-side spill buffer")

        source.put(PAYLOADS[2])
        bench.step(2)
        if int(bench.sim.read("src_ready_o")) != 0:
            raise AssertionError("source accepted a third beat while destination was stalled")

        sink.pause = False
        sink.expect_sequence(PAYLOADS, timeout=40)
        source.wait_drain(timeout=40)

    print(f"isochronous_spill_register passed: two-clock stall and {len(PAYLOADS)} ordered beats ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
