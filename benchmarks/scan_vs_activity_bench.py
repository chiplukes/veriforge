#!/usr/bin/env python3
"""Stage 0 reproducer for `notes/plans/work_queue_delta_engine.md`.

Confirms (or refutes) the hypothesis that the compiled engine's per-edge
delta-loop cost scales with *total design size* (`N_SIGS`/`N_cont`),
independent of how much actually changes on a given edge -- before any
engine code is touched.

Usage:
    uv run python benchmarks/scan_vs_activity_bench.py             # both sweeps, console output
    uv run python benchmarks/scan_vs_activity_bench.py --update    # also write notes/benchmarks_work_queue.md
    uv run python benchmarks/scan_vs_activity_bench.py --cycles 20000
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from veriforge.sim.testbench import Simulator  # noqa: E402
from veriforge.sim.value import Value  # noqa: E402
from veriforge.transforms import tree_to_design  # noqa: E402
from veriforge.verilog_parser import verilog_parser  # noqa: E402

from benchmark import collect_machine_info  # noqa: E402
from wide_bench_gen import make_wide_bench  # noqa: E402

_PARSER = verilog_parser(start="source_text")


def _build_module(n_lanes: int, active_lanes: int):
    src = make_wide_bench(n_lanes, active_lanes)
    tree = _PARSER.build_tree(src)
    design = tree_to_design(tree)
    return design.modules[0]


def measure_compiled_batch(n_lanes: int, active_lanes: int, cycles: int) -> dict:
    """Compile+run `make_wide_bench(n_lanes, active_lanes)` via the compiled
    engine's batch_run(), same warm-up/bootstrap pattern as
    `benchmark.py:run_compiled_batch` (reused here, not reimplemented, by
    following the identical sequence against a parameterized module).
    """
    module = _build_module(n_lanes, active_lanes)

    t0 = time.perf_counter()
    sim = Simulator(module, engine="compiled")
    compile_time = time.perf_counter() - t0

    csim = sim._sched._sim  # noqa: SLF001 -- same access pattern as benchmark.py's run_compiled_batch

    sim.drive("rst", Value(1, width=1))
    sim.drive("clk", Value(0, width=1))
    csim.snapshot()
    sim.drive("clk", Value(1, width=1))
    csim.step()
    csim.snapshot()
    sim.drive("clk", Value(0, width=1))
    csim.step()
    sim.drive("rst", Value(0, width=1))
    csim.snapshot()
    sim.drive("clk", Value(1, width=1))
    csim.step()
    csim.snapshot()
    sim.drive("clk", Value(0, width=1))
    csim.step()

    t0 = time.perf_counter()
    sim.batch_run(cycles, "clk", clock_period=10)
    elapsed = time.perf_counter() - t0
    return {
        "n_lanes": n_lanes,
        "active_lanes": active_lanes,
        "compile_time": compile_time,
        "time": elapsed,
        "throughput": cycles / elapsed if elapsed > 0 else 0,
    }


def run_sweep_a(cycles: int, n_lanes_values: list[int], active_lanes: int) -> list[dict]:
    """Fix active_lanes, vary n_lanes. Expose size-dependent, activity-
    independent cost: cycles/s should drop roughly ~1/n_lanes if the
    hypothesis holds.
    """
    return [measure_compiled_batch(n, active_lanes, cycles) for n in n_lanes_values]


def run_sweep_b(cycles: int, n_lanes: int, active_lanes_values: list[int]) -> list[dict]:
    """Fix n_lanes, vary active_lanes. Expose activity-insensitivity:
    cycles/s should stay roughly flat across this sweep if the hypothesis
    holds (today's engine doesn't respond to how much is actually
    happening).
    """
    return [measure_compiled_batch(n_lanes, a, cycles) for a in active_lanes_values]


def _fmt_results(results: list[dict], *, vary: str) -> str:
    lines = [f"| {vary} | compile_time | time | throughput | n_lanes | active_lanes |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r[vary]} | {r['compile_time']:.1f}s | {r['time']:.3f}s | "
            f"{r['throughput'] / 1000:.1f}K cyc/s | {r['n_lanes']} | {r['active_lanes']} |"
        )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=10_000)
    parser.add_argument("--update", action="store_true", help="write notes/benchmarks_work_queue.md")
    parser.add_argument(
        "--sweep-a-lanes",
        type=int,
        nargs="+",
        default=[8, 64, 256, 1024, 4096],
        help="N_LANES values for Sweep A (default: 8 64 256 1024 4096)",
    )
    parser.add_argument("--sweep-a-active", type=int, default=8, help="fixed ACTIVE_LANES for Sweep A")
    parser.add_argument("--sweep-b-lanes", type=int, default=2048, help="fixed N_LANES for Sweep B")
    parser.add_argument(
        "--sweep-b-active",
        type=int,
        nargs="+",
        default=[1, 8, 64, 512, 2048],
        help="ACTIVE_LANES values for Sweep B (default: 1 8 64 512 2048)",
    )
    args = parser.parse_args()

    print(f"=== Sweep A: fix ACTIVE_LANES={args.sweep_a_active}, vary N_LANES={args.sweep_a_lanes} ===")
    print("Hypothesis: cycles/s drops roughly ~1/N_LANES even though real work-per-cycle is pinned constant.")
    sweep_a = run_sweep_a(args.cycles, args.sweep_a_lanes, args.sweep_a_active)
    print(_fmt_results(sweep_a, vary="n_lanes"))

    print()
    print(f"=== Sweep B: fix N_LANES={args.sweep_b_lanes}, vary ACTIVE_LANES={args.sweep_b_active} ===")
    print("Hypothesis: cycles/s stays roughly flat -- today's engine cost doesn't track real activity.")
    sweep_b = run_sweep_b(args.cycles, args.sweep_b_lanes, args.sweep_b_active)
    print(_fmt_results(sweep_b, vary="active_lanes"))

    # Gate check: Sweep A's throughput should drop meaningfully (the plan's
    # own gate -- if it doesn't, the hypothesis needs revisiting before
    # Stage 1/2 is attempted).
    tp_first, tp_last = sweep_a[0]["throughput"], sweep_a[-1]["throughput"]
    ratio = tp_first / tp_last if tp_last > 0 else float("inf")
    size_ratio = args.sweep_a_lanes[-1] / args.sweep_a_lanes[0]
    print()
    print(
        f"Sweep A throughput ratio (N_LANES={args.sweep_a_lanes[0]} vs {args.sweep_a_lanes[-1]}): "
        f"{ratio:.1f}x (size ratio: {size_ratio:.1f}x)"
    )
    if ratio < size_ratio * 0.3:
        print("GATE: throughput did not drop anywhere near proportionally to design size -- re-investigate.")
    else:
        print("GATE: throughput drop is size-proportional, as predicted -- hypothesis confirmed.")

    if args.update:
        out_path = os.path.join(ROOT, "notes", "benchmarks_work_queue.md")
        machine = collect_machine_info()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("# Work-Queue Delta Engine — Reproducer Benchmark\n\n")
            f.write(f"> Auto-generated by `benchmarks/scan_vs_activity_bench.py`. Last updated: {now}\n\n")
            f.write("See `notes/plans/work_queue_delta_engine.md` for context.\n\n")
            f.write("## Test Machine\n\n")
            for k, v in machine.items():
                f.write(f"- {k}: {v}\n")
            f.write(f"\n## Sweep A ({args.cycles} cycles, ACTIVE_LANES={args.sweep_a_active} fixed)\n\n")
            f.write(_fmt_results(sweep_a, vary="n_lanes") + "\n")
            f.write(f"\n## Sweep B ({args.cycles} cycles, N_LANES={args.sweep_b_lanes} fixed)\n\n")
            f.write(_fmt_results(sweep_b, vary="active_lanes") + "\n")
        print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
