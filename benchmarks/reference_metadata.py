#!/usr/bin/env python3
"""Compare reference evaluation with and without context-local signedness caching.

Run: uv run python benchmarks/reference_metadata.py
Simulator construction is excluded from timing. Both modes must finish with
identical signals and memories.
"""

from __future__ import annotations

import argparse
import statistics
import time
from copy import deepcopy

from benchmark import _make_ref_src
from reference_processes import _module
from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


def _run(module, max_time: int, *, cached: bool, clock: bool):
    sim = Simulator(deepcopy(module), engine="reference")
    if clock:
        sim.fork(Clock(sim.signal("clk"), period=10))
    if not cached:
        sim._sched.ctx._expr_signed_cache = None
    start = time.perf_counter()
    sim.run(max_time=max_time)
    elapsed = time.perf_counter() - start
    signals = tuple(sorted((name, val.val, val.mask, val.width) for name, val in sim._sched.ctx._signals.items()))
    memories = tuple(sorted((name, repr(data)) for name, data in sim._sched.ctx._memories.items()))
    return elapsed, (signals, memories)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=1000)
    parser.add_argument("--active-cycles", type=int, default=100)
    parser.add_argument("--processes", type=int, default=64)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    if min(args.cycles, args.active_cycles, args.processes, args.repeat) < 1:
        parser.error("all counts must be positive")

    source = _make_ref_src(args.cycles * 10 + 40)
    mixed = tree_to_design(verilog_parser(start="source_text").build_tree(source)).modules[0]
    active = _module(args.processes, active=True)
    cases = (
        ("mixed DUT", mixed, args.cycles * 10 + 80, False),
        ("active processes", active, args.active_cycles * 10 - 5, True),
    )
    for label, module, max_time, clock in cases:
        timings = {False: [], True: []}
        expected = None
        # Alternating order reduces drift from warm-up or CPU frequency.
        for repeat in range(args.repeat):
            for cached in (False, True) if repeat % 2 == 0 else (True, False):
                elapsed, state = _run(module, max_time, cached=cached, clock=clock)
                if expected is None:
                    expected = state
                elif state != expected:
                    raise AssertionError(f"{label}: cached and uncached states differ")
                timings[cached].append(elapsed)
        uncached = statistics.median(timings[False])
        cached = statistics.median(timings[True])
        print(f"{label}: {uncached:.4f}s uncached → {cached:.4f}s cached ({uncached / cached:.2f}x)")


if __name__ == "__main__":
    main()
