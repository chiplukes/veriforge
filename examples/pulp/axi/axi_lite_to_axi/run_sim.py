"""Run the adapted AXI-Lite-to-AXI bridge example.

Run from the repository root:

    uv run python examples/pulp/axi/axi_lite_to_axi/run_sim.py
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.example_runner import available_engines
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator


SCRIPT_DIR = Path(__file__).resolve().parent
RTL_DIR = SCRIPT_DIR / "rtl"
TB_FILE = SCRIPT_DIR / "tb" / "axi_lite_to_axi_tb.sv"
FILES = [
    str(RTL_DIR / "axi_pkg.sv"),
    str(RTL_DIR / "axi_lite_to_axi.sv"),
    str(TB_FILE),
]
MAX_TIME = 80
ENGINES = available_engines()


def _read_int(sim: Simulator, signal_name: str) -> int:
    raw = sim.read(signal_name)
    try:
        return int(raw)
    except Exception as exc:
        raise RuntimeError(f"{signal_name} is not fully resolved: {raw}") from exc


def _expect(sim: Simulator, signal_name: str, expected: int, message: str) -> None:
    actual = _read_int(sim, signal_name)
    if actual != expected:
        raise RuntimeError(f"{message}: expected {expected:#x}, got {actual:#x}")


def _settle_drives(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=sim.time)
    else:
        step_eval_now(sim)


def _make_step_sim(design, engine: str) -> Simulator:
    top = design.get_module("axi_lite_to_axi_exec_tb")
    if top is None:
        raise RuntimeError("Top module 'axi_lite_to_axi_exec_tb' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    for signal_name in [
        "clk",
        "rst_n",
        "slv_aw_addr",
        "slv_aw_prot",
        "slv_aw_cache",
        "slv_aw_valid",
        "slv_w_data",
        "slv_w_strb",
        "slv_w_valid",
        "slv_b_ready",
        "slv_ar_addr",
        "slv_ar_prot",
        "slv_ar_cache",
        "slv_ar_valid",
        "slv_r_ready",
        "mst_aw_ready",
        "mst_w_ready",
        "mst_b_resp",
        "mst_b_valid",
        "mst_ar_ready",
        "mst_r_data",
        "mst_r_resp",
        "mst_r_valid",
    ]:
        step_drive(sim, engine, signal_name, 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), MAX_TIME)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _drive_case_one(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv_aw_addr", 0x104)
    step_drive(sim, engine, "slv_aw_prot", 0x5)
    step_drive(sim, engine, "slv_aw_cache", 0xB)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0xCAFEBABE)
    step_drive(sim, engine, "slv_w_strb", 0xA)
    step_drive(sim, engine, "slv_w_valid", 1)
    step_drive(sim, engine, "slv_b_ready", 1)
    step_drive(sim, engine, "slv_ar_addr", 0x208)
    step_drive(sim, engine, "slv_ar_prot", 0x3)
    step_drive(sim, engine, "slv_ar_cache", 0x6)
    step_drive(sim, engine, "slv_ar_valid", 1)
    step_drive(sim, engine, "slv_r_ready", 1)
    step_drive(sim, engine, "mst_aw_ready", 1)
    step_drive(sim, engine, "mst_w_ready", 0)
    step_drive(sim, engine, "mst_b_resp", 0x2)
    step_drive(sim, engine, "mst_b_valid", 1)
    step_drive(sim, engine, "mst_ar_ready", 1)
    step_drive(sim, engine, "mst_r_data", 0x12345678)
    step_drive(sim, engine, "mst_r_resp", 0x1)
    step_drive(sim, engine, "mst_r_valid", 1)
    _settle_drives(sim, engine)


def _expect_case_one(sim: Simulator) -> None:
    _expect(sim, "mst_aw_addr", 0x104, "write-address bridge mismatch")
    _expect(sim, "mst_aw_prot", 0x5, "write protection bridge mismatch")
    _expect(sim, "mst_aw_size", 0x2, "write size should be fixed to 32-bit beats")
    _expect(sim, "mst_aw_burst", 0x0, "write burst should be fixed")
    _expect(sim, "mst_aw_cache", 0xB, "write cache bridge mismatch")
    _expect(sim, "mst_aw_valid", 0x1, "write valid bridge mismatch")
    _expect(sim, "mst_w_data", 0xCAFEBABE, "write-data bridge mismatch")
    _expect(sim, "mst_w_strb", 0xA, "write strobe bridge mismatch")
    _expect(sim, "mst_w_last", 0x1, "write last should be forced high")
    _expect(sim, "mst_w_valid", 0x1, "write-data valid bridge mismatch")
    _expect(sim, "mst_b_ready", 0x1, "write-response ready bridge mismatch")
    _expect(sim, "mst_ar_addr", 0x208, "read-address bridge mismatch")
    _expect(sim, "mst_ar_prot", 0x3, "read protection bridge mismatch")
    _expect(sim, "mst_ar_size", 0x2, "read size should be fixed to 32-bit beats")
    _expect(sim, "mst_ar_burst", 0x0, "read burst should be fixed")
    _expect(sim, "mst_ar_cache", 0x6, "read cache bridge mismatch")
    _expect(sim, "mst_ar_valid", 0x1, "read valid bridge mismatch")
    _expect(sim, "mst_r_ready", 0x1, "read-response ready bridge mismatch")
    _expect(sim, "slv_aw_ready", 0x1, "slave AW ready mismatch")
    _expect(sim, "slv_w_ready", 0x0, "slave W ready mismatch")
    _expect(sim, "slv_b_resp", 0x2, "slave B response mismatch")
    _expect(sim, "slv_b_valid", 0x1, "slave B valid mismatch")
    _expect(sim, "slv_ar_ready", 0x1, "slave AR ready mismatch")
    _expect(sim, "slv_r_data", 0x12345678, "slave R data mismatch")
    _expect(sim, "slv_r_resp", 0x1, "slave R response mismatch")
    _expect(sim, "slv_r_valid", 0x1, "slave R valid mismatch")


def _drive_case_two(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv_aw_addr", 0x3FC)
    step_drive(sim, engine, "slv_aw_prot", 0x2)
    step_drive(sim, engine, "slv_aw_cache", 0x1)
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_data", 0x01020304)
    step_drive(sim, engine, "slv_w_strb", 0x5)
    step_drive(sim, engine, "slv_w_valid", 0)
    step_drive(sim, engine, "slv_b_ready", 0)
    step_drive(sim, engine, "slv_ar_addr", 0x40)
    step_drive(sim, engine, "slv_ar_prot", 0x7)
    step_drive(sim, engine, "slv_ar_cache", 0xF)
    step_drive(sim, engine, "slv_ar_valid", 0)
    step_drive(sim, engine, "slv_r_ready", 0)
    step_drive(sim, engine, "mst_aw_ready", 0)
    step_drive(sim, engine, "mst_w_ready", 1)
    step_drive(sim, engine, "mst_b_resp", 0x0)
    step_drive(sim, engine, "mst_b_valid", 0)
    step_drive(sim, engine, "mst_ar_ready", 0)
    step_drive(sim, engine, "mst_r_data", 0xDEADBEEF)
    step_drive(sim, engine, "mst_r_resp", 0x2)
    step_drive(sim, engine, "mst_r_valid", 0)
    _settle_drives(sim, engine)


def _expect_case_two(sim: Simulator) -> None:
    _expect(sim, "mst_aw_addr", 0x3FC, "write-address update mismatch")
    _expect(sim, "mst_aw_prot", 0x2, "write protection update mismatch")
    _expect(sim, "mst_aw_cache", 0x1, "write cache update mismatch")
    _expect(sim, "mst_aw_valid", 0x0, "write valid deassert mismatch")
    _expect(sim, "mst_w_data", 0x01020304, "write-data update mismatch")
    _expect(sim, "mst_w_strb", 0x5, "write strobe update mismatch")
    _expect(sim, "mst_w_valid", 0x0, "write-data valid deassert mismatch")
    _expect(sim, "mst_b_ready", 0x0, "write-response ready deassert mismatch")
    _expect(sim, "mst_ar_addr", 0x40, "read-address update mismatch")
    _expect(sim, "mst_ar_prot", 0x7, "read protection update mismatch")
    _expect(sim, "mst_ar_cache", 0xF, "read cache update mismatch")
    _expect(sim, "mst_ar_valid", 0x0, "read valid deassert mismatch")
    _expect(sim, "mst_r_ready", 0x0, "read-response ready deassert mismatch")
    _expect(sim, "slv_aw_ready", 0x0, "slave AW ready update mismatch")
    _expect(sim, "slv_w_ready", 0x1, "slave W ready update mismatch")
    _expect(sim, "slv_b_resp", 0x0, "slave B response update mismatch")
    _expect(sim, "slv_b_valid", 0x0, "slave B valid update mismatch")
    _expect(sim, "slv_ar_ready", 0x0, "slave AR ready update mismatch")
    _expect(sim, "slv_r_data", 0xDEADBEEF, "slave R data update mismatch")
    _expect(sim, "slv_r_resp", 0x2, "slave R response update mismatch")
    _expect(sim, "slv_r_valid", 0x0, "slave R valid update mismatch")


def _exercise_bridge(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    _drive_case_one(sim, engine)
    _expect_case_one(sim)
    _drive_case_two(sim, engine)
    _expect_case_two(sim)


def main() -> int:
    design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_axi_lite_to_axi_pcache")
    if design.get_module("axi_lite_to_axi") is None:
        raise RuntimeError("Imported typed module 'axi_lite_to_axi' was not parsed")

    start = time.perf_counter()
    try:
        for engine in ENGINES:
            _exercise_bridge(design, engine)
    except Exception:
        print("axi_lite_to_axi run failed", file=sys.stderr)
        traceback.print_exc()
        return 1

    elapsed = time.perf_counter() - start
    print(f"axi_lite_to_axi passed on {', '.join(ENGINES)} in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
