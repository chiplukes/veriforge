"""SERV bit-serial RISC-V CPU — RV32I instruction test runner.

Parses and simulates the SERV CPU running a comprehensive RV32I
instruction test firmware.  Reports pass/fail per test group.

Run from the repository root:

    uv run python examples/serv/run_sim.py

Generate firmware first:

    uv run python examples/serv/gen_firmware.py
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
MAX_TIME = 10_000_000  # 10ms sim time (500k cycles at 10ns/cycle)
ENGINE = "reference"  # "reference", "vm", or "compiled"

# All SERV RTL files
RTL_FILES = [
    "serv_top.v",
    "serv_state.v",
    "serv_decode.v",
    "serv_immdec.v",
    "serv_bufreg.v",
    "serv_bufreg2.v",
    "serv_ctrl.v",
    "serv_alu.v",
    "serv_rf_if.v",
    "serv_mem_if.v",
    "serv_csr.v",
    "serv_rf_ram_if.v",
    "serv_rf_ram.v",
    "serv_aligner.v",
    "serv_compdec.v",
    "serv_debug.v",
    "servile.v",
    "servile_arbiter.v",
    "servile_mux.v",
]

FILES = [os.path.join(RTL_DIR, f) for f in RTL_FILES] + [
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
    print("Parsing SERV design...")
    t0 = time.time()
    design = parse_files(
        FILES,
        preprocess=True,
        cache_dir=os.path.join(SCRIPT_DIR, ".pcache"),
        defines={"SERV_CLEAR_RAM": "1"},
    )
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
    else:
        print(f"\nWARNING: done flag not set (value={done_val}) — program may not have completed")
        # Try to read some diagnostic signals
        for sig in ["wb_mem_adr", "wb_mem_stb", "wb_mem_we", "rst", "cycle_count"]:
            try:
                val = sim.read(sig)
                print(f"  {sig} = {val}")
            except (KeyError, AttributeError):
                pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
