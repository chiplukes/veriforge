"""Run the adapted AXI-Lite DW converter example.

Run from the repository root:

    uv run python examples/pulp/axi/axi_lite_dw_converter/run_sim.py
"""

from __future__ import annotations

import os
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
TB_FILE = SCRIPT_DIR / "tb" / "axi_lite_dw_tb.sv"
FILES = [
    str(RTL_DIR / "axi_pkg.sv"),
    str(RTL_DIR / "axi_lite_dw_converter.sv"),
    str(TB_FILE),
]
MAX_TIME = 320
ENGINES = available_engines()
SLV_AW_READY = "i_core.i_dut.slv_aw_ready_int"
SLV_W_READY = "i_core.i_dut.slv_w_ready_int"
SLV_B_RESP = "i_core.i_dut.slv_b_resp_int"
SLV_B_VALID = "i_core.i_dut.slv_b_valid_int"
SLV_AR_READY = "i_core.i_dut.slv_ar_ready_int"
SLV_R_DATA = "i_core.i_dut.slv_r_data_int"
SLV_R_RESP = "i_core.i_dut.slv_r_resp_int"
SLV_R_VALID = "i_core.i_dut.slv_r_valid_int"
MST_AW_ADDR = "i_core.i_dut.mst_aw_addr_int"
MST_AW_VALID = "i_core.i_dut.mst_aw_valid_int"
MST_W_DATA = "i_core.i_dut.mst_w_data_int"
MST_W_STRB = "i_core.i_dut.mst_w_strb_int"
MST_W_VALID = "i_core.i_dut.mst_w_valid_int"
MST_B_READY = "i_core.i_dut.mst_b_ready_int"
MST_AR_ADDR = "i_core.i_dut.mst_ar_addr_int"
MST_AR_VALID = "i_core.i_dut.mst_ar_valid_int"
MST_R_READY = "i_core.i_dut.mst_r_ready_int"
WIDE_REPEAT_32 = int("89ABCDEF" * 4, 16)
WIDE_REPEAT_64 = int("0123456789ABCDEF" * 2, 16)


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
        sim.run(max_time=0)
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


def _run_until_high(sim: Simulator, signal_name: str, limit: int, message: str) -> None:
    while sim.time < limit:
        if _read_int(sim, signal_name) == 1:
            return
        if not sim.run_step():
            raise RuntimeError(f"stepped engine stopped before {message}")
    raise RuntimeError(message)


