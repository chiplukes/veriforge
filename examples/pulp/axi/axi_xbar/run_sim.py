"""Run the adapted AXI crossbar example.

Run from the repository root:

    uv run python examples/pulp/axi/axi_xbar/run_sim.py
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
TB_FILE = SCRIPT_DIR / "tb" / "axi_xbar_tb.sv"
FILES = [
    str(RTL_DIR / "axi_pkg.sv"),
    str(RTL_DIR / "axi_xbar.sv"),
    str(TB_FILE),
]
MAX_TIME = 320
ENGINES = available_engines()
TARGET0_INIT = 0x11111111
TARGET1_INIT = 0x22222222
TARGET0_WRITE = 0xCAFEBABE
TARGET1_WRITE = 0x10203040
ARB_FIRST = 0x12345678
ARB_SECOND = 0xDEADBEEF
ADDR_TARGET0 = 0x000
ADDR_TARGET1 = 0x100
ADDR_INVALID = 0x200


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
    top = design.get_module("axi_xbar_exec_tb")
    if top is None:
        raise RuntimeError("Top module 'axi_xbar_exec_tb' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    for signal_name in [
        "clk",
        "rst_n",
        "slv0_aw_id",
        "slv0_aw_addr",
        "slv0_aw_prot",
        "slv0_aw_len",
        "slv0_aw_valid",
        "slv0_w_data",
        "slv0_w_strb",
        "slv0_w_last",
        "slv0_w_valid",
        "slv0_b_ready",
        "slv0_ar_id",
        "slv0_ar_addr",
        "slv0_ar_prot",
        "slv0_ar_len",
        "slv0_ar_valid",
        "slv0_r_ready",
        "slv1_aw_id",
        "slv1_aw_addr",
        "slv1_aw_prot",
        "slv1_aw_len",
        "slv1_aw_valid",
        "slv1_w_data",
        "slv1_w_strb",
        "slv1_w_last",
        "slv1_w_valid",
        "slv1_b_ready",
        "slv1_ar_id",
        "slv1_ar_addr",
        "slv1_ar_prot",
        "slv1_ar_len",
        "slv1_ar_valid",
        "slv1_r_ready",
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


def _drive_idle(sim: Simulator, engine: str) -> None:
    for signal_name in [
        "slv0_aw_valid",
        "slv0_w_valid",
        "slv0_b_ready",
        "slv0_ar_valid",
        "slv0_r_ready",
        "slv1_aw_valid",
        "slv1_w_valid",
        "slv1_b_ready",
        "slv1_ar_valid",
        "slv1_r_ready",
    ]:
        step_drive(sim, engine, signal_name, 0)


def _drive_parallel_writes(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv0_aw_id", 0x1)
    step_drive(sim, engine, "slv0_aw_addr", ADDR_TARGET0)
    step_drive(sim, engine, "slv0_aw_len", 0)
    step_drive(sim, engine, "slv0_aw_valid", 1)
    step_drive(sim, engine, "slv0_w_data", TARGET0_WRITE)
    step_drive(sim, engine, "slv0_w_strb", 0xF)
    step_drive(sim, engine, "slv0_w_last", 1)
    step_drive(sim, engine, "slv0_w_valid", 1)
    step_drive(sim, engine, "slv0_b_ready", 0)
    step_drive(sim, engine, "slv1_aw_id", 0x2)
    step_drive(sim, engine, "slv1_aw_addr", ADDR_TARGET1)
    step_drive(sim, engine, "slv1_aw_len", 0)
    step_drive(sim, engine, "slv1_aw_valid", 1)
    step_drive(sim, engine, "slv1_w_data", TARGET1_WRITE)
    step_drive(sim, engine, "slv1_w_strb", 0xF)
    step_drive(sim, engine, "slv1_w_last", 1)
    step_drive(sim, engine, "slv1_w_valid", 1)
    step_drive(sim, engine, "slv1_b_ready", 0)
    _settle_drives(sim, engine)


def _check_parallel_write_capture(sim: Simulator, engine: str) -> None:
    _expect(sim, "slv0_aw_ready", 1, "port0 target0 write should be accepted")
    _expect(sim, "slv1_aw_ready", 1, "port1 target1 write should be accepted")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "parallel write capture edge not observed")
    _settle_drives(sim, engine)

    step_drive(sim, engine, "slv0_aw_valid", 0)
    step_drive(sim, engine, "slv0_w_valid", 0)
    step_drive(sim, engine, "slv1_aw_valid", 0)
    step_drive(sim, engine, "slv1_w_valid", 0)
    _settle_drives(sim, engine)


def _check_parallel_write_responses(sim: Simulator) -> None:
    _expect(sim, "slv0_b_valid", 1, "port0 write response should be pending")
    _expect(sim, "slv0_b_id", 0x1, "port0 write response ID mismatch")
    _expect(sim, "slv0_b_resp", 0x0, "port0 write response code mismatch")
    _expect(sim, "slv1_b_valid", 1, "port1 write response should be pending")
    _expect(sim, "slv1_b_id", 0x2, "port1 write response ID mismatch")
    _expect(sim, "slv1_b_resp", 0x0, "port1 write response code mismatch")
    _expect(sim, "target0_data", TARGET0_WRITE, "target0 write data mismatch")
    _expect(sim, "target1_data", TARGET1_WRITE, "target1 write data mismatch")
    _expect(sim, "mst0_last_aw_id", 0x1, "target0 widened AW ID mismatch")
    _expect(sim, "mst1_last_aw_id", 0x6, "target1 widened AW ID mismatch")


def _release_parallel_write_responses(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv0_b_ready", 1)
    step_drive(sim, engine, "slv1_b_ready", 1)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "parallel write release edge not observed")
    _settle_drives(sim, engine)

    _expect(sim, "slv0_b_valid", 0, "port0 write response should clear")
    _expect(sim, "slv1_b_valid", 0, "port1 write response should clear")


def _drive_parallel_reads(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv0_ar_id", 0x0)
    step_drive(sim, engine, "slv0_ar_addr", ADDR_TARGET1)
    step_drive(sim, engine, "slv0_ar_len", 0)
    step_drive(sim, engine, "slv0_ar_valid", 1)
    step_drive(sim, engine, "slv0_r_ready", 0)
    step_drive(sim, engine, "slv1_ar_id", 0x3)
    step_drive(sim, engine, "slv1_ar_addr", ADDR_TARGET0)
    step_drive(sim, engine, "slv1_ar_len", 0)
    step_drive(sim, engine, "slv1_ar_valid", 1)
    step_drive(sim, engine, "slv1_r_ready", 0)
    _settle_drives(sim, engine)


def _check_parallel_read_capture(sim: Simulator, engine: str) -> None:
    _expect(sim, "slv0_ar_ready", 1, "port0 target1 read should be accepted")
    _expect(sim, "slv1_ar_ready", 1, "port1 target0 read should be accepted")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "parallel read capture edge not observed")
    _settle_drives(sim, engine)

    step_drive(sim, engine, "slv0_ar_valid", 0)
    step_drive(sim, engine, "slv1_ar_valid", 0)
    _settle_drives(sim, engine)


def _check_parallel_read_responses(sim: Simulator) -> None:
    _expect(sim, "slv0_r_valid", 1, "port0 read response should be pending")
    _expect(sim, "slv0_r_id", 0x0, "port0 read response ID mismatch")
    _expect(sim, "slv0_r_data", TARGET1_WRITE, "port0 read data mismatch")
    _expect(sim, "slv0_r_resp", 0x0, "port0 read response code mismatch")
    _expect(sim, "slv0_r_last", 1, "port0 read last mismatch")
    _expect(sim, "slv1_r_valid", 1, "port1 read response should be pending")
    _expect(sim, "slv1_r_id", 0x3, "port1 read response ID mismatch")
    _expect(sim, "slv1_r_data", TARGET0_WRITE, "port1 read data mismatch")
    _expect(sim, "slv1_r_resp", 0x0, "port1 read response code mismatch")
    _expect(sim, "slv1_r_last", 1, "port1 read last mismatch")
    _expect(sim, "mst0_last_ar_id", 0x7, "target0 widened AR ID mismatch")
    _expect(sim, "mst1_last_ar_id", 0x0, "target1 widened AR ID mismatch")


def _release_parallel_read_responses(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "slv0_r_ready", 1)
    step_drive(sim, engine, "slv1_r_ready", 1)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "parallel read release edge not observed")
    _settle_drives(sim, engine)

    _expect(sim, "slv0_r_valid", 0, "port0 read response should clear")
    _expect(sim, "slv1_r_valid", 0, "port1 read response should clear")


def _exercise_parallel_routes(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)

    _drive_parallel_writes(sim, engine)
    _check_parallel_write_capture(sim, engine)
    _check_parallel_write_responses(sim)
    _release_parallel_write_responses(sim, engine)

    _drive_parallel_reads(sim, engine)
    _check_parallel_read_capture(sim, engine)
    _check_parallel_read_responses(sim)
    _release_parallel_read_responses(sim, engine)


def _exercise_decode_errors(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)

    step_drive(sim, engine, "slv0_aw_id", 0x2)
    step_drive(sim, engine, "slv0_aw_addr", ADDR_INVALID)
    step_drive(sim, engine, "slv0_aw_len", 0)
    step_drive(sim, engine, "slv0_aw_valid", 1)
    step_drive(sim, engine, "slv0_w_data", 0x55AA55AA)
    step_drive(sim, engine, "slv0_w_strb", 0xF)
    step_drive(sim, engine, "slv0_w_last", 1)
    step_drive(sim, engine, "slv0_w_valid", 1)
    step_drive(sim, engine, "slv0_b_ready", 0)
    _settle_drives(sim, engine)

    _expect(sim, "slv0_aw_ready", 1, "decode-error write should be accepted")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "decode-error write capture edge not observed")
    _settle_drives(sim, engine)

    step_drive(sim, engine, "slv0_aw_valid", 0)
    step_drive(sim, engine, "slv0_w_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, "slv0_b_valid", 1, "decode-error write response should be pending")
    _expect(sim, "slv0_b_id", 0x2, "decode-error write response ID mismatch")
    _expect(sim, "slv0_b_resp", 0x3, "decode-error write response code mismatch")
    _expect(sim, "target0_data", TARGET0_INIT, "target0 must remain unchanged on decode error")
    _expect(sim, "target1_data", TARGET1_INIT, "target1 must remain unchanged on decode error")

    step_drive(sim, engine, "slv0_b_ready", 1)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "decode-error write release edge not observed")
    _settle_drives(sim, engine)

    step_drive(sim, engine, "slv1_ar_id", 0x1)
    step_drive(sim, engine, "slv1_ar_addr", ADDR_INVALID)
    step_drive(sim, engine, "slv1_ar_len", 0)
    step_drive(sim, engine, "slv1_ar_valid", 1)
    step_drive(sim, engine, "slv1_r_ready", 0)
    _settle_drives(sim, engine)

    _expect(sim, "slv1_ar_ready", 1, "decode-error read should be accepted")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "decode-error read capture edge not observed")
    _settle_drives(sim, engine)

    step_drive(sim, engine, "slv1_ar_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, "slv1_r_valid", 1, "decode-error read response should be pending")
    _expect(sim, "slv1_r_id", 0x1, "decode-error read response ID mismatch")
    _expect(sim, "slv1_r_data", 0xBADCAB1E, "decode-error read data mismatch")
    _expect(sim, "slv1_r_resp", 0x3, "decode-error read response code mismatch")
    _expect(sim, "slv1_r_last", 1, "decode-error read last mismatch")

    step_drive(sim, engine, "slv1_r_ready", 1)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "decode-error read release edge not observed")
    _settle_drives(sim, engine)


def _exercise_same_target_write_arbitration(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)

    step_drive(sim, engine, "slv0_aw_id", 0x1)
    step_drive(sim, engine, "slv0_aw_addr", ADDR_TARGET0)
    step_drive(sim, engine, "slv0_aw_len", 0)
    step_drive(sim, engine, "slv0_aw_valid", 1)
    step_drive(sim, engine, "slv0_w_data", ARB_FIRST)
    step_drive(sim, engine, "slv0_w_strb", 0xF)
    step_drive(sim, engine, "slv0_w_last", 1)
    step_drive(sim, engine, "slv0_w_valid", 1)
    step_drive(sim, engine, "slv0_b_ready", 0)
    step_drive(sim, engine, "slv1_aw_id", 0x2)
    step_drive(sim, engine, "slv1_aw_addr", ADDR_TARGET0 + 4)
    step_drive(sim, engine, "slv1_aw_len", 0)
    step_drive(sim, engine, "slv1_aw_valid", 1)
    step_drive(sim, engine, "slv1_w_data", ARB_SECOND)
    step_drive(sim, engine, "slv1_w_strb", 0xF)
    step_drive(sim, engine, "slv1_w_last", 1)
    step_drive(sim, engine, "slv1_w_valid", 1)
    step_drive(sim, engine, "slv1_b_ready", 0)
    _settle_drives(sim, engine)

    _expect(sim, "slv0_aw_ready", 1, "port0 should win first target0 arbitration")
    _expect(sim, "slv1_aw_ready", 0, "port1 should stall behind port0 on target0")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "first arbitration capture edge not observed")
    _settle_drives(sim, engine)

    step_drive(sim, engine, "slv0_aw_valid", 0)
    step_drive(sim, engine, "slv0_w_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, "slv0_b_valid", 1, "port0 first write response should be pending")
    _expect(sim, "slv1_aw_ready", 0, "port1 should remain stalled while target0 response is pending")
    _expect(sim, "target0_data", ARB_FIRST, "target0 should hold the first write before release")

    step_drive(sim, engine, "slv0_b_ready", 1)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "first arbitration release edge not observed")
    _settle_drives(sim, engine)

    _expect(sim, "slv0_b_valid", 0, "port0 first write response should clear")
    _expect(sim, "slv1_aw_ready", 1, "port1 should become ready after target0 release")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "second arbitration capture edge not observed")
    _settle_drives(sim, engine)

    step_drive(sim, engine, "slv1_aw_valid", 0)
    step_drive(sim, engine, "slv1_w_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, "slv1_b_valid", 1, "port1 deferred write response should be pending")
    _expect(sim, "slv1_b_id", 0x2, "port1 deferred write response ID mismatch")
    _expect(sim, "target0_data", ARB_SECOND, "target0 should contain the deferred write data")
    _expect(sim, "mst0_last_aw_id", 0x6, "target0 widened AW ID should update for the deferred write")

    step_drive(sim, engine, "slv1_b_ready", 1)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "second arbitration release edge not observed")
    _settle_drives(sim, engine)
    _drive_idle(sim, engine)


def main() -> int:
    design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_axi_xbar_pcache")
    if design.get_module("axi_xbar") is None:
        raise RuntimeError("Imported typed module 'axi_xbar' was not parsed")

    start = time.perf_counter()
    try:
        for engine in ENGINES:
            _exercise_parallel_routes(design, engine)
            _exercise_decode_errors(design, engine)
            _exercise_same_target_write_arbitration(design, engine)
    except Exception:
        print("axi_xbar run failed", file=sys.stderr)
        traceback.print_exc()
        return 1

    elapsed = time.perf_counter() - start
    print(f"axi_xbar passed on {', '.join(ENGINES)} in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
