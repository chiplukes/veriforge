"""CLI: ``uv run -m veriforge.reduce FILES --top T (--mismatch | --error REGEX | --cmd CMD) -o OUT``."""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from ..sim.diverge import add_design_arguments, design_from_args, stimulus_from_args
from . import (
    AllOf,
    Command,
    EngineError,
    EngineMismatch,
    IcarusAccepts,
    Reducer,
    ReduceError,
    emit,
    regression_test_source,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m veriforge.reduce",
        description="Shrink a design while a failure still reproduces.",
    )
    add_design_arguments(ap)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mismatch", action="store_true", help="interesting while the engines disagree on an output")
    mode.add_argument(
        "--error", metavar="REGEX", help='interesting while an error matching REGEX ("Type: message") occurs'
    )
    mode.add_argument("--cmd", metavar="CMD", help="interesting while CMD exits 0; {file} is the candidate file")
    ap.add_argument("--engines", default=None, help="engines (default: reference,compiled; --error: reference)")
    ap.add_argument(
        "--signal",
        action="append",
        help="--mismatch: only these signals count -- outputs or flattened internal names such as u.q (repeatable)",
    )
    ap.add_argument(
        "--valid",
        choices=("auto", "iverilog", "none"),
        default="auto",
        help="keep every candidate valid for Icarus (auto: when iverilog is installed and accepts the input)",
    )
    ap.add_argument("--timeout", type=float, default=120.0, help="seconds per check (default 120)")
    ap.add_argument(
        "--mem-limit-gb",
        type=float,
        default=8.0,
        help="address-space cap per check, inherited by the compiled build (POSIX; 0 for none; default 8)",
    )
    ap.add_argument(
        "-o",
        "--output",
        default="reduced.v",
        help="reduced design, rewritten after every improvement (default reduced.v)",
    )
    ap.add_argument("--test-out", help="also write a pytest skeleton reproducing the failure")
    args = ap.parse_args(argv)

    design = design_from_args(args)
    stim = stimulus_from_args(args)
    predicate: Command | EngineError | EngineMismatch
    if args.cmd:
        predicate = Command(args.cmd, timeout=args.timeout)
    elif args.error:
        engines = tuple((args.engines or "reference").split(","))
        predicate = EngineError(args.top, args.error, engines=engines, stimulus=stim)
    else:
        engines = tuple((args.engines or "reference,compiled").split(","))
        if len(engines) < 2:
            ap.error("--mismatch needs at least two engines")
        mismatch = EngineMismatch(
            args.top,
            engines=engines,
            stimulus=stim,
            signals=tuple(args.signal) if args.signal else None,
            ignore_x=args.ignore_x,
        ).narrowed(emit(design))
        if mismatch.stimulus.cycles != stim.cycles:
            print(f"[reduce] first divergence in cycle {mismatch.stimulus.cycles - 1}: checking that many cycles")
        predicate = mismatch

    check: Callable[[str], bool] = predicate
    if args.valid != "none":
        guard = IcarusAccepts(args.top)
        if not shutil.which("iverilog"):
            if args.valid == "iverilog":
                ap.error("--valid iverilog: iverilog not found")
            print("[reduce] iverilog not found: candidates are not checked for validity")
        elif guard(emit(design)):
            check = AllOf((guard, predicate))
        elif args.valid == "iverilog":
            print("[reduce] iverilog rejects the input design (use --valid none)", file=sys.stderr)
            return 2
        else:
            print("[reduce] iverilog rejects the input design: candidates are not checked for validity")

    try:
        # (Written after every improvement: a stopped run resumes with -o's file as its input.)
        text = Reducer(
            check,
            timeout=args.timeout,
            mem_limit_gb=args.mem_limit_gb,
            checkpoint=lambda best: Path(args.output).write_text(best, encoding="utf-8"),
        ).reduce(design)
    except ReduceError as exc:
        print(f"[reduce] {exc}", file=sys.stderr)
        return 2
    Path(args.output).write_text(text, encoding="utf-8")
    print(f"[reduce] wrote {args.output}")
    if args.test_out and not isinstance(predicate, Command):
        Path(args.test_out).write_text(regression_test_source(predicate, text), encoding="utf-8")
        print(f"[reduce] wrote {args.test_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
