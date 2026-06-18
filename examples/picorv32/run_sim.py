"""PicoRV32 RV32I instruction test runner.

Parses and simulates the PicoRV32 RISC-V CPU running a comprehensive
RV32I instruction test firmware.  Reports pass/fail per test group.

Run from the repository root:

    uv run python examples/picorv32/run_sim.py

Generate firmware first:

    uv run python examples/picorv32/gen_firmware.py
"""

import os
import sys
import time
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(SCRIPT_DIR, "sim")
RTL_DIR = os.path.join(SCRIPT_DIR, "rtl")

# $readmemh path in testbench is relative, so set CWD
os.chdir(SIM_DIR)

from veriforge.project import parse_files  # noqa: E402
from veriforge.sim.testbench import Simulator  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────
MAX_TIME = 200_000
ENGINE = "reference"  # "reference", "vm", or "compiled"

FILES = [
    os.path.join(RTL_DIR, "picorv32.v"),
    os.path.join(SIM_DIR, "testbench.v"),
]

# Test result addresses (must match gen_firmware.py)
RESULT_BASE = 0x600
TEST_NAMES = [
    "LUI/AUIPC",
    "JAL/JALR",
    "BRANCH",
    "LOAD/STORE (word)",
    "ALU immediate",
    "ALU register",
    "SHIFT",
    "COMPARE (SLT)",
    "LOGICAL",
    "LOAD/STORE (byte)",
    "LOAD/STORE (half)",
]


def main() -> int:
    # ── Parse ────────────────────────────────────────────────────────
    print("Parsing PicoRV32 design...")
    t0 = time.time()
    design = parse_files(FILES, preprocess=True, cache_dir=os.path.join(SCRIPT_DIR, ".pcache"))
    print(f"  {len(design.modules)} modules parsed in {time.time() - t0:.2f}s")

    top = design.get_module("testbench")
    if top is None:
        tops = design.get_top_modules()
        top = tops[0]
    print(f"  Top module: {top.name}")

    # ── Create simulator ─────────────────────────────────────────────
    print(f"\nCreating simulator (engine={ENGINE})...")
    t0 = time.time()
    sim = Simulator(top, engine=ENGINE, design=design)
    print(f"  Created in {time.time() - t0:.2f}s — {len(sim.signals())} signals")

    # ── Run ──────────────────────────────────────────────────────────
    print(f"\nRunning simulation (max_time={MAX_TIME})...")
    t0 = time.time()
    try:
        sim.run(max_time=MAX_TIME)
        elapsed = time.time() - t0
        print(f"  Completed in {elapsed:.2f}s — sim time = {sim.time}")
    except Exception:
        elapsed = time.time() - t0
        traceback.print_exc()
        print(f"\n  Failed after {elapsed:.2f}s — sim time = {sim.time}")

    # ── Display output (testbench $display) ──────────────────────────
    if sim.display_output:
        print(f"\n$display output ({len(sim.display_output)} lines):")
        for line in sim.display_output:
            print(f"  {line}")

    # ── Read results from memory ─────────────────────────────────────
    trap_val = sim.read("trap")
    print(f"\ntrap = {trap_val}")

    if trap_val == 1:
        print("\n==============================")
        print("RV32I Instruction Test Results")
        print("==============================")
        n_pass = 0
        n_fail = 0
        for i, name in enumerate(TEST_NAMES):
            addr = RESULT_BASE + i * 4
            word_idx = addr >> 2  # memory word index
            try:
                val = sim.read(f"memory[{word_idx}]")
                passed = val == 1
            except (KeyError, AttributeError):
                passed = False
                val = "?"
            status = "PASS" if passed else "FAIL"
            print(f"  [{status}] {name}")
            if passed:
                n_pass += 1
            else:
                n_fail += 1
        print("==============================")
        print(f"  {n_pass}/{len(TEST_NAMES)} passed, {n_fail} failed")

        try:
            cycles = sim.read("uut.count_cycle")
            print(f"  Completed in {cycles} cycles")
        except (KeyError, AttributeError):
            pass
    else:
        print("WARNING: trap not asserted — program may not have completed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
