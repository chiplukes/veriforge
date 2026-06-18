"""Run the imported common_cells stream_delay example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_delay/run_sim.py
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
RTL_FILE = SCRIPT_DIR / "rtl" / "stream_delay.sv"
TB_FILE = SCRIPT_DIR / "tb" / "stream_delay_tb_local.sv"
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
    top = design.get_module("stream_delay_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_delay_tb_local' not found")

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


def _check_stalled_sink(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    _expect(sim, "valid_o", 0, "stream_delay should be idle after reset")
    _expect(sim, "ready_o", 0, "stream_delay should not assert ready after reset")

    step_drive(sim, engine, "data_i", 0x34)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_delay should not assert valid immediately")
    _expect(sim, "ready_o", 0, "stream_delay should not assert ready during the delay window")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_delay first delay edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_delay should still be delaying after the first edge")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_delay second delay edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_delay should assert valid after two delay edges")
    _expect(sim, "data_o", 0x34, "stream_delay should preserve the payload through the delay")
    _expect(sim, "ready_o", 0, "stream_delay should keep ready low while the sink stalls")

    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_delay should hold valid until the delayed transfer is accepted")
    _expect(sim, "ready_o", 1, "stream_delay should reflect ready once the sink can accept")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_delay accept edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_delay should return to idle after acceptance")
    _expect(sim, "ready_o", 0, "stream_delay should clear ready again once idle")


def _check_ready_preasserted(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)

    step_drive(sim, engine, "data_i", 0x56)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_delay should not bypass the delay when ready is already high")
    _expect(sim, "ready_o", 0, "stream_delay should keep ready low until the delay expires")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_delay pre-ready first edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_delay should still be delaying after the first pre-ready edge")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_delay pre-ready second edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_delay should assert valid after the same two-edge delay when ready is high")
    _expect(sim, "ready_o", 1, "stream_delay should expose ready once the delayed transfer becomes valid")
    _expect(sim, "data_o", 0x56, "stream_delay should preserve the second payload through the delay")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _check_stalled_sink(design, engine)
        _check_ready_preasserted(design, engine)
    except Exception as exc:
        print(f"  FAIL stream_delay python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_delay python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_delay example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_delay example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_stream_delay_pcache")
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
