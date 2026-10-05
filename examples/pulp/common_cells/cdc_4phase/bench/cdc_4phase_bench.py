"""Check decoupled source progress through a four-phase CDC bridge.

Run from the repository root:

    uv run python examples/pulp/common_cells/cdc_4phase/bench/cdc_4phase_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, Testbench

RTL_DIR = Path(__file__).resolve().parents[1] / "rtl"
RTL_FILES = [
    RTL_DIR / "sync.sv",
    RTL_DIR / "spill_register_flushable.sv",
    RTL_DIR / "spill_register.sv",
    RTL_DIR / "cdc_4phase.sv",
]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("cdc_4phase")
    if dut is None:
        raise RuntimeError("Top module 'cdc_4phase' not found")
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
        source.wait_drain(timeout=100)

        for _ in range(100):
            if int(bench.sim.read("src_ready_o")) and int(bench.sim.read("dst_valid_o")):
                break
            bench.step()
        else:
            raise AssertionError("source did not reopen while the first destination beat was stalled")
        if sink.pending():
            raise AssertionError("stalled destination accepted the first beat")

        source.put(0x22)
        source.wait_drain(timeout=100)
        sink.pause = False
        sink.expect_sequence([0x11, 0x22], timeout=200)

    print(f"cdc_4phase passed: decoupled source and two ordered beats ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
