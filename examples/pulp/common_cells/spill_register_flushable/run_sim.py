"""Run the imported common_cells spill_register_flushable example.

Run from the repository root:

    uv run python examples/pulp/common_cells/spill_register_flushable/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "spill_register_flushable.sv"),
    str(SCRIPT_DIR / "tb" / "spill_register_flushable_tb_local.sv"),
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
    step_drive(sim, engine, "flush", 0)
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


def _tx(sim: Simulator, engine: str, values: dict[str, int]) -> None:
    step_drive(sim, engine, "data_i", values.get("data_i", 0))
    step_drive(sim, engine, "valid_i", values.get("valid_i", 0))
    step_drive(sim, engine, "ready_i", values.get("ready_i", 0))
    step_drive(sim, engine, "flush", values.get("flush", 0))
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "next rising clock edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "flush", 0)
    _settle_drives(sim, engine)


def _run_non_bypass(design, engine: str) -> None:
    sim = _make_step_sim(design, "spill_reg_flush_tb", engine)

    _expect(sim, "valid_o", 0, "spill_register_flushable should be empty after reset")
    _expect(sim, "ready_o", 1, "spill_register_flushable should be ready after reset")

    _tx(sim, engine, {"valid_i": 1, "data_i": 0x12})
    _expect(sim, "valid_o", 1, "spill_register_flushable should capture the first stalled item")
    _expect(sim, "data_o", 0x12, "spill_register_flushable first payload mismatch")
    _expect(sim, "ready_o", 1, "spill_register_flushable should still accept one more item")

    _tx(sim, engine, {"valid_i": 1, "data_i": 0x34})
    _expect(sim, "valid_o", 1, "spill_register_flushable should remain occupied with two queued items")
    _expect(sim, "data_o", 0x12, "spill_register_flushable should keep the oldest payload at the output")
    _expect(sim, "ready_o", 0, "spill_register_flushable should backpressure once both stages are full")

    _tx(sim, engine, {"valid_i": 1, "data_i": 0x56, "flush": 1})
    _expect(sim, "valid_o", 0, "spill_register_flushable flush should clear both buffered stages")
    _expect(sim, "ready_o", 1, "spill_register_flushable flush should restore ready")

    _tx(sim, engine, {"valid_i": 1, "data_i": 0x78})
    _expect(sim, "valid_o", 1, "spill_register_flushable should accept a new item after flush")
    _expect(sim, "data_o", 0x78, "spill_register_flushable refill payload mismatch")
    _expect(sim, "ready_o", 1, "spill_register_flushable should remain open with one queued item")

    _tx(sim, engine, {"ready_i": 1})
    _expect(sim, "valid_o", 0, "spill_register_flushable should drain cleanly after refill")
    _expect(sim, "ready_o", 1, "spill_register_flushable should be ready again after draining")


def _run_bypass(design, engine: str) -> None:
    sim = _make_step_sim(design, "spill_reg_flush_bp_tb", engine)

    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "flush", 1)
    step_drive(sim, engine, "data_i", 0x9A)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "spill_register_flushable bypass should pass valid combinationally")
    _expect(sim, "ready_o", 0, "spill_register_flushable bypass should pass ready combinationally")
    _expect(sim, "data_o", 0x9A, "spill_register_flushable bypass should pass data combinationally")

    step_drive(sim, engine, "ready_i", 1)
    step_drive(sim, engine, "flush", 0)
    step_drive(sim, engine, "data_i", 0xBC)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 1, "spill_register_flushable bypass should reopen immediately")
    _expect(sim, "valid_o", 1, "spill_register_flushable bypass should remain transparent with valid input")
    _expect(sim, "data_o", 0xBC, "spill_register_flushable bypass should update data immediately")

    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "spill_register_flushable bypass should clear valid immediately")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _run_non_bypass(design, engine)
        _run_bypass(design, engine)
    except Exception as exc:
        print(f"  FAIL spill_register_flushable python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS spill_register_flushable python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("spill_register_flushable example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing spill_register_flushable example...")
    t0 = time.time()
    try:
        design = parse_files(
            FILES,
            preprocess=True,
            cache_dir=SCRIPT_DIR / "_vtc_spill_register_flushable_pcache",
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
