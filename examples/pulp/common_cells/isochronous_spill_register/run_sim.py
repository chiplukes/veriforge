"""Run the imported common_cells isochronous_spill_register example.

Run from the repository root:

    uv run python examples/pulp/common_cells/isochronous_spill_register/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "isochronous_spill_register.sv"),
    str(SCRIPT_DIR / "tb" / "isochronous_spill_register_tb_local.sv"),
]
MAX_TIME = 260
ENGINES = available_engines()
FIRST_WORD = 0x11
SECOND_WORD = 0x22
BYPASS_WORD0 = 0x5A
BYPASS_WORD1 = 0xA5


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


def _make_step_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    if top is None:
        raise RuntimeError(f"Top module {top_name!r} not found")

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
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=20), MAX_TIME)
    _settle_drives(sim, engine)
    return sim


def _release_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 36)


def _release_non_bypass_reset(sim: Simulator, engine: str) -> None:
    _release_reset(sim, engine)
    _expect(sim, "src_ready_o", 1, "source should be ready after reset")
    _expect(sim, "dst_valid_o", 0, "destination should be idle after reset")


def _wait_for_source_low(sim: Simulator) -> None:
    _run_until_condition(
        sim,
        sim.time + 20,
        lambda s: _read_int(s, "src_clk_i") == 0,
        "source clock never reached a low phase before the next drive",
    )


def _run_non_bypass(design, engine: str) -> None:
    sim = _make_step_sim(design, "isochronous_spill_register_tb_local", engine)
    _release_non_bypass_reset(sim, engine)

    _wait_for_source_low(sim)
    step_drive(sim, engine, "src_data_i", FIRST_WORD)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", 60, "source write edge not observed for first queued item")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "src_ready_o", 1, "source should still have one free slot after the first write")

    _wait_for_source_low(sim)
    step_drive(sim, engine, "src_data_i", SECOND_WORD)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", 80, "source write edge not observed for second queued item")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        90,
        lambda s: _read_int(s, "src_ready_o") == 0,
        "spill register never reported full after two queued items",
    )

    _run_until_condition(
        sim,
        120,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == FIRST_WORD,
        "first queued item never appeared at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should stay blocked while both entries remain occupied")

    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        170,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == SECOND_WORD,
        "second queued item never surfaced after draining the first",
    )
    _run_until_condition(
        sim,
        190,
        lambda s: _read_int(s, "src_ready_o") == 1,
        "source ready never reopened after draining one entry",
    )
    _run_until_condition(
        sim,
        220,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "destination valid never cleared after draining the second entry",
    )


def _run_bypass(design, engine: str) -> None:
    sim = _make_step_sim(design, "isochronous_spill_register_bypass_tb_local", engine)
    _release_reset(sim, engine)

    step_drive(sim, engine, "dst_ready_i", 0)
    step_drive(sim, engine, "src_valid_i", 1)
    step_drive(sim, engine, "src_data_i", BYPASS_WORD0)
    _settle_drives(sim, engine)
    _expect(sim, "src_ready_o", 0, "bypass mode should pass destination ready combinationally")
    _expect(sim, "dst_valid_o", 1, "bypass mode should pass source valid combinationally")
    _expect(sim, "dst_data_o", BYPASS_WORD0, "bypass mode should pass source data combinationally")

    step_drive(sim, engine, "dst_ready_i", 1)
    step_drive(sim, engine, "src_data_i", BYPASS_WORD1)
    _settle_drives(sim, engine)
    _expect(sim, "src_ready_o", 1, "bypass mode should reopen immediately")
    _expect(sim, "dst_valid_o", 1, "bypass mode should remain transparent while valid is asserted")
    _expect(sim, "dst_data_o", BYPASS_WORD1, "bypass mode should update data immediately")

    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "dst_valid_o", 0, "bypass mode should clear valid immediately")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _run_non_bypass(design, engine)
        _run_bypass(design, engine)
    except Exception as exc:
        print(f"  FAIL isochronous_spill_register python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS isochronous_spill_register python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [path for path in map(Path, FILES) if not path.exists()]
    if missing:
        print("isochronous_spill_register example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing isochronous_spill_register example...")
    t0 = time.time()
    try:
        design = parse_files(
            FILES,
            preprocess=True,
            cache_dir=SCRIPT_DIR / "_vtc_isochronous_spill_register_pcache",
        )
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
