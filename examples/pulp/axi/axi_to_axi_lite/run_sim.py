"""Run the adapted AXI-to-AXI-Lite bridge example.

Run from the repository root:

    uv run python examples/pulp/axi/axi_to_axi_lite/run_sim.py
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.endpoints import AXILiteResponseDriver
from veriforge.sim.example_runner import available_engines
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator


SCRIPT_DIR = Path(__file__).resolve().parent
RTL_DIR = SCRIPT_DIR / "rtl"
TB_FILE = SCRIPT_DIR / "tb" / "axi_to_axi_lite_tb.sv"
FILES = [
    str(RTL_DIR / "axi_pkg.sv"),
    str(RTL_DIR / "axi_to_axi_lite.sv"),
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


def _make_step_sim(design, engine: str) -> Simulator:
    top = design.get_module("axi_to_axi_lite_exec_tb")
    if top is None:
        raise RuntimeError("Top module 'axi_to_axi_lite_exec_tb' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    for signal_name in [
        "clk",
        "rst_n",
        "slv_aw_id",
        "slv_aw_addr",
        "slv_aw_prot",
        "slv_aw_len",
        "slv_aw_atop",
        "slv_aw_valid",
        "slv_w_data",
        "slv_w_strb",
        "slv_w_last",
        "slv_w_valid",
        "slv_b_ready",
        "slv_ar_id",
        "slv_ar_addr",
        "slv_ar_prot",
        "slv_ar_len",
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


def _begin_write(sim: Simulator, engine: str, request: tuple[int, int, int, int, int]) -> None:
    request_id, addr, prot, data, strb = request
    step_drive(sim, engine, "slv_aw_id", request_id)
    step_drive(sim, engine, "slv_aw_addr", addr)
    step_drive(sim, engine, "slv_aw_prot", prot)
    step_drive(sim, engine, "slv_aw_len", 0)
    step_drive(sim, engine, "slv_aw_atop", 0)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", data)
    step_drive(sim, engine, "slv_w_strb", strb)
    step_drive(sim, engine, "slv_w_last", 1)
    step_drive(sim, engine, "slv_w_valid", 1)
    step_drive(sim, engine, "slv_b_ready", 1)


def _end_write(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)


def _begin_read(sim: Simulator, engine: str, *, request_id: int, addr: int, prot: int) -> None:
    step_drive(sim, engine, "slv_ar_id", request_id)
    step_drive(sim, engine, "slv_ar_addr", addr)
    step_drive(sim, engine, "slv_ar_prot", prot)
    step_drive(sim, engine, "slv_ar_len", 0)
    step_drive(sim, engine, "slv_ar_valid", 1)
    step_drive(sim, engine, "slv_r_ready", 1)


def _end_read(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv_ar_valid", 0)


def _expect_write_forwarding(sim: Simulator) -> None:
    _expect(sim, "slv_aw_ready", 0x1, "write AW ready mismatch")
    _expect(sim, "slv_w_ready", 0x1, "write W ready mismatch")
    _expect(sim, "mst_aw_addr", 0x44, "write AW address mismatch")
    _expect(sim, "mst_aw_prot", 0x3, "write AW protection mismatch")
    _expect(sim, "mst_aw_valid", 0x1, "write AW valid mismatch")
    _expect(sim, "mst_w_data", 0xCAFEBABE, "write data mismatch")
    _expect(sim, "mst_w_strb", 0xA, "write strobe mismatch")
    _expect(sim, "mst_w_valid", 0x1, "write W valid mismatch")
    _expect(sim, "mst_b_ready", 0x0, "write response should wait for reflected ID")


def _expect_write_pending(sim: Simulator) -> None:
    _expect(sim, "slv_aw_ready", 0x0, "write path should stall while response is pending")
    _expect(sim, "mst_b_ready", 0x1, "write response ready mismatch after ID capture")


def _expect_read_forwarding(sim: Simulator) -> None:
    _expect(sim, "slv_ar_ready", 0x1, "read AR ready mismatch")
    _expect(sim, "mst_ar_addr", 0x88, "read AR address mismatch")
    _expect(sim, "mst_ar_prot", 0x5, "read AR protection mismatch")
    _expect(sim, "mst_ar_valid", 0x1, "read AR valid mismatch")
    _expect(sim, "mst_r_ready", 0x0, "read response should wait for reflected ID")


def _expect_read_pending(sim: Simulator) -> None:
    _expect(sim, "slv_ar_ready", 0x0, "read path should stall while response is pending")
    _expect(sim, "mst_r_ready", 0x1, "read response ready mismatch after ID capture")


def _exercise_bridge(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    response_driver = AXILiteResponseDriver(sim, "mst")

    response_driver.set_write_ready(True)
    _begin_write(sim, engine, (0x2, 0x44, 0x3, 0xCAFEBABE, 0xA))
    _settle_drives(sim, engine)
    _expect_write_forwarding(sim)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "write capture edge not observed")
    _end_write(sim, engine)
    _settle_drives(sim, engine)
    _expect_write_pending(sim)

    response_driver.begin_write_response(0x2)
    _settle_drives(sim, engine)
    _expect(sim, "slv_b_id", 0x2, "write response ID reflection mismatch")
    _expect(sim, "slv_b_resp", 0x2, "write response code mismatch")
    _expect(sim, "slv_b_valid", 0x1, "write response valid mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "write response consume edge not observed")
    response_driver.end_write_response()
    _settle_drives(sim, engine)
    _expect(sim, "slv_aw_ready", 0x1, "write path did not recover after response")

    response_driver.set_read_ready(True)
    _begin_read(sim, engine, request_id=0x1, addr=0x88, prot=0x5)
    _settle_drives(sim, engine)
    _expect_read_forwarding(sim)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "read capture edge not observed")
    _end_read(sim, engine)
    _settle_drives(sim, engine)
    _expect_read_pending(sim)

    response_driver.begin_read_response(0x12345678, resp=0x1)
    _settle_drives(sim, engine)
    _expect(sim, "slv_r_id", 0x1, "read response ID reflection mismatch")
    _expect(sim, "slv_r_data", 0x12345678, "read response data mismatch")
    _expect(sim, "slv_r_resp", 0x1, "read response code mismatch")
    _expect(sim, "slv_r_last", 0x1, "read response last mismatch")
    _expect(sim, "slv_r_valid", 0x1, "read response valid mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "read response consume edge not observed")
    response_driver.end_read_response()
    _settle_drives(sim, engine)
    _expect(sim, "slv_ar_ready", 0x1, "read path did not recover after response")


def main() -> int:
    design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_axi_to_axi_lite_pcache")
    if design.get_module("axi_to_axi_lite") is None:
        raise RuntimeError("Imported typed module 'axi_to_axi_lite' was not parsed")

    start = time.perf_counter()
    try:
        for engine in ENGINES:
            _exercise_bridge(design, engine)
    except Exception:
        print("axi_to_axi_lite run failed", file=sys.stderr)
        traceback.print_exc()
        return 1

    elapsed = time.perf_counter() - start
    print(f"axi_to_axi_lite passed on {', '.join(ENGINES)} in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
