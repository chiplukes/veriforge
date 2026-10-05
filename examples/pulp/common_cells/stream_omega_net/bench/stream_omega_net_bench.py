"""Route a permutation and two contended pairs through the omega network.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_omega_net/bench/stream_omega_net_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RTL_FILES = [
    EXAMPLE_DIR / "rtl" / "spill_register_flushable.sv",
    EXAMPLE_DIR / "rtl" / "spill_register.sv",
    EXAMPLE_DIR / "rtl" / "stream_omega_net.sv",
    Path(__file__).with_name("stream_omega_net_bench_top.sv"),
]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_omega_net_bench_top")
    if dut is None:
        raise RuntimeError("Top module 'stream_omega_net_bench_top' not found")
    return Testbench(dut, engine=engine, design=design)


def _expect_route(sink, payload: int, index: int) -> None:
    data, sideband = sink.get(timeout=40)
    if (data, sideband.get("idx")) != (payload, index):
        raise AssertionError(f"omega route mismatch: got data={data:#x}, sideband={sideband}")


def _flush(bench: Testbench) -> None:
    bench.sim.drive("flush_i", 1)
    bench.step(domain="clk_i")
    bench.sim.drive("flush_i", 0)
    bench.sim.settle()


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.sim.drive("flush_i", 0)
        bench.reset_all()
        sources = [bench.iface(f"in{lane}") for lane in range(4)]
        sinks = [bench.iface(f"out{lane}") for lane in range(4)]

        for lane, destination in enumerate((0, 2, 1, 3)):
            sources[lane].put(0xA0 + lane, sideband={"sel": destination})
        for destination, source in enumerate((0, 2, 1, 3)):
            _expect_route(sinks[destination], 0xA0 + source, source)
        for source in sources:
            source.wait_drain(timeout=40)

        _flush(bench)
        sources[0].put(0x10, sideband={"sel": 0})
        sources[1].put(0x21, sideband={"sel": 0})
        _expect_route(sinks[0], 0x10, 0)
        _expect_route(sinks[0], 0x21, 1)
        sources[0].wait_drain(timeout=40)
        sources[1].wait_drain(timeout=40)

        _flush(bench)
        sources[0].put(0x30, sideband={"sel": 0})
        sources[2].put(0x42, sideband={"sel": 0})
        _expect_route(sinks[0], 0x30, 0)
        _expect_route(sinks[0], 0x42, 2)
        sources[0].wait_drain(timeout=40)
        sources[2].wait_drain(timeout=40)
        bench.step(2)
        if any(sink.pending() for sink in sinks):
            raise AssertionError("omega network produced an extra beat")

    print(f"stream_omega_net passed: permutation and two contention stages ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
