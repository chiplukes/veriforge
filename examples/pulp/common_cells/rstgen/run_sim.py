"""Run the imported common_cells rstgen example.

Run from the repository root:

    uv run python examples/pulp/common_cells/rstgen/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "tc_clk_mux2.sv"),
    str(SCRIPT_DIR / "rtl" / "rstgen_bypass.sv"),
    str(SCRIPT_DIR / "rtl" / "rstgen.sv"),
    str(SCRIPT_DIR / "tb" / "rstgen_tb_local.sv"),
]
MAX_TIME = 170
RESET_RELEASE_TIME = 31
PRE_SYNC_CHECK_TIME = 60
SYNC_DONE_DEADLINE = 80
TESTMODE_SETTLE_TIME = 130
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
        step_eval_now(sim, "clk_i")


def _run_until_condition(sim: Simulator, target_time: int, predicate, message: str) -> None:
    while sim.time < target_time:
        if predicate(sim):
            return
        if not sim.run_step():
            raise RuntimeError(f"stepped engine stopped before {message}")
    if not predicate(sim):
        raise RuntimeError(message)


def _make_step_sim(design, engine: str) -> Simulator:
    top = design.get_module("rstgen_tb_local")
    if top is None:
        raise RuntimeError("Top module 'rstgen_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk_i", 0)
    step_drive(sim, engine, "rst_ni", 1)
    step_drive(sim, engine, "test_mode_i", 0)
    _settle_drives(sim, engine)
    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk_i"), period=10), MAX_TIME)
    _settle_drives(sim, engine)
    return sim


def _run_engine_checks(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    _expect(sim, "rst_no", 0, "functional reset should hold rst_no low initially")
    _expect(sim, "init_no", 0, "functional reset should hold init_no low initially")

    step_run_until(sim, RESET_RELEASE_TIME)
    step_drive(sim, engine, "rst_ni", 1)
    _settle_drives(sim, engine)
    _expect(sim, "rst_no", 0, "synchronized reset output should stay low immediately after release")
    _expect(sim, "init_no", 0, "synchronized init output should stay low immediately after release")

    step_run_until(sim, PRE_SYNC_CHECK_TIME)
    _expect(sim, "rst_no", 0, "rst_no should still be low before the final sync stage fills")
    _expect(sim, "init_no", 0, "init_no should still be low before the final sync stage fills")
    _run_until_condition(
        sim,
        SYNC_DONE_DEADLINE,
        lambda s: _read_int(s, "rst_no") == 1 and _read_int(s, "init_no") == 1,
        "outputs never asserted after the synchronized release window",
    )

    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine)
    _expect(sim, "rst_no", 0, "functional reset reassertion should clear rst_no immediately")
    _expect(sim, "init_no", 0, "functional reset reassertion should clear init_no immediately")

    step_drive(sim, engine, "test_mode_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "rst_no", 0, "test mode should still reflect rst_ni on rst_no while reset is asserted")
    _expect(sim, "init_no", 1, "test mode should force init_no high even while reset stays asserted")

    step_drive(sim, engine, "rst_ni", 1)
    _settle_drives(sim, engine)
    _expect(sim, "rst_no", 1, "test mode should bypass rst_no immediately from rst_ni")
    _expect(sim, "init_no", 1, "test mode should keep init_no high after reset release")

    step_run_until(sim, TESTMODE_SETTLE_TIME)
    _expect(sim, "rst_no", 1, "rst_no should stay high while test mode remains enabled")
    _expect(sim, "init_no", 1, "init_no should stay high while test mode remains enabled")

    step_drive(sim, engine, "test_mode_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "rst_no", 1, "leaving test mode should keep rst_no high after the sync path refills")
    _expect(sim, "init_no", 1, "leaving test mode should keep init_no high after the sync path refills")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _run_engine_checks(design, engine)
    except Exception as exc:
        print(f"  FAIL rstgen python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS rstgen python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [path for path in map(Path, FILES) if not path.exists()]
    if missing:
        print("rstgen example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing rstgen example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_rstgen_pcache")
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
