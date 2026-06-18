"""FemtoRV32 Quark RV32I instruction test runner.

Parses and simulates the FemtoRV32 Quark RISC-V CPU running a
comprehensive RV32I instruction test firmware.  Reports pass/fail
per test group.

Run from the repository root:

    uv run python examples/femtorv/run_sim.py

Generate firmware first:

    uv run python examples/femtorv/gen_firmware.py
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
    os.path.join(RTL_DIR, "femtorv32_quark.v"),
    os.path.join(SIM_DIR, "testbench.v"),
]

# Test result addresses (must match gen_firmware.py)
RESULT_BASE = 0x600
DONE_ADDR = 0x7FC
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
    print("Parsing FemtoRV32 Quark design...")
    t0 = time.time()
    design = parse_files(FILES, preprocess=True, cache_dir=os.path.join(SCRIPT_DIR, ".pcache"), defines={"BENCH": "1"})
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
    done_word_idx = DONE_ADDR >> 2
    try:
        done_val = sim.read(f"memory[{done_word_idx}]")
    except (KeyError, AttributeError):
        done_val = 0

    if done_val == 1:
        print("\n==============================")
        print("RV32I Instruction Test Results")
        print("==============================")
        n_pass = 0
        n_fail = 0
        for i, name in enumerate(TEST_NAMES):
            addr = RESULT_BASE + i * 4
            word_idx = addr >> 2
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
            cycles = sim.read("uut.cycles")
            print(f"  Completed in {cycles} cycles")
        except (KeyError, AttributeError):
            pass
    else:
        print(f"\nWARNING: done flag not set (value={done_val}) — program may not have completed")
        for sig in ["uut.PC", "uut.state", "uut.cycles", "reset_n"]:
            try:
                val = sim.read(sig)
                print(f"  {sig} = {val}")
            except (KeyError, AttributeError):
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
