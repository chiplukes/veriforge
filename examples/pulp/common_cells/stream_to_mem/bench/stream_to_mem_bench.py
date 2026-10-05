"""Check bounded requests and buffered responses through stream_to_mem.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_to_mem/bench/stream_to_mem_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench

EXAMPLE_DIR = Path(__file__).resolve().parents[1]
RTL_FILES = [EXAMPLE_DIR / "rtl" / "stream_to_mem.sv", Path(__file__).with_name("stream_to_mem_bench_top.sv")]


def build_bench(*, engine: str = "reference") -> Testbench:
    design = parse_files([str(path) for path in RTL_FILES], preprocess=True)
    dut = design.get_module("stream_to_mem_bench_top")
    if dut is None:
        raise RuntimeError("Top module 'stream_to_mem_bench_top' not found")
    return Testbench(dut, engine=engine, design=design)


def _memory_response(bench: Testbench, data: int) -> None:
    """Memory responses have valid/data but no ready handshake."""
    bench.sim.drive("mem_resp_data_i", data)
    bench.sim.drive("mem_resp_valid_i", 1)
    bench.step(domain="clk_i")
    bench.sim.drive("mem_resp_valid_i", 0)
    bench.sim.settle()


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--engine", default="reference", choices=("reference", "vm", "vm-fast", "compiled"))
    args = parser.parse_args()

    bench = build_bench(engine=args.engine)
    with bench.run():
        bench.sim.drive("mem_resp_valid_i", 0)
        bench.sim.drive("mem_resp_data_i", 0)
        bench.reset_all()
        source = bench.iface("req")
        memory = bench.iface("mem_req")
        response = bench.iface("resp")
        response.pause = True

        source.write([0x11, 0x22, 0x33])
        memory.expect_sequence([0x11, 0x22], timeout=20)
        bench.step(2)
        if int(bench.sim.read("req_ready_o")) != 0 or memory.pending():
            raise AssertionError("third request escaped the two-outstanding limit")

        _memory_response(bench, 0xA1)
        _memory_response(bench, 0xB2)
        if int(bench.sim.read("resp_valid_o")) != 1 or response.pending():
            raise AssertionError("memory responses were not buffered during output stall")
        response.pause = False
        response.expect(0xA1, timeout=20)
        memory.expect(0x33, timeout=20)
        response.expect(0xB2, timeout=20)
        _memory_response(bench, 0xC3)
        response.expect(0xC3, timeout=20)
        source.wait_drain(timeout=20)
        bench.step(2)
        if memory.pending() or response.pending():
            raise AssertionError("stream_to_mem produced an extra transaction")

    print(f"stream_to_mem passed: bounded requests and ordered buffered responses ({args.engine})")


if __name__ == "__main__":
    run_smoke_test()
