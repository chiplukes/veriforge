"""Run the imported common_cells stream_filter example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_filter/run_sim.py
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
RTL_FILE = SCRIPT_DIR / "rtl" / "stream_filter.sv"
TB_FILE = SCRIPT_DIR / "tb" / "stream_filter_tb_local.sv"
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
    top = design.get_module("stream_filter_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_filter_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "drop_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    return sim


def _run_checks(sim: Simulator, engine: str) -> None:
    _expect(sim, "valid_o", 0, "stream_filter should be idle when input valid is low")
    _expect(sim, "ready_o", 0, "stream_filter should follow downstream ready in pass-through mode")

    step_drive(sim, engine, "valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_filter should pass valid through when drop is low")
    _expect(sim, "ready_o", 0, "stream_filter should keep ready low while downstream is not ready")

    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_filter should keep valid asserted in pass-through mode")
    _expect(sim, "ready_o", 1, "stream_filter should pass ready through when drop is low")

    step_drive(sim, engine, "drop_i", 1)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_filter should suppress downstream valid when drop is high")
    _expect(sim, "ready_o", 1, "stream_filter should force upstream ready high when drop is high")

    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_filter drop mode should stay invalid when input valid is low")
    _expect(sim, "ready_o", 1, "stream_filter drop mode should keep upstream ready high")

    step_drive(sim, engine, "drop_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_filter should return to pass-through mode when drop clears")
    _expect(sim, "ready_o", 0, "stream_filter should resume following downstream ready when drop clears")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_sim(design, engine)
        _run_checks(sim, engine)
    except Exception as exc:
        print(f"  FAIL stream_filter python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_filter python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_filter example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_filter example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_stream_filter_pcache")
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
