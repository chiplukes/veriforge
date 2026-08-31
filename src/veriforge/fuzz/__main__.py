"""CLI entry point for the Veriforge grammar-driven Verilog fuzzer.

Usage:  uv run -m veriforge.fuzz [--seed N] [--max MODULES] [--hours H] [--output DIR] [--no-icarus] [--verilator]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

from ..codegen.verilog_emitter import emit_design
from ._module_gen import ModuleGenerator
from ._runner import FuzzRunner


def _repro_seed(seed: int, *, icarus: bool = True, verilator: bool = False) -> None:
    """Re-run a specific seed with detailed per-vector output comparison.

    *icarus*/*verilator* mirror the main run's own `--no-icarus`/
    `--verilator` flags (`main()` passes the CLI's actual settings through)
    so a saved mismatch that involved one of the external oracles can be
    reproduced with that oracle's own per-vector output shown, not just the
    4 internal engines.

    Uses `ModuleGenerator.generate_design()` (not the single-module
    `generate()`) to match `FuzzRunner._run_one`'s own generation path
    exactly -- the `hierarchical` strategy produces a child module plus a
    parent, and re-parsing/re-simulating only the parent's own text (as
    `generate()` + `emit_module()` used to here) left the child
    unresolvable ("Cannot resolve module 'c' for instance") for any
    hierarchical-strategy seed.
    """
    rng = random.Random(seed)  # noqa: S311
    gen = ModuleGenerator(rng)
    design = gen.generate_design()
    top = design.get_module("t")
    assert top is not None, "generator always names the top module 't'"
    verilog = emit_design(design)

    print(f"=== Seed {seed} ===")
    print(verilog)
    print()

    runner = FuzzRunner(output_dir=".", seed=seed, icarus=icarus, verilator=verilator)
    vectors = runner._gen_stimulus(top, rng)

    for engine in runner._engines:
        print(f"--- Engine: {engine} ---")
        try:
            results = runner._simulate(verilog, engine, vectors)
            for vi, vec_results in enumerate(results):
                print(f"  Vector {vi}:")
                for name, val in sorted(vec_results.items()):
                    print(f"    {name}: {val}")
        except Exception as e:
            print(f"  ERROR: {e}")

    if runner._icarus:
        print("--- Engine: iverilog ---")
        try:
            results = runner._simulate_icarus(verilog, vectors, top)
            for vi, vec_results in enumerate(results):
                print(f"  Vector {vi}:")
                for name, val in sorted(vec_results.items()):
                    print(f"    {name}: {val}")
        except Exception as e:
            print(f"  ERROR: {e}")

    if runner._verilator:
        print("--- Engine: verilator ---")
        try:
            results = runner._simulate_verilator(verilog, vectors, top)
            for vi, vec_results in enumerate(results):
                print(f"  Vector {vi}:")
                for name, val in sorted(vec_results.items()):
                    print(f"    {name}: {val}")
        except Exception as e:
            print(f"  ERROR: {e}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grammar-driven Verilog fuzzer — cross-engine + Icarus comparison",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run -m veriforge.fuzz --max 100              # 100 modules\n"
            "  uv run -m veriforge.fuzz --hours 8              # 8 hours\n"
            "  uv run -m veriforge.fuzz --no-icarus            # skip Icarus\n"
            "  uv run -m veriforge.fuzz --verilator            # also cross-check with Verilator\n"
            "  uv run -m veriforge.fuzz --output my_fuzz       # custom output dir\n"
            "  uv run -m veriforge.fuzz --repro fuzz_output/mismatch_00042\n"
        ),
    )
    parser.add_argument("--seed", type=int, default=0, help="Starting seed (default: 0)")
    parser.add_argument("--max", type=int, dest="max_modules", default=None, help="Stop after N modules")
    parser.add_argument("--hours", type=float, default=None, help="Stop after H hours")
    parser.add_argument("--output", type=str, default="fuzz_output", help="Output directory for mismatch artifacts")
    parser.add_argument("--no-icarus", action="store_true", help="Disable Icarus cross-check")
    parser.add_argument(
        "--verilator",
        action="store_true",
        help="Enable Verilator cross-check (opt-in: ~8-10x slower per module than Icarus; "
        "2-state only, so comparison is value-only on reference-defined bits)",
    )
    parser.add_argument(
        "--engines",
        type=str,
        nargs="+",
        default=None,
        help="Engines to test (default: all available: reference vm vm-fast)",
    )
    parser.add_argument(
        "--repro",
        type=str,
        default=None,
        help="Re-run a specific seed or mismatch directory path",
    )

    args = parser.parse_args()

    if args.repro:
        repro_path = Path(args.repro)
        if repro_path.is_dir():
            info_path = repro_path / "info.json"
            if info_path.exists():
                info = json.loads(info_path.read_text())
                seed = info["seed"]
            else:
                seed_str = repro_path.name.replace("mismatch_", "")
                seed = int(seed_str)
        else:
            seed = int(args.repro)
        _repro_seed(seed, icarus=not args.no_icarus, verilator=args.verilator)
        return

    if args.max_modules is None and args.hours is None:
        print("Specify --max or --hours to limit the run (or Ctrl-C to stop).")
        print("Starting infinite run...")

    runner = FuzzRunner(
        output_dir=args.output,
        seed=args.seed,
        engines=args.engines,
        icarus=not args.no_icarus,
        verilator=args.verilator,
    )

    try:
        runner.run(max_modules=args.max_modules, max_hours=args.hours)
    except KeyboardInterrupt:
        print("\n[fuzz] interrupted — saving final stats...")
        runner._save_stats()
        sys.exit(0)


if __name__ == "__main__":
    main()
