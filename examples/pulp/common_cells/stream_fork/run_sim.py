"""Run the imported common_cells stream_fork example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_fork/run_sim.py
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
RTL_FILE = SCRIPT_DIR / "rtl" / "stream_fork.sv"
TB_FILE = SCRIPT_DIR / "tb" / "stream_fork_tb_local.sv"
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
    top = design.get_module("stream_fork_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_fork_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
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


def _check_partial_handshakes(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b111, "stream_fork should fan out valid to every output when a transfer starts")
    _expect(sim, "ready_o", 0, "stream_fork should not accept the input until every output has handshaked")

    step_drive(sim, engine, "ready_i", 0b001)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b111, "stream_fork should still present all outputs before the first handshake edge")
    _expect(sim, "ready_o", 0, "stream_fork should stay blocked until every output is ready")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "first partial handshake edge not observed")

    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b110, "stream_fork should remember that output 0 has already handshaked")
    _expect(sim, "ready_o", 0, "stream_fork should keep input ready low while outputs remain pending")

    step_drive(sim, engine, "ready_i", 0b100)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b110, "stream_fork should keep only the remaining outputs pending before the next edge")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "second partial handshake edge not observed")

    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b010, "stream_fork should remember that only output 1 is still pending")
    _expect(sim, "ready_o", 0, "stream_fork should not accept the input before the final output is served")


def _check_final_accept_and_restart(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "ready_i", 0b010)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b010, "stream_fork should present only the last pending output before final acceptance")
    _expect(sim, "ready_o", 1, "stream_fork should accept the input when the last pending output is ready")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "final handshake edge not observed")

    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b000, "stream_fork should return to idle after the input handshake completes")
    _expect(sim, "ready_o", 0, "idle stream_fork should not assert input ready")

    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 0b111)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b111, "stream_fork should restart cleanly for a fully-ready transaction")
    _expect(sim, "ready_o", 1, "stream_fork should accept immediately when every output is ready")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "all-ready handshake edge not observed")

    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b000, "stream_fork should remain idle after the all-ready transaction")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_step_sim(design, engine)

        _expect(sim, "valid_o", 0b000, "stream_fork should be idle after reset")
        _expect(sim, "ready_o", 0, "stream_fork should not assert input ready after reset")

        _check_partial_handshakes(sim, engine)
        _check_final_accept_and_restart(sim, engine)
    except Exception as exc:
        print(f"  FAIL stream_fork python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_fork python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_fork example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_fork example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_stream_fork_pcache")
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
