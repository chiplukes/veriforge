"""Run the imported common_cells stream_join example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_join/run_sim.py
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
RTL_DIR = SCRIPT_DIR / "rtl"
TB_FILE = SCRIPT_DIR / "tb" / "stream_join_tb_local.sv"
FILES = [
    str(RTL_DIR / "stream_join_dynamic.sv"),
    str(RTL_DIR / "stream_join.sv"),
    str(TB_FILE),
]
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
    top = design.get_module("stream_join_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_join_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    return sim


def _run_checks(sim: Simulator, engine: str) -> None:
    _expect(sim, "oup_valid_o", 0, "stream_join should be idle with no valid inputs")
    _expect(sim, "inp_ready_o", 0, "stream_join should not ready any input while idle")

    step_drive(sim, engine, "inp_valid_i", 0b101)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0, "partial-valid inputs should not assert joined valid")
    _expect(sim, "inp_ready_o", 0, "partial-valid inputs should not see ready fanout")

    step_drive(sim, engine, "inp_valid_i", 0b111)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 1, "all inputs valid should assert joined valid")
    _expect(sim, "inp_ready_o", 0, "stalled downstream should block input ready fanout")

    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 1, "joined valid should stay asserted while all inputs remain valid")
    _expect(sim, "inp_ready_o", 0b111, "joined handshake should fan ready to all inputs at once")

    step_drive(sim, engine, "inp_valid_i", 0b110)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0, "dropping one input should deassert joined valid immediately")
    _expect(sim, "inp_ready_o", 0, "dropping one input should remove ready fanout")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_sim(design, engine)
        _run_checks(sim, engine)
    except Exception as exc:
        print(f"  FAIL stream_join python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_join python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_join example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_join example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_stream_join_pcache")
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
