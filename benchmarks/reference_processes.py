#!/usr/bin/env python3
"""Measure reference scheduling with sparse or active always blocks.

Run: uv run python benchmarks/reference_processes.py --processes 256 --cycles 200
Parsing and simulator construction are excluded from execution timing. Final
signal values, masks, and widths are compared with event-driven vm-fast.
"""

from __future__ import annotations

import argparse
import statistics
import time
from copy import deepcopy

from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _module(count: int, *, active: bool):
    declarations = " ".join(f"reg src{i}; reg [7:0] combo{i}, seq{i};" for i in range(count))
    initial = " ".join(f"src{i}=0; combo{i}=0; seq{i}=0;" for i in range(count))
    processes = " ".join(
        f"always @(*) combo{i}={('clk' if active else f'src{i}')}+1; "
        f"always @(posedge {('clk' if active else f'src{i}')}) seq{i}<=seq{i}+1;"
        for i in range(count)
    )
    source = (
        "module process_bench(input clk, output reg [31:0] q);"
        + declarations
        + " initial begin q=0; "
        + initial
        + " end always @(posedge clk) q<=q+1; "
        + processes
        + " endmodule"
    )
    return tree_to_design(verilog_parser(start="source_text").build_tree(source)).modules[0]


def _run(module, cycles: int, engine: str):
    sim = Simulator(deepcopy(module), engine=engine)
    sim.fork(Clock(sim.signal("clk"), period=10))
    start = time.perf_counter()
    sim.run(max_time=cycles * 10 - 5)
    elapsed = time.perf_counter() - start
    state = {
        name: (value.val, value.mask, value.width) for name in sim._sched.signal_names() for value in [sim.read(name)]
    }
    return elapsed, state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--processes", type=int, default=256)
    parser.add_argument("--cycles", type=int, default=200)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    if min(args.processes, args.cycles, args.repeat) < 1:
        parser.error("all counts must be positive")

    for label, active in (("sparse", False), ("active", True)):
        module = _module(args.processes, active=active)
        _, expected = _run(module, args.cycles, "vm-fast")
        timings = []
        for _ in range(args.repeat):
            elapsed, actual = _run(module, args.cycles, "reference")
            if actual != expected:
                differences = [name for name, value in expected.items() if actual.get(name) != value]
                raise AssertionError(f"{label}: state differs from vm-fast at {differences[:8]}")
            timings.append(elapsed)
        median = statistics.median(timings)
        print(f"{label}: {median:.4f}s, {args.cycles / median:,.0f} reference cycles/s", flush=True)


if __name__ == "__main__":
    main()
