"""Run the imported common_cells stream_throttle example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_throttle/run_sim.py
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.example_runner import available_engines
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator

SCRIPT_DIR = Path(__file__).resolve().parent
FILES = [
    str(SCRIPT_DIR / "rtl" / "stream_throttle.sv"),
    str(SCRIPT_DIR / "tb" / "stream_throttle_tb_local.sv"),
]
MAX_TIME = 220
ENGINES = available_engines()


def _read_int(sim: Simulator, signal_name: str) -> int:
    raw = sim.read(signal_name)
    try:
        return int(raw)
    except Exception as exc:
        raise RuntimeError(f"{signal_name} is not fully resolved: {raw}") from exc


def _expect(sim: Simulator, signal_name: str, expected: int, message: str) -> None:
    actual = _read_int(sim, signal_name)
    if actual != expected:
        raise RuntimeError(f"{message}: expected {expected:#x}, got {actual:#x}")


def _settle_drives(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=0)
    else:
        step_eval_now(sim)


def _run_until_rising_edge(sim: Simulator, signal_name: str, limit: int, message: str) -> None:
    previous = _read_int(sim, signal_name)
    while sim.time < limit:
        if not sim.run_step():
            raise RuntimeError(f"stepped engine stopped before {message}")
        current = _read_int(sim, signal_name)
        if previous == 0 and current == 1:
            return
        previous = current
    raise RuntimeError(message)


def _make_sim(design, engine: str) -> Simulator:
    top = design.get_module("stream_throttle_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_throttle_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "req_valid_i", 0)
    step_drive(sim, engine, "req_ready_i", 0)
    step_drive(sim, engine, "rsp_valid_i", 0)
    step_drive(sim, engine, "rsp_ready_i", 0)
    step_drive(sim, engine, "credit_i", 2)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), MAX_TIME)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _drive(
    sim: Simulator,
    engine: str,
    *,
    credit: int,
    req_valid: int = 0,
    req_ready: int = 0,
    rsp_valid: int = 0,
    rsp_ready: int = 0,
) -> None:
    step_drive(sim, engine, "credit_i", credit)
    step_drive(sim, engine, "req_valid_i", req_valid)
    step_drive(sim, engine, "req_ready_i", req_ready)
    step_drive(sim, engine, "rsp_valid_i", rsp_valid)
    step_drive(sim, engine, "rsp_ready_i", rsp_ready)
    _settle_drives(sim, engine)


def _tick(sim: Simulator) -> None:
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_throttle next rising edge not observed")


def _run_checks(sim: Simulator, engine: str) -> None:
    _expect(sim, "req_valid_o", 0, "stream_throttle should be idle after reset")
    _expect(sim, "req_ready_o", 0, "stream_throttle should keep ready low when upstream ready is low")

    _drive(sim, engine, credit=2, req_valid=1, req_ready=0)
    _expect(sim, "req_valid_o", 1, "stream_throttle should pass valid when credit is available")
    _expect(sim, "req_ready_o", 0, "stream_throttle should still reflect downstream ready")

    _drive(sim, engine, credit=2, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 1, "stream_throttle should pass valid when downstream is ready")
    _expect(sim, "req_ready_o", 1, "stream_throttle should pass ready when credit is available")
    _tick(sim)

    _drive(sim, engine, credit=2)
    _expect(sim, "req_valid_o", 0, "stream_throttle should return low when request valid drops")
    _expect(sim, "req_ready_o", 0, "stream_throttle should return ready low when request ready drops")

    _drive(sim, engine, credit=2, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 1, "stream_throttle should still allow the second request at credit two")
    _expect(sim, "req_ready_o", 1, "stream_throttle should still allow ready for the second request")
    _tick(sim)

    _drive(sim, engine, credit=2, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 0, "stream_throttle should block once outstanding requests reach the credit")
    _expect(sim, "req_ready_o", 0, "stream_throttle should deassert ready once the credit is exhausted")

    _drive(sim, engine, credit=2, req_valid=1, req_ready=1, rsp_valid=1, rsp_ready=1)
    _expect(sim, "req_valid_o", 0, "stream_throttle should stay blocked until a response is clocked in")
    _expect(sim, "req_ready_o", 0, "stream_throttle should stay blocked until a response is clocked in")
    _tick(sim)

    _drive(sim, engine, credit=2, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 1, "stream_throttle should reopen after one response reduces outstanding count")
    _expect(sim, "req_ready_o", 1, "stream_throttle should reopen ready after one response")

    _drive(sim, engine, credit=2, req_valid=1, req_ready=1, rsp_valid=1, rsp_ready=1)
    _expect(
        sim,
        "req_valid_o",
        1,
        "stream_throttle should still pass the request during a simultaneous request and response",
    )
    _expect(
        sim,
        "req_ready_o",
        1,
        "stream_throttle should still pass ready during a simultaneous request and response",
    )
    _tick(sim)

    _drive(sim, engine, credit=2, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 1, "stream_throttle simultaneous request/response should preserve outstanding count")
    _expect(sim, "req_ready_o", 1, "stream_throttle simultaneous request/response should preserve credit availability")

    _drive(sim, engine, credit=1, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 0, "stream_throttle should block immediately when runtime credit is lowered")
    _expect(sim, "req_ready_o", 0, "stream_throttle should block ready immediately when runtime credit is lowered")

    _drive(sim, engine, credit=1, req_valid=1, req_ready=1, rsp_valid=1, rsp_ready=1)
    _expect(sim, "req_valid_o", 0, "stream_throttle should stay blocked until the lowered-credit response is accepted")
    _expect(
        sim, "req_ready_o", 0, "stream_throttle should keep ready blocked until the lowered-credit response is accepted"
    )
    _tick(sim)

    _drive(sim, engine, credit=1, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 1, "stream_throttle should reopen once outstanding count drops below lowered credit")
    _expect(
        sim, "req_ready_o", 1, "stream_throttle should reopen ready once outstanding count drops below lowered credit"
    )


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_sim(design, engine)
        _run_checks(sim, engine)
    except Exception as exc:
        print(f"  FAIL stream_throttle python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_throttle python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_throttle example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_throttle example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_stream_throttle_pcache")
    except Exception:
        traceback.print_exc()
        return 1

    print(f"  parsed {len(design.modules)} modules in {time.time() - t0:.2f}s")

    status = 0
    for engine in ENGINES:
        try:
            status |= _run_engine(design, engine)
        except Exception:
            traceback.print_exc()
            status = 1

    if "compiled" not in ENGINES:
        print("\nCompiled engine skipped: Cython or a supported C compiler is not available.")

    return status


if __name__ == "__main__":
    sys.exit(main())
