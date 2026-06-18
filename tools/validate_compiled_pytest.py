from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path


PASSED_RE = re.compile(r"(?P<count>\d+) passed")
FAILED_RE = re.compile(r"(?P<count>\d+) failed")
ERROR_RE = re.compile(r"(?P<count>\d+) error")

SUITES: dict[str, list[str]] = {
    "sub-signal-mask-mix": [
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_signal_sub_mask_binop_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_signal_sub_or_mask_binop_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_combo_blocking_signal_sub_mask_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_seq_nba_signal_sub_mask_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_combo_blocking_signal_sub_or_mask_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_seq_nba_signal_sub_or_mask_cross_engine",
    ],
    "sub-const-bitwise-mix": [
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_sub_const_xor_binop_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_const_sub_xor_binop_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_sub_const_and_binop_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_const_sub_and_binop_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_sub_const_or_binop_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_const_sub_or_binop_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_combo_blocking_sub_const_xor_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_seq_nba_sub_const_xor_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_combo_blocking_const_sub_xor_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_seq_nba_const_sub_xor_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_combo_blocking_sub_const_and_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_seq_nba_sub_const_and_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_combo_blocking_const_sub_and_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_seq_nba_const_sub_and_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_combo_blocking_sub_const_or_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_seq_nba_sub_const_or_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_combo_blocking_const_sub_or_cross_engine",
        "tests/test_sim/test_compiled.py::TestWideSignalExternalIO::test_wide_seq_nba_const_sub_or_cross_engine",
    ],
}


def _parse_count(pattern: re.Pattern[str], text: str) -> int:
    match = pattern.search(text)
    return int(match.group("count")) if match else 0


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _default_cache_root(repo_root: Path) -> Path:
    return repo_root / "_vtc"


def _write_summary(summary_path: Path, summary_lines: list[str]) -> None:
    summary_path.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    proc.kill()


def _run_batch(
    command: list[str], repo_root: Path, env: dict[str, str], log_path: Path, timeout: float | None
) -> tuple[int, bool]:
    timed_out = False
    with log_path.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(
            command,
            cwd=repo_root,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            log_file.write(f"\nTIMEOUT: batch exceeded {timeout:.1f}s\n")
            log_file.flush()
            _terminate_process(proc)
            proc.wait()
            returncode = 124
        except KeyboardInterrupt:
            log_file.write("\nINTERRUPTED: validator stopped by user\n")
            log_file.flush()
            _terminate_process(proc)
            proc.wait()
            raise
    return returncode, timed_out


def main() -> int:
    raw_argv = sys.argv[1:]
    passthrough_pytest_args: list[str] | None = None
    if "--" in raw_argv:
        split_index = raw_argv.index("--")
        passthrough_pytest_args = raw_argv[split_index + 1 :]
        raw_argv = raw_argv[:split_index]

    parser = argparse.ArgumentParser(
        description="Run compiled-engine pytest nodes in small batches and normalize the Windows post-pass KeyboardInterrupt artifact."
    )
    parser.add_argument("nodes", nargs="*", help="Exact pytest node ids to run.")
    parser.add_argument("--batch-size", type=int, default=1, help="Number of node ids to run per subprocess.")
    parser.add_argument(
        "--suite",
        action="append",
        default=[],
        choices=sorted(SUITES),
        help="Named validation suite to expand into exact pytest node ids.",
    )
    parser.add_argument("--list-suites", action="store_true", help="List available suite names and exit.")
    parser.add_argument(
        "--cache-root",
        default=None,
        help="Root directory for per-batch VERILOG_TOOLS_COMPILE_CACHE values.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum seconds to allow each pytest batch to run before terminating it. Use 0 to disable.",
    )
    parser.add_argument(
        "--pytest-args",
        nargs=argparse.REMAINDER,
        help="Additional pytest arguments appended after the node list.",
    )
    args = parser.parse_args(raw_argv)

    if args.list_suites:
        for suite_name in sorted(SUITES):
            print(suite_name)
        return 0

    resolved_nodes: list[str] = []
    for suite_name in args.suite:
        resolved_nodes.extend(SUITES[suite_name])
    resolved_nodes.extend(args.nodes)
    if not resolved_nodes:
        parser.error("Provide at least one node or --suite value.")

    seen_nodes: set[str] = set()
    nodes = [node for node in resolved_nodes if not (node in seen_nodes or seen_nodes.add(node))]

    repo_root = Path(__file__).resolve().parents[1]
    cache_root = Path(args.cache_root) if args.cache_root else _default_cache_root(repo_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    timeout = None if args.timeout_seconds <= 0 else args.timeout_seconds

    pytest_args = passthrough_pytest_args if passthrough_pytest_args is not None else args.pytest_args
    if pytest_args is None:
        pytest_args = ["--tb=no", "-q"]
    overall_ok = True
    summary_lines: list[str] = []
    summary_path = cache_root / "summary.txt"

    try:
        for batch_index, batch in enumerate(_chunked(nodes, args.batch_size), start=1):
            cache_dir = cache_root / f"batch_{batch_index:02d}"
            cache_dir.mkdir(parents=True, exist_ok=True)
            env = os.environ.copy()
            env["VERILOG_TOOLS_COMPILE_CACHE"] = str(cache_dir)
            command = [sys.executable, "-m", "pytest", *batch, *pytest_args]
            log_path = cache_dir / "pytest_output.txt"
            start_time = time.monotonic()
            summary_lines.append(f"batch {batch_index}: RUNNING :: {' | '.join(batch)} :: {log_path}")
            _write_summary(summary_path, summary_lines)
            try:
                returncode, timed_out = _run_batch(command, repo_root, env, log_path, timeout)
            except KeyboardInterrupt:
                summary_lines[-1] = f"batch {batch_index}: INTERRUPTED :: {' | '.join(batch)} :: {log_path}"
                _write_summary(summary_path, summary_lines)
                print(f"Interrupted while running batch {batch_index}: {' | '.join(batch)}")
                return 130

            duration = time.monotonic() - start_time
            combined = log_path.read_text(encoding="utf-8")
            passed = _parse_count(PASSED_RE, combined)
            failed = _parse_count(FAILED_RE, combined)
            errors = _parse_count(ERROR_RE, combined)
            interrupted_after_pass = (
                returncode == 2
                and "KeyboardInterrupt" in combined
                and passed > 0
                and failed == 0
                and errors == 0
                and "FAILED" not in combined
                and "ERROR:" not in combined
            )

            if timed_out:
                overall_ok = False
                summary_lines[-1] = (
                    f"batch {batch_index}: TIMEOUT ({duration:.1f}s) :: {' | '.join(batch)} :: {log_path}"
                )
                summary_lines.append(combined.strip())
                _write_summary(summary_path, summary_lines)
                continue

            if returncode == 0 or interrupted_after_pass:
                suffix = " normalized interrupt" if interrupted_after_pass else ""
                summary_lines[-1] = (
                    f"batch {batch_index}: PASS ({passed} passed, {duration:.1f}s){suffix} :: {' | '.join(batch)}"
                )
                _write_summary(summary_path, summary_lines)
                continue

            overall_ok = False
            summary_lines[-1] = (
                f"batch {batch_index}: FAIL (exit {returncode}, {duration:.1f}s) :: {' | '.join(batch)} :: {log_path}"
            )
            summary_lines.append(combined.strip())
            _write_summary(summary_path, summary_lines)
    except KeyboardInterrupt:
        _write_summary(summary_path, summary_lines)
        return 130

    _write_summary(summary_path, summary_lines)
    for line in summary_lines:
        print(line)

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
