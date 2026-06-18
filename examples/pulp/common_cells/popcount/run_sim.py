"""Run the imported common_cells popcount example.

Run from the repository root:

    uv run python examples/pulp/common_cells/popcount/run_sim.py
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
RTL_FILE = SCRIPT_DIR / "rtl" / "popcount.sv"
TB_FILE = SCRIPT_DIR / "tb" / "popcount_tb_local.sv"
VM_TB_FILE = SCRIPT_DIR / "tb" / "popcount_tb_vm_local.sv"
FILES = [str(RTL_FILE), str(TB_FILE), str(VM_TB_FILE)]
MAX_TIME = 100
ENGINES = available_engines()


TOP_MODULES = {
    "reference": "popcount_tb_local",
    "vm": "popcount_tb_vm_local",
    "compiled": "popcount_tb_vm_local",
}


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    top_name = TOP_MODULES[engine]
    top = design.get_module(top_name)
    if top is None:
        raise RuntimeError(f"Top module {top_name!r} not found")

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
    missing = [path for path in (RTL_FILE, TB_FILE, VM_TB_FILE) if not path.exists()]
    if missing:
        print("popcount example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        print("")
        print("Next steps:")
        print("  1. Vendor common_cells/src/popcount.sv into rtl/")
        print("  2. Add a deterministic local wrapper in tb/")
        print("  3. Wire this runner to parse and simulate both files")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing popcount example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / ".pcache")
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

    if "compiled" in ENGINES:
        print("\nCompiled validation uses the reduced-width local wrapper to avoid the 981-bit instance.")
    else:
        print("\nCompiled engine skipped: Cython or a supported C compiler is not available.")

    print("VM validation uses a reduced-width local wrapper to keep runtime practical.")
    return status


if __name__ == "__main__":
    sys.exit(main())