def _make_step_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    if top is None:
        raise RuntimeError(f"Top module {top_name!r} not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    for signal_name in [
        "clk",
        "rst_n",
        "slv_aw_addr",
        "slv_aw_valid",
        "slv_w_data",
        "slv_w_strb",
        "slv_w_valid",
        "slv_b_ready",
        "slv_ar_addr",
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


def _check_downsize(design, engine: str) -> None:
    sim = _make_step_sim(design, "axi_lite_dw_down_tb", engine)

    _expect(sim, SLV_AW_READY, 1, "downsizer should accept AW after reset")
    _expect(sim, SLV_W_READY, 1, "downsizer should accept W after reset")
    _expect(sim, SLV_AR_READY, 1, "downsizer should accept AR after reset")

    step_drive(sim, engine, "mst_aw_ready", 1)
    step_drive(sim, engine, "mst_w_ready", 1)
    step_drive(sim, engine, "mst_ar_ready", 1)
    step_drive(sim, engine, "slv_aw_addr", 0x2)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0x61112222)
    step_drive(sim, engine, "slv_w_strb", 0xC)
    step_drive(sim, engine, "slv_w_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer write capture edge not observed")
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, MST_AW_VALID, 1, "downsizer should start first narrow AW")
    _expect(sim, MST_W_VALID, 1, "downsizer should start first narrow W")
    _expect(sim, MST_AW_ADDR, 0x0, "downsizer first AW address mismatch")
    _expect(sim, MST_W_DATA, 0x2222, "downsizer first W data mismatch")
    _expect(sim, MST_W_STRB, 0x0, "downsizer first W strobe mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer first narrow write edge not observed")
    _run_until_high(sim, MST_B_READY, sim.time + 20, "downsizer first B ready not observed")

    step_drive(sim, engine, "mst_b_resp", 0)
    step_drive(sim, engine, "mst_b_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer first B edge not observed")
    _run_until_high(sim, MST_AW_VALID, sim.time + 20, "downsizer second narrow AW not observed")
    step_drive(sim, engine, "mst_b_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, MST_AW_VALID, 1, "downsizer should start second narrow AW")
    _expect(sim, MST_W_VALID, 1, "downsizer should start second narrow W")
    _expect(sim, MST_AW_ADDR, 0x2, "downsizer second AW address mismatch")
    _expect(sim, MST_W_DATA, 0x6111, "downsizer second W data mismatch")
    _expect(sim, MST_W_STRB, 0x3, "downsizer second W strobe mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer second narrow write edge not observed")
    _run_until_high(sim, MST_B_READY, sim.time + 20, "downsizer second B ready not observed")

    step_drive(sim, engine, "mst_b_resp", 0x2)
    step_drive(sim, engine, "mst_b_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer second B edge not observed")
    _run_until_high(sim, SLV_B_VALID, sim.time + 20, "downsizer slave B response not observed")
    step_drive(sim, engine, "mst_b_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, SLV_B_VALID, 1, "downsizer should aggregate a slave B response")
    _expect(sim, SLV_B_RESP, 0x2, "downsizer should OR narrow B responses")
    step_drive(sim, engine, "slv_b_ready", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer slave B consume edge not observed")
    step_drive(sim, engine, "slv_b_ready", 0)
    _settle_drives(sim, engine)

    step_drive(sim, engine, "slv_ar_addr", 0x2)
    step_drive(sim, engine, "slv_ar_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer read capture edge not observed")
    step_drive(sim, engine, "slv_ar_valid", 0)
    _settle_drives(sim, engine)
    _run_until_high(sim, MST_AR_VALID, sim.time + 20, "downsizer first narrow AR not observed")

    _expect(sim, MST_AR_VALID, 1, "downsizer should start first narrow AR")
    _expect(sim, MST_AR_ADDR, 0x0, "downsizer first AR address mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer first AR edge not observed")
    _run_until_high(sim, MST_R_READY, sim.time + 20, "downsizer first R ready not observed")

    step_drive(sim, engine, "mst_r_data", 0x11111111)
    step_drive(sim, engine, "mst_r_resp", 0x0)
    step_drive(sim, engine, "mst_r_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer first R edge not observed")
    _run_until_high(sim, MST_AR_VALID, sim.time + 20, "downsizer second narrow AR not observed")
    step_drive(sim, engine, "mst_r_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, MST_AR_VALID, 1, "downsizer should start second narrow AR")
    _expect(sim, MST_AR_ADDR, 0x2, "downsizer second AR address mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer second AR edge not observed")
    _run_until_high(sim, MST_R_READY, sim.time + 20, "downsizer second R ready not observed")

    step_drive(sim, engine, "mst_r_data", 0x22222222)
    step_drive(sim, engine, "mst_r_resp", 0x3)
    step_drive(sim, engine, "mst_r_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer second R edge not observed")
    _run_until_high(sim, SLV_R_VALID, sim.time + 20, "downsizer slave R response not observed")
    step_drive(sim, engine, "mst_r_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, SLV_R_VALID, 1, "downsizer should aggregate a wide slave R response")
    _expect(sim, SLV_R_RESP, 0x3, "downsizer should OR narrow R responses")
    _expect(sim, SLV_R_DATA, 0x22221111, "downsizer read aggregation mismatch")


def _check_upsize(design, engine: str) -> None:
    sim = _make_step_sim(design, "axi_lite_dw_up_tb", engine)

    step_drive(sim, engine, "mst_aw_ready", 1)
    step_drive(sim, engine, "mst_w_ready", 1)
    step_drive(sim, engine, "mst_ar_ready", 1)
    step_drive(sim, engine, "slv_aw_addr", 0x2)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0x1EEF)
    step_drive(sim, engine, "slv_w_strb", 0x3)
    step_drive(sim, engine, "slv_w_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "upsizer write capture edge not observed")
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, MST_AW_VALID, 1, "upsizer should emit a single wide AW")
    _expect(sim, MST_W_VALID, 1, "upsizer should emit a single wide W")
    _expect(sim, MST_AW_ADDR, 0x2, "upsizer AW address mismatch")
    _expect(sim, MST_W_DATA, 0x1EEF1EEF, "upsizer replicated W data mismatch")
    _expect(sim, MST_W_STRB, 0xC, "upsizer shifted W strobe mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "upsizer wide write edge not observed")
    _run_until_high(sim, MST_B_READY, sim.time + 20, "upsizer B ready not observed")

    step_drive(sim, engine, "mst_b_resp", 0x0)
    step_drive(sim, engine, "mst_b_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "upsizer B edge not observed")
    _run_until_high(sim, SLV_B_VALID, sim.time + 20, "upsizer slave B response not observed")
    step_drive(sim, engine, "mst_b_valid", 0)
    _settle_drives(sim, engine)
    _expect(sim, SLV_B_VALID, 1, "upsizer should return a slave B response")
    _expect(sim, SLV_B_RESP, 0x0, "upsizer B response mismatch")
    step_drive(sim, engine, "slv_b_ready", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "upsizer slave B consume edge not observed")
    step_drive(sim, engine, "slv_b_ready", 0)
    _settle_drives(sim, engine)

    step_drive(sim, engine, "slv_ar_addr", 0x2)
    step_drive(sim, engine, "slv_ar_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "upsizer read capture edge not observed")
    step_drive(sim, engine, "slv_ar_valid", 0)
    _settle_drives(sim, engine)
    _run_until_high(sim, MST_AR_VALID, sim.time + 20, "upsizer wide AR not observed")

    _expect(sim, MST_AR_VALID, 1, "upsizer should emit a single wide AR")
    _expect(sim, MST_AR_ADDR, 0x2, "upsizer AR address mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "upsizer wide AR edge not observed")
    _run_until_high(sim, MST_R_READY, sim.time + 20, "upsizer R ready not observed")

    step_drive(sim, engine, "mst_r_data", 0x11223344)
    step_drive(sim, engine, "mst_r_resp", 0x2)
    step_drive(sim, engine, "mst_r_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "upsizer wide R edge not observed")
    _run_until_high(sim, SLV_R_VALID, sim.time + 20, "upsizer slave R response not observed")
    step_drive(sim, engine, "mst_r_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, SLV_R_VALID, 1, "upsizer should return a narrow slave R response")
    _expect(sim, SLV_R_RESP, 0x2, "upsizer R response mismatch")
    _expect(sim, SLV_R_DATA, 0x1122, "upsizer read lane selection mismatch")


def _check_passthrough(design, engine: str) -> None:
    sim = _make_step_sim(design, "axi_lite_dw_same_tb", engine)

    step_drive(sim, engine, "mst_aw_ready", 1)
    step_drive(sim, engine, "mst_w_ready", 1)
    step_drive(sim, engine, "mst_ar_ready", 1)
    step_drive(sim, engine, "slv_aw_addr", 0x8)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0xCAFEBABE)
    step_drive(sim, engine, "slv_w_strb", 0xF)
    step_drive(sim, engine, "slv_w_valid", 1)
    _settle_drives(sim, engine)
    _expect(sim, MST_AW_VALID, 1, "passthrough should forward AW immediately")
    _expect(sim, MST_AW_ADDR, 0x8, "passthrough AW address mismatch")
    _expect(sim, MST_W_VALID, 1, "passthrough should forward W immediately")
    _expect(sim, MST_W_DATA, 0xCAFEBABE, "passthrough W data mismatch")
    _expect(sim, MST_W_STRB, 0xF, "passthrough W strobe mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "passthrough write edge not observed")
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)
    step_drive(sim, engine, "mst_b_resp", 0x3)
    step_drive(sim, engine, "mst_b_valid", 1)
    _settle_drives(sim, engine)
    _expect(sim, SLV_B_VALID, 1, "passthrough should forward B immediately")
    _expect(sim, SLV_B_RESP, 0x3, "passthrough B response mismatch")

    step_drive(sim, engine, "slv_ar_addr", 0xC)
    step_drive(sim, engine, "slv_ar_valid", 1)
    step_drive(sim, engine, "mst_r_data", 0x13579BDF)
    step_drive(sim, engine, "mst_r_resp", 0x0)
    step_drive(sim, engine, "mst_r_valid", 1)
    _settle_drives(sim, engine)
    _expect(sim, MST_AR_VALID, 1, "passthrough should forward AR immediately")
    _expect(sim, MST_AR_ADDR, 0xC, "passthrough AR address mismatch")
    _expect(sim, SLV_R_VALID, 1, "passthrough should forward R immediately")
    _expect(sim, SLV_R_DATA, 0x13579BDF, "passthrough R data mismatch")
    _expect(sim, SLV_R_RESP, 0x0, "passthrough R response mismatch")


def _check_typed_up32_128(design, engine: str) -> None:
    sim = _make_step_sim(design, "axi_lite_dw_typed_up32_128_tb", engine)

    step_drive(sim, engine, "mst_aw_ready", 1)
    step_drive(sim, engine, "mst_w_ready", 1)
    step_drive(sim, engine, "mst_ar_ready", 1)
    step_drive(sim, engine, "slv_aw_addr", 0x8)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0x89ABCDEF)
    step_drive(sim, engine, "slv_w_strb", 0xF)
    step_drive(sim, engine, "slv_w_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->128 write capture edge not observed")
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, "mst_aw_addr", 0x8, "typed 32->128 AW address mismatch")
    _expect(sim, "mst_w_data", WIDE_REPEAT_32, "typed 32->128 W replication mismatch")
    _expect(sim, "mst_w_strb", 0x0F00, "typed 32->128 W strobe shift mismatch")
    _run_until_high(sim, "mst_b_ready", sim.time + 20, "typed 32->128 B ready not observed")
    step_drive(sim, engine, "mst_b_resp", 0x1)
    step_drive(sim, engine, "mst_b_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->128 B edge not observed")
    _run_until_high(sim, "slv_b_valid", sim.time + 20, "typed 32->128 slave B response not observed")
    step_drive(sim, engine, "mst_b_valid", 0)
    _settle_drives(sim, engine)
    _expect(sim, "slv_b_resp", 0x1, "typed 32->128 slave B response mismatch")

    step_drive(sim, engine, "slv_ar_addr", 0x8)
    step_drive(sim, engine, "slv_ar_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->128 read capture edge not observed")
    step_drive(sim, engine, "slv_ar_valid", 0)
    _settle_drives(sim, engine)
    _run_until_high(sim, "mst_r_ready", sim.time + 20, "typed 32->128 R ready not observed")
    step_drive(sim, engine, "mst_r_data", int("112233445566778899AABBCCDDEEFF00", 16))
    step_drive(sim, engine, "mst_r_resp", 0x2)
    step_drive(sim, engine, "mst_r_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->128 R edge not observed")
    _run_until_high(sim, "slv_r_valid", sim.time + 20, "typed 32->128 slave R response not observed")
    step_drive(sim, engine, "mst_r_valid", 0)
    _settle_drives(sim, engine)
    _expect(sim, "slv_r_resp", 0x2, "typed 32->128 slave R response mismatch")
    _expect(sim, "slv_r_data", 0x55667788, "typed 32->128 read lane selection mismatch")


def _check_typed_up64_128(design, engine: str) -> None:
    sim = _make_step_sim(design, "axi_lite_dw_typed_up64_128_tb", engine)

    step_drive(sim, engine, "mst_aw_ready", 1)
    step_drive(sim, engine, "mst_w_ready", 1)
    step_drive(sim, engine, "mst_ar_ready", 1)
    step_drive(sim, engine, "slv_aw_addr", 0x8)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0x0123456789ABCDEF)
    step_drive(sim, engine, "slv_w_strb", 0xFF)
    step_drive(sim, engine, "slv_w_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 64->128 write capture edge not observed")
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)
    _settle_drives(sim, engine)

    _expect(sim, "mst_aw_addr", 0x8, "typed 64->128 AW address mismatch")
    _expect(sim, "mst_w_data", WIDE_REPEAT_64, "typed 64->128 W replication mismatch")
    _expect(sim, "mst_w_strb", 0xFF00, "typed 64->128 W strobe shift mismatch")
    _run_until_high(sim, "mst_b_ready", sim.time + 20, "typed 64->128 B ready not observed")
    step_drive(sim, engine, "mst_b_resp", 0x3)
    step_drive(sim, engine, "mst_b_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 64->128 B edge not observed")
    _run_until_high(sim, "slv_b_valid", sim.time + 20, "typed 64->128 slave B response not observed")
    step_drive(sim, engine, "mst_b_valid", 0)
    _settle_drives(sim, engine)
    _expect(sim, "slv_b_resp", 0x3, "typed 64->128 slave B response mismatch")

    step_drive(sim, engine, "slv_ar_addr", 0x8)
    step_drive(sim, engine, "slv_ar_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 64->128 read capture edge not observed")
    step_drive(sim, engine, "slv_ar_valid", 0)
    _settle_drives(sim, engine)
    _run_until_high(sim, "mst_r_ready", sim.time + 20, "typed 64->128 R ready not observed")
    step_drive(sim, engine, "mst_r_data", int("0123456789ABCDEFFEEDC0DEBAADF00D", 16))
    step_drive(sim, engine, "mst_r_resp", 0x2)
    step_drive(sim, engine, "mst_r_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 64->128 R edge not observed")
    _run_until_high(sim, "slv_r_valid", sim.time + 20, "typed 64->128 slave R response not observed")
    step_drive(sim, engine, "mst_r_valid", 0)
    _settle_drives(sim, engine)
    _expect(sim, "slv_r_resp", 0x2, "typed 64->128 slave R response mismatch")
    _expect(sim, "slv_r_data", 0x0123456789ABCDEF, "typed 64->128 read lane selection mismatch")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _check_downsize(design, engine)
        _check_upsize(design, engine)
        _check_passthrough(design, engine)
        _check_typed_up32_128(design, engine)
        _check_typed_up64_128(design, engine)
    except Exception as exc:
        print(f"  FAIL axi_lite_dw_converter python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS axi_lite_dw_converter python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    print("Parsing AXI-Lite DW converter example...")
    os.chdir(SCRIPT_DIR)
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_axi_lite_dw_converter_pcache")
    except Exception:
        traceback.print_exc()
        return 1

    print(f"  parsed {len(design.modules)} modules in {time.time() - t0:.2f}s")

    status = 0
    for engine in ENGINES:
        status |= _run_engine(design, engine)
    return status


if __name__ == "__main__":
    sys.exit(main())
