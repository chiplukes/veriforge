"""Hand-authored Wave D-4 testbench for the pulp axi_to_axi_lite bridge.

The DUT is a single-beat AXI4 -> AXI-Lite bridge. The TB top exposes:

* ``slv`` — AXI4-style slave (id/len/last present), but the detector
  groups it as AXI-Lite because aw_size/aw_burst are absent.
* ``mst`` — pure AXI-Lite master (DUT drives, bench responds).

Both sides use ``bench.iface()`` for a single-beat write/read sweep: the
bench drives ``slv`` as an AXI-Lite master and responds on ``mst`` with
an in-memory AXI-Lite slave. The separate signal-level tests verify the
bridge's pending-state timing and AXI4 sidebands.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import PlannerOverrides, Testbench

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


def exercise_bridge(bench: Testbench) -> None:
    sim = bench.sim
    for name, value in {
        "slv_aw_id": 0,
        "slv_aw_len": 0,
        "slv_aw_atop": 0,
        "slv_w_last": 1,
        "slv_ar_id": 0,
        "slv_ar_len": 0,
    }.items():
        sim.drive(name, value)
    sim.settle()

    mst = bench.iface("mst")
    slv = bench.iface("slv")

    payload = {addr: 0xBEEF_0000 | addr for addr in (0x000, 0x010, 0x040, 0x080, 0x100, 0x200, 0x3F8)}

    for addr, value in payload.items():
        response = slv.write(addr, value, timeout_cycles=50)
        if response != 0:
            raise AssertionError(f"write to 0x{addr:03x}: expected OKAY, got {response:#x}")

    if mst.memory != payload:
        raise AssertionError(f"responder memory mismatch: expected {payload!r}, got {mst.memory!r}")
    expected_writes = [(addr, value, 0xF) for addr, value in payload.items()]
    if mst.write_log != expected_writes:
        raise AssertionError(f"responder write log mismatch: expected {expected_writes!r}, got {mst.write_log!r}")

    for addr, value in payload.items():
        got = slv.read(addr, timeout_cycles=50)
        if got != value:
            raise AssertionError(f"slv read mismatch at 0x{addr:03x}: expected 0x{value:08x}, got 0x{got:08x}")
    if mst.read_log != list(payload):
        raise AssertionError(f"responder read log mismatch: expected {list(payload)!r}, got {mst.read_log!r}")

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
