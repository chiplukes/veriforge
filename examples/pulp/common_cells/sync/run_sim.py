"""Run the imported common_cells sync example.

Run from the repository root:

    uv run python examples/pulp/common_cells/sync/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "sync.sv"),
    str(SCRIPT_DIR / "tb" / "sync_tb_local.sv"),
]
MAX_TIME = 180
ENGINES = available_engines()
SYNC_TOP = "sync_tb_local"
SYNC_RESET_ONE_TOP = "sync_reset_one_tb_local"
RISE_DEADLINE = 120
FALL_DEADLINE = 180


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
        step_eval_now(sim, "clk_i")


def _run_until_condition(sim: Simulator, target_time: int, predicate, message: str) -> None:
    while sim.time < target_time:
        if predicate(sim):
            return
        if not sim.run_step():
            raise RuntimeError(f"stepped engine stopped before {message}")
    if not predicate(sim):
        raise RuntimeError(message)


def _run_until_rising_edge(sim: Simulator, target_time: int, message: str) -> None:
    previous = _read_int(sim, "clk_i")
    while sim.time < target_time:
        if not sim.run_step():
            raise RuntimeError(f"stepped engine stopped before {message}")
        current = _read_int(sim, "clk_i")
        if previous == 0 and current == 1:
            return
        previous = current
    raise RuntimeError(message)


def _wait_for_clock_low(sim: Simulator) -> None:
    _run_until_condition(
        sim,
        sim.time + 20,
        lambda s: _read_int(s, "clk_i") == 0,
        "clock never reached a low phase before the next drive",
    )


def _make_step_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    if top is None:
        raise RuntimeError(f"Top module {top_name!r} not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk_i", 0)
    step_drive(sim, engine, "rst_ni", 1)
    step_drive(sim, engine, "serial_i", 0)
    _settle_drives(sim, engine)
    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk_i"), period=10), MAX_TIME)
    _settle_drives(sim, engine)
    return sim


def _release_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "rst_ni", 1)
    _settle_drives(sim, engine)


def _run_default_reset_case(design, engine: str) -> None:
    sim = _make_step_sim(design, SYNC_TOP, engine)
    _expect(sim, "serial_o", 0, "default reset value should drive serial_o low under reset")

    _release_reset(sim, engine)
    _expect(sim, "serial_o", 0, "default reset case should stay low immediately after release")

    _wait_for_clock_low(sim)
    step_drive(sim, engine, "serial_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, 60, "first rising sample edge not observed")
    _expect(sim, "serial_o", 0, "stage 1 should not reach the output immediately")
    _run_until_rising_edge(sim, 80, "second rising sample edge not observed")
    _expect(sim, "serial_o", 0, "stage 2 should not reach the output immediately")
    _run_until_rising_edge(sim, RISE_DEADLINE, "third rising sample edge not observed")
    _expect(sim, "serial_o", 1, "three-stage synchronizer should propagate a rising input on the third edge")

    _wait_for_clock_low(sim)
    step_drive(sim, engine, "serial_i", 0)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, 140, "first falling sample edge not observed")
    _expect(sim, "serial_o", 1, "output should hold high for the first falling sample edge")
    _run_until_rising_edge(sim, 160, "second falling sample edge not observed")
    _expect(sim, "serial_o", 1, "output should hold high for the second falling sample edge")
    _run_until_rising_edge(sim, FALL_DEADLINE, "third falling sample edge not observed")
    _expect(sim, "serial_o", 0, "three-stage synchronizer should propagate a falling input on the third edge")


def _run_reset_one_case(design, engine: str) -> None:
    sim = _make_step_sim(design, SYNC_RESET_ONE_TOP, engine)
    _expect(sim, "serial_o", 1, "RESET_VALUE=1 should drive serial_o high under reset")

    _release_reset(sim, engine)
    _expect(sim, "serial_o", 1, "RESET_VALUE=1 case should stay high immediately after release")
    _run_until_rising_edge(sim, 60, "first drain edge not observed")
    _expect(sim, "serial_o", 1, "RESET_VALUE=1 should hold high on the first drain edge")
    _run_until_rising_edge(sim, 80, "second drain edge not observed")
    _expect(sim, "serial_o", 1, "RESET_VALUE=1 should hold high on the second drain edge")
    _run_until_rising_edge(sim, RISE_DEADLINE, "third drain edge not observed")
    _expect(sim, "serial_o", 0, "RESET_VALUE=1 should drain to zero on the third edge when serial_i stays low")

    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine)
    _expect(sim, "serial_o", 1, "async reset reassertion should immediately restore RESET_VALUE=1")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _run_default_reset_case(design, engine)
        _run_reset_one_case(design, engine)
    except Exception as exc:
        print(f"  FAIL sync python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS sync python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [path for path in map(Path, FILES) if not path.exists()]
    if missing:
        print("sync example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing sync example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_sync_pcache")
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
