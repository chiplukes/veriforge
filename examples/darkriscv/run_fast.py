"""DarkRISCV fast simulation — uses batch_run for pure-C execution.

Uses a minimal Verilog testbench (darksimv_fast.v) with no timing
and no clock generator.  Clock and reset are driven entirely from
batch_run(), which runs the full simulation in C with nogil.

The only Python involvement is:
  1. Parse and elaborate (one-time setup)
  2. run(max_time=0) to execute $readmemh and settle initial values
  3. A single batch_run() call for all cycles

No VCD recording.  This is the fastest possible execution path.

Usage:
    uv run python examples/darkriscv/run_fast.py
"""

import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(SCRIPT_DIR, "sim")
RTL_DIR = os.path.join(SCRIPT_DIR, "rtl")

os.chdir(SIM_DIR)

from veriforge.project import parse_files  # noqa: E402
from veriforge.sim.testbench import Simulator  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────
MAX_TIME = 500_000
ENGINE = "compiled"
DEFINES = {"SIMULATION": "", "__ICARUS__": "", "__RESETPC__": "32'd0"}
# Clock period in time units (half-period=5 → period=10)
CLOCK_PERIOD = 10
# Reset release: at cycle 100 (= 1000 time units), set RES to 0
RESET_CYCLE = 1000 // CLOCK_PERIOD  # cycle 100

FILES = [
    os.path.join(RTL_DIR, "config.vh"),
    os.path.join(SIM_DIR, "darksimv_fast.v"),  # minimal testbench — no timing
    os.path.join(RTL_DIR, "darksocv.v"),
    os.path.join(RTL_DIR, "darkbridge.v"),
    os.path.join(RTL_DIR, "darkriscv.v"),
    os.path.join(RTL_DIR, "darkram.v"),
    os.path.join(RTL_DIR, "darkio.v"),
    os.path.join(RTL_DIR, "darkuart.v"),
    os.path.join(RTL_DIR, "darkpll.v"),
]


def main() -> int:
    # ── Parse ────────────────────────────────────────────────────────
    print("Parsing DarkRISCV design...")
    t0 = time.time()
    design = parse_files(
        FILES,
        preprocess=True,
        defines=DEFINES,
        include_paths=[RTL_DIR],
        cache_dir=os.path.join(SCRIPT_DIR, ".pcache"),
    )
    print(f"  {len(design.modules)} modules parsed in {time.time() - t0:.2f}s")

    top = design.get_top_modules()[0]
    print(f"  Top module: {top.name}")

    # ── Create simulator ─────────────────────────────────────────────
    print(f"\nCreating simulator (engine={ENGINE})...")
    t0 = time.time()
    sim = Simulator(top, engine=ENGINE, design=design)
    print(f"  Created in {time.time() - t0:.2f}s — {len(sim.signals())} signals")

    # ── Initialize: execute $readmemh and settle ─────────────────────
    print("\nInitializing (run $readmemh, settle combinational logic)...")
    t0 = time.time()
    sim.run(max_time=0)
    t_init = time.time() - t0
    print(f"  Init done in {t_init:.2f}s")

    # ── batch_run: everything in C ───────────────────────────────────
    total_cycles = MAX_TIME // CLOCK_PERIOD
    events = [(RESET_CYCLE, "RES", 0)]  # release reset at cycle 100

    print(f"\nbatch_run: {total_cycles} cycles, reset at cycle {RESET_CYCLE}...")
    t0 = time.time()
    completed = sim.batch_run(total_cycles, "CLK", clock_period=CLOCK_PERIOD, events=events)
    # Drain any $display output accumulated during batch_run
    sim._sched._drain_compiled_output()
    t_batch = time.time() - t0
    print(f"  Completed {completed} cycles in {t_batch:.2f}s")
    if sim._sched._sim.is_finished():
        print("  ($finish encountered)")

    # ── Display output ───────────────────────────────────────────────
    if sim.display_output:
        print(f"\n$display output ({len(sim.display_output)} lines):")
        for line in sim.display_output:
            print(f"  {line}")

    # ── Key signals ──────────────────────────────────────────────────
    print("\nKey signals:")
    for name in [
        "CLK",
        "RES",
        "soc0.bridge0.core0.PC",
        "soc0.bridge0.core0.HLT",
        "soc0.bridge0.core0.FLUSH",
    ]:
        print(f"  {name} = {sim.read(name)}")

    print(f"\nSim time = {sim.time}")
    print(f"Total: init={t_init:.2f}s + batch={t_batch:.2f}s = {t_init + t_batch:.2f}s")

    return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
