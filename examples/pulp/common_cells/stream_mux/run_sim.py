"""Run the imported common_cells stream_mux example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_mux/run_sim.py
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.example_runner import available_engines
from veriforge.sim.step_harness import step_drive, step_eval_now
from veriforge.sim.testbench import Simulator


SCRIPT_DIR = Path(__file__).resolve().parent
RTL_FILE = SCRIPT_DIR / "rtl" / "stream_mux.sv"
TB_FILE = SCRIPT_DIR / "tb" / "stream_mux_tb_local.sv"
FILES = [str(RTL_FILE), str(TB_FILE)]
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


def _pack_inputs(d0: int, d1: int, d2: int) -> int:
    return (d2 << 16) | (d1 << 8) | d0


def _settle_drives(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=0)
    else:
        step_eval_now(sim)


def _make_sim(design, engine: str) -> Simulator:
    top = design.get_module("stream_mux_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_mux_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "inp_data_i", 0)
    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "inp_sel_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    return sim


def _run_checks(sim: Simulator, engine: str) -> None:
    _expect(sim, "oup_valid_o", 0, "stream_mux should be idle when the selected input is invalid")
    _expect(sim, "inp_ready_o", 0, "stream_mux should not fan out ready while downstream stalls")

    step_drive(sim, engine, "inp_data_i", _pack_inputs(0x11, 0x22, 0x33))
    step_drive(sim, engine, "inp_valid_i", 0b010)
    step_drive(sim, engine, "inp_sel_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0x22, "stream_mux should route selected input 1 data")
    _expect(sim, "oup_valid_o", 1, "stream_mux should route selected input 1 valid")
    _expect(sim, "inp_ready_o", 0, "stream_mux should keep ready low while downstream stalls")

    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "inp_ready_o", 0b010, "stream_mux should fan ready only to the selected input")

    step_drive(sim, engine, "inp_valid_i", 0b101)
    step_drive(sim, engine, "inp_sel_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0x11, "stream_mux should reroute data immediately when select changes")
    _expect(sim, "oup_valid_o", 1, "stream_mux should reroute valid immediately when select changes")
    _expect(sim, "inp_ready_o", 0b001, "stream_mux should move ready fanout with the selection")

    step_drive(sim, engine, "inp_sel_i", 2)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0x33, "stream_mux should route selected input 2 data")
    _expect(sim, "oup_valid_o", 1, "stream_mux should route selected input 2 valid")
    _expect(sim, "inp_ready_o", 0b100, "stream_mux should fan ready only to selected input 2")

    step_drive(sim, engine, "inp_valid_i", 0b001)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0, "non-selected valids should not assert output valid")
    _expect(sim, "oup_data_o", 0x33, "selected data path should remain selected even when invalid")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_sim(design, engine)
        _run_checks(sim, engine)
    except Exception as exc:
        print(f"  FAIL stream_mux python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_mux python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_mux example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_mux example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_stream_mux_pcache")
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
