"""Run the imported common_cells stream_demux example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_demux/run_sim.py
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
RTL_FILE = SCRIPT_DIR / "rtl" / "stream_demux.sv"
TB_FILE = SCRIPT_DIR / "tb" / "stream_demux_tb_local.sv"
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


def _settle_drives(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=0)
    else:
        step_eval_now(sim)


def _make_sim(design, engine: str) -> Simulator:
    top = design.get_module("stream_demux_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_demux_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_sel_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    return sim


def _run_checks(sim: Simulator, engine: str) -> None:
    _expect(sim, "oup_valid_o", 0b000, "stream_demux should be idle when input valid is low")
    _expect(sim, "inp_ready_o", 0, "stream_demux ready should reflect selected output ready")

    step_drive(sim, engine, "oup_ready_i", 0b001)
    _settle_drives(sim, engine)
    _expect(sim, "inp_ready_o", 1, "stream_demux should return selected output 0 ready even when idle")

    step_drive(sim, engine, "inp_valid_i", 1)
    step_drive(sim, engine, "oup_sel_i", 1)
    step_drive(sim, engine, "oup_ready_i", 0b001)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0b010, "stream_demux should assert only selected output 1 valid")
    _expect(sim, "inp_ready_o", 0, "stream_demux input ready should follow selected output 1 ready")

    step_drive(sim, engine, "oup_ready_i", 0b010)
    _settle_drives(sim, engine)
    _expect(sim, "inp_ready_o", 1, "stream_demux should return selected output 1 ready")

    step_drive(sim, engine, "oup_sel_i", 2)
    step_drive(sim, engine, "oup_ready_i", 0b100)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0b100, "stream_demux should reroute valid immediately when select changes")
    _expect(sim, "inp_ready_o", 1, "stream_demux should reroute ready immediately when select changes")

    step_drive(sim, engine, "inp_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0b000, "stream_demux should clear valid fanout when input valid drops")
    _expect(sim, "inp_ready_o", 1, "stream_demux ready should still reflect selected output when idle")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_sim(design, engine)
        _run_checks(sim, engine)
    except Exception as exc:
        print(f"  FAIL stream_demux python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_demux python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_demux example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_demux example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_stream_demux_pcache")
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
