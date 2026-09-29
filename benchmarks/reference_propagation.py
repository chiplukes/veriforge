#!/usr/bin/env python3
"""Measure reference continuous-assignment scheduling with sparse/dense activity.

Run: uv run python benchmarks/reference_propagation.py --assigns 256 --cycles 200
Parsing and simulator construction are excluded from execution timing. Final
signal values, masks, and widths are compared with event-driven vm-fast.
"""

from __future__ import annotations

import argparse
import statistics
import time
from copy import deepcopy

from benchmark import Clock, Simulator
from vm_propagation import _many_assigns


def _run(module, cycles, engine):
    sim = Simulator(deepcopy(module), engine=engine)
    sim.fork(Clock(sim.signal("clk"), period=10))
    start = time.perf_counter()
    sim.run(max_time=cycles * 10 - 5)
    elapsed = time.perf_counter() - start
    state = {
        name: (value.val, value.mask, value.width) for name in sim._sched.signal_names() for value in [sim.read(name)]
    }
    return elapsed, state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assigns", type=int, default=256)
    parser.add_argument("--cycles", type=int, default=200)
    parser.add_argument("--repeat", type=int, default=5)
    args = parser.parse_args()
    if min(args.assigns, args.cycles, args.repeat) < 1:
        parser.error("all counts must be positive")

    for label, active in (("sparse", False), ("active", True)):
        module = _many_assigns(args.assigns, active=active)[0]
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
