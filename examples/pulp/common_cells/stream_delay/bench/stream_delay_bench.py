"""Transaction-level ready/valid test for the two-cycle stream delay.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_delay/bench/stream_delay_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

RTL_FILE = Path(__file__).resolve().parents[1] / "rtl" / "stream_delay.sv"
PAYLOADS = [0x00, 0x34, 0x56, 0xA5, 0xFF]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(RTL_FILE)], preprocess=True)
    dut = design.get_module("stream_delay")
    if dut is None:
        raise RuntimeError("Top module 'stream_delay' not found")
    return Testbench(dut, engine=engine, design=design)


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.reset_all()
        bench.iface("in").write(PAYLOADS)
        bench.iface("out").expect_sequence(PAYLOADS, timeout=100)
        bench.iface("in").wait_drain(timeout=100)
    print(f"stream_delay passed: {len(PAYLOADS)} beats in order ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
