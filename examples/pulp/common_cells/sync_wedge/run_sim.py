"""Run the imported common_cells sync_wedge example.

Run from the repository root:

    uv run python examples/pulp/common_cells/sync_wedge/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "sync_wedge.sv"),
    str(SCRIPT_DIR / "tb" / "sync_wedge_tb_local.sv"),
]
MAX_TIME = 190
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


def _make_step_sim(design, engine: str) -> Simulator:
    top = design.get_module("sync_wedge_tb_local")
    if top is None:
        raise RuntimeError("Top module 'sync_wedge_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk_i", 0)
    step_drive(sim, engine, "rst_ni", 1)
    step_drive(sim, engine, "en_i", 1)
    step_drive(sim, engine, "serial_i", 0)
    _settle_drives(sim, engine)
    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk_i"), period=10), MAX_TIME)
    _settle_drives(sim, engine)
    return sim


def _run_engine_checks(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    _expect(sim, "serial_o", 0, "reset should clear the sampled serial output")
    _expect(sim, "r_edge_o", 0, "reset should clear the rising-edge pulse")
    _expect(sim, "f_edge_o", 0, "reset should clear the falling-edge pulse")

    step_run_until(sim, 31)
    step_drive(sim, engine, "rst_ni", 1)
    _settle_drives(sim, engine)
    _expect(sim, "serial_o", 0, "release should not change serial_o immediately")
    _expect(sim, "r_edge_o", 0, "release should not create a rising-edge pulse")
    _expect(sim, "f_edge_o", 0, "release should not create a falling-edge pulse")

    _wait_for_clock_low(sim)
    step_drive(sim, engine, "serial_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, 60, "first rising sample edge not observed")
    _expect(sim, "r_edge_o", 0, "first synchronized stage should not pulse immediately")
    _expect(sim, "serial_o", 0, "serial_o should stay low through the first sample edge")
    _run_until_rising_edge(sim, 80, "second rising sample edge not observed")
    _expect(sim, "r_edge_o", 1, "second synchronized stage should produce the rising-edge pulse")
    _expect(sim, "serial_o", 0, "serial_o should update one cycle after the rising pulse")
    _expect(sim, "f_edge_o", 0, "rising transition should not create a falling-edge pulse")
    _run_until_rising_edge(sim, 100, "third rising sample edge not observed")
    _expect(sim, "r_edge_o", 0, "rising-edge pulse should clear on the following sample edge")
    _expect(sim, "serial_o", 1, "serial_o should go high after the rising pulse cycle")

    step_drive(sim, engine, "en_i", 0)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, 120, "disabled hold edge not observed")
    _expect(sim, "serial_o", 1, "disabled hold should preserve the sampled high level")
    _expect(sim, "r_edge_o", 0, "disabled hold should not emit a rising-edge pulse")
    _expect(sim, "f_edge_o", 0, "disabled hold should not emit a falling-edge pulse")
    step_drive(sim, engine, "en_i", 1)
    _settle_drives(sim, engine)

    _wait_for_clock_low(sim)
    step_drive(sim, engine, "serial_i", 0)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, 140, "first falling sample edge not observed")
    _expect(sim, "f_edge_o", 0, "first falling sample should not pulse immediately")
    _expect(sim, "serial_o", 1, "serial_o should stay high through the first falling sample")
    _run_until_rising_edge(sim, 160, "second falling sample edge not observed")
    _expect(sim, "f_edge_o", 1, "second synchronized stage should produce the falling-edge pulse")
    _expect(sim, "serial_o", 1, "serial_o should still be high during the falling pulse cycle")
    _expect(sim, "r_edge_o", 0, "falling transition should not create a rising-edge pulse")
    _run_until_rising_edge(sim, 180, "third falling sample edge not observed")
    _expect(sim, "f_edge_o", 0, "falling-edge pulse should clear on the following sample edge")
    _expect(sim, "serial_o", 0, "serial_o should return low after the falling pulse cycle")

    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine)
    _expect(sim, "serial_o", 0, "async reset reassertion should clear serial_o immediately")
    _expect(sim, "r_edge_o", 0, "async reset reassertion should clear r_edge_o immediately")
    _expect(sim, "f_edge_o", 0, "async reset reassertion should clear f_edge_o immediately")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _run_engine_checks(design, engine)
    except Exception as exc:
        print(f"  FAIL sync_wedge python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS sync_wedge python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [path for path in map(Path, FILES) if not path.exists()]
    if missing:
        print("sync_wedge example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing sync_wedge example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_sync_wedge_pcache")
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
