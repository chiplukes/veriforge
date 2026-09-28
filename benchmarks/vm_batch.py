#!/usr/bin/env python3
"""Compare native VM batching with its event loop, checking all final signal/memory values.

Run: .venv/bin/python benchmarks/vm_batch.py --cycles 50000 --repeat 3
Parsing, construction, and execution are reported separately. Both execution modes
include two reset cycles and run exactly the same number of rising/falling edges.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import statistics
import time

from benchmark import Clock, Simulator, Value, _parse_design


def run_case(mode, cycles, design):
    start = time.perf_counter()
    sim = Simulator(deepcopy(design.modules[0]), engine="vm-fast")
    if sim._sched._cy_ctx is None or not hasattr(sim._sched._cy_ctx, "batch_run"):
        raise RuntimeError("Build the native VM first: python setup_cython.py build_ext --inplace")
    setup = time.perf_counter() - start
    total = cycles + 2
    start = time.perf_counter()
    if mode == "batch":
        assert sim.batch_run(total, "clk", events=[(0, "rst", 1), (2, "rst", 0)]) == total
    else:
        sim.drive("rst", 1)
        sim.fork(Clock(sim.signal("clk"), period=10))
        sim._sched.schedule_at(20, ("clock_toggle", "rst", Value(0)))
        sim.run(max_time=total * 10 - 5)
    elapsed = time.perf_counter() - start
    names = sorted(sim._sched.signal_names())
    for name, mid in sim._sched.compiler.mem_map.items():
        _, depth, _ = sim._sched.compiler.mem_info[mid]
        names.extend(f"{name}[{i}]" for i in range(depth))
    state = {name: (v.val, v.mask, v.width) for name in names for v in [sim.read(name)]}
    return setup, elapsed, state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=50000)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    if args.cycles < 1 or args.repeat < 1:
        parser.error("cycles and repeat must be positive")
    start = time.perf_counter()
    design = _parse_design()
    print(f"Parse: {time.perf_counter() - start:.3f}s")
    for mode in ("step", "batch"):
        run_case(mode, 100, design)
    measurements = {"step": [], "batch": []}
    for _ in range(args.repeat):
        step = run_case("step", args.cycles, design)
        batch = run_case("batch", args.cycles, design)
        assert step[2] == batch[2], "Final signal or memory state differs"
        measurements["step"].append(step[:2])
        measurements["batch"].append(batch[:2])
    durations = {}
    for mode, rows in measurements.items():
        setup = statistics.median(row[0] for row in rows)
        elapsed = statistics.median(row[1] for row in rows)
        durations[mode] = elapsed
        print(f"VM {mode}: setup {setup:.4f}s; execution {elapsed:.4f}s; "
              f"{(args.cycles + 2) / elapsed:,.0f} cycles/s")
    print(f"Batch speedup: {durations['step'] / durations['batch']:.2f}x; all final states match")


if __name__ == "__main__":
    main()
