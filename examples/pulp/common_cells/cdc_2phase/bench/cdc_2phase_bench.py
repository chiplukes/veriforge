"""Check one in-flight transfer at a time through a two-phase CDC bridge.

Run from the repository root:

    uv run python examples/pulp/common_cells/cdc_2phase/bench/cdc_2phase_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, Testbench

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RTL_FILES = [EXAMPLE_DIR / "rtl" / "cdc_2phase.sv", EXAMPLE_DIR / "tb" / "cdc_2phase_tb_local.sv"]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("cdc_2phase_tb_local")
    if dut is None:
        raise RuntimeError("Top module 'cdc_2phase_tb_local' not found")
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
        source.put(0x11)
        source.wait_drain(timeout=50)
        source.put(0x22)
        bench.step(10)
        if int(bench.sim.read("src_ready_o")) != 0 or int(bench.sim.read("dst_valid_o")) != 1:
            raise AssertionError("source should remain blocked while the first destination beat is stalled")
        if sink.pending():
            raise AssertionError("stalled destination accepted the first beat")

        sink.pause = False
        sink.expect_sequence([0x11, 0x22], timeout=120)
        source.wait_drain(timeout=120)

    print(f"cdc_2phase passed: stalled source and two ordered beats ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
