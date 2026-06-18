"""Run the imported common_cells spill_register example.

Run from the repository root:

    uv run python examples/pulp/common_cells/spill_register/run_sim.py
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
RTL_DIR = SCRIPT_DIR / "rtl"
TB_FILE = SCRIPT_DIR / "tb" / "spill_register_tb_local.sv"
FILES = [
    str(RTL_DIR / "spill_register_flushable.sv"),
    str(RTL_DIR / "spill_register.sv"),
    str(TB_FILE),
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


def _make_step_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    if top is None:
        raise RuntimeError(f"Top module {top_name!r} not found")

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


def _pulse_cycle(
    sim: Simulator, engine: str, *, valid: int = 0, ready: int | None = None, data: int | None = None
) -> None:
    if ready is not None:
        step_drive(sim, engine, "ready_i", ready)
    if data is not None:
        step_drive(sim, engine, "data_i", data)
    step_drive(sim, engine, "valid_i", valid)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "next rising clock edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)


def _run_non_bypass(design, engine: str) -> None:
    sim = _make_step_sim(design, "spill_reg_tb", engine)

    _expect(sim, "valid_o", 0, "spill register should be empty after reset")
    _expect(sim, "ready_o", 1, "spill register should accept input after reset")

    step_drive(sim, engine, "ready_i", 1)
    step_drive(sim, engine, "data_i", 0x11)
    step_drive(sim, engine, "valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "spill register should cut valid combinationally")
    _expect(sim, "ready_o", 1, "spill register should still be ready before first capture")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "first capture edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "spill register should present captured data after one edge")
    _expect(sim, "data_o", 0x11, "spill register captured payload mismatch")
    _expect(sim, "ready_o", 1, "spill register should still accept a second item while b is empty")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "first drain edge not observed")
    _expect(sim, "valid_o", 0, "spill register should drain when downstream stays ready")
    _expect(sim, "ready_o", 1, "spill register should reopen after draining")

    _pulse_cycle(sim, engine, valid=1, ready=0, data=0x22)
    _expect(sim, "valid_o", 1, "first stalled item should be buffered")
    _expect(sim, "data_o", 0x22, "first stalled item payload mismatch")
    _expect(sim, "ready_o", 1, "spill register should still accept one more item with only a full")

    _pulse_cycle(sim, engine, valid=1, ready=0, data=0x33)
    _expect(sim, "valid_o", 1, "spill register should remain valid with two queued items")
    _expect(sim, "data_o", 0x22, "oldest queued item should remain at the output")
    _expect(sim, "ready_o", 0, "spill register should backpressure once both stages are full")

    _pulse_cycle(sim, engine, valid=1, ready=0, data=0x44)
    _expect(sim, "valid_o", 1, "blocked third item should not disturb queued data")
    _expect(sim, "data_o", 0x22, "blocked third item should not replace the head")
    _expect(sim, "ready_o", 0, "blocked third item should leave backpressure asserted")

    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 0, "spill register should stay full until a drain clock edge occurs")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill drain edge not observed")
    _expect(sim, "valid_o", 1, "second queued item should remain valid after first drain")
    _expect(sim, "data_o", 0x33, "second queued item should surface after draining the head")
    _expect(sim, "ready_o", 1, "spill register should reopen once b drains")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "final drain edge not observed")
    _expect(sim, "valid_o", 0, "spill register should empty after draining both queued items")
    _expect(sim, "ready_o", 1, "spill register should be ready again after full drain")


def _run_bypass(design, engine: str) -> None:
    sim = _make_step_sim(design, "spill_reg_bp_tb", engine)

    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "data_i", 0x5A)
    step_drive(sim, engine, "valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "bypass mode should pass valid combinationally")
    _expect(sim, "ready_o", 0, "bypass mode should pass ready combinationally")
    _expect(sim, "data_o", 0x5A, "bypass mode should pass data combinationally")

    step_drive(sim, engine, "ready_i", 1)
    step_drive(sim, engine, "data_i", 0xA5)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 1, "bypass mode should reopen immediately with downstream ready")
    _expect(sim, "valid_o", 1, "bypass mode should keep valid high while input is valid")
    _expect(sim, "data_o", 0xA5, "bypass mode should update data immediately")

    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "bypass mode should clear valid immediately")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _run_non_bypass(design, engine)
        _run_bypass(design, engine)
    except Exception as exc:
        print(f"  FAIL spill_register python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS spill_register python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("spill_register example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing spill_register example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / ".pcache")
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
