"""DarkRISCV simulation runner.

Preprocesses, parses, and simulates the DarkRISCV RISC-V SoC design.
Run from the repository root:

    uv run python examples/darkriscv/run_sim.py

The script changes into examples/darkriscv/sim/ so that relative paths
in the Verilog source (e.g. $readmemh("../src/darksocv.mem")) resolve
correctly.
"""

import os
import sys
import time
import traceback

# Ensure the script can be run from anywhere by making paths absolute early.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(SCRIPT_DIR, "sim")
RTL_DIR = os.path.join(SCRIPT_DIR, "rtl")

# $readmemh paths in darkram.v are relative ("../src/darksocv.mem"),
# so set CWD to the sim/ subdirectory.
os.chdir(SIM_DIR)

from veriforge.project import parse_files  # noqa: E402
from veriforge.sim.testbench import Simulator  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────
MAX_TIME = 500_000  # simulation time limit (time units)
ENGINE = "compiled"  # "reference", "vm", or "compiled"
DEFINES = {"SIMULATION": "", "__ICARUS__": "", "__RESETPC__": "32'd0"}

FILES = [
    os.path.join(RTL_DIR, "config.vh"),
    os.path.join(SIM_DIR, "darksimv.v"),
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

    # ── Run ──────────────────────────────────────────────────────────
    print(f"\nRunning simulation for {MAX_TIME} time units...")
    t0 = time.time()
    try:
        sim.run(max_time=MAX_TIME)
        elapsed = time.time() - t0
        print(f"  Completed in {elapsed:.2f}s — sim time = {sim.time}")
    except Exception:
        elapsed = time.time() - t0
        traceback.print_exc()
        print(f"\n  Failed after {elapsed:.2f}s — sim time = {sim.time}")

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
        "soc0.CLK",
        "soc0.RES",
        "soc0.bridge0.core0.CLK",
        "soc0.bridge0.core0.RES",
        "soc0.bridge0.core0.PC",
        "soc0.bridge0.core0.HLT",
        "soc0.bridge0.core0.IDATA",
        # HLT constituents
        "soc0.bridge0.core0.DDREQ",
        "soc0.bridge0.core0.DDACK",
        "soc0.bridge0.core0.IDREQ",
        "soc0.bridge0.core0.IDACK",
        # Bridge signals
        "soc0.bridge0.XDREQ",
        "soc0.bridge0.XXDACK",
        "soc0.bridge0.IDREQ",
        "soc0.bridge0.IDACK",
        # darkram IDACK
        "soc0.bram0.IDREQ",
        "soc0.bram0.IDACK",
        # darkio ACK
        "soc0.io0.XDREQ",
        "soc0.io0.XDACK",
        "soc0.io0.DTACK",
        # Dynamic array mux
        "soc0.XADDR",
        "soc0.XDREQ",
        "soc0.XDREQMUX",
        "soc0.XDACKMUX[0]",
        "soc0.XDACKMUX[1]",
        "soc0.XDACKMUX[2]",
        "soc0.XDACKMUX[3]",
    ]:
        try:
            print(f"  {name} = {sim.read(name)}")
        except (KeyError, AttributeError):
            pass

    # Show some time trace for CLK/RES
    print(f"\nSim time = {sim.time}")
    print(f"Max time = {MAX_TIME}")

    # ── Clean up VCD if produced ─────────────────────────────────────
    for vcd in ("darksocv.vcd", "dump.vcd"):
        vcd_path = os.path.join(SIM_DIR, vcd)
        if os.path.exists(vcd_path):
            os.remove(vcd_path)
            print(f"\nRemoved {vcd}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
