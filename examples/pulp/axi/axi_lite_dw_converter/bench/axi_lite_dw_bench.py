"""Hand-authored Wave D-10 testbench for the pulp axi_lite_dw_converter.

Five variants are exercised, each on its own fresh ``Testbench`` instance:

* **downsize**   32-bit slv → 16-bit mst: one wide write splits into two
  narrow writes; two narrow reads are assembled into one wide read.
* **upsize**     16-bit slv → 32-bit mst: narrow write is replicated into
  one wide write; wide read lane-selected to return narrow data.
* **passthrough** 32-bit slv → 32-bit mst: combinational passthrough.
* **typed_up32_128** 32-bit slv → 128-bit mst: replication + strobe shift.
* **typed_up64_128** 64-bit slv → 128-bit mst: replication + strobe shift.

All signals are driven manually via ``step_drive``.  The bench acts as both
AXI-Lite master (driving ``slv_*`` inputs) and AXI-Lite slave responder
(driving ``mst_*`` response inputs back into the DUT).

Run with::

    uv run python examples/pulp/axi/axi_lite_dw_converter/bench/axi_lite_dw_bench.py
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
TB_FILE = EX_ROOT / "tb" / "axi_lite_dw_tb.sv"
FILES = [
    str(RTL_DIR / "axi_pkg.sv"),
    str(RTL_DIR / "axi_lite_dw_converter.sv"),
    str(TB_FILE),
]

WIDE_REPEAT_32 = int("89ABCDEF" * 4, 16)
WIDE_REPEAT_64 = int("0123456789ABCDEF" * 2, 16)


def parse_design():
    return parse_files(
        FILES,
        preprocess=True,
        cache_dir=SCRIPT_DIR / "_vtc_axi_lite_dw_pcache",
    )


def _build_bench(top_name: str, design) -> Testbench:
    dut = design.get_module(top_name)
    if dut is None:
        raise RuntimeError(f"Top module {top_name!r} not found")
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


def _wait_posedge(bench: Testbench) -> None:
    bench.step(1)
    _settle_current_time(bench.sim, "clk")


def _wait_until(bench: Testbench, predicate, max_cycles: int = 50, message: str = "timeout") -> None:
    for _ in range(max_cycles):
        if predicate():
            return
        _wait_posedge(bench)
    raise AssertionError(f"{message}: condition not met after {max_cycles} cycles")


def _init_idle(bench: Testbench) -> None:
    """Drive all bench-controlled inputs to 0 (called after bench.reset_all())."""
    _drive(
        bench,
        slv_aw_addr=0,
        slv_aw_valid=0,
        slv_w_data=0,
        slv_w_strb=0,
        slv_w_valid=0,
        slv_b_ready=0,
        slv_ar_addr=0,
        slv_ar_valid=0,
        slv_r_ready=0,
        mst_aw_ready=0,
        mst_w_ready=0,
        mst_b_resp=0,
        mst_b_valid=0,
        mst_ar_ready=0,
        mst_r_data=0,
        mst_r_resp=0,
        mst_r_valid=0,
    )


# ---------------------------------------------------------------------------
# Check 1: downsize 32→16
# ---------------------------------------------------------------------------


def check_downsize(bench: Testbench) -> None:
    """32-bit slv → 16-bit mst: one wide write/read splits into two narrow beats."""
    _init_idle(bench)
    _drive(bench, mst_aw_ready=1, mst_w_ready=1, mst_ar_ready=1)

    _expect(bench, "slv_aw_ready", 1, "downsizer should accept AW after reset")
    _expect(bench, "slv_w_ready", 1, "downsizer should accept W after reset")
    _expect(bench, "slv_ar_ready", 1, "downsizer should accept AR after reset")

    # Issue 32-bit write: addr=0x2, data=0x61112222, strb=0xC (upper 2 bytes active).
    _drive(bench, slv_aw_addr=0x2, slv_aw_valid=1, slv_w_data=0x61112222, slv_w_strb=0xC, slv_w_valid=1)
    _wait_posedge(bench)
    _drive(bench, slv_aw_valid=0, slv_w_valid=0)

    # First narrow beat: lower 16-bit half (addr=0x0, data=0x2222, strb=0x0).
    _expect(bench, "mst_aw_valid", 1, "downsizer should start first narrow AW")
    _expect(bench, "mst_w_valid", 1, "downsizer should start first narrow W")
    _expect(bench, "mst_aw_addr", 0x0, "downsizer first AW address mismatch")
    _expect(bench, "mst_w_data", 0x2222, "downsizer first W data mismatch")
    _expect(bench, "mst_w_strb", 0x0, "downsizer first W strobe mismatch")

    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "mst_b_ready") == 1, message="downsizer first B ready not observed")

    # Accept first narrow B (OKAY).
    _drive(bench, mst_b_resp=0, mst_b_valid=1)
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "mst_aw_valid") == 1, message="downsizer second narrow AW not observed")
    _drive(bench, mst_b_valid=0)

    # Second narrow beat: upper 16-bit half (addr=0x2, data=0x6111, strb=0x3).
    _expect(bench, "mst_aw_valid", 1, "downsizer should start second narrow AW")
    _expect(bench, "mst_w_valid", 1, "downsizer should start second narrow W")
    _expect(bench, "mst_aw_addr", 0x2, "downsizer second AW address mismatch")
    _expect(bench, "mst_w_data", 0x6111, "downsizer second W data mismatch")
    _expect(bench, "mst_w_strb", 0x3, "downsizer second W strobe mismatch")

    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "mst_b_ready") == 1, message="downsizer second B ready not observed")

    # Accept second narrow B with SLVERR — aggregated slv_b_resp should be OR'd.
    _drive(bench, mst_b_resp=0x2, mst_b_valid=1)
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "slv_b_valid") == 1, message="downsizer slave B response not observed")
    _drive(bench, mst_b_valid=0)

    _expect(bench, "slv_b_valid", 1, "downsizer should aggregate a slave B response")
    _expect(bench, "slv_b_resp", 0x2, "downsizer should OR narrow B responses")
    _drive(bench, slv_b_ready=1)
    _wait_posedge(bench)
    _drive(bench, slv_b_ready=0)

    # Issue 32-bit read: addr=0x2 → expect two narrow ARs, then one assembled wide R.
    _drive(bench, slv_ar_addr=0x2, slv_ar_valid=1)
    _wait_posedge(bench)
    _drive(bench, slv_ar_valid=0)
    _wait_until(bench, lambda: _read(bench, "mst_ar_valid") == 1, message="downsizer first narrow AR not observed")

    _expect(bench, "mst_ar_valid", 1, "downsizer should start first narrow AR")
    _expect(bench, "mst_ar_addr", 0x0, "downsizer first AR address mismatch")
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "mst_r_ready") == 1, message="downsizer first R ready not observed")

    # Return first narrow R (low half, 16-bit).
    _drive(bench, mst_r_data=0x11111111, mst_r_resp=0x0, mst_r_valid=1)
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "mst_ar_valid") == 1, message="downsizer second narrow AR not observed")
    _drive(bench, mst_r_valid=0)

    _expect(bench, "mst_ar_valid", 1, "downsizer should start second narrow AR")
    _expect(bench, "mst_ar_addr", 0x2, "downsizer second AR address mismatch")
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "mst_r_ready") == 1, message="downsizer second R ready not observed")

    # Return second narrow R (high half, 16-bit) with DECERR — aggregated resp should be OR'd.
    _drive(bench, mst_r_data=0x22222222, mst_r_resp=0x3, mst_r_valid=1)
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "slv_r_valid") == 1, message="downsizer slave R response not observed")
    _drive(bench, mst_r_valid=0)

    # Expected result: {high_half[15:0], low_half[15:0]} = {0x2222, 0x1111} = 0x22221111; resp OR'd = 0x3.
    _expect(bench, "slv_r_valid", 1, "downsizer should aggregate a wide slave R response")
    _expect(bench, "slv_r_resp", 0x3, "downsizer should OR narrow R responses")
    _expect(bench, "slv_r_data", 0x22221111, "downsizer read aggregation mismatch")

    print("axi_lite_dw downsize (32->16) passed")


# ---------------------------------------------------------------------------
# Check 2: upsize 16→32
# ---------------------------------------------------------------------------


def check_upsize(bench: Testbench) -> None:
    """16-bit slv → 32-bit mst: narrow write replicated; read lane-selected."""
    _init_idle(bench)
    _drive(bench, mst_aw_ready=1, mst_w_ready=1, mst_ar_ready=1)

    # Issue 16-bit write: addr=0x2, data=0x1EEF, strb=0x3.
    _drive(bench, slv_aw_addr=0x2, slv_aw_valid=1, slv_w_data=0x1EEF, slv_w_strb=0x3, slv_w_valid=1)
    _wait_posedge(bench)
    _drive(bench, slv_aw_valid=0, slv_w_valid=0)

    # DUT emits one wide write (data replicated, strobe shifted to addr alignment).
    _expect(bench, "mst_aw_valid", 1, "upsizer should emit a single wide AW")
    _expect(bench, "mst_w_valid", 1, "upsizer should emit a single wide W")
    _expect(bench, "mst_aw_addr", 0x2, "upsizer AW address mismatch")
    _expect(bench, "mst_w_data", 0x1EEF1EEF, "upsizer replicated W data mismatch")
    _expect(bench, "mst_w_strb", 0xC, "upsizer shifted W strobe mismatch")

    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "mst_b_ready") == 1, message="upsizer B ready not observed")

    _drive(bench, mst_b_resp=0x0, mst_b_valid=1)
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "slv_b_valid") == 1, message="upsizer slave B response not observed")
    _drive(bench, mst_b_valid=0)

    _expect(bench, "slv_b_valid", 1, "upsizer should return a slave B response")
    _expect(bench, "slv_b_resp", 0x0, "upsizer B response mismatch")
    _drive(bench, slv_b_ready=1)
    _wait_posedge(bench)
    _drive(bench, slv_b_ready=0)

    # Issue 16-bit read: addr=0x2 → one wide AR, one wide R response, narrow lane selected.
    _drive(bench, slv_ar_addr=0x2, slv_ar_valid=1)
    _wait_posedge(bench)
    _drive(bench, slv_ar_valid=0)
    _wait_until(bench, lambda: _read(bench, "mst_ar_valid") == 1, message="upsizer wide AR not observed")

    _expect(bench, "mst_ar_valid", 1, "upsizer should emit a single wide AR")
    _expect(bench, "mst_ar_addr", 0x2, "upsizer AR address mismatch")
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "mst_r_ready") == 1, message="upsizer R ready not observed")

    _drive(bench, mst_r_data=0x11223344, mst_r_resp=0x2, mst_r_valid=1)
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "slv_r_valid") == 1, message="upsizer slave R response not observed")
    _drive(bench, mst_r_valid=0)

    # addr=0x2 selects the upper 16-bit lane of the 32-bit word → 0x1122.
    _expect(bench, "slv_r_valid", 1, "upsizer should return a narrow slave R response")
    _expect(bench, "slv_r_resp", 0x2, "upsizer R response mismatch")
    _expect(bench, "slv_r_data", 0x1122, "upsizer read lane selection mismatch")

    print("axi_lite_dw upsize (16->32) passed")


# ---------------------------------------------------------------------------
# Check 3: passthrough 32→32
# ---------------------------------------------------------------------------


def check_passthrough(bench: Testbench) -> None:
    """32-bit slv → 32-bit mst: combinational passthrough, no splitting."""
    _init_idle(bench)
    _drive(bench, mst_aw_ready=1, mst_w_ready=1, mst_ar_ready=1)

    # Drive write — passthrough exposes it on mst side immediately (combinational).
    _drive(bench, slv_aw_addr=0x8, slv_aw_valid=1, slv_w_data=0xCAFEBABE, slv_w_strb=0xF, slv_w_valid=1)
    _expect(bench, "mst_aw_valid", 1, "passthrough should forward AW immediately")
    _expect(bench, "mst_aw_addr", 0x8, "passthrough AW address mismatch")
    _expect(bench, "mst_w_valid", 1, "passthrough should forward W immediately")
    _expect(bench, "mst_w_data", 0xCAFEBABE, "passthrough W data mismatch")
    _expect(bench, "mst_w_strb", 0xF, "passthrough W strobe mismatch")

    _wait_posedge(bench)
    # Deassert slv write, inject mst B response simultaneously.
    _drive(bench, slv_aw_valid=0, slv_w_valid=0, mst_b_resp=0x3, mst_b_valid=1)
    _expect(bench, "slv_b_valid", 1, "passthrough should forward B immediately")
    _expect(bench, "slv_b_resp", 0x3, "passthrough B response mismatch")

    # Drive read alongside mst R response — both sides visible immediately.
    _drive(bench, slv_ar_addr=0xC, slv_ar_valid=1, mst_r_data=0x13579BDF, mst_r_resp=0x0, mst_r_valid=1)
    _expect(bench, "mst_ar_valid", 1, "passthrough should forward AR immediately")
    _expect(bench, "mst_ar_addr", 0xC, "passthrough AR address mismatch")
    _expect(bench, "slv_r_valid", 1, "passthrough should forward R immediately")
    _expect(bench, "slv_r_data", 0x13579BDF, "passthrough R data mismatch")
    _expect(bench, "slv_r_resp", 0x0, "passthrough R response mismatch")

    print("axi_lite_dw passthrough (32->32) passed")


# ---------------------------------------------------------------------------
# Check 4: typed upsize 32→128
# ---------------------------------------------------------------------------


def check_typed_up32_128(bench: Testbench) -> None:
    """32-bit slv → 128-bit mst: data replicated 4x, strobe shifted by addr."""
    _init_idle(bench)
    _drive(bench, mst_aw_ready=1, mst_w_ready=1, mst_ar_ready=1)

    # Issue 32-bit write: addr=0x8, data=0x89ABCDEF, strb=0xF.
    _drive(bench, slv_aw_addr=0x8, slv_aw_valid=1, slv_w_data=0x89ABCDEF, slv_w_strb=0xF, slv_w_valid=1)
    _wait_posedge(bench)
    _drive(bench, slv_aw_valid=0, slv_w_valid=0)

    # Expected: data replicated to fill 128-bit bus; strobe at byte-offset 8 of 16.
    _expect(bench, "mst_aw_addr", 0x8, "typed 32->128 AW address mismatch")
    _expect(bench, "mst_w_data", WIDE_REPEAT_32, "typed 32->128 W replication mismatch")
    _expect(bench, "mst_w_strb", 0x0F00, "typed 32->128 W strobe shift mismatch")

    _wait_until(bench, lambda: _read(bench, "mst_b_ready") == 1, message="typed 32->128 B ready not observed")
    _drive(bench, mst_b_resp=0x1, mst_b_valid=1)
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "slv_b_valid") == 1, message="typed 32->128 slave B response not observed")
    _drive(bench, mst_b_valid=0)
    _expect(bench, "slv_b_resp", 0x1, "typed 32->128 slave B response mismatch")

    # Issue 32-bit read: addr=0x8 → single wide AR then lane-selected 32-bit R.
    _drive(bench, slv_ar_addr=0x8, slv_ar_valid=1)
    _wait_posedge(bench)
    _drive(bench, slv_ar_valid=0)
    _wait_until(bench, lambda: _read(bench, "mst_r_ready") == 1, message="typed 32->128 R ready not observed")

    # 128-bit word "112233445566778899AABBCCDDEEFF00"; bytes 8-11 (offset 0x8) = 0x55667788.
    _drive(bench, mst_r_data=int("112233445566778899AABBCCDDEEFF00", 16), mst_r_resp=0x2, mst_r_valid=1)
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "slv_r_valid") == 1, message="typed 32->128 slave R response not observed")
    _drive(bench, mst_r_valid=0)

    _expect(bench, "slv_r_resp", 0x2, "typed 32->128 slave R response mismatch")
    _expect(bench, "slv_r_data", 0x55667788, "typed 32->128 read lane selection mismatch")

    print("axi_lite_dw typed upsize (32->128) passed")


# ---------------------------------------------------------------------------
# Check 5: typed upsize 64→128
# ---------------------------------------------------------------------------


def check_typed_up64_128(bench: Testbench) -> None:
    """64-bit slv → 128-bit mst: data replicated 2x, strobe shifted by addr."""
    _init_idle(bench)
    _drive(bench, mst_aw_ready=1, mst_w_ready=1, mst_ar_ready=1)

    # Issue 64-bit write: addr=0x8, data=0x0123456789ABCDEF, strb=0xFF.
    _drive(bench, slv_aw_addr=0x8, slv_aw_valid=1, slv_w_data=0x0123456789ABCDEF, slv_w_strb=0xFF, slv_w_valid=1)
    _wait_posedge(bench)
    _drive(bench, slv_aw_valid=0, slv_w_valid=0)

    # Expected: data replicated to fill 128-bit bus; strobe at byte-offset 8 of 16.
    _expect(bench, "mst_aw_addr", 0x8, "typed 64->128 AW address mismatch")
    _expect(bench, "mst_w_data", WIDE_REPEAT_64, "typed 64->128 W replication mismatch")
    _expect(bench, "mst_w_strb", 0xFF00, "typed 64->128 W strobe shift mismatch")

    _wait_until(bench, lambda: _read(bench, "mst_b_ready") == 1, message="typed 64->128 B ready not observed")
    _drive(bench, mst_b_resp=0x3, mst_b_valid=1)
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "slv_b_valid") == 1, message="typed 64->128 slave B response not observed")
    _drive(bench, mst_b_valid=0)
    _expect(bench, "slv_b_resp", 0x3, "typed 64->128 slave B response mismatch")

    # Issue 64-bit read: addr=0x8 → single wide AR, lane-selected 64-bit R.
    _drive(bench, slv_ar_addr=0x8, slv_ar_valid=1)
    _wait_posedge(bench)
    _drive(bench, slv_ar_valid=0)
    _wait_until(bench, lambda: _read(bench, "mst_r_ready") == 1, message="typed 64->128 R ready not observed")

    # 128-bit word "0123456789ABCDEFFEEDC0DEBAADF00D"; bytes 8-15 (offset 0x8) = 0x0123456789ABCDEF.
    _drive(bench, mst_r_data=int("0123456789ABCDEFFEEDC0DEBAADF00D", 16), mst_r_resp=0x2, mst_r_valid=1)
    _wait_posedge(bench)
    _wait_until(bench, lambda: _read(bench, "slv_r_valid") == 1, message="typed 64->128 slave R response not observed")
    _drive(bench, mst_r_valid=0)

    _expect(bench, "slv_r_resp", 0x2, "typed 64->128 slave R response mismatch")
    _expect(bench, "slv_r_data", 0x0123456789ABCDEF, "typed 64->128 read lane selection mismatch")

    print("axi_lite_dw typed upsize (64->128) passed")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

VARIANTS = [
    ("axi_lite_dw_down_tb", check_downsize),
    ("axi_lite_dw_up_tb", check_upsize),
    ("axi_lite_dw_same_tb", check_passthrough),
    ("axi_lite_dw_typed_up32_128_tb", check_typed_up32_128),
    ("axi_lite_dw_typed_up64_128_tb", check_typed_up64_128),
]


def run_smoke_test() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vcd", type=Path, default=None, help="Optional VCD output base path.")
    args = parser.parse_args()

    design = parse_design()

    for idx, (top_name, check_fn) in enumerate(VARIANTS):
        bench = _build_bench(top_name, design)
        if idx == 0:
            print(bench.plan.summary())
            print()

        vcd_path = None
        if args.vcd is not None:
            vcd_path = args.vcd.with_name(f"{args.vcd.stem}_{top_name}{args.vcd.suffix}")

        with bench.run(vcd=vcd_path):
            if vcd_path is not None:
                print(f"VCD tracing -> {vcd_path}")
            bench.reset_all()
            check_fn(bench)


if __name__ == "__main__":
    run_smoke_test()
