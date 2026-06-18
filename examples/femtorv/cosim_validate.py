"""FemtoRV32 cosim validation: compare reference engine vs Icarus Verilog.

Uses run_cycle_by_cycle to step both simulators in lockstep and report
the first divergence.

Usage:
    uv run python examples/femtorv/cosim_validate.py
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(SCRIPT_DIR, "sim")
RTL_DIR = os.path.join(SCRIPT_DIR, "rtl")
os.chdir(SIM_DIR)

from veriforge.sim.cosim import IcarusCosim  # noqa: E402

RTL = [os.path.join(RTL_DIR, "femtorv32_quark.v")]

# Icarus uses self-clocking testbench (with $dumpvars for VCD)
# Our sim uses external-clocking testbench (driven from Python)
ICARUS_FILES = [*RTL, os.path.join(SIM_DIR, "testbench.v")]
SIM_FILES = [*RTL, os.path.join(SIM_DIR, "testbench_fast.v")]

cosim = IcarusCosim(
    files=ICARUS_FILES,
    top_module="testbench",
    defines={"BENCH": "1"},
    work_dir=SIM_DIR,
)

# FemtoRV32 uses active-LOW reset (reset_n: starts 0, release to 1)
# uut.cycles is a free-running counter with a known 1-cycle offset
result = cosim.run_cycle_by_cycle(
    engine="reference",
    max_cycles=12000,
    reset_cycles=100,
    clock_name="clk",
    reset_name="reset_n",
    clock_period=10,
    reset_active_high=False,
    sim_files=SIM_FILES,
    ignore_signals={"uut.cycles"},
    verbose=True,
)

if result is None:
    print("\nAll cycles match!")
else:
    print(f"\nFirst mismatch at cycle {result.cycle}:")
    for sig, ic_val, ref_val in result.signals[:20]:
        print(f"  {sig}: icarus={ic_val} ref={ref_val}")
    print(f"  ({len(result.signals)} total mismatching signals)")

sys.exit(0 if result is None else 1)
