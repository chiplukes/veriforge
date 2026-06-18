"""Ibex RISC-V simulation runner.

Parses and simulates the Ibex RISC-V core running RV32I instruction
tests.  Uses a Verilog testbench wrapper that provides memory and
bus logic, following the same pattern as the PicoRV32 example.

Run from the repository root:

    uv run python examples/ibex/gen_firmware.py   # generate firmware first
    uv run python examples/ibex/run_sim.py
"""

import os
import sys
import time
import traceback

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(SCRIPT_DIR, "sim")
RTL_DIR = os.path.join(SCRIPT_DIR, "rtl")

# $readmemh path in testbench is relative, so set CWD to sim/
os.chdir(SIM_DIR)

from veriforge.project import parse_files  # noqa: E402
from veriforge.sim.testbench import Simulator  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────
MAX_TIME = 50_000  # simulation time limit
ENGINE = "compiled"  # "reference", "vm", or "compiled"
DEFINES = {"SYNTHESIS": ""}

# All RTL files in dependency order (package first), then testbench
FILES = [
    os.path.join(RTL_DIR, "prim_assert.sv"),
    os.path.join(RTL_DIR, "dv_fcov_macros.svh"),
    os.path.join(RTL_DIR, "ibex_pkg.sv"),
    os.path.join(RTL_DIR, "ibex_alu.sv"),
    os.path.join(RTL_DIR, "ibex_branch_predict.sv"),
    os.path.join(RTL_DIR, "ibex_compressed_decoder.sv"),
    os.path.join(RTL_DIR, "ibex_counter.sv"),
    os.path.join(RTL_DIR, "ibex_csr.sv"),
    os.path.join(RTL_DIR, "ibex_decoder.sv"),
    os.path.join(RTL_DIR, "ibex_fetch_fifo.sv"),
    os.path.join(RTL_DIR, "ibex_multdiv_fast.sv"),
    os.path.join(RTL_DIR, "ibex_pmp.sv"),
    os.path.join(RTL_DIR, "ibex_prefetch_buffer.sv"),
    os.path.join(RTL_DIR, "ibex_register_file_ff.sv"),
    os.path.join(RTL_DIR, "ibex_wb_stage.sv"),
    os.path.join(RTL_DIR, "ibex_load_store_unit.sv"),
    os.path.join(RTL_DIR, "ibex_ex_block.sv"),
    os.path.join(RTL_DIR, "ibex_if_stage.sv"),
    os.path.join(RTL_DIR, "ibex_cs_registers.sv"),
    os.path.join(RTL_DIR, "ibex_id_stage.sv"),
    os.path.join(RTL_DIR, "ibex_controller.sv"),
    os.path.join(RTL_DIR, "ibex_core.sv"),
    os.path.join(SIM_DIR, "testbench.v"),
]

# Test result addresses (must match gen_firmware.py)
RESULT_BASE = 0x800
TEST_NAMES = [
    "LUI/AUIPC",
    "ADDI",
    "ADD/SUB",
    "LOGIC (AND/OR/XOR)",
    "SHIFT",
    "COMPARE (SLT)",
    "BRANCH",
    "JAL/JALR",
    "LOAD/STORE",
]


def main() -> int:
    # ── Parse ────────────────────────────────────────────────────────
    print("Parsing Ibex design...")
    t0 = time.time()
    design = parse_files(
        FILES,
        preprocess=True,
        defines=DEFINES,
        include_paths=[RTL_DIR],
        cache_dir=os.path.join(SCRIPT_DIR, ".pcache"),
    )
    elapsed = time.time() - t0
    print(f"  {len(design.modules)} modules, {len(design.packages)} packages in {elapsed:.1f}s")

    for m in design.modules:
        print(f"    {m.name}: {len(m.ports)} ports, {len(m.instances)} instances")

    # Use testbench as top
    top = design.get_module("testbench")
    if top is None:
        tops = design.get_top_modules()
        top = tops[0]
    print(f"  Top module: {top.name}")

    # ── Create simulator ─────────────────────────────────────────────
    print(f"\nCreating simulator (engine={ENGINE})...")
    t0 = time.time()
    try:
        sim = Simulator(top, engine=ENGINE, design=design)
        print(f"  Created in {time.time() - t0:.2f}s — {len(sim.signals())} signals")
    except Exception:
        traceback.print_exc()
        print(f"\n  Simulator creation failed after {time.time() - t0:.2f}s")
        return 1

    # ── Drive unconnected IC cache inputs from Python ──────────────
    # These are unpacked array ports omitted from the Verilog port map
    for name in ["core.ic_tag_rdata_i", "core.ic_data_rdata_i"]:
        try:
            sim.drive(name, 0)
        except Exception:
            pass

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

    # ── Display output ───────────────────────────────────────────────
    if sim.display_output:
        print(f"\n$display output ({len(sim.display_output)} lines):")
        for line in sim.display_output:
            print(f"  {line}")

    # ── Read results from Python side ────────────────────────────────
    print("\nKey signals:")
    for name in [
        "clk",
        "rst_n",
        "cycle_count",
        "instr_req",
        "instr_addr",
        "data_req",
        "data_addr",
        "core.instr_req_o",
        "core.instr_addr_o",
    ]:
        try:
            val = sim.read(name)
            if isinstance(val, int) and val > 0xFFFF:
                print(f"  {name} = 0x{val:08X}")
            else:
                print(f"  {name} = {val}")
        except (KeyError, AttributeError):
            pass

    # Check results from memory (Python-side readback)
    print("\n── Python-side result check ──")
    for i, name in enumerate(TEST_NAMES):
        word_idx = (RESULT_BASE // 4) + i
        try:
            val = sim.read(f"memory[{word_idx}]")
            status = "PASS" if val == 1 else "FAIL"
            print(f"  [{status}] {name}")
        except (KeyError, AttributeError):
            print(f"  [????] {name} (memory not readable)")

    print(f"\nSim time = {sim.time}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
