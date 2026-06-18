"""FemtoRV32 Quark fast RV32I instruction test — uses batch_run for pure-C execution.

Uses a minimal Verilog testbench (testbench_fast.v) with no timing
and no clock generator.  Clock and reset are driven entirely from
batch_run(), which runs the full simulation in C with nogil.

Usage:
    uv run python examples/femtorv/run_fast.py
"""

import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(SCRIPT_DIR, "sim")
RTL_DIR = os.path.join(SCRIPT_DIR, "rtl")

# $readmemh path in testbench is relative, so set CWD
os.chdir(SIM_DIR)

from veriforge.project import parse_files  # noqa: E402
from veriforge.sim.testbench import Simulator  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────
CLOCK_PERIOD = 10
RESET_CYCLES = 100
RUN_CYCLES = 5000
TOTAL_CYCLES = RESET_CYCLES + RUN_CYCLES

FILES = [
    os.path.join(RTL_DIR, "femtorv32_quark.v"),
    os.path.join(SIM_DIR, "testbench_fast.v"),
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
    print("\nCreating simulator (engine=compiled)...")
    t0 = time.time()
    sim = Simulator(top, engine="compiled", design=design)
    print(f"  Created in {time.time() - t0:.2f}s — {len(sim.signals())} signals")

    # ── Initialize ───────────────────────────────────────────────────
    print("\nInitializing...")
    t0 = time.time()
    sim.run(max_time=0)
    t_init = time.time() - t0
    print(f"  Init done in {t_init:.2f}s")

    # ── batch_run ────────────────────────────────────────────────────
    events = [(RESET_CYCLES, "reset_n", 1)]

    print(f"\nbatch_run: {TOTAL_CYCLES} cycles, reset release at cycle {RESET_CYCLES}...")
    t0 = time.time()
    completed = sim.batch_run(TOTAL_CYCLES, "clk", clock_period=CLOCK_PERIOD, events=events)
    sim._sched._drain_compiled_output()
    t_batch = time.time() - t0
    print(f"  Completed {completed} cycles in {t_batch:.2f}s")
    if sim._sched._sim.is_finished():
        print("  ($finish encountered)")

    # ── Display output ───────────────────────────────────────────────
    if sim.display_output:
        print(f"\n$display output ({len(sim.display_output)} lines):")
        for line in sim.display_output[:20]:
            print(f"  {line}")

    # ── Read results from memory ─────────────────────────────────────
    done_word_idx = DONE_ADDR >> 2
    try:
        done_val = sim.read(f"memory[{done_word_idx}]")
    except (KeyError, AttributeError):
        done_val = 0

    print(f"\nSim time = {sim.time}")
    print(f"Total: init={t_init:.2f}s + batch={t_batch:.2f}s = {t_init + t_batch:.2f}s")

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

    return 0


if __name__ == "__main__":
    sys.exit(main())
