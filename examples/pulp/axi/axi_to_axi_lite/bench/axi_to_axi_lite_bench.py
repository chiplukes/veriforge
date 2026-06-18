"""Hand-authored Wave D-4 testbench for the pulp axi_to_axi_lite bridge.

The DUT is a single-beat AXI4 -> AXI-Lite bridge. The TB top exposes:

* ``slv`` — AXI4-style slave (id/len/last present), but the detector
  groups it as AXI-Lite because aw_size/aw_burst are absent.
* ``mst`` — pure AXI-Lite master (DUT drives, bench responds).

Because the bridge has an internal ``aw_pending_q`` flop that gates
both ``slv_aw_ready`` and ``slv_b_valid``, we drive the slv side with
explicit signal pokes (mirroring the pulp ``run_sim.py`` style) rather
than the AXILiteMaster endpoint (which would race the bridge's gated
combinational paths). The ``mst`` side uses the auto-tick
``AXILiteResponder`` via ``bench.iface('mst')``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, Testbench
from veriforge.sim.endpoints.helpers import _settle_current_time
from veriforge.sim.step_harness import step_drive

SCRIPT_DIR = Path(__file__).resolve().parent
EX_ROOT = SCRIPT_DIR.parent
RTL_DIR = EX_ROOT / "rtl"
TB_FILE = EX_ROOT / "tb" / "axi_to_axi_lite_tb.sv"
FILES = [
    str(RTL_DIR / "axi_pkg.sv"),
    str(RTL_DIR / "axi_to_axi_lite.sv"),
    str(TB_FILE),
]


def parse_dut():
    design = parse_files(
        FILES,
        preprocess=True,
        cache_dir=SCRIPT_DIR / "_vtc_axi_to_axi_lite_pcache",
    )
    return design, design.get_module("axi_to_axi_lite_exec_tb")


def build_bench() -> Testbench:
    design, dut = parse_dut()
    overrides = PlannerOverrides(iface_domains={"slv": "clk", "mst": "clk"})
    return Testbench(dut, design=design, overrides=overrides, engine="reference")


def _slv_write(bench: Testbench, addr: int, data: int, *, strb: int = 0xF, timeout: int = 30) -> None:
    sim = bench.sim
    eng = sim._engine
    step_drive(sim, eng, "slv_aw_id", 0)
    step_drive(sim, eng, "slv_aw_addr", addr)
    step_drive(sim, eng, "slv_aw_prot", 0)
    step_drive(sim, eng, "slv_aw_len", 0)
    step_drive(sim, eng, "slv_aw_atop", 0)
    step_drive(sim, eng, "slv_aw_valid", 1)
    step_drive(sim, eng, "slv_w_data", data)
    step_drive(sim, eng, "slv_w_strb", strb)
    step_drive(sim, eng, "slv_w_last", 1)
    step_drive(sim, eng, "slv_w_valid", 1)
    step_drive(sim, eng, "slv_b_ready", 1)

    aw_done = w_done = False
    for _ in range(timeout):
        bench.step(1)
        _settle_current_time(sim, "clk")
        if not aw_done and int(sim.read("slv_aw_ready").val) == 1:
            aw_done = True
            step_drive(sim, eng, "slv_aw_valid", 0)
        if not w_done and int(sim.read("slv_w_ready").val) == 1:
            w_done = True
            step_drive(sim, eng, "slv_w_valid", 0)
        if int(sim.read("slv_b_valid").val) == 1:
            step_drive(sim, eng, "slv_aw_valid", 0)
            step_drive(sim, eng, "slv_w_valid", 0)
            # Hold b_ready high through the next posedge so the bridge can
            # observe aw_complete = slv_b_valid & mst_b_ready and clear its
            # internal aw_pending_q flop. Then drop b_ready.
            bench.step(1)
            _settle_current_time(sim, "clk")
            step_drive(sim, eng, "slv_b_ready", 0)
            return
    raise TimeoutError(f"slv write to 0x{addr:x} did not complete")


def _slv_read(bench: Testbench, addr: int, *, timeout: int = 30) -> int:
    sim = bench.sim
    eng = sim._engine
    step_drive(sim, eng, "slv_ar_id", 0)
    step_drive(sim, eng, "slv_ar_addr", addr)
    step_drive(sim, eng, "slv_ar_prot", 0)
    step_drive(sim, eng, "slv_ar_len", 0)
    step_drive(sim, eng, "slv_ar_valid", 1)
    step_drive(sim, eng, "slv_r_ready", 1)

    ar_done = False
    for _ in range(timeout):
        bench.step(1)
        _settle_current_time(sim, "clk")
        if not ar_done and int(sim.read("slv_ar_ready").val) == 1:
            ar_done = True
            step_drive(sim, eng, "slv_ar_valid", 0)
        if int(sim.read("slv_r_valid").val) == 1:
            data = int(sim.read("slv_r_data").val)
            step_drive(sim, eng, "slv_ar_valid", 0)
            # Hold r_ready high through the next posedge so the bridge can
            # observe ar_complete and clear its internal ar_pending_q flop.
            bench.step(1)
            _settle_current_time(sim, "clk")
            step_drive(sim, eng, "slv_r_ready", 0)
            return data
    raise TimeoutError(f"slv read at 0x{addr:x} did not complete")


def exercise_bridge(bench: Testbench) -> None:
    bench.iface("slv")  # plan-only; we drive slv ports manually
    mst = bench.iface("mst")  # creates the AXILiteResponder

    payload = {addr: 0xBEEF_0000 | addr for addr in (0x000, 0x010, 0x040, 0x080, 0x100, 0x200, 0x3F8)}

    for addr, value in payload.items():
        _slv_write(bench, addr, value)

    for addr, value in payload.items():
        observed = mst.memory.get(addr)
        if observed != value:
            raise AssertionError(f"responder memory mismatch at 0x{addr:03x}: expected 0x{value:08x}, got {observed!r}")

    for addr, value in payload.items():
        got = _slv_read(bench, addr)
        if got != value:
            raise AssertionError(f"slv read mismatch at 0x{addr:03x}: expected 0x{value:08x}, got 0x{got:08x}")

    print("axi_to_axi_lite passed: write/read sweep through bridge with responder echo")


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vcd", type=Path, default=None, help="Optional VCD output path.")
    args = parser.parse_args()

    bench = build_bench()
    print("Discovered testbench plan:\n")
    print(bench.plan.summary())
    print()

    with bench.run(vcd=args.vcd):
        if args.vcd is not None:
            print(f"VCD tracing -> {args.vcd}\n")
        bench.reset_all()
        exercise_bridge(bench)


if __name__ == "__main__":
    run_smoke_test()
