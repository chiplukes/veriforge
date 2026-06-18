"""Run the adapted AXI-Lite crossbar example.

Run from the repository root:

    uv run python examples/pulp/axi/axi_lite_xbar/run_sim.py
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.endpoints import AXILiteMaster, AXILiteResponseError
from veriforge.sim.example_runner import available_engines
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator


SCRIPT_DIR = Path(__file__).resolve().parent
RTL_DIR = SCRIPT_DIR / "rtl"
TB_FILE = SCRIPT_DIR / "tb" / "axi_lite_xbar_tb.sv"
FILES = [
    str(RTL_DIR / "axi_pkg.sv"),
    str(RTL_DIR / "axi_lite_xbar.sv"),
    str(TB_FILE),
]
MAX_TIME = 260
ENGINES = available_engines()
TARGET0_INIT = 0x11111111
TARGET1_INIT = 0x22222222
TARGET0_WRITE = 0x11223344
TARGET1_WRITE = 0xAABBCCDD
ARBITRATION_FIRST = 0x12345678
ARBITRATION_SECOND = 0xDEADBEEF
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


def _expect_value(actual: int, expected: int, message: str) -> None:
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
    top = design.get_module("axi_lite_xbar_exec_tb")
    if top is None:
        raise RuntimeError("Top module 'axi_lite_xbar_exec_tb' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    for signal_name in [
        "clk",
        "rst_n",
        "slv0_aw_addr",
        "slv0_aw_prot",
        "slv0_aw_valid",
        "slv0_w_data",
        "slv0_w_strb",
        "slv0_w_valid",
        "slv0_b_ready",
        "slv0_ar_addr",
        "slv0_ar_prot",
        "slv0_ar_valid",
        "slv0_r_ready",
        "slv1_aw_addr",
        "slv1_aw_prot",
        "slv1_aw_valid",
        "slv1_w_data",
        "slv1_w_strb",
        "slv1_w_valid",
        "slv1_b_ready",
        "slv1_ar_addr",
        "slv1_ar_prot",
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


def _make_axi_lite_master(sim: Simulator, prefix: str, *, timeout_cycles: int = 10) -> AXILiteMaster:
    return AXILiteMaster(sim, prefix, default_timeout_cycles=timeout_cycles)


def _expect_read_error(master: AXILiteMaster, addr: int) -> None:
    try:
        master.read(addr)
    except AXILiteResponseError as exc:
        if "expected 0x0, got 0x3" not in str(exc):
            raise RuntimeError(f"unexpected AXI-Lite read error text: {exc}") from exc
        return
    raise RuntimeError("expected AXI-Lite read response error")


def _exercise_routing(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    port0 = _make_axi_lite_master(sim, "slv0")
    port1 = _make_axi_lite_master(sim, "slv1")
    _settle_drives(sim, engine)

    _expect_value(port0.read(ADDR_TARGET0), TARGET0_INIT, "target0 reset read mismatch")
    _expect_value(port1.read(ADDR_TARGET1), TARGET1_INIT, "target1 reset read mismatch")
    _expect_value(port0.write(ADDR_TARGET1, TARGET1_WRITE), 0x0, "target1 write response mismatch")
    _expect_value(port1.read(ADDR_TARGET1), TARGET1_WRITE, "cross-port target1 read mismatch")
    _expect_value(port1.write(ADDR_TARGET0, TARGET0_WRITE), 0x0, "target0 write response mismatch")
    _expect_value(port0.read(ADDR_TARGET0), TARGET0_WRITE, "cross-port target0 read mismatch")
    _expect_value(port0.write(ADDR_INVALID, 0x55AA55AA, expected_resp=0x3), 0x3, "decode error write response mismatch")
    _expect_read_error(port1, ADDR_INVALID)


def _exercise_same_target_arbitration(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)

    step_drive(sim, engine, "slv0_aw_addr", ADDR_TARGET0)
    step_drive(sim, engine, "slv0_aw_valid", 1)
    step_drive(sim, engine, "slv0_w_data", ARBITRATION_FIRST)
    step_drive(sim, engine, "slv0_w_strb", 0xF)
    step_drive(sim, engine, "slv0_w_valid", 1)
    step_drive(sim, engine, "slv0_b_ready", 0)
    step_drive(sim, engine, "slv1_aw_addr", ADDR_TARGET0 + 4)
    step_drive(sim, engine, "slv1_aw_valid", 1)
    step_drive(sim, engine, "slv1_w_data", ARBITRATION_SECOND)
    step_drive(sim, engine, "slv1_w_strb", 0xF)
    step_drive(sim, engine, "slv1_w_valid", 1)
    step_drive(sim, engine, "slv1_b_ready", 0)
    _settle_drives(sim, engine)

    _expect(sim, "slv0_aw_ready", 1, "port0 should win first same-target arbitration")
    _expect(sim, "slv0_w_ready", 1, "port0 write data should be accepted first")
    _expect(sim, "slv1_aw_ready", 0, "port1 should stall behind port0 for same target")
    _expect(sim, "slv1_w_ready", 0, "port1 write data should stall behind port0")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "first arbitration capture edge not observed")
    _settle_drives(sim, engine)

    step_drive(sim, engine, "slv0_aw_valid", 0)
    step_drive(sim, engine, "slv0_w_valid", 0)
    _settle_drives(sim, engine)
    _expect(sim, "slv0_b_valid", 1, "port0 should hold the first write response")
    _expect(sim, "slv1_aw_ready", 0, "port1 should remain stalled while target0 response is pending")

    step_drive(sim, engine, "slv0_b_ready", 1)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "first arbitration response release edge not observed")
    _settle_drives(sim, engine)

    _expect(sim, "slv0_b_valid", 0, "port0 response should clear after release")
    _expect(sim, "slv1_aw_ready", 1, "port1 should become ready after port0 releases target0")
    _expect(sim, "slv1_w_ready", 1, "port1 write data should become ready after release")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "second arbitration capture edge not observed")
    _settle_drives(sim, engine)

    step_drive(sim, engine, "slv1_aw_valid", 0)
    step_drive(sim, engine, "slv1_w_valid", 0)
    _settle_drives(sim, engine)
    _expect(sim, "slv1_b_valid", 1, "port1 should receive the deferred write response")
    _expect(sim, "target0_data", ARBITRATION_SECOND, "target0 should contain the second write after arbitration")
    _expect(sim, "target1_data", TARGET1_INIT, "target1 should remain unchanged during target0 arbitration")


def main() -> int:
    design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_axi_lite_xbar_pcache")
    if design.get_module("axi_lite_xbar") is None:
        raise RuntimeError("Imported typed module 'axi_lite_xbar' was not parsed")

    start = time.perf_counter()
    try:
        for engine in ENGINES:
            _exercise_routing(design, engine)
            _exercise_same_target_arbitration(design, engine)
    except Exception:
        print("axi_lite_xbar run failed", file=sys.stderr)
        traceback.print_exc()
        return 1

    elapsed = time.perf_counter() - start
    print(f"axi_lite_xbar passed on {', '.join(ENGINES)} in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
