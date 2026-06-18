"""Hand-authored Wave D-9 testbench for the pulp axi_xbar crossbar.

The DUT is a 2x2 AXI4 crossbar (2 slave ports, 2 master ports) with a
fixed address map:

* Target 0 → 0x000–0x0FF (master port 0)
* Target 1 → 0x100–0x1FF (master port 1)
* 0x200+ → decode error

Both master ports have internal target registers inside the TB that respond
automatically, so the bench only needs to drive the two slave ports
(``slv0_*`` / ``slv1_*``) and observe the outputs.

The bench uses ``Testbench`` for clock/reset scaffolding and drives all
slave-side signals manually (the TB uses struct-packed AXI buses internally,
so auto-responders are not used here).

Generate the scaffold with::

    uv run veriforge generate-python-testbench \\
      -f examples/pulp/axi/axi_xbar/tb/axi_xbar_tb.sv \\
      --module axi_xbar_exec_tb \\
      --enhanced --style bench --no-strict \\
      -o examples/pulp/axi/axi_xbar/bench/axi_xbar_bench.py

Run with::

    uv run python examples/pulp/axi/axi_xbar/bench/axi_xbar_bench.py
"""

from __future__ import annotations

import argparse
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.bench import Testbench
from veriforge.sim.endpoints.helpers import _settle_current_time
from veriforge.sim.step_harness import step_drive

SCRIPT_DIR = Path(__file__).resolve().parent
EX_ROOT = SCRIPT_DIR.parent
RTL_DIR = EX_ROOT / "rtl"
TB_FILE = EX_ROOT / "tb" / "axi_xbar_tb.sv"
FILES = [
    str(RTL_DIR / "axi_pkg.sv"),
    str(RTL_DIR / "axi_xbar.sv"),
    str(TB_FILE),
]

TARGET0_INIT = 0x11111111
TARGET1_INIT = 0x22222222
TARGET0_WRITE = 0xCAFEBABE
TARGET1_WRITE = 0x10203040
ARB_FIRST = 0x12345678
ARB_SECOND = 0xDEADBEEF
ADDR_TARGET0 = 0x000
ADDR_TARGET1 = 0x100
ADDR_INVALID = 0x200


def parse_design():
    return parse_files(
        FILES,
        preprocess=True,
        cache_dir=SCRIPT_DIR / "_vtc_axi_xbar_pcache",
    )


def build_bench() -> Testbench:
    design = parse_design()
    dut = design.get_module("axi_xbar_exec_tb")
    if dut is None:
        raise RuntimeError("Top module 'axi_xbar_exec_tb' not found")
    return Testbench(dut, design=design, engine="reference", strict=False)


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


def _wait_posedge(bench: Testbench, n: int = 1) -> None:
    bench.step(n)
    _settle_current_time(bench.sim, "clk")


def _idle(bench: Testbench) -> None:
    _drive(
        bench,
        slv0_aw_valid=0,
        slv0_w_valid=0,
        slv0_b_ready=0,
        slv0_ar_valid=0,
        slv0_r_ready=0,
        slv1_aw_valid=0,
        slv1_w_valid=0,
        slv1_b_ready=0,
        slv1_ar_valid=0,
        slv1_r_ready=0,
    )


# ---------------------------------------------------------------------------
# Exercise 1: parallel writes + reads to different targets
# ---------------------------------------------------------------------------


