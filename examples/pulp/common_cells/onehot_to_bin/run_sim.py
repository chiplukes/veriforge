"""Run the imported common_cells onehot_to_bin example.

Run from the repository root:

    uv run python examples/pulp/common_cells/onehot_to_bin/run_sim.py
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.example_runner import available_engines, display_lines
from veriforge.sim.testbench import Simulator

SCRIPT_DIR = Path(__file__).resolve().parent
RTL_FILE = SCRIPT_DIR / "rtl" / "onehot_to_bin.sv"
TB_FILE = SCRIPT_DIR / "tb" / "onehot_to_bin_tb_local.sv"
FILES = [str(RTL_FILE), str(TB_FILE)]
MAX_TIME = 20
ENGINES = available_engines()


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    top = design.get_module("onehot_to_bin_tb_local")
    if top is None:
        raise RuntimeError("Top module 'onehot_to_bin_tb_local' not found")

    t0 = time.time()
    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=MAX_TIME)
    elapsed = time.time() - t0

    lines = display_lines(sim)
    for line in lines:
        print(f"  {line}")

    if any("FAIL" in line for line in lines):
        print(f"  engine={engine} failed in {elapsed:.2f}s")
        return 1

    if not any("PASS" in line for line in lines):
        print(f"  engine={engine} produced no PASS marker in {elapsed:.2f}s")
        return 1

    print(f"  engine={engine} passed in {elapsed:.2f}s at sim time {sim.time}")
    return 0


def main() -> int:
    missing = [path for path in (RTL_FILE, TB_FILE) if not path.exists()]
    if missing:
        print("onehot_to_bin example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing onehot_to_bin example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_onehot_to_bin_pcache")
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
