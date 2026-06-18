"""Run the imported common_cells stream_register example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_register/run_sim.py
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
RTL_FILE = SCRIPT_DIR / "rtl" / "stream_register.sv"
TB_FILE = SCRIPT_DIR / "tb" / "stream_register_tb_local.sv"
FILES = [str(RTL_FILE), str(TB_FILE)]
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


def _make_step_sim(design, engine: str) -> Simulator:
    top = design.get_module("stream_register_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_register_tb_local' not found")

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


def _check_capture_without_passthrough(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "ready_i", 1)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x11)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_register should not pass valid combinationally")
    _expect(sim, "ready_o", 1, "empty stream_register should accept input")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "capture edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "captured item should become valid after one edge")
    _expect(sim, "data_o", 0x11, "captured item payload mismatch")


def _check_blocked_overwrite_and_refill(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 0, "full stream_register should block new input while stalled")

    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x22)
    _settle_drives(sim, engine)
    _expect(sim, "data_o", 0x11, "blocked overwrite should not disturb the buffered head")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "blocked overwrite edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "blocked overwrite should leave the stored item valid")
    _expect(sim, "data_o", 0x11, "blocked overwrite should preserve the stored payload")

    step_drive(sim, engine, "ready_i", 1)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x33)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 1, "stream_register should reopen immediately when draining")
    _expect(sim, "data_o", 0x11, "old payload should remain visible until the drain edge")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "drain/refill edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "drain/refill should keep the register occupied")
    _expect(sim, "data_o", 0x33, "drain/refill should replace the payload with the new item")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "final drain edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_register should empty after draining the replacement item")
    _expect(sim, "ready_o", 1, "empty stream_register should become ready again")


def _check_synchronous_clear(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x44)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "clear setup edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "clear scenario should start with a stored item")
    _expect(sim, "data_o", 0x44, "clear scenario payload mismatch")

    step_drive(sim, engine, "clr", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "clear edge not observed")
    step_drive(sim, engine, "clr", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "synchronous clear should empty the stream_register")
    _expect(sim, "ready_o", 1, "synchronous clear should restore ready")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_step_sim(design, engine)

        _expect(sim, "valid_o", 0, "stream_register should be empty after reset")
        _expect(sim, "ready_o", 1, "stream_register should be ready after reset")

        _check_capture_without_passthrough(sim, engine)
        _check_blocked_overwrite_and_refill(sim, engine)
        _check_synchronous_clear(sim, engine)
    except Exception as exc:
        print(f"  FAIL stream_register python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_register python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_register example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_register example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_stream_register_pcache")
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