def exercise_parallel_routes(bench: Testbench) -> None:
    # Drive parallel writes: slv0 → target0, slv1 → target1.
    _drive(
        bench,
        slv0_aw_id=0x1,
        slv0_aw_addr=ADDR_TARGET0,
        slv0_aw_len=0,
        slv0_aw_valid=1,
        slv0_w_data=TARGET0_WRITE,
        slv0_w_strb=0xF,
        slv0_w_last=1,
        slv0_w_valid=1,
        slv0_b_ready=0,
        slv1_aw_id=0x2,
        slv1_aw_addr=ADDR_TARGET1,
        slv1_aw_len=0,
        slv1_aw_valid=1,
        slv1_w_data=TARGET1_WRITE,
        slv1_w_strb=0xF,
        slv1_w_last=1,
        slv1_w_valid=1,
        slv1_b_ready=0,
    )
    _expect(bench, "slv0_aw_ready", 1, "port0 target0 write should be accepted")
    _expect(bench, "slv1_aw_ready", 1, "port1 target1 write should be accepted")

    _wait_posedge(bench)
    _drive(bench, slv0_aw_valid=0, slv0_w_valid=0, slv1_aw_valid=0, slv1_w_valid=0)

    _expect(bench, "slv0_b_valid", 1, "port0 write response should be pending")
    _expect(bench, "slv0_b_id", 0x1, "port0 write response ID mismatch")
    _expect(bench, "slv0_b_resp", 0x0, "port0 write response code mismatch")
    _expect(bench, "slv1_b_valid", 1, "port1 write response should be pending")
    _expect(bench, "slv1_b_id", 0x2, "port1 write response ID mismatch")
    _expect(bench, "slv1_b_resp", 0x0, "port1 write response code mismatch")
    _expect(bench, "target0_data", TARGET0_WRITE, "target0 write data mismatch")
    _expect(bench, "target1_data", TARGET1_WRITE, "target1 write data mismatch")
    _expect(bench, "mst0_last_aw_id", 0x1, "target0 widened AW ID mismatch")
    _expect(bench, "mst1_last_aw_id", 0x6, "target1 widened AW ID mismatch")

    # Release write responses.
    _drive(bench, slv0_b_ready=1, slv1_b_ready=1)
    _wait_posedge(bench)
    _drive(bench, slv0_b_ready=0, slv1_b_ready=0)
    _expect(bench, "slv0_b_valid", 0, "port0 write response should clear")
    _expect(bench, "slv1_b_valid", 0, "port1 write response should clear")

    # Drive parallel reads: slv0 → target1 (cross), slv1 → target0 (cross).
    _drive(
        bench,
        slv0_ar_id=0x0,
        slv0_ar_addr=ADDR_TARGET1,
        slv0_ar_len=0,
        slv0_ar_valid=1,
        slv0_r_ready=0,
        slv1_ar_id=0x3,
        slv1_ar_addr=ADDR_TARGET0,
        slv1_ar_len=0,
        slv1_ar_valid=1,
        slv1_r_ready=0,
    )
    _expect(bench, "slv0_ar_ready", 1, "port0 target1 read should be accepted")
    _expect(bench, "slv1_ar_ready", 1, "port1 target0 read should be accepted")

    _wait_posedge(bench)
    _drive(bench, slv0_ar_valid=0, slv1_ar_valid=0)

    _expect(bench, "slv0_r_valid", 1, "port0 read response should be pending")
    _expect(bench, "slv0_r_id", 0x0, "port0 read response ID mismatch")
    _expect(bench, "slv0_r_data", TARGET1_WRITE, "port0 read data mismatch (cross-read)")
    _expect(bench, "slv0_r_resp", 0x0, "port0 read response code mismatch")
    _expect(bench, "slv0_r_last", 1, "port0 read last mismatch")
    _expect(bench, "slv1_r_valid", 1, "port1 read response should be pending")
    _expect(bench, "slv1_r_id", 0x3, "port1 read response ID mismatch")
    _expect(bench, "slv1_r_data", TARGET0_WRITE, "port1 read data mismatch (cross-read)")
    _expect(bench, "slv1_r_resp", 0x0, "port1 read response code mismatch")
    _expect(bench, "slv1_r_last", 1, "port1 read last mismatch")
    _expect(bench, "mst0_last_ar_id", 0x7, "target0 widened AR ID mismatch")
    _expect(bench, "mst1_last_ar_id", 0x0, "target1 widened AR ID mismatch")

    # Release read responses.
    _drive(bench, slv0_r_ready=1, slv1_r_ready=1)
    _wait_posedge(bench)
    _drive(bench, slv0_r_ready=0, slv1_r_ready=0)
    _expect(bench, "slv0_r_valid", 0, "port0 read response should clear")
    _expect(bench, "slv1_r_valid", 0, "port1 read response should clear")

    print("axi_xbar parallel routes passed: parallel writes + cross-reads to separate targets")


# ---------------------------------------------------------------------------
# Exercise 2: decode errors (unmapped address)
# ---------------------------------------------------------------------------


