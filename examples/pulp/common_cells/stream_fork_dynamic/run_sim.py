"""Run the imported common_cells stream_fork_dynamic example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_fork_dynamic/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "stream_fork.sv"),
    str(SCRIPT_DIR / "rtl" / "stream_fork_dynamic.sv"),
    str(SCRIPT_DIR / "tb" / "stream_fork_dynamic_tb_local.sv"),
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


def _make_step_sim(design, engine: str) -> Simulator:
    top = design.get_module("stream_fork_dynamic_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_fork_dynamic_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "sel_i", 0)
    step_drive(sim, engine, "sel_valid_i", 0)
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


def _check_selector_gating(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "sel_i", 0b101)
    step_drive(sim, engine, "ready_i", 0b101)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b000, "stream_fork_dynamic should gate outputs until the selector stream is valid")
    _expect(sim, "ready_o", 0, "stream_fork_dynamic should gate input ready until the selector stream is valid")
    _expect(sim, "sel_ready_o", 0, "stream_fork_dynamic should keep selector ready low while the selector is invalid")


def _check_masked_partial_completion(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "sel_valid_i", 1)
    step_drive(sim, engine, "ready_i", 0b010)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b101, "stream_fork_dynamic should fan out valid only to the selected outputs")
    _expect(sim, "ready_o", 0, "non-selected ready bits must not complete the transaction")
    _expect(sim, "sel_ready_o", 0, "selector ready must stay low until the selected subset completes")

    step_drive(sim, engine, "ready_i", 0b001)
    _settle_drives(sim, engine)
    _expect(
        sim, "valid_o", 0b101, "stream_fork_dynamic should still present the full selected subset before the first edge"
    )
    _run_until_rising_edge(sim, "clk", sim.time + 20, "first masked partial edge not observed")

    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b100, "stream_fork_dynamic should remember that output 0 already handshaked")
    _expect(sim, "ready_o", 0, "stream_fork_dynamic should stay blocked while one selected output remains pending")

    step_drive(sim, engine, "ready_i", 0b100)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b100, "stream_fork_dynamic should present only the last selected output before completion")
    _expect(sim, "ready_o", 1, "stream_fork_dynamic should accept once the last selected output is ready")
    _expect(sim, "sel_ready_o", 1, "stream_fork_dynamic should accept the selector stream with the data handshake")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "final masked handshake edge not observed")


def _check_single_output_restart(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "sel_valid_i", 0)
    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b000, "stream_fork_dynamic should return to idle after the masked transaction completes")
    _expect(sim, "ready_o", 0, "idle stream_fork_dynamic should not assert input ready")
    _expect(sim, "sel_ready_o", 0, "idle stream_fork_dynamic should not assert selector ready")

    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "sel_i", 0b010)
    step_drive(sim, engine, "sel_valid_i", 1)
    step_drive(sim, engine, "ready_i", 0b010)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b010, "stream_fork_dynamic should route a one-hot mask to the single selected output")
    _expect(sim, "ready_o", 1, "stream_fork_dynamic should accept immediately for a ready single-output mask")
    _expect(sim, "sel_ready_o", 1, "selector ready should track the accepted single-output transaction")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_step_sim(design, engine)

        _expect(sim, "valid_o", 0b000, "stream_fork_dynamic should be idle after reset")
        _expect(sim, "ready_o", 0, "stream_fork_dynamic should not assert input ready after reset")
        _expect(sim, "sel_ready_o", 0, "stream_fork_dynamic should not assert selector ready after reset")

        _check_selector_gating(sim, engine)
        _check_masked_partial_completion(sim, engine)
        _check_single_output_restart(sim, engine)
    except Exception as exc:
        print(f"  FAIL stream_fork_dynamic python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_fork_dynamic python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_fork_dynamic example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_fork_dynamic example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_stream_fork_dynamic_pcache")
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
