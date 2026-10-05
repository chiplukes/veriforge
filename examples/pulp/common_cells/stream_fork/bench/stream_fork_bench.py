"""Check that a fork delivers each input beat once to every output.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_fork/bench/stream_fork_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RTL_FILES = [EXAMPLE_DIR / "rtl" / "stream_fork.sv", Path(__file__).with_name("stream_fork_bench_top.sv")]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_fork_bench_top")
    if dut is None:
        raise RuntimeError("Top module 'stream_fork_bench_top' not found")
    return Testbench(dut, engine=engine, design=design)


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.reset_all()
        source = bench.iface("in")
        sinks = [bench.iface(f"out{lane}") for lane in range(3)]
        sinks[1].pause = True
        sinks[2].pause = True
        source.put(0x55)
        bench.step(3)
        if sinks[0].pending() != 1 or sinks[1].pending() or sinks[2].pending():
            raise AssertionError("fork did not track the first partial fanout")
        if int(bench.sim.read("in_ready_o")) != 0:
            raise AssertionError("fork accepted the input before every output handshook")

        sinks[2].pause = False
        sinks[2].expect(0x55, timeout=20)
        if sinks[0].pending() != 1 or sinks[1].pending():
            raise AssertionError("fork duplicated or misplaced a partial transfer")
        sinks[1].pause = False
        sinks[1].expect(0x55, timeout=20)
        source.wait_drain(timeout=20)
        sinks[0].expect(0x55, timeout=20)

        source.put(0x66)
        for sink in sinks:
            sink.expect(0x66, timeout=20)
        source.wait_drain(timeout=20)
        bench.step(2)
        if any(sink.pending() for sink in sinks):
            raise AssertionError("fork produced a duplicate beat")

    print(f"stream_fork passed: partial fanout and full restart ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
