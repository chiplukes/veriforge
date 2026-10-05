"""Show pass-through and newest-value overwrite in a lossy stream adapter.

Run from the repository root:

    uv run python examples/pulp/common_cells/lossy_valid_to_stream/bench/lossy_valid_to_stream_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

RTL_FILE = Path(__file__).resolve().parents[1] / "rtl" / "lossy_valid_to_stream.sv"


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(RTL_FILE)], preprocess=True)
    dut = design.get_module("lossy_valid_to_stream")
    if dut is None:
        raise RuntimeError("Top module 'lossy_valid_to_stream' not found")
    return Testbench(dut, engine=engine, design=design)


def _pulse_input(bench: Testbench, value: int) -> None:
    """The input has valid/data but no ready; each clock edge consumes a pulse."""
    bench.sim.drive("data_i", value)
    bench.sim.drive("valid_i", 1)
    bench.step(domain="clk_i")
    bench.sim.drive("valid_i", 0)
    bench.sim.settle()


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.sim.drive("valid_i", 0)
        bench.sim.drive("data_i", 0)
        bench.reset_all()
        sink = bench.iface("out")

        _pulse_input(bench, 0x11)
        sink.expect(0x11, timeout=10)
        if int(bench.sim.read("busy_o")):
            raise AssertionError("pass-through value should not occupy the buffer")

        sink.pause = True
        for value in (0x22, 0x33, 0x44):
            _pulse_input(bench, value)
        if sink.pending() or int(bench.sim.read("busy_o")) != 1:
            raise AssertionError("stalled input burst was not held in the lossy buffer")

        sink.pause = False
        sink.expect_sequence([0x22, 0x44], timeout=20)
        bench.step(2)
        if sink.pending() or int(bench.sim.read("busy_o")):
            raise AssertionError("lossy buffer did not drain after the newest value")

    print(f"lossy_valid_to_stream passed: pass-through and newest-value overwrite ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
