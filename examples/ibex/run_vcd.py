"""Run Ibex simulation and produce a VCD waveform file.

Parses and simulates the Ibex RISC-V core, dumping all signals to a VCD
file for waveform viewing (e.g., GTKWave).

Usage:
    uv run python examples/ibex/gen_firmware.py   # generate firmware first
    uv run python examples/ibex/run_vcd.py

The VCD file is written to examples/ibex/sim/ibex_trace.vcd by default.
Use --output to override, --signals to filter, and --max-time to limit.
"""

import argparse
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
from veriforge.sim.vcd import VcdWriter  # noqa: E402

# ── Configuration ────────────────────────────────────────────────────
ENGINE = "compiled"
DEFINES = {"SYNTHESIS": ""}
DEFAULT_VCD = os.path.join(SIM_DIR, "ibex_trace.vcd")

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

# Curated signal list for a readable VCD.  Use --all for everything.
DEFAULT_SIGNALS = [
    # Clock & reset
    "clk",
    "rst_n",
    # Instruction fetch
    "core.instr_req_o",
    "core.instr_gnt_i",
    "core.instr_rvalid_i",
    "core.instr_addr_o",
    "core.instr_rdata_i",
    # Data bus
    "core.data_req_o",
    "core.data_gnt_i",
    "core.data_rvalid_i",
    "core.data_addr_o",
    "core.data_wdata_o",
    "core.data_rdata_i",
    "core.data_we_o",
    "core.data_be_o",
    # Pipeline
    "core.if_stage_i.pc_if_o",
    "core.id_stage_i.instr_rdata_i",
    "core.id_stage_i.instr_valid_i",
    "core.id_stage_i.stall_id",
    # ALU
    "core.alu_operand_a_ex",
    "core.alu_operand_b_ex",
    "core.ex_block_i.alu_i.result_o",
    # Register file writes
    "core.rf_we_wb",
    "core.rf_waddr_wb",
    "core.rf_wdata_wb",
    # Controller
    "core.id_stage_i.controller_i.ctrl_fsm_cs",
    # CSR
    "core.cs_registers_i.priv_lvl_q",
]


def _build_scope(name: str) -> str:
    """Extract scope from a hierarchical signal name."""
    parts = name.rsplit(".", 1)
    return parts[0] if len(parts) > 1 else "top"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Ibex sim and generate VCD")
    parser.add_argument("-o", "--output", default=DEFAULT_VCD, help="VCD output path")
    parser.add_argument("-t", "--max-time", type=int, default=2000, help="Max sim time (default: 2000)")
    parser.add_argument("--step", type=int, default=5, help="VCD dump interval (default: 5 = half clock)")
    parser.add_argument("--all", action="store_true", help="Dump all signals (slow, large file)")
    parser.add_argument("--signals", nargs="*", help="Custom signal list (overrides default)")
    args = parser.parse_args()

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
    print(f"  Parsed in {time.time() - t0:.1f}s")

    top = design.get_module("testbench")
    if top is None:
        top = design.get_top_modules()[0]

    # ── Create simulator ─────────────────────────────────────────────
    print(f"Creating simulator (engine={ENGINE})...")
    t0 = time.time()
    try:
        sim = Simulator(top, engine=ENGINE, design=design)
        print(f"  Created in {time.time() - t0:.2f}s — {len(sim.signals())} signals")
    except Exception:
        traceback.print_exc()
        return 1

    for name in ["core.ic_tag_rdata_i", "core.ic_data_rdata_i"]:
        try:
            sim.drive(name, 0)
        except Exception:
            pass

    # ── Select signals to trace ──────────────────────────────────────
    all_signals = set(sim.signals())
    if args.signals:
        trace_signals = [s for s in args.signals if s in all_signals]
    elif args.all:
        trace_signals = sorted(all_signals)
    else:
        trace_signals = [s for s in DEFAULT_SIGNALS if s in all_signals]

    print(f"  Tracing {len(trace_signals)} signals to {args.output}")

    # ── Set up VCD writer ────────────────────────────────────────────
    vcd = VcdWriter(args.output, timescale="1ps")
    for name in trace_signals:
        val = sim.read(name)
        w = val.width if hasattr(val, "width") else 1
        vcd.add_signal(name, width=w, scope=_build_scope(name))
    vcd.write_header()
    vcd.write_initial({name: sim.read(name) for name in trace_signals})

    # ── Run simulation with VCD dumps ────────────────────────────────
    print(f"Running simulation (max_time={args.max_time}, step={args.step})...")
    t0 = time.time()
    try:
        t = 0
        while t < args.max_time:
            t += args.step
            sim.run(max_time=t)
            vcd.dump_all(sim.time, {name: sim.read(name) for name in trace_signals})
            if sim.time < t:
                break
        elapsed = time.time() - t0
        print(f"  Completed in {elapsed:.2f}s — sim time = {sim.time}")
    except Exception:
        traceback.print_exc()
        print(f"\n  Failed at sim time = {sim.time}")

    vcd.finalize()
    file_size = os.path.getsize(args.output)
    print(f"  VCD written: {args.output} ({file_size / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
