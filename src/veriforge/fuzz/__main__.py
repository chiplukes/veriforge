"""CLI entry point for the Veriforge grammar-driven Verilog fuzzer.

Usage:  uv run -m veriforge.fuzz [--seed N] [--max MODULES] [--hours H] [--output DIR] [--no-icarus]
"""

from __future__ import annotations

import argparse
import sys

from ._runner import FuzzRunner


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Grammar-driven Verilog fuzzer — cross-engine + Icarus comparison",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run -m veriforge.fuzz --max 100              # 100 modules\n"
            "  uv run -m veriforge.fuzz --hours 8              # 8 hours\n"
            "  uv run -m veriforge.fuzz --no-icarus            # skip Icarus\n"
            "  uv run -m veriforge.fuzz --output my_fuzz       # custom output dir\n"
        ),
    )
    parser.add_argument("--seed", type=int, default=0, help="Starting seed (default: 0)")
    parser.add_argument("--max", type=int, dest="max_modules", default=None, help="Stop after N modules")
    parser.add_argument("--hours", type=float, default=None, help="Stop after H hours")
    parser.add_argument("--output", type=str, default="fuzz_output", help="Output directory for mismatch artifacts")
    parser.add_argument("--no-icarus", action="store_true", help="Disable Icarus cross-check")
    parser.add_argument(
        "--engines",
        type=str,
        nargs="+",
        default=None,
        help="Engines to test (default: all available: reference vm vm-fast)",
    )

    args = parser.parse_args()

    if args.max_modules is None and args.hours is None:
        print("Specify --max or --hours to limit the run (or Ctrl-C to stop).")
        print("Starting infinite run...")

    runner = FuzzRunner(
        output_dir=args.output,
        seed=args.seed,
        engines=args.engines,
        icarus=not args.no_icarus,
    )

    try:
        runner.run(max_modules=args.max_modules, max_hours=args.hours)
    except KeyboardInterrupt:
        print("\n[fuzz] interrupted — saving final stats...")
        runner._save_stats()
        sys.exit(0)


if __name__ == "__main__":
    main()
