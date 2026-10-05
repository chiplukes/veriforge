"""Transfer ordered beats through a dual-clock two-phase CDC FIFO.

Run from the repository root:

    uv run python examples/pulp/common_cells/cdc_fifo/bench/cdc_fifo_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, Testbench

RTL_DIR = Path(__file__).resolve().parents[1] / "rtl"
RTL_FILES = [RTL_DIR / "cdc_2phase.sv", RTL_DIR / "cdc_fifo_2phase.sv"]
PAYLOADS = [0x11, 0x22, 0x33]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("cdc_fifo_2phase")
    if dut is None:
        raise RuntimeError("Top module 'cdc_fifo_2phase' not found")
    overrides = PlannerOverrides(clock_periods={"src_clk_i": 10, "dst_clk_i": 14})
    return Testbench(dut, engine=engine, design=design, overrides=overrides)


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
        source.wait_drain(timeout=80)
        if sink.pending() or int(bench.sim.read("src_ready_o")) != 0:
            raise AssertionError("CDC FIFO should hold two beats and backpressure the source")

        source.put(PAYLOADS[2])
        bench.step(4)
        if int(bench.sim.read("src_ready_o")) != 0:
            raise AssertionError("CDC FIFO accepted a third beat while destination was stalled")

        sink.pause = False
        sink.expect_sequence(PAYLOADS, timeout=200)
        source.wait_drain(timeout=200)

    print(f"cdc_fifo passed: 10/14 clocks, full stall, and {len(PAYLOADS)} ordered beats ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
