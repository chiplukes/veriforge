"""Run the typed upstream-style stream_xbar stress example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_xbar_typed/run_sim.py
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
TB_FILE = SCRIPT_DIR / "tb" / "sxt_tb.sv"
FILES = [
    str(RTL_DIR / "stream_demux.sv"),
    str(RTL_DIR / "spill_register.sv"),
    str(RTL_DIR / "rr_arb_tree.sv"),
    str(RTL_DIR / "stream_xbar.sv"),
    str(TB_FILE),
]
MAX_TIME = 240
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
    step_drive(sim, engine, "flush", 0)
    step_drive(sim, engine, "data_i", 0)
    step_drive(sim, engine, "sel_i", 0)
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


def _pack_word(payload: int, meta: int) -> int:
    return ((meta & 0xF) << 8) | (payload & 0xFF)


def _pack_inputs(d0: int, d1: int, d2: int) -> int:
    return (d2 << 24) | (d1 << 12) | d0


def _payload(word: int) -> int:
    return word & 0xFF


def _check_nospill(design, engine: str) -> None:
    sim = _make_step_sim(design, "sxt0_tb", engine)

    _expect(sim, "valid_o", 0, "typed stream_xbar should be idle after reset")

    step_drive(sim, engine, "ready_i", 0b11)
    step_drive(sim, engine, "data_i", _pack_inputs(_pack_word(0xA0, 0x1), _pack_word(0xB1, 0x2), _pack_word(0xC2, 0x3)))
    step_drive(sim, engine, "sel_i", 0b100)
    step_drive(sim, engine, "valid_i", 0b101)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b11, "typed stream_xbar should route two independent outputs")
    _expect(
        sim,
        "data_o",
        _pack_inputs(_pack_word(0xA0, 0x1), _pack_word(0xC2, 0x3), 0) & 0xFFFFFF,
        "typed stream_xbar payload/meta routing mismatch",
    )
    _expect(sim, "idx_o", 0b1000, "typed stream_xbar idx routing mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed independent-routing edge not observed")

    step_drive(sim, engine, "data_i", _pack_inputs(_pack_word(0x10, 0x4), _pack_word(0x21, 0x5), _pack_word(0x32, 0x6)))
    step_drive(sim, engine, "sel_i", 0b000)
    step_drive(sim, engine, "valid_i", 0b011)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b01, "typed stream_xbar should contend on output 0")
    _expect(sim, "data_o", _pack_word(0x10, 0x4), "typed stream_xbar should grant input 0 first")
    _expect(sim, "idx_o", 0b0000, "typed stream_xbar should report input 0 first")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed contention edge not observed")

    _expect(sim, "data_o", _pack_word(0x21, 0x5), "typed stream_xbar should rotate to input 1")
    _expect(sim, "idx_o", 0b0001, "typed stream_xbar should report input 1 after rotation")


def _check_spill(design, engine: str) -> None:
    sim = _make_step_sim(design, "sxt1_tb", engine)

    step_drive(sim, engine, "ready_i", 0b00)
    step_drive(sim, engine, "data_i", _pack_inputs(_pack_word(0x44, 0x1), _pack_word(0x55, 0x2), _pack_word(0x66, 0x3)))
    step_drive(sim, engine, "sel_i", 0b000)
    step_drive(sim, engine, "valid_i", 0b011)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b00, "typed stream_xbar spill outputs should stay empty before capture")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed spill first capture edge not observed")
    _expect(sim, "valid_o", 0b01, "typed stream_xbar spill should capture first contender")
    _expect(sim, "data_o", _pack_word(0x44, 0x1), "typed stream_xbar spill should preserve struct payload")
    _expect(sim, "idx_o", 0b0000, "typed stream_xbar spill should preserve source index")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _check_nospill(design, engine)
        _check_spill(design, engine)
    except Exception as exc:
        print(f"  FAIL typed stream_xbar python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS typed stream_xbar python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    print("Parsing typed stream_xbar example...")
    os.chdir(SCRIPT_DIR)
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / ".pcache")
    except Exception:
        traceback.print_exc()
        return 1

    print(f"  parsed {len(design.modules)} modules in {time.time() - t0:.2f}s")

    status = 0
    for engine in ENGINES:
        status |= _run_engine(design, engine)
    return status


if __name__ == "__main__":
    sys.exit(main())
