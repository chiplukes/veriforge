"""Check selected fanout and paired data/mask handshakes.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_fork_dynamic/bench/stream_fork_dynamic_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RTL_FILES = [
    EXAMPLE_DIR / "rtl" / "stream_fork.sv",
    EXAMPLE_DIR / "rtl" / "stream_fork_dynamic.sv",
    Path(__file__).with_name("stream_fork_dynamic_bench_top.sv"),
]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_fork_dynamic_bench_top")
    if dut is None:
        raise RuntimeError("Top module 'stream_fork_dynamic_bench_top' not found")
    return Testbench(dut, engine=engine, design=design)


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.reset_all()
        source = bench.iface("in")
        mask = bench.iface("mask")
        sinks = [bench.iface(f"out{lane}") for lane in range(3)]
        sinks[2].pause = True

        source.put(0x55)
        bench.step(2)
        if any(int(bench.sim.read(f"out{lane}_valid_o")) for lane in range(3)):
            raise AssertionError("fork forwarded data without a selector mask")
        mask.put(0b101)
        bench.step(3)
        if sinks[0].pending() != 1 or sinks[1].pending() or sinks[2].pending():
            raise AssertionError("fork did not deliver to the first selected output")
        if int(bench.sim.read("in_ready_o")) or int(bench.sim.read("mask_ready_o")):
            raise AssertionError("fork accepted data or mask before the selected outputs completed")

        sinks[2].pause = False
        sinks[2].expect(0x55, timeout=20)
        source.wait_drain(timeout=20)
        mask.wait_drain(timeout=20)
        sinks[0].expect(0x55, timeout=20)
        if sinks[1].pending():
            raise AssertionError("unselected output received the first beat")

        source.put(0x66)
        mask.put(0b010)
        sinks[1].expect(0x66, timeout=20)
        source.wait_drain(timeout=20)
        mask.wait_drain(timeout=20)
        bench.step(2)
        if any(sink.pending() for sink in sinks):
            raise AssertionError("dynamic fork produced a duplicate or unselected beat")

    print(f"stream_fork_dynamic passed: two selector masks and partial fanout ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
