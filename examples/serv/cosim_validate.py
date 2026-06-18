"""SERV cosim validation: compare reference engine vs Icarus Verilog.

Uses run_cycle_by_cycle to step both simulators in lockstep and report
the first divergence.

Usage:
    uv run python examples/serv/cosim_validate.py
"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(SCRIPT_DIR, "sim")
RTL_DIR = os.path.join(SCRIPT_DIR, "rtl")
os.chdir(SIM_DIR)

from veriforge.sim.cosim import IcarusCosim  # noqa: E402

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

RTL = [os.path.join(RTL_DIR, f) for f in RTL_FILES]

# Icarus uses self-clocking testbench (with $dumpvars for VCD)
# Our sim uses external-clocking testbench (driven from Python)
ICARUS_FILES = [*RTL, os.path.join(SIM_DIR, "testbench.v")]
SIM_FILES = [*RTL, os.path.join(SIM_DIR, "testbench_fast.v")]

cosim = IcarusCosim(
    files=ICARUS_FILES,
    top_module="testbench",
    defines={"SERV_CLEAR_RAM": "1"},
    work_dir=SIM_DIR,
)

# Full run — compare all cycles Icarus produces (~12k posedge snapshots)
result = cosim.run_cycle_by_cycle(
    engine="reference",
    max_cycles=15000,
    reset_cycles=100,
    clock_name="clk",
    reset_name="rst",
    clock_period=10,
    sim_files=SIM_FILES,
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
