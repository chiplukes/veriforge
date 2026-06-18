"""Hand-authored Wave D-7 testbench for the pulp axi_fifo passthrough.

The pulp ``axi_fifo`` IP buffers AXI4 AW/W/B/AR/R channels through a
small FIFO. The TB exposes two depth variants:

* ``axi_fifo_depth0_tb`` — degenerate combinational passthrough.
* ``axi_fifo_depth1_tb`` — single-entry FIFO that captures requests on
  one cycle and releases them after backpressure is removed.

Because the test verifies *signal-level passthrough* (id, prot, last,
resp), we don't use the AXILite master/responder endpoints. Instead we
let the bench framework provide clock/reset/init scaffolding and drive
the wires directly (mirroring the original ``run_sim.py``).

Generate the scaffold with::

    uv run veriforge generate-python-testbench \\
      -f examples/pulp/axi/axi_fifo/tb/axi_fifo_tb.sv \\
      --module axi_fifo_depth0_tb \\
      --enhanced --style bench --no-strict \\
      -o examples/pulp/axi/axi_fifo/bench/axi_fifo_bench.py

Run with::

    uv run python examples/pulp/axi/axi_fifo/bench/axi_fifo_bench.py
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
TB_FILE = EX_ROOT / "tb" / "axi_fifo_tb.sv"
FILES = [
    str(RTL_DIR / "axi_fifo.sv"),
    str(TB_FILE),
]


def parse_design():
    return parse_files(
        FILES,
        preprocess=True,
        cache_dir=SCRIPT_DIR / "_vtc_axi_fifo_pcache",
    )


def build_bench(top_name: str) -> Testbench:
    design = parse_design()
    dut = design.get_module(top_name)
    if dut is None:
        raise RuntimeError(f"Top module {top_name!r} not found")
    overrides = PlannerOverrides(iface_domains={"slv": "clk", "mst": "clk"})
    return Testbench(dut, design=design, overrides=overrides, engine="reference")


def _read(bench: Testbench, name: str) -> int:
    return int(bench.sim.read(name).val)


def _expect(bench: Testbench, name: str, expected: int, message: str) -> None:
    actual = _read(bench, name)
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected:#x}, got {actual:#x}")


def _drive(bench: Testbench, **values: int) -> None:
    sim = bench.sim
    eng = sim._engine
    for name, val in values.items():
        step_drive(sim, eng, name, val)
    _settle_current_time(sim, "clk")


def _wait_posedge(bench: Testbench) -> None:
    bench.step(1)
    _settle_current_time(bench.sim, "clk")


# ---------------------------------------------------------------------------
# depth0: combinational passthrough
# ---------------------------------------------------------------------------


def exercise_depth0(bench: Testbench) -> None:
    _drive(
        bench,
        slv_aw_id=0x2,
        slv_aw_addr=0x44,
        slv_aw_prot=0x3,
        slv_aw_valid=1,
        slv_w_data=0xCAFEBABE,
        slv_w_strb=0xA,
        slv_w_last=1,
        slv_w_valid=1,
        slv_b_ready=1,
        slv_ar_id=0x1,
        slv_ar_addr=0x88,
        slv_ar_prot=0x5,
        slv_ar_valid=1,
        slv_r_ready=1,
        mst_aw_ready=1,
        mst_w_ready=0,
        mst_b_id=0x3,
        mst_b_resp=0x2,
        mst_b_valid=1,
        mst_ar_ready=1,
        mst_r_id=0x1,
        mst_r_data=0x12345678,
        mst_r_resp=0x1,
        mst_r_last=1,
        mst_r_valid=1,
    )

    checks = [
        ("mst_aw_id", 0x2, "depth0 AW id"),
        ("mst_aw_addr", 0x44, "depth0 AW address"),
        ("mst_aw_prot", 0x3, "depth0 AW prot"),
        ("mst_aw_valid", 0x1, "depth0 AW valid"),
        ("slv_aw_ready", 0x1, "depth0 AW ready"),
        ("mst_w_data", 0xCAFEBABE, "depth0 W data"),
        ("mst_w_strb", 0xA, "depth0 W strb"),
        ("mst_w_last", 0x1, "depth0 W last"),
        ("mst_w_valid", 0x1, "depth0 W valid"),
        ("slv_w_ready", 0x0, "depth0 W ready (backpressure)"),
        ("slv_b_id", 0x3, "depth0 B id"),
        ("slv_b_resp", 0x2, "depth0 B resp"),
        ("slv_b_valid", 0x1, "depth0 B valid"),
        ("mst_b_ready", 0x1, "depth0 B ready"),
        ("mst_ar_id", 0x1, "depth0 AR id"),
        ("mst_ar_addr", 0x88, "depth0 AR address"),
        ("mst_ar_prot", 0x5, "depth0 AR prot"),
        ("mst_ar_valid", 0x1, "depth0 AR valid"),
        ("slv_ar_ready", 0x1, "depth0 AR ready"),
        ("slv_r_id", 0x1, "depth0 R id"),
        ("slv_r_data", 0x12345678, "depth0 R data"),
        ("slv_r_resp", 0x1, "depth0 R resp"),
        ("slv_r_last", 0x1, "depth0 R last"),
        ("slv_r_valid", 0x1, "depth0 R valid"),
        ("mst_r_ready", 0x1, "depth0 R ready"),
    ]
    for name, expected, msg in checks:
        _expect(bench, name, expected, msg)
    print("axi_fifo depth0 passed: combinational AXI passthrough")


# ---------------------------------------------------------------------------
# depth1: single-entry capture / release
# ---------------------------------------------------------------------------


def exercise_depth1(bench: Testbench) -> None:
    # Empty FIFO: backpressure mst, drive a request set on slv.
    _drive(
        bench,
        mst_aw_ready=0,
        mst_w_ready=0,
        mst_ar_ready=0,
        slv_aw_id=0x2,
        slv_aw_addr=0x44,
        slv_aw_prot=0x3,
        slv_aw_valid=1,
        slv_w_data=0xCAFEBABE,
        slv_w_strb=0xA,
        slv_w_last=1,
        slv_w_valid=1,
        slv_ar_id=0x1,
        slv_ar_addr=0x88,
        slv_ar_prot=0x5,
        slv_ar_valid=1,
    )
    for name, expected, msg in [
        ("slv_aw_ready", 0x1, "depth1 AW accept into empty fifo"),
        ("slv_w_ready", 0x1, "depth1 W accept into empty fifo"),
        ("slv_ar_ready", 0x1, "depth1 AR accept into empty fifo"),
        ("mst_aw_valid", 0x0, "depth1 AW pre-capture"),
        ("mst_w_valid", 0x0, "depth1 W pre-capture"),
        ("mst_ar_valid", 0x0, "depth1 AR pre-capture"),
    ]:
        _expect(bench, name, expected, msg)

    _wait_posedge(bench)
    _drive(bench, slv_aw_valid=0, slv_w_valid=0, slv_ar_valid=0)

    for name, expected, msg in [
        ("mst_aw_id", 0x2, "depth1 AW id buffered"),
        ("mst_aw_addr", 0x44, "depth1 AW addr buffered"),
        ("mst_aw_prot", 0x3, "depth1 AW prot buffered"),
        ("mst_aw_valid", 0x1, "depth1 AW valid buffered"),
        ("mst_w_data", 0xCAFEBABE, "depth1 W data buffered"),
        ("mst_w_strb", 0xA, "depth1 W strb buffered"),
        ("mst_w_last", 0x1, "depth1 W last buffered"),
        ("mst_w_valid", 0x1, "depth1 W valid buffered"),
        ("mst_ar_id", 0x1, "depth1 AR id buffered"),
        ("mst_ar_addr", 0x88, "depth1 AR addr buffered"),
        ("mst_ar_prot", 0x5, "depth1 AR prot buffered"),
        ("mst_ar_valid", 0x1, "depth1 AR valid buffered"),
        ("slv_aw_ready", 0x0, "depth1 AW backpressure when full"),
        ("slv_w_ready", 0x0, "depth1 W backpressure when full"),
        ("slv_ar_ready", 0x0, "depth1 AR backpressure when full"),
    ]:
        _expect(bench, name, expected, msg)

    _drive(bench, mst_aw_ready=1, mst_w_ready=1, mst_ar_ready=1)
    _wait_posedge(bench)
    for name, expected, msg in [
        ("mst_aw_valid", 0x0, "depth1 AW cleared after pop"),
        ("mst_w_valid", 0x0, "depth1 W cleared after pop"),
        ("mst_ar_valid", 0x0, "depth1 AR cleared after pop"),
        ("slv_aw_ready", 0x1, "depth1 AW recovered"),
        ("slv_w_ready", 0x1, "depth1 W recovered"),
        ("slv_ar_ready", 0x1, "depth1 AR recovered"),
    ]:
        _expect(bench, name, expected, msg)

    # Response side: drive mst, observe slv buffering.
    _drive(
        bench,
        slv_b_ready=0,
        slv_r_ready=0,
        mst_b_id=0x3,
        mst_b_resp=0x2,
        mst_b_valid=1,
        mst_r_id=0x1,
        mst_r_data=0x12345678,
        mst_r_resp=0x1,
        mst_r_last=1,
        mst_r_valid=1,
    )
    for name, expected, msg in [
        ("mst_b_ready", 0x1, "depth1 B accept into empty fifo"),
        ("mst_r_ready", 0x1, "depth1 R accept into empty fifo"),
        ("slv_b_valid", 0x0, "depth1 B pre-capture"),
        ("slv_r_valid", 0x0, "depth1 R pre-capture"),
    ]:
        _expect(bench, name, expected, msg)

    _wait_posedge(bench)
    _drive(bench, mst_b_valid=0, mst_r_valid=0)
    for name, expected, msg in [
        ("slv_b_id", 0x3, "depth1 B id buffered"),
        ("slv_b_resp", 0x2, "depth1 B resp buffered"),
        ("slv_b_valid", 0x1, "depth1 B valid buffered"),
        ("slv_r_id", 0x1, "depth1 R id buffered"),
        ("slv_r_data", 0x12345678, "depth1 R data buffered"),
        ("slv_r_resp", 0x1, "depth1 R resp buffered"),
        ("slv_r_last", 0x1, "depth1 R last buffered"),
        ("slv_r_valid", 0x1, "depth1 R valid buffered"),
        ("mst_b_ready", 0x0, "depth1 B backpressure when full"),
        ("mst_r_ready", 0x0, "depth1 R backpressure when full"),
    ]:
        _expect(bench, name, expected, msg)

    _drive(bench, slv_b_ready=1, slv_r_ready=1)
    _wait_posedge(bench)
    for name, expected, msg in [
        ("slv_b_valid", 0x0, "depth1 B cleared after pop"),
        ("slv_r_valid", 0x0, "depth1 R cleared after pop"),
        ("mst_b_ready", 0x1, "depth1 B recovered"),
        ("mst_r_ready", 0x1, "depth1 R recovered"),
    ]:
        _expect(bench, name, expected, msg)

    print("axi_fifo depth1 passed: capture/backpressure/release timing")


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vcd", type=Path, default=None, help="Optional VCD output path.")
    args = parser.parse_args()

    for top, exercise in (
        ("axi_fifo_depth0_tb", exercise_depth0),
        ("axi_fifo_depth1_tb", exercise_depth1),
    ):
        bench = build_bench(top)
        print(f"\n=== {top} plan ===")
        print(bench.plan.summary())

        vcd_path = None
        if args.vcd is not None:
            vcd_path = args.vcd.with_name(f"{args.vcd.stem}_{top}{args.vcd.suffix}")

        with bench.run(vcd=vcd_path):
            if vcd_path is not None:
                print(f"VCD tracing -> {vcd_path}")
            bench.reset_all()
            exercise(bench)


if __name__ == "__main__":
    run_smoke_test()