def exercise_decode_errors(bench: Testbench) -> None:
    # Write to unmapped address.
    _drive(
        bench,
        slv0_aw_id=0x2,
        slv0_aw_addr=ADDR_INVALID,
        slv0_aw_len=0,
        slv0_aw_valid=1,
        slv0_w_data=0x55AA55AA,
        slv0_w_strb=0xF,
        slv0_w_last=1,
        slv0_w_valid=1,
        slv0_b_ready=0,
    )
    _expect(bench, "slv0_aw_ready", 1, "decode-error write should be accepted")

    _wait_posedge(bench)
    _drive(bench, slv0_aw_valid=0, slv0_w_valid=0)

    _expect(bench, "slv0_b_valid", 1, "decode-error write response should be pending")
    _expect(bench, "slv0_b_id", 0x2, "decode-error write response ID mismatch")
    _expect(bench, "slv0_b_resp", 0x3, "decode-error write response code must be DECERR")
    _expect(bench, "target0_data", TARGET0_WRITE, "target0 must remain unchanged on decode error")
    _expect(bench, "target1_data", TARGET1_WRITE, "target1 must remain unchanged on decode error")

    _drive(bench, slv0_b_ready=1)
    _wait_posedge(bench)
    _drive(bench, slv0_b_ready=0)

    # Read from unmapped address.
    _drive(
        bench,
        slv1_ar_id=0x1,
        slv1_ar_addr=ADDR_INVALID,
        slv1_ar_len=0,
        slv1_ar_valid=1,
        slv1_r_ready=0,
    )
    _expect(bench, "slv1_ar_ready", 1, "decode-error read should be accepted")

    _wait_posedge(bench)
    _drive(bench, slv1_ar_valid=0)

    _expect(bench, "slv1_r_valid", 1, "decode-error read response should be pending")
    _expect(bench, "slv1_r_id", 0x1, "decode-error read response ID mismatch")
    _expect(bench, "slv1_r_data", 0xBADCAB1E, "decode-error read data mismatch")
    _expect(bench, "slv1_r_resp", 0x3, "decode-error read response code must be DECERR")
    _expect(bench, "slv1_r_last", 1, "decode-error read last mismatch")

    _drive(bench, slv1_r_ready=1)
    _wait_posedge(bench)
    _drive(bench, slv1_r_ready=0)

    print("axi_xbar decode errors passed: unmapped address -> DECERR on both write + read")


# ---------------------------------------------------------------------------
# Exercise 3: same-target write arbitration
# ---------------------------------------------------------------------------


def exercise_same_target_write_arbitration(bench: Testbench) -> None:
    # Both ports target the same address region; slv0 should win first.
    _drive(
        bench,
        slv0_aw_id=0x1,
        slv0_aw_addr=ADDR_TARGET0,
        slv0_aw_len=0,
        slv0_aw_valid=1,
        slv0_w_data=ARB_FIRST,
        slv0_w_strb=0xF,
        slv0_w_last=1,
        slv0_w_valid=1,
        slv0_b_ready=0,
        slv1_aw_id=0x2,
        slv1_aw_addr=ADDR_TARGET0 + 4,
        slv1_aw_len=0,
        slv1_aw_valid=1,
        slv1_w_data=ARB_SECOND,
        slv1_w_strb=0xF,
        slv1_w_last=1,
        slv1_w_valid=1,
        slv1_b_ready=0,
    )
    _expect(bench, "slv0_aw_ready", 1, "port0 should win first target0 arbitration")
    _expect(bench, "slv1_aw_ready", 0, "port1 should stall behind port0 on target0")

    _wait_posedge(bench)
    _drive(bench, slv0_aw_valid=0, slv0_w_valid=0)

    _expect(bench, "slv0_b_valid", 1, "port0 first write response should be pending")
    _expect(bench, "slv1_aw_ready", 0, "port1 should remain stalled while target0 response is pending")
    _expect(bench, "target0_data", ARB_FIRST, "target0 should hold the first write before release")

    _drive(bench, slv0_b_ready=1)
    _wait_posedge(bench)
    _drive(bench, slv0_b_ready=0)
    _expect(bench, "slv0_b_valid", 0, "port0 first write response should clear")
    _expect(bench, "slv1_aw_ready", 1, "port1 should become ready after target0 release")

    _wait_posedge(bench)
    _drive(bench, slv1_aw_valid=0, slv1_w_valid=0)

    _expect(bench, "slv1_b_valid", 1, "port1 deferred write response should be pending")
    _expect(bench, "slv1_b_id", 0x2, "port1 deferred write response ID mismatch")
    _expect(bench, "target0_data", ARB_SECOND, "target0 should contain the deferred write data")
    _expect(bench, "mst0_last_aw_id", 0x6, "target0 widened AW ID should update for deferred write")

    _drive(bench, slv1_b_ready=1)
    _wait_posedge(bench)
    _drive(bench, slv1_b_ready=0)

    _idle(bench)
    print("axi_xbar arbitration passed: same-target write collision serialized correctly")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vcd", type=Path, default=None, help="Optional VCD output path.")
    args = parser.parse_args()

    bench = build_bench()
    print(bench.plan.summary())
    print()

    with bench.run(vcd=args.vcd):
        if args.vcd is not None:
            print(f"VCD tracing -> {args.vcd}")
        bench.reset_all()
        # Exercises are chained on the same bench so target-register state is cumulative.
        exercise_parallel_routes(bench)
        exercise_decode_errors(bench)
        exercise_same_target_write_arbitration(bench)


if __name__ == "__main__":
    run_smoke_test()
