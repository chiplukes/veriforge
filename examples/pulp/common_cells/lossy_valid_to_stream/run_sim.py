"""Run the imported common_cells lossy_valid_to_stream example.

Run from the repository root:

    uv run python examples/pulp/common_cells/lossy_valid_to_stream/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "lossy_valid_to_stream.sv"),
    str(SCRIPT_DIR / "tb" / "lossy_valid_to_stream_tb_local.sv"),
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
    top = design.get_module("lossy_valid_to_stream_tb_local")
    if top is None:
        raise RuntimeError("Top module 'lossy_valid_to_stream_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "data_i", 0)
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


def _drive(sim: Simulator, engine: str, *, valid: int = 0, ready: int = 0, data: int = 0) -> None:
    step_drive(sim, engine, "valid_i", valid)
    step_drive(sim, engine, "ready_i", ready)
    step_drive(sim, engine, "data_i", data)
    _settle_drives(sim, engine)


def _tick(sim: Simulator) -> None:
    _run_until_rising_edge(sim, "clk", sim.time + 20, "lossy_valid_to_stream next rising edge not observed")


def _run_checks(sim: Simulator, engine: str) -> None:
    _expect(sim, "valid_o", 0, "lossy_valid_to_stream should be idle after reset")
    _expect(sim, "busy_o", 0, "lossy_valid_to_stream should not be busy after reset")

    _drive(sim, engine, valid=1, ready=1, data=0x11)
    _expect(sim, "valid_o", 1, "lossy_valid_to_stream should pass through valid when empty and ready")
    _expect(sim, "data_o", 0x11, "lossy_valid_to_stream should pass through the payload when empty and ready")
    _expect(sim, "busy_o", 0, "lossy_valid_to_stream pass-through should not mark the buffer busy")
    _tick(sim)
    _drive(sim, engine)
    _expect(sim, "valid_o", 0, "lossy_valid_to_stream should return idle after a pass-through transfer")
    _expect(sim, "busy_o", 0, "lossy_valid_to_stream should remain not busy after a pass-through transfer")

    _drive(sim, engine, valid=1, ready=0, data=0x22)
    _expect(sim, "valid_o", 1, "lossy_valid_to_stream should expose a stalled first value immediately")
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream should expose the stalled input payload immediately")
    _expect(sim, "busy_o", 0, "lossy_valid_to_stream should not mark busy until the stalled value is clocked in")
    _tick(sim)
    _drive(sim, engine, ready=0)
    _expect(sim, "valid_o", 1, "lossy_valid_to_stream should keep the first stalled value buffered")
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream buffered first value mismatch")
    _expect(sim, "busy_o", 1, "lossy_valid_to_stream should report busy once a value is buffered")

    _drive(sim, engine, valid=1, ready=0, data=0x33)
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream second stalled value should not replace the head yet")
    _tick(sim)
    _drive(sim, engine, ready=0)
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream head should remain oldest after filling the second slot")
    _expect(sim, "busy_o", 1, "lossy_valid_to_stream should stay busy after filling the second slot")

    _drive(sim, engine, valid=1, ready=0, data=0x44)
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream full overwrite should preserve the oldest head")
    _tick(sim)
    _drive(sim, engine, ready=0)
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream full overwrite should still leave the oldest head first")
    _expect(sim, "busy_o", 1, "lossy_valid_to_stream should stay busy after overwriting the newest slot")

    _drive(sim, engine, ready=1)
    _expect(sim, "valid_o", 1, "lossy_valid_to_stream should keep output valid while draining")
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream should drain the oldest buffered value first")
    _tick(sim)
    _drive(sim, engine, ready=0)
    _expect(sim, "valid_o", 1, "lossy_valid_to_stream should still have one buffered value after first drain")
    _expect(sim, "data_o", 0x44, "lossy_valid_to_stream should expose the overwritten newest value second")
    _expect(sim, "busy_o", 1, "lossy_valid_to_stream should remain busy until the final buffered value drains")

    _drive(sim, engine, ready=1)
    _expect(
        sim, "data_o", 0x44, "lossy_valid_to_stream final drain should keep the newest buffered value at the output"
    )
    _tick(sim)
    _drive(sim, engine)
    _expect(sim, "valid_o", 0, "lossy_valid_to_stream should return idle after draining both buffered values")
    _expect(sim, "busy_o", 0, "lossy_valid_to_stream should clear busy after draining both buffered values")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_sim(design, engine)
        _run_checks(sim, engine)
    except Exception as exc:
        print(f"  FAIL lossy_valid_to_stream python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS lossy_valid_to_stream python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("lossy_valid_to_stream example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing lossy_valid_to_stream example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_lossy_valid_to_stream_pcache")
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
