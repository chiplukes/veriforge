"""Check ordered delivery through a gray-pointer CDC FIFO and spill stage.

Run from the repository root:

    uv run python examples/pulp/common_cells/cdc_fifo_gray/bench/cdc_fifo_gray_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, Testbench

RTL_DIR = Path(__file__).resolve().parents[1] / "rtl"
RTL_FILES = [
    RTL_DIR / "binary_to_gray.sv",
    RTL_DIR / "gray_to_binary.sv",
    RTL_DIR / "sync.sv",
    RTL_DIR / "spill_register_flushable.sv",
    RTL_DIR / "spill_register.sv",
    RTL_DIR / "cdc_fifo_gray.sv",
]
PAYLOADS = [0x11, 0x22, 0x33, 0x44, 0x55]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("cdc_fifo_gray")
    if dut is None:
        raise RuntimeError("Top module 'cdc_fifo_gray' not found")
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
        source.write(PAYLOADS[:4])
        source.wait_drain(timeout=100)
        if sink.pending() or int(bench.sim.read("src_ready_o")) != 0:
            raise AssertionError("gray FIFO and spill stage should hold four beats before backpressure")

        source.put(PAYLOADS[4])
        bench.step(4)
        if int(bench.sim.read("src_ready_o")) != 0:
            raise AssertionError("source accepted a fifth beat while the destination was stalled")

        sink.pause = False
        sink.expect_sequence(PAYLOADS, timeout=300)
        source.wait_drain(timeout=300)

    print(f"cdc_fifo_gray passed: 10/14 clocks, spill stall, and {len(PAYLOADS)} ordered beats ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
