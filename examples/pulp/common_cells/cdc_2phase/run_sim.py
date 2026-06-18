"""Run the imported common_cells cdc_2phase example.

Run from the repository root:

    uv run python examples/pulp/common_cells/cdc_2phase/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "cdc_2phase.sv"),
    str(SCRIPT_DIR / "tb" / "cdc_2phase_tb_local.sv"),
]
MAX_TIME = 320
ENGINES = available_engines()
FIRST_WORD = 0x11
BLOCKED_WORD = 0x22
SECOND_WORD = 0x33


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
        step_eval_now(sim, "src_clk_i")


def _run_until_condition(sim: Simulator, target_time: int, predicate, message: str) -> None:
    while sim.time < target_time:
        if predicate(sim):
            return
        if not sim.run_step():
            raise RuntimeError(f"stepped engine stopped before {message}")
    if not predicate(sim):
        raise RuntimeError(message)


def _run_until_rising_edge(sim: Simulator, signal_name: str, target_time: int, message: str) -> None:
    previous = _read_int(sim, signal_name)
    while sim.time < target_time:
        if not sim.run_step():
            raise RuntimeError(f"stepped engine stopped before {message}")
        current = _read_int(sim, signal_name)
        if previous == 0 and current == 1:
            return
        previous = current
    raise RuntimeError(message)


def _make_step_sim(design, engine: str) -> Simulator:
    top = design.get_module("cdc_2phase_tb_local")
    if top is None:
        raise RuntimeError("Top module 'cdc_2phase_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "src_clk_i", 0)
    step_drive(sim, engine, "dst_clk_i", 0)
    step_drive(sim, engine, "src_rst_ni", 0)
    step_drive(sim, engine, "dst_rst_ni", 0)
    step_drive(sim, engine, "src_data_i", 0)
    step_drive(sim, engine, "src_valid_i", 0)
    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), MAX_TIME)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=14), MAX_TIME)
    _settle_drives(sim, engine)
    return sim


def _release_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 45)
    _expect(sim, "src_ready_o", 1, "source should be ready after reset")
    _expect(sim, "dst_valid_o", 0, "destination should be idle after reset")


def _run_engine_checks(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    _release_reset(sim, engine)

    step_drive(sim, engine, "src_data_i", FIRST_WORD)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", 90, "source write edge not observed for first transfer")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "src_ready_o", 0, "source should backpressure while the first transfer is in flight")

    _run_until_condition(
        sim,
        160,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == FIRST_WORD,
        "first transfer never became visible at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should remain blocked until the destination acknowledges")

    step_drive(sim, engine, "src_data_i", BLOCKED_WORD)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", 190, "blocked source attempt edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "src_ready_o", 0, "blocked source attempt should not reopen ready")
    _expect(sim, "dst_data_o", FIRST_WORD, "blocked source attempt should not overwrite the in-flight payload")

    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        220,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "first transfer never drained from the destination",
    )
    _run_until_condition(
        sim,
        260,
        lambda s: _read_int(s, "src_ready_o") == 1,
        "source ready never reopened after the first acknowledgement",
    )
    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine)

    step_drive(sim, engine, "src_data_i", SECOND_WORD)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", 280, "source write edge not observed for second transfer")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine)

    _run_until_condition(
        sim,
        310,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == SECOND_WORD,
        "second transfer never became visible at the destination",
    )


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _run_engine_checks(design, engine)
    except Exception as exc:
        print(f"  FAIL cdc_2phase python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS cdc_2phase python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [path for path in map(Path, FILES) if not path.exists()]
    if missing:
        print("cdc_2phase example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing cdc_2phase example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_cdc_2phase_pcache")
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
