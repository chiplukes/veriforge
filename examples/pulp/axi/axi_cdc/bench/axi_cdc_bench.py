"""Hand-authored Wave D-8 testbench for the pulp axi_cdc CDC bridge.

The DUT crosses an AXI4 bus between two asynchronous clock domains:

* ``src_clk_i`` / ``src_rst_ni`` drives the source side (bench-master).
* ``dst_clk_i`` / ``dst_rst_ni`` drives the destination side (bench-responder).

The two clocks run at different periods (10 and 14 ns) so the CDC FIFOs
are exercised across phase relationships.

Like ``axi_fifo``, this is fundamentally a signal-passthrough timing
test — the bench framework provides clock/reset scaffolding for *both*
domains, and the test logic drives signals manually with explicit
condition-waits across the CDC.

Generate the scaffold with::

    uv run veriforge generate-python-testbench \\
      -f examples/pulp/axi/axi_cdc/tb/axi_cdc_tb.sv \\
      --module axi_cdc_exec_tb \\
      --enhanced --style bench --no-strict \\
      -o examples/pulp/axi/axi_cdc/bench/axi_cdc_bench.py

Run with::

    uv run python examples/pulp/axi/axi_cdc/bench/axi_cdc_bench.py
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
TB_FILE = EX_ROOT / "tb" / "axi_cdc_tb.sv"
FILES = [
    str(RTL_DIR / "axi_cdc.sv"),
    str(RTL_DIR / "cdc_2phase.sv"),
    str(RTL_DIR / "cdc_fifo_2phase.sv"),
    str(TB_FILE),
]

SRC_PERIOD = 10
DST_PERIOD = 14


def parse_dut():
    design = parse_files(
        FILES,
        preprocess=True,
        cache_dir=SCRIPT_DIR / "_vtc_axi_cdc_pcache",
    )
    return design, design.get_module("axi_cdc_exec_tb")


def build_bench() -> Testbench:
    design, dut = parse_dut()
    overrides = PlannerOverrides(
        clock_periods={"src_clk_i": SRC_PERIOD, "dst_clk_i": DST_PERIOD},
    )
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
    _settle_current_time(sim, "src_clk_i")


def _wait_until(bench: Testbench, predicate, *, max_cycles: int = 80, message: str) -> None:
    """Step both domains until ``predicate(bench)`` returns True."""
    for _ in range(max_cycles):
        if predicate(bench):
            return
        if not bench.step(1):
            raise RuntimeError(f"{message}: simulator stalled")
        _settle_current_time(bench.sim, "src_clk_i")
    raise RuntimeError(f"{message}: predicate never satisfied within {max_cycles} cycles")


def _step_src(bench: Testbench, n: int = 1) -> None:
    bench.step(n, domain="src_clk_i")
    _settle_current_time(bench.sim, "src_clk_i")


def _step_dst(bench: Testbench, n: int = 1) -> None:
    bench.step(n, domain="dst_clk_i")
    _settle_current_time(bench.sim, "src_clk_i")


# ---------------------------------------------------------------------------
# Write transfer: src -> dst -> src
# ---------------------------------------------------------------------------


def exercise_write_transfer(bench: Testbench) -> None:
    # Source side issues an AW + W beat.
    _drive(
        bench,
        src_aw_id=0x2,
        src_aw_addr=0x44,
        src_aw_prot=0x3,
        src_aw_len=0,
        src_aw_valid=1,
        src_w_data=0xCAFEBABE,
        src_w_strb=0xA,
        src_w_last=1,
        src_w_valid=1,
        src_b_ready=0,
        dst_aw_ready=0,
        dst_w_ready=0,
        dst_b_valid=0,
    )
    _expect(bench, "src_aw_ready", 1, "src AW ready before issue")
    _expect(bench, "src_w_ready", 1, "src W ready before issue")

    _step_src(bench, 1)
    _drive(bench, src_aw_valid=0, src_w_valid=0)

    _wait_until(
        bench,
        lambda b: _read(b, "dst_aw_valid") == 1 and _read(b, "dst_w_valid") == 1,
        max_cycles=40,
        message="write request never crossed CDC to dst",
    )
    _expect(bench, "dst_aw_id", 0x2, "dst AW id mismatch")
    _expect(bench, "dst_aw_addr", 0x44, "dst AW addr mismatch")
    _expect(bench, "dst_aw_prot", 0x3, "dst AW prot mismatch")
    _expect(bench, "dst_aw_len", 0, "dst AW len mismatch")
    _expect(bench, "dst_w_data", 0xCAFEBABE, "dst W data mismatch")
    _expect(bench, "dst_w_strb", 0xA, "dst W strb mismatch")
    _expect(bench, "dst_w_last", 1, "dst W last mismatch")

    # Dst accepts.
    _drive(bench, dst_aw_ready=1, dst_w_ready=1)
    _step_dst(bench, 1)
    _drive(bench, dst_aw_ready=0, dst_w_ready=0)
    _wait_until(
        bench,
        lambda b: _read(b, "dst_aw_valid") == 0 and _read(b, "dst_w_valid") == 0,
        max_cycles=20,
        message="dst write request never drained",
    )

    # Dst sends a B response back across to src.
    _expect(bench, "dst_b_ready", 1, "dst B ready for response")
    _drive(bench, dst_b_id=0x2, dst_b_resp=0x1, dst_b_valid=1)
    # Wait for one dst_clk_i rising edge so the 2-phase CDC captures the
    # request toggle+data, then de-assert (mirrors the original run_sim.py).
    _step_dst(bench, 1)
    _drive(bench, dst_b_valid=0)
    _wait_until(
        bench,
        lambda b: _read(b, "src_b_valid") == 1,
        max_cycles=40,
        message="write response never crossed back to src",
    )
    _expect(bench, "src_b_id", 0x2, "src B id mismatch")
    _expect(bench, "src_b_resp", 0x1, "src B resp mismatch")

    _drive(bench, src_b_ready=1)
    _step_src(bench, 1)
    _wait_until(
        bench,
        lambda b: _read(b, "src_b_valid") == 0,
        max_cycles=20,
        message="src B never cleared",
    )
    print("axi_cdc write transfer passed: src -> dst -> src across CDC")


# ---------------------------------------------------------------------------
# Read transfer: src -> dst -> src
# ---------------------------------------------------------------------------


def exercise_read_transfer(bench: Testbench) -> None:
    _drive(
        bench,
        src_ar_id=0x1,
        src_ar_addr=0x88,
        src_ar_prot=0x5,
        src_ar_len=0,
        src_ar_valid=1,
        src_r_ready=0,
        dst_ar_ready=0,
        dst_r_valid=0,
    )
    _expect(bench, "src_ar_ready", 1, "src AR ready before issue")

    _step_src(bench, 1)
    _drive(bench, src_ar_valid=0)

    _wait_until(
        bench,
        lambda b: _read(b, "dst_ar_valid") == 1,
        max_cycles=40,
        message="read request never crossed CDC to dst",
    )
    _expect(bench, "dst_ar_id", 0x1, "dst AR id mismatch")
    _expect(bench, "dst_ar_addr", 0x88, "dst AR addr mismatch")
    _expect(bench, "dst_ar_prot", 0x5, "dst AR prot mismatch")
    _expect(bench, "dst_ar_len", 0, "dst AR len mismatch")

    _drive(bench, dst_ar_ready=1)
    _step_dst(bench, 1)
    _drive(bench, dst_ar_ready=0)
    _wait_until(
        bench,
        lambda b: _read(b, "dst_ar_valid") == 0,
        max_cycles=20,
        message="dst AR never drained",
    )

    _expect(bench, "dst_r_ready", 1, "dst R ready for response")
    _drive(
        bench,
        dst_r_id=0x1,
        dst_r_data=0x12345678,
        dst_r_resp=0x2,
        dst_r_last=1,
        dst_r_valid=1,
    )
    # Wait for one dst_clk_i edge so the CDC captures the response, then de-assert.
    _step_dst(bench, 1)
    _drive(bench, dst_r_valid=0)
    _wait_until(
        bench,
        lambda b: _read(b, "src_r_valid") == 1,
        max_cycles=40,
        message="read response never crossed back to src",
    )
    _expect(bench, "src_r_id", 0x1, "src R id mismatch")
    _expect(bench, "src_r_data", 0x12345678, "src R data mismatch")
    _expect(bench, "src_r_resp", 0x2, "src R resp mismatch")
    _expect(bench, "src_r_last", 1, "src R last mismatch")
    _drive(bench, dst_r_valid=0)

    _drive(bench, src_r_ready=1)
    _step_src(bench, 1)
    _wait_until(
        bench,
        lambda b: _read(b, "src_r_valid") == 0,
        max_cycles=20,
        message="src R never cleared",
    )
    print("axi_cdc read transfer passed: src -> dst -> src across CDC")


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vcd", type=Path, default=None, help="Optional VCD output path.")
    args = parser.parse_args()

    for label, exercise in (
        ("write", exercise_write_transfer),
        ("read", exercise_read_transfer),
    ):
        bench = build_bench()
        if label == "write":
            print(bench.plan.summary())
            print()

        vcd_path = None
        if args.vcd is not None:
            vcd_path = args.vcd.with_name(f"{args.vcd.stem}_{label}{args.vcd.suffix}")

        with bench.run(vcd=vcd_path):
            if vcd_path is not None:
                print(f"VCD tracing -> {vcd_path}")
            bench.reset_all()
            exercise(bench)


if __name__ == "__main__":
    run_smoke_test()
