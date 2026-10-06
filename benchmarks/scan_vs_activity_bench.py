#!/usr/bin/env python3
"""Stage 0 reproducer for `notes/plans/work_queue_delta_engine.md`.

Confirms (or refutes) the hypothesis that the compiled engine's per-edge
delta-loop cost scales with *total design size* (`N_SIGS`/`N_cont`),
independent of how much actually changes on a given edge -- before any
engine code is touched.

Usage:
    uv run python benchmarks/scan_vs_activity_bench.py             # both sweeps x both engines, console
    uv run python benchmarks/scan_vs_activity_bench.py --engine queue
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
from wide_bench_gen import make_cont_bench, make_wide_bench  # noqa: E402

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


def run_sweep_c(cycles: int, n_lanes_values: list[int], k_values: list[int], depth: int = 4) -> list[dict]:
    """Controlled activity: `make_cont_bench(n_lanes)` -- independent
    continuous-assign chains fed by input ports, one clocked process --
    driven by batch_run events that change exactly `k` lanes' inputs per
    cycle. Real per-cycle work is ~k * depth process executions regardless
    of n_lanes, so anything beyond that is delta-loop bookkeeping.

    (Sweeps A/B's `make_wide_bench` can't isolate this: every lane's enable
    reads a register that changes every cycle, and every lane has its own
    always block, so real work there also grows with N_LANES.)
    """
    results = []
    for n in n_lanes_values:
        tree = _PARSER.build_tree(make_cont_bench(n, depth))
        module = tree_to_design(tree).modules[0]
        t0 = time.perf_counter()
        sim = Simulator(module, engine="compiled")
        compile_time = time.perf_counter() - t0
        sim.drive("rst", Value(1, width=1))
        sim.drive("clk", Value(0, width=1))
        for i in range(n):
            sim.drive(f"in_{i}", Value(0, width=16))
        sim.batch_run(2, "clk")
        sim.drive("rst", Value(0, width=1))
        sim.batch_run(2, "clk")
        for k in k_values:
            if k > n:
                continue
            events = [
                (cyc, f"in_{((cyc * k) + j) % n}", (cyc * 2654435761 + j) & 0xFFFF)
                for cyc in range(cycles)
                for j in range(k)
            ]
            t0 = time.perf_counter()
            sim.batch_run(cycles, "clk", events=events)
            elapsed = time.perf_counter() - t0
            results.append(
                {
                    "n_lanes": n,
                    "active_lanes": k,
                    "n_k": f"{n}/{k}",
                    "compile_time": compile_time,
                    "time": elapsed,
                    "throughput": cycles / elapsed if elapsed > 0 else 0,
                }
            )
    return results


def _fmt_results(results: list[dict], *, vary: str) -> str:
    lines = [f"| {vary} | compile_time | time | throughput | n_lanes | active_lanes |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| {r[vary]} | {r['compile_time']:.1f}s | {r['time']:.3f}s | "
            f"{r['throughput'] / 1000:.1f}K cyc/s | {r['n_lanes']} | {r['active_lanes']} |"
        )
    return "\n".join(lines)


def _fmt_comparison(by_engine: dict[str, list[dict]], *, vary: str) -> str:
    engines = list(by_engine)
    head = f"| {vary} | " + " | ".join(f"{e} cyc/s" for e in engines)
    sep = "|---|" + "---|" * len(engines)
    if len(engines) == 2:
        head += f" | {engines[1]} / {engines[0]} |"
        sep += "---|"
    else:
        head += " |"
    lines = [head, sep]
    for rows in zip(*by_engine.values(), strict=True):
        cells = " | ".join(f"{r['throughput'] / 1000:.1f}K" for r in rows)
        line = f"| {rows[0][vary]} | {cells}"
        if len(rows) == 2:
            line += f" | {rows[1]['throughput'] / rows[0]['throughput']:.2f}x"
        lines.append(line + " |")
    return "\n".join(lines)


def main() -> None:  # noqa: PLR0915
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=10_000)
    parser.add_argument("--update", action="store_true", help="write notes/benchmarks_work_queue.md")
    parser.add_argument(
        "--engine",
        nargs="+",
        choices=["scan", "queue"],
        default=["scan", "queue"],
        help="delta_loop engine(s) to measure (VERIFORGE_DELTA_ENGINE); default: both, side by side",
    )
    # Defaults are the largest sizes that compile within the C compiler's
    # timeout on the dev machine -- the plan's original 1024/2048/4096 did not
    # (see notes/plans/work_queue_delta_engine.md, Stage 0).
    parser.add_argument(
        "--sweep-a-lanes",
        type=int,
        nargs="+",
        default=[8, 64, 256, 384],
        help="N_LANES values for Sweep A (default: 8 64 256 384)",
    )
    parser.add_argument("--sweep-a-active", type=int, default=8, help="fixed ACTIVE_LANES for Sweep A")
    parser.add_argument("--sweep-b-lanes", type=int, default=256, help="fixed N_LANES for Sweep B")
    parser.add_argument(
        "--sweep-b-active",
        type=int,
        nargs="+",
        default=[1, 8, 64, 256],
        help="ACTIVE_LANES values for Sweep B (default: 1 8 64 256)",
    )
    parser.add_argument(
        "--sweep-c-lanes",
        type=int,
        nargs="+",
        default=[64, 256, 512],
        help="N_LANES values for Sweep C, controlled activity (default: 64 256 512)",
    )
    parser.add_argument(
        "--sweep-c-k",
        type=int,
        nargs="+",
        default=[1, 8, 64],
        help="lanes driven per cycle in Sweep C (default: 1 8 64)",
    )
    parser.add_argument("--skip-ab", action="store_true", help="run only Sweep C")
    args = parser.parse_args()

    sweep_a: dict[str, list[dict]] = {}
    sweep_b: dict[str, list[dict]] = {}
    sweep_c: dict[str, list[dict]] = {}
    for engine in args.engine:
        os.environ["VERIFORGE_DELTA_ENGINE"] = engine
        print(f"=== [{engine}] Sweep C: N_LANES={args.sweep_c_lanes}, k lanes driven per cycle={args.sweep_c_k} ===")
        sweep_c[engine] = run_sweep_c(args.cycles, args.sweep_c_lanes, args.sweep_c_k)
        print(_fmt_results(sweep_c[engine], vary="n_k"))
        print()
        if args.skip_ab:
            continue
        print(f"=== [{engine}] Sweep A: fix ACTIVE_LANES={args.sweep_a_active}, vary N_LANES={args.sweep_a_lanes} ===")
        sweep_a[engine] = run_sweep_a(args.cycles, args.sweep_a_lanes, args.sweep_a_active)
        print(_fmt_results(sweep_a[engine], vary="n_lanes"))
        print()
        print(f"=== [{engine}] Sweep B: fix N_LANES={args.sweep_b_lanes}, vary ACTIVE_LANES={args.sweep_b_active} ===")
        sweep_b[engine] = run_sweep_b(args.cycles, args.sweep_b_lanes, args.sweep_b_active)
        print(_fmt_results(sweep_b[engine], vary="active_lanes"))
        print()
        # Size sensitivity at fixed activity (Stage 0's gate, for the scan
        # engine; for the queue engine this ratio should be far smaller).
        tp_first, tp_last = sweep_a[engine][0]["throughput"], sweep_a[engine][-1]["throughput"]
        ratio = tp_first / tp_last if tp_last > 0 else float("inf")
        size_ratio = args.sweep_a_lanes[-1] / args.sweep_a_lanes[0]
        print(
            f"[{engine}] Sweep A throughput ratio (N_LANES={args.sweep_a_lanes[0]} vs {args.sweep_a_lanes[-1]}): "
            f"{ratio:.1f}x for a {size_ratio:.1f}x size increase"
        )
        print()

    if len(args.engine) > 1:
        print("=== Comparison: Sweep C (controlled activity; n_lanes/k) ===")
        print(_fmt_comparison(sweep_c, vary="n_k"))
        if not args.skip_ab:
            print()
            print("=== Comparison: Sweep A ===")
            print(_fmt_comparison(sweep_a, vary="n_lanes"))
            print()
            print("=== Comparison: Sweep B ===")
            print(_fmt_comparison(sweep_b, vary="active_lanes"))

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
            if len(args.engine) > 1:
                f.write(f"\n## Sweep C comparison ({args.cycles} cycles; n_lanes/k = lanes driven per cycle)\n\n")
                f.write(_fmt_comparison(sweep_c, vary="n_k") + "\n")
            if len(args.engine) > 1 and not args.skip_ab:
                f.write(f"\n## Sweep A comparison ({args.cycles} cycles, ACTIVE_LANES={args.sweep_a_active} fixed)\n\n")
                f.write(_fmt_comparison(sweep_a, vary="n_lanes") + "\n")
                f.write(f"\n## Sweep B comparison ({args.cycles} cycles, N_LANES={args.sweep_b_lanes} fixed)\n\n")
                f.write(_fmt_comparison(sweep_b, vary="active_lanes") + "\n")
            for engine in args.engine:
                f.write(f"\n## [{engine}] Sweep C ({args.cycles} cycles)\n\n")
                f.write(_fmt_results(sweep_c[engine], vary="n_k") + "\n")
                if args.skip_ab:
                    continue
                f.write(f"\n## [{engine}] Sweep A ({args.cycles} cycles, ACTIVE_LANES={args.sweep_a_active} fixed)\n\n")
                f.write(_fmt_results(sweep_a[engine], vary="n_lanes") + "\n")
                f.write(f"\n## [{engine}] Sweep B ({args.cycles} cycles, N_LANES={args.sweep_b_lanes} fixed)\n\n")
                f.write(_fmt_results(sweep_b[engine], vary="active_lanes") + "\n")
        print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
