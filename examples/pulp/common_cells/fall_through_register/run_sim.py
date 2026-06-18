"""Run the imported common_cells fall_through_register example.

Run from the repository root:

    uv run python examples/pulp/common_cells/fall_through_register/run_sim.py
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
TB_FILE = SCRIPT_DIR / "tb" / "ft_reg_tb.sv"
FILES = [
    str(RTL_DIR / "fifo_v3.sv"),
    str(RTL_DIR / "fall_through_register.sv"),
    str(TB_FILE),
]
MAX_TIME = 200
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


def _make_step_sim(design, engine: str) -> Simulator:
    top = design.get_module("ft_reg_tb")
    if top is None:
        raise RuntimeError("Top module 'ft_reg_tb' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "clr", 0)
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


def _check_empty_pass_through(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "ready_i", 1)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x11)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "empty fall-through register should assert valid immediately")
    _expect(sim, "ready_o", 1, "empty fall-through register should remain ready immediately")
    _expect(sim, "data_o", 0x11, "empty fall-through register should forward data immediately")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "pass-through handshake edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "same-cycle accepted item should leave the register empty")
    _expect(sim, "ready_o", 1, "register should reopen after same-cycle pass-through")


def _check_stall_and_drain(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x22)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stalled empty register should still assert valid immediately")
    _expect(sim, "ready_o", 1, "stalled empty register should show default ready before capture")
    _expect(sim, "data_o", 0x22, "stalled empty register should forward input data immediately")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stall capture edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "captured stalled item should remain valid")
    _expect(sim, "data_o", 0x22, "captured stalled item payload mismatch")
    _expect(sim, "ready_o", 0, "depth-1 fall-through register should backpressure once filled")

    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x33)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 0, "full fall-through register should hold backpressure combinationally")
    _expect(sim, "data_o", 0x22, "full fall-through register should keep the stored head stable")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "blocked input edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "data_o", 0x22, "blocked input should not replace stored data")

    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stored item should remain valid until the drain edge")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "drain edge not observed")
    _expect(sim, "valid_o", 0, "register should empty after draining the stored item")
    _expect(sim, "ready_o", 1, "register should reopen after draining")


def _check_synchronous_clear(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x44)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "clear setup capture edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "clear scenario should start with a stored item")

    step_drive(sim, engine, "clr", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "clear edge not observed")
    step_drive(sim, engine, "clr", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "synchronous clear should empty the fall-through register")
    _expect(sim, "ready_o", 1, "synchronous clear should restore ready")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_step_sim(design, engine)

        _expect(sim, "valid_o", 0, "fall-through register should be empty after reset")
        _expect(sim, "ready_o", 1, "fall-through register should be ready after reset")

        _check_empty_pass_through(sim, engine)
        _check_stall_and_drain(sim, engine)
        _check_synchronous_clear(sim, engine)
    except Exception as exc:
        print(f"  FAIL fall_through_register python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS fall_through_register python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("fall_through_register example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing fall_through_register example...")
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
