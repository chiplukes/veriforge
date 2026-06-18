"""Run the adapted AXI FIFO example.

Run from the repository root:

    uv run python examples/pulp/axi/axi_fifo/run_sim.py
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
TB_FILE = SCRIPT_DIR / "tb" / "axi_fifo_tb.sv"
FILES = [
    str(RTL_DIR / "axi_fifo.sv"),
    str(TB_FILE),
]
MAX_TIME = 120
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


def _run_until_rising_edge(sim: Simulator, signal_name: str, limit: int, message: str) -> None:
    previous = _read_int(sim, signal_name)
    while sim.time < limit:
        if not sim.run_step():
            raise RuntimeError(f"stepped engine stopped before {message}")
        current = _read_int(sim, signal_name)
        if previous == 0 and current == 1:
            return
        previous = current
    raise RuntimeError(message)


def _make_step_sim(design, top_name: str, engine: str, *, max_time: int = MAX_TIME) -> Simulator:
    top = design.get_module(top_name)
    if top is None:
        raise RuntimeError(f"Top module {top_name!r} not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    for signal_name in [
        "clk",
        "rst_n",
        "slv_aw_id",
        "slv_aw_addr",
        "slv_aw_prot",
        "slv_aw_valid",
        "slv_w_data",
        "slv_w_strb",
        "slv_w_last",
        "slv_w_valid",
        "slv_b_ready",
        "slv_ar_id",
        "slv_ar_addr",
        "slv_ar_prot",
        "slv_ar_valid",
        "slv_r_ready",
        "mst_aw_ready",
        "mst_w_ready",
        "mst_b_id",
        "mst_b_resp",
        "mst_b_valid",
        "mst_ar_ready",
        "mst_r_id",
        "mst_r_data",
        "mst_r_resp",
        "mst_r_last",
        "mst_r_valid",
    ]:
        step_drive(sim, engine, signal_name, 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), max_time)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _drive_depth0(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv_aw_id", 0x2)
    step_drive(sim, engine, "slv_aw_addr", 0x44)
    step_drive(sim, engine, "slv_aw_prot", 0x3)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0xCAFEBABE)
    step_drive(sim, engine, "slv_w_strb", 0xA)
    step_drive(sim, engine, "slv_w_last", 1)
    step_drive(sim, engine, "slv_w_valid", 1)
    step_drive(sim, engine, "slv_b_ready", 1)
    step_drive(sim, engine, "slv_ar_id", 0x1)
    step_drive(sim, engine, "slv_ar_addr", 0x88)
    step_drive(sim, engine, "slv_ar_prot", 0x5)
    step_drive(sim, engine, "slv_ar_valid", 1)
    step_drive(sim, engine, "slv_r_ready", 1)
    step_drive(sim, engine, "mst_aw_ready", 1)
    step_drive(sim, engine, "mst_w_ready", 0)
    step_drive(sim, engine, "mst_b_id", 0x3)
    step_drive(sim, engine, "mst_b_resp", 0x2)
    step_drive(sim, engine, "mst_b_valid", 1)
    step_drive(sim, engine, "mst_ar_ready", 1)
    step_drive(sim, engine, "mst_r_id", 0x1)
    step_drive(sim, engine, "mst_r_data", 0x12345678)
    step_drive(sim, engine, "mst_r_resp", 0x1)
    step_drive(sim, engine, "mst_r_last", 1)
    step_drive(sim, engine, "mst_r_valid", 1)
    _settle_drives(sim, engine)


def _expect_depth0(sim: Simulator) -> None:
    _expect(sim, "mst_aw_id", 0x2, "depth0 AW id passthrough mismatch")
    _expect(sim, "mst_aw_addr", 0x44, "depth0 AW address passthrough mismatch")
    _expect(sim, "mst_aw_prot", 0x3, "depth0 AW protection passthrough mismatch")
    _expect(sim, "mst_aw_valid", 0x1, "depth0 AW valid passthrough mismatch")
    _expect(sim, "slv_aw_ready", 0x1, "depth0 AW ready passthrough mismatch")
    _expect(sim, "mst_w_data", 0xCAFEBABE, "depth0 W data passthrough mismatch")
    _expect(sim, "mst_w_strb", 0xA, "depth0 W strobe passthrough mismatch")
    _expect(sim, "mst_w_last", 0x1, "depth0 W last passthrough mismatch")
    _expect(sim, "mst_w_valid", 0x1, "depth0 W valid passthrough mismatch")
    _expect(sim, "slv_w_ready", 0x0, "depth0 W ready passthrough mismatch")
    _expect(sim, "slv_b_id", 0x3, "depth0 B id passthrough mismatch")
    _expect(sim, "slv_b_resp", 0x2, "depth0 B response passthrough mismatch")
    _expect(sim, "slv_b_valid", 0x1, "depth0 B valid passthrough mismatch")
    _expect(sim, "mst_b_ready", 0x1, "depth0 B ready passthrough mismatch")
    _expect(sim, "mst_ar_id", 0x1, "depth0 AR id passthrough mismatch")
    _expect(sim, "mst_ar_addr", 0x88, "depth0 AR address passthrough mismatch")
    _expect(sim, "mst_ar_prot", 0x5, "depth0 AR protection passthrough mismatch")
    _expect(sim, "mst_ar_valid", 0x1, "depth0 AR valid passthrough mismatch")
    _expect(sim, "slv_ar_ready", 0x1, "depth0 AR ready passthrough mismatch")
    _expect(sim, "slv_r_id", 0x1, "depth0 R id passthrough mismatch")
    _expect(sim, "slv_r_data", 0x12345678, "depth0 R data passthrough mismatch")
    _expect(sim, "slv_r_resp", 0x1, "depth0 R response passthrough mismatch")
    _expect(sim, "slv_r_last", 0x1, "depth0 R last passthrough mismatch")
    _expect(sim, "slv_r_valid", 0x1, "depth0 R valid passthrough mismatch")
    _expect(sim, "mst_r_ready", 0x1, "depth0 R ready passthrough mismatch")


def _run_depth0(design, engine: str) -> None:
    sim = _make_step_sim(design, "axi_fifo_depth0_tb", engine, max_time=80)
    _drive_depth0(sim, engine)
    _expect_depth0(sim)


def _drive_depth1_requests(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "mst_aw_ready", 0)
    step_drive(sim, engine, "mst_w_ready", 0)
    step_drive(sim, engine, "mst_ar_ready", 0)
    step_drive(sim, engine, "slv_aw_id", 0x2)
    step_drive(sim, engine, "slv_aw_addr", 0x44)
    step_drive(sim, engine, "slv_aw_prot", 0x3)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0xCAFEBABE)
    step_drive(sim, engine, "slv_w_strb", 0xA)
    step_drive(sim, engine, "slv_w_last", 1)
    step_drive(sim, engine, "slv_w_valid", 1)
    step_drive(sim, engine, "slv_ar_id", 0x1)
    step_drive(sim, engine, "slv_ar_addr", 0x88)
    step_drive(sim, engine, "slv_ar_prot", 0x5)
    step_drive(sim, engine, "slv_ar_valid", 1)
    _settle_drives(sim, engine)


def _expect_depth1_request_pre(sim: Simulator) -> None:
    _expect(sim, "slv_aw_ready", 0x1, "depth1 AW should accept into empty fifo")
    _expect(sim, "slv_w_ready", 0x1, "depth1 W should accept into empty fifo")
    _expect(sim, "slv_ar_ready", 0x1, "depth1 AR should accept into empty fifo")
    _expect(sim, "mst_aw_valid", 0x0, "depth1 AW should not appear before capture")
    _expect(sim, "mst_w_valid", 0x0, "depth1 W should not appear before capture")
    _expect(sim, "mst_ar_valid", 0x0, "depth1 AR should not appear before capture")


def _drain_depth1_request_inputs(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)
    step_drive(sim, engine, "slv_ar_valid", 0)
    _settle_drives(sim, engine)


def _expect_depth1_request_buffered(sim: Simulator) -> None:
    _expect(sim, "mst_aw_id", 0x2, "depth1 AW id mismatch")
    _expect(sim, "mst_aw_addr", 0x44, "depth1 AW address mismatch")
    _expect(sim, "mst_aw_prot", 0x3, "depth1 AW protection mismatch")
    _expect(sim, "mst_aw_valid", 0x1, "depth1 AW valid mismatch")
    _expect(sim, "mst_w_data", 0xCAFEBABE, "depth1 W data mismatch")
    _expect(sim, "mst_w_strb", 0xA, "depth1 W strobe mismatch")
    _expect(sim, "mst_w_last", 0x1, "depth1 W last mismatch")
    _expect(sim, "mst_w_valid", 0x1, "depth1 W valid mismatch")
    _expect(sim, "mst_ar_id", 0x1, "depth1 AR id mismatch")
    _expect(sim, "mst_ar_addr", 0x88, "depth1 AR address mismatch")
    _expect(sim, "mst_ar_prot", 0x5, "depth1 AR protection mismatch")
    _expect(sim, "mst_ar_valid", 0x1, "depth1 AR valid mismatch")
    _expect(sim, "slv_aw_ready", 0x0, "depth1 AW should backpressure while occupied")
    _expect(sim, "slv_w_ready", 0x0, "depth1 W should backpressure while occupied")
    _expect(sim, "slv_ar_ready", 0x0, "depth1 AR should backpressure while occupied")


def _release_depth1_requests(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "mst_aw_ready", 1)
    step_drive(sim, engine, "mst_w_ready", 1)
    step_drive(sim, engine, "mst_ar_ready", 1)
    _settle_drives(sim, engine)


def _expect_depth1_request_recovered(sim: Simulator) -> None:
    _expect(sim, "mst_aw_valid", 0x0, "depth1 AW should clear after pop")
    _expect(sim, "mst_w_valid", 0x0, "depth1 W should clear after pop")
    _expect(sim, "mst_ar_valid", 0x0, "depth1 AR should clear after pop")
    _expect(sim, "slv_aw_ready", 0x1, "depth1 AW should recover after pop")
    _expect(sim, "slv_w_ready", 0x1, "depth1 W should recover after pop")
    _expect(sim, "slv_ar_ready", 0x1, "depth1 AR should recover after pop")


def _drive_depth1_responses(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv_b_ready", 0)
    step_drive(sim, engine, "slv_r_ready", 0)
    step_drive(sim, engine, "mst_b_id", 0x3)
    step_drive(sim, engine, "mst_b_resp", 0x2)
    step_drive(sim, engine, "mst_b_valid", 1)
    step_drive(sim, engine, "mst_r_id", 0x1)
    step_drive(sim, engine, "mst_r_data", 0x12345678)
    step_drive(sim, engine, "mst_r_resp", 0x1)
    step_drive(sim, engine, "mst_r_last", 1)
    step_drive(sim, engine, "mst_r_valid", 1)
    _settle_drives(sim, engine)


def _expect_depth1_response_pre(sim: Simulator) -> None:
    _expect(sim, "mst_b_ready", 0x1, "depth1 B should accept into empty fifo")
    _expect(sim, "mst_r_ready", 0x1, "depth1 R should accept into empty fifo")
    _expect(sim, "slv_b_valid", 0x0, "depth1 B should not appear before capture")
    _expect(sim, "slv_r_valid", 0x0, "depth1 R should not appear before capture")


def _drain_depth1_response_inputs(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "mst_b_valid", 0)
    step_drive(sim, engine, "mst_r_valid", 0)
    _settle_drives(sim, engine)


def _expect_depth1_response_buffered(sim: Simulator) -> None:
    _expect(sim, "slv_b_id", 0x3, "depth1 B id mismatch")
    _expect(sim, "slv_b_resp", 0x2, "depth1 B response mismatch")
    _expect(sim, "slv_b_valid", 0x1, "depth1 B valid mismatch")
    _expect(sim, "slv_r_id", 0x1, "depth1 R id mismatch")
    _expect(sim, "slv_r_data", 0x12345678, "depth1 R data mismatch")
    _expect(sim, "slv_r_resp", 0x1, "depth1 R response mismatch")
    _expect(sim, "slv_r_last", 0x1, "depth1 R last mismatch")
    _expect(sim, "slv_r_valid", 0x1, "depth1 R valid mismatch")
    _expect(sim, "mst_b_ready", 0x0, "depth1 B should backpressure while occupied")
    _expect(sim, "mst_r_ready", 0x0, "depth1 R should backpressure while occupied")


def _release_depth1_responses(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv_b_ready", 1)
    step_drive(sim, engine, "slv_r_ready", 1)
    _settle_drives(sim, engine)


def _expect_depth1_response_recovered(sim: Simulator) -> None:
    _expect(sim, "slv_b_valid", 0x0, "depth1 B should clear after pop")
    _expect(sim, "slv_r_valid", 0x0, "depth1 R should clear after pop")
    _expect(sim, "mst_b_ready", 0x1, "depth1 B should recover after pop")
    _expect(sim, "mst_r_ready", 0x1, "depth1 R should recover after pop")


def _run_depth1(design, engine: str) -> None:
    sim = _make_step_sim(design, "axi_fifo_depth1_tb", engine)
    _drive_depth1_requests(sim, engine)
    _expect_depth1_request_pre(sim)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "depth1 request capture edge not observed")
    _drain_depth1_request_inputs(sim, engine)
    _expect_depth1_request_buffered(sim)
    _release_depth1_requests(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "depth1 request release edge not observed")
    _settle_drives(sim, engine)
    _expect_depth1_request_recovered(sim)
    _drive_depth1_responses(sim, engine)
    _expect_depth1_response_pre(sim)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "depth1 response capture edge not observed")
    _drain_depth1_response_inputs(sim, engine)
    _expect_depth1_response_buffered(sim)
    _release_depth1_responses(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "depth1 response release edge not observed")
    _settle_drives(sim, engine)
    _expect_depth1_response_recovered(sim)


def main() -> int:
    design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_axi_fifo_pcache")
    if design.get_module("axi_fifo") is None:
        raise RuntimeError("Imported typed module 'axi_fifo' was not parsed")

    start = time.perf_counter()
    try:
        for engine in ENGINES:
            _run_depth0(design, engine)
            _run_depth1(design, engine)
    except Exception:
        print("axi_fifo run failed", file=sys.stderr)
        traceback.print_exc()
        return 1

    elapsed = time.perf_counter() - start
    print(f"axi_fifo passed on {', '.join(ENGINES)} in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
