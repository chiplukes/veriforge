from __future__ import annotations

import shutil
from contextlib import nullcontext
from pathlib import Path

import pytest

Cython = pytest.importorskip("Cython")

from veriforge.project import parse_files  # noqa: E402
from veriforge.sim.endpoints import (  # noqa: E402
    AXILiteMaster,
    AXILiteRequestDriver,
    AXILiteResponseDriver,
    AXILiteResponseError,
)
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until  # noqa: E402
from veriforge.sim.testbench import Clock, Simulator  # noqa: E402
from veriforge.sim.trace import attach_vcd  # noqa: E402


_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")


def _engines() -> list[str]:
    engines = ["reference", "vm", "vm-fast"]
    if _has_compiler:
        engines.append("compiled")
    return engines


ENGINES = _engines()
REPO_ROOT = Path(__file__).resolve().parents[2]
SLV_B_RESP = "i_core.i_dut.slv_b_resp_int"
SLV_B_VALID = "i_core.i_dut.slv_b_valid_int"
SLV_R_DATA = "i_core.i_dut.slv_r_data_int"
SLV_R_RESP = "i_core.i_dut.slv_r_resp_int"
SLV_R_VALID = "i_core.i_dut.slv_r_valid_int"
MST_AW_ADDR = "i_core.i_dut.mst_aw_addr_int"
MST_W_DATA = "i_core.i_dut.mst_w_data_int"
MST_W_STRB = "i_core.i_dut.mst_w_strb_int"
MST_AW_VALID = "i_core.i_dut.mst_aw_valid_int"
MST_B_READY = "i_core.i_dut.mst_b_ready_int"
MST_R_READY = "i_core.i_dut.mst_r_ready_int"
MST_INTERNAL_PREFIX = "i_core.i_dut.mst"
WIDE_REPEAT_32 = int("89ABCDEF" * 4, 16)
WIDE_REPEAT_64 = int("0123456789ABCDEF" * 2, 16)
WIDE_VALUE_128 = int("89ABCDEF01234567FEDCBA9876543210", 16)
WIDE_REPEAT_128 = int("89ABCDEF01234567FEDCBA9876543210" * 2, 16)
WIDE_READ_256 = int("112233445566778899AABBCCDDEEFF0089ABCDEF01234567FEDCBA9876543210", 16)


def _open_vcd_trace(sim: Simulator, *, vcd_dir: Path | None, stem: str):
    if vcd_dir is None:
        return nullcontext()

    vcd_dir.mkdir(parents=True, exist_ok=True)
    return attach_vcd(sim, vcd_dir / f"{stem}.vcd")


def _read_int(sim: Simulator, signal_name: str) -> int:
    raw = sim.read(signal_name)
    try:
        return int(raw)
    except Exception as exc:
        raise RuntimeError(f"{signal_name} is not fully resolved: {raw}") from exc


def _expect(sim: Simulator, signal_name: str, expected: int, message: str) -> None:
    actual = _read_int(sim, signal_name)
    assert actual == expected, f"{message}: expected {expected:#x}, got {actual:#x}"


def _settle_drives(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=sim.time)
    else:
        step_eval_now(sim)


def _run_until_rising_edge(sim: Simulator, signal_name: str, limit: int, message: str) -> None:
    previous = _read_int(sim, signal_name)
    while sim.time < limit:
        assert sim.run_step(), f"stepped engine stopped before {message}"
        current = _read_int(sim, signal_name)
        if previous == 0 and current == 1:
            return
        previous = current
    raise AssertionError(message)


def _run_until_high(sim: Simulator, signal_name: str, limit: int, message: str) -> None:
    while sim.time < limit:
        if _read_int(sim, signal_name) == 1:
            return
        assert sim.run_step(), f"stepped engine stopped before {message}"
    raise AssertionError(message)


def _run_until_condition(sim: Simulator, limit: int, predicate, message: str) -> None:
    while sim.time < limit:
        if predicate(sim):
            return
        assert sim.run_step(), f"stepped engine stopped before {message}"
    if not predicate(sim):
        raise AssertionError(message)


def _set_byte(flat_value: int, byte_index: int, byte_value: int) -> int:
    mask = 0xFF << (byte_index * 8)
    return (flat_value & ~mask) | ((byte_value & 0xFF) << (byte_index * 8))


def _make_step_sim(design, top_name: str, engine: str, max_time: int = 320) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

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
        "reg_d_flat",
        "reg_load",
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
        try:
            step_drive(sim, engine, signal_name, 0)
        except Exception:  # noqa: S110 - optional testbench signals vary by top module
            pass
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


def _parse_axi_dw_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_lite_dw_converter"
    files = [
        str(example / "rtl" / "axi_pkg.sv"),
        str(example / "rtl" / "axi_lite_dw_converter.sv"),
        str(example / "tb" / "axi_lite_dw_tb.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "axi_dw_pcache")


def _parse_axi_lite_to_axi_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_lite_to_axi"
    files = [
        str(example / "rtl" / "axi_pkg.sv"),
        str(example / "rtl" / "axi_lite_to_axi.sv"),
        str(example / "tb" / "axi_lite_to_axi_tb.sv"),
    ]
    design = parse_files(files, preprocess=True, cache_dir=tmp_path / "axi_lite_to_axi_pcache")
    assert design.get_module("axi_lite_to_axi") is not None
    return design


def _parse_axi_fifo_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_fifo"
    files = [
        str(example / "rtl" / "axi_fifo.sv"),
        str(example / "tb" / "axi_fifo_tb.sv"),
    ]
    design = parse_files(files, preprocess=True, cache_dir=tmp_path / "axi_fifo_pcache")
    assert design.get_module("axi_fifo") is not None
    return design


def _parse_axi_cdc_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_cdc"
    files = [
        str(example / "rtl" / "axi_cdc.sv"),
        str(example / "rtl" / "cdc_2phase.sv"),
        str(example / "rtl" / "cdc_fifo_2phase.sv"),
        str(example / "tb" / "axi_cdc_tb.sv"),
    ]
    design = parse_files(files, preprocess=True, cache_dir=tmp_path / "axi_cdc_pcache")
    assert design.get_module("axi_cdc") is not None
    return design


def _parse_axi_xbar_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_xbar"
    files = [
        str(example / "rtl" / "axi_pkg.sv"),
        str(example / "rtl" / "axi_xbar.sv"),
        str(example / "tb" / "axi_xbar_tb.sv"),
    ]
    design = parse_files(files, preprocess=True, cache_dir=tmp_path / "axi_xbar_pcache")
    assert design.get_module("axi_xbar") is not None
    return design


def _parse_axi_lite_xbar_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_lite_xbar"
    files = [
        str(example / "rtl" / "axi_pkg.sv"),
        str(example / "rtl" / "axi_lite_xbar.sv"),
        str(example / "tb" / "axi_lite_xbar_tb.sv"),
    ]
    design = parse_files(files, preprocess=True, cache_dir=tmp_path / "axi_lite_xbar_pcache")
    assert design.get_module("axi_lite_xbar") is not None
    return design


def _parse_axi_lite_mailbox_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_lite_mailbox"
    files = [
        str(example / "rtl" / "axi_lite_mailbox.sv"),
        str(example / "tb" / "axi_lite_mailbox_tb.sv"),
    ]
    design = parse_files(files, preprocess=True, cache_dir=tmp_path / "axi_lite_mailbox_pcache")
    assert design.get_module("axi_lite_mailbox") is not None
    return design


def _parse_axi_to_axi_lite_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_to_axi_lite"
    files = [
        str(example / "rtl" / "axi_pkg.sv"),
        str(example / "rtl" / "axi_to_axi_lite.sv"),
        str(example / "tb" / "axi_to_axi_lite_tb.sv"),
    ]
    design = parse_files(files, preprocess=True, cache_dir=tmp_path / "axi_to_axi_lite_pcache")
    assert design.get_module("axi_to_axi_lite") is not None
    return design


def _parse_axi_regs_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_lite_regs"
    files = [
        str(example / "rtl" / "axi_pkg.sv"),
        str(example / "rtl" / "axi_lite_regs.sv"),
        str(example / "tb" / "axi_lite_regs_tb.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "axi_regs_pcache")


def _make_axi_lite_master(sim: Simulator, prefix: str = "slv", *, timeout_cycles: int = 6) -> AXILiteMaster:
    return AXILiteMaster(sim, prefix, default_timeout_cycles=timeout_cycles)


def _make_axi_lite_request_driver(sim: Simulator) -> AXILiteRequestDriver:
    return AXILiteRequestDriver(sim, "slv")


def _make_axi_lite_response_driver(sim: Simulator) -> AXILiteResponseDriver:
    return AXILiteResponseDriver(sim, "mst")


def _settle_axi_cdc_drives(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=0)
    else:
        step_eval_now(sim, "src_clk_i")


def _make_axi_cdc_step_sim(design, engine: str, max_time: int = 320) -> Simulator:
    top = design.get_module("axi_cdc_exec_tb")
    assert top is not None, "Top module 'axi_cdc_exec_tb' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    for signal_name in [
        "src_clk_i",
        "dst_clk_i",
        "src_rst_ni",
        "dst_rst_ni",
        "src_aw_id",
        "src_aw_addr",
        "src_aw_prot",
        "src_aw_len",
        "src_aw_valid",
        "src_w_data",
        "src_w_strb",
        "src_w_last",
        "src_w_valid",
        "src_b_ready",
        "src_ar_id",
        "src_ar_addr",
        "src_ar_prot",
        "src_ar_len",
        "src_ar_valid",
        "src_r_ready",
        "dst_aw_ready",
        "dst_w_ready",
        "dst_b_id",
        "dst_b_resp",
        "dst_b_valid",
        "dst_ar_ready",
        "dst_r_id",
        "dst_r_data",
        "dst_r_resp",
        "dst_r_last",
        "dst_r_valid",
    ]:
        step_drive(sim, engine, signal_name, 0)
    _settle_axi_cdc_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), max_time)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=14), max_time)
    _settle_axi_cdc_drives(sim, engine)
    return sim


def _make_axi_cdc_req_fifo_step_sim(design, engine: str, max_time: int = 360) -> Simulator:
    top = design.get_module("axi_cdc_req_fifo_exec_tb")
    assert top is not None, "Top module 'axi_cdc_req_fifo_exec_tb' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    for signal_name in [
        "src_clk_i",
        "dst_clk_i",
        "src_rst_ni",
        "dst_rst_ni",
        "src_push_i",
        "src_aw_id",
        "src_aw_addr",
        "src_aw_prot",
        "src_aw_len",
        "src_aw_valid",
        "src_w_data",
        "src_w_strb",
        "src_w_last",
        "src_w_valid",
        "src_b_ready",
        "src_ar_id",
        "src_ar_addr",
        "src_ar_prot",
        "src_ar_len",
        "src_ar_valid",
        "src_r_ready",
        "dst_ready_i",
    ]:
        step_drive(sim, engine, signal_name, 0)
    _settle_axi_cdc_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), max_time)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=14), max_time)
    _settle_axi_cdc_drives(sim, engine)
    return sim


def _make_axi_cdc_resp_fifo_step_sim(design, engine: str, max_time: int = 360) -> Simulator:
    top = design.get_module("axi_cdc_resp_fifo_exec_tb")
    assert top is not None, "Top module 'axi_cdc_resp_fifo_exec_tb' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    for signal_name in [
        "src_clk_i",
        "dst_clk_i",
        "src_rst_ni",
        "dst_rst_ni",
        "src_ready_i",
        "dst_push_i",
        "dst_aw_ready",
        "dst_w_ready",
        "dst_b_id",
        "dst_b_resp",
        "dst_b_valid",
        "dst_ar_ready",
        "dst_r_id",
        "dst_r_data",
        "dst_r_resp",
        "dst_r_last",
        "dst_r_valid",
    ]:
        step_drive(sim, engine, signal_name, 0)
    _settle_axi_cdc_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), max_time)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=14), max_time)
    _settle_axi_cdc_drives(sim, engine)
    return sim


def _release_axi_cdc_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_axi_cdc_drives(sim, engine)
    step_run_until(sim, 45)
    _expect(sim, "src_aw_ready", 0x1, "write-address channel should be ready after reset")
    _expect(sim, "src_w_ready", 0x1, "write-data channel should be ready after reset")
    _expect(sim, "src_ar_ready", 0x1, "read-address channel should be ready after reset")
    _expect(sim, "src_b_valid", 0x0, "write response channel should be idle after reset")
    _expect(sim, "src_r_valid", 0x0, "read response channel should be idle after reset")
    _expect(sim, "dst_aw_valid", 0x0, "destination AW channel should be idle after reset")
    _expect(sim, "dst_w_valid", 0x0, "destination W channel should be idle after reset")
    _expect(sim, "dst_ar_valid", 0x0, "destination AR channel should be idle after reset")


def _release_axi_cdc_req_fifo_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_axi_cdc_drives(sim, engine)
    step_run_until(sim, 45)
    _expect(sim, "src_ready_o", 0x1, "request fifo should be ready after reset")
    _expect(sim, "dst_valid_o", 0x0, "request fifo destination should be idle after reset")


def _release_axi_cdc_resp_fifo_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_axi_cdc_drives(sim, engine)
    step_run_until(sim, 45)
    _expect(sim, "dst_ready_o", 0x1, "response fifo should be ready after reset")
    _expect(sim, "src_valid_o", 0x0, "response fifo source output should be idle after reset")


def _check_axi_cdc_write_transfer(sim: Simulator, engine: str) -> None:
    _release_axi_cdc_reset(sim, engine)

    step_drive(sim, engine, "src_aw_id", 0x2)
    step_drive(sim, engine, "src_aw_addr", 0x44)
    step_drive(sim, engine, "src_aw_prot", 0x3)
    step_drive(sim, engine, "src_aw_len", 0)
    step_drive(sim, engine, "src_aw_valid", 1)
    step_drive(sim, engine, "src_w_data", 0xCAFEBABE)
    step_drive(sim, engine, "src_w_strb", 0xA)
    step_drive(sim, engine, "src_w_last", 1)
    step_drive(sim, engine, "src_w_valid", 1)
    _settle_axi_cdc_drives(sim, engine)
    _expect(sim, "src_aw_ready", 0x1, "source AW should be ready before the first transfer")
    _expect(sim, "src_w_ready", 0x1, "source W should be ready before the first transfer")

    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "source write capture edge not observed")
    step_drive(sim, engine, "src_aw_valid", 0)
    step_drive(sim, engine, "src_w_valid", 0)
    _settle_axi_cdc_drives(sim, engine)

    _run_until_condition(
        sim,
        sim.time + 140,
        lambda s: _read_int(s, "dst_aw_valid") == 1 and _read_int(s, "dst_w_valid") == 1,
        "write request never appeared in the destination clock domain",
    )
    _expect(sim, "dst_aw_id", 0x2, "destination AW ID mismatch")
    _expect(sim, "dst_aw_addr", 0x44, "destination AW address mismatch")
    _expect(sim, "dst_aw_prot", 0x3, "destination AW protection mismatch")
    _expect(sim, "dst_aw_len", 0x0, "destination AW length mismatch")
    _expect(sim, "dst_w_data", 0xCAFEBABE, "destination W data mismatch")
    _expect(sim, "dst_w_strb", 0xA, "destination W strobe mismatch")
    _expect(sim, "dst_w_last", 0x1, "destination W last mismatch")

    step_drive(sim, engine, "dst_aw_ready", 1)
    step_drive(sim, engine, "dst_w_ready", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 50, "destination write consume edge not observed")
    step_drive(sim, engine, "dst_aw_ready", 0)
    step_drive(sim, engine, "dst_w_ready", 0)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + 80,
        lambda s: _read_int(s, "dst_aw_valid") == 0 and _read_int(s, "dst_w_valid") == 0,
        "destination write request never drained",
    )

    _expect(sim, "dst_b_ready", 0x1, "destination B channel should be ready for a response")
    step_drive(sim, engine, "dst_b_id", 0x2)
    step_drive(sim, engine, "dst_b_resp", 0x1)
    step_drive(sim, engine, "dst_b_valid", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 50, "destination write-response capture edge not observed")
    step_drive(sim, engine, "dst_b_valid", 0)
    _settle_axi_cdc_drives(sim, engine)

    _run_until_condition(
        sim,
        sim.time + 140,
        lambda s: _read_int(s, "src_b_valid") == 1,
        "write response never returned to the source clock domain",
    )
    _expect(sim, "src_b_id", 0x2, "source B ID mismatch")
    _expect(sim, "src_b_resp", 0x1, "source B response mismatch")

    step_drive(sim, engine, "src_b_ready", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "source write-response consume edge not observed")
    _run_until_condition(
        sim,
        sim.time + 80,
        lambda s: _read_int(s, "src_b_valid") == 0,
        "write response never cleared from the source clock domain",
    )


def _check_axi_cdc_read_transfer(sim: Simulator, engine: str) -> None:
    _release_axi_cdc_reset(sim, engine)

    step_drive(sim, engine, "src_ar_id", 0x1)
    step_drive(sim, engine, "src_ar_addr", 0x88)
    step_drive(sim, engine, "src_ar_prot", 0x5)
    step_drive(sim, engine, "src_ar_len", 0)
    step_drive(sim, engine, "src_ar_valid", 1)
    _settle_axi_cdc_drives(sim, engine)
    _expect(sim, "src_ar_ready", 0x1, "source AR should be ready before the first transfer")

    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "source read capture edge not observed")
    step_drive(sim, engine, "src_ar_valid", 0)
    _settle_axi_cdc_drives(sim, engine)

    _run_until_condition(
        sim,
        sim.time + 140,
        lambda s: _read_int(s, "dst_ar_valid") == 1,
        "read request never appeared in the destination clock domain",
    )
    _expect(sim, "dst_ar_id", 0x1, "destination AR ID mismatch")
    _expect(sim, "dst_ar_addr", 0x88, "destination AR address mismatch")
    _expect(sim, "dst_ar_prot", 0x5, "destination AR protection mismatch")
    _expect(sim, "dst_ar_len", 0x0, "destination AR length mismatch")

    step_drive(sim, engine, "dst_ar_ready", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 50, "destination read consume edge not observed")
    step_drive(sim, engine, "dst_ar_ready", 0)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + 80,
        lambda s: _read_int(s, "dst_ar_valid") == 0,
        "destination read request never drained",
    )

    _expect(sim, "dst_r_ready", 0x1, "destination R channel should be ready for a response")
    step_drive(sim, engine, "dst_r_id", 0x1)
    step_drive(sim, engine, "dst_r_data", 0x12345678)
    step_drive(sim, engine, "dst_r_resp", 0x2)
    step_drive(sim, engine, "dst_r_last", 1)
    step_drive(sim, engine, "dst_r_valid", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 50, "destination read-response capture edge not observed")
    step_drive(sim, engine, "dst_r_valid", 0)
    _settle_axi_cdc_drives(sim, engine)

    _run_until_condition(
        sim,
        sim.time + 140,
        lambda s: _read_int(s, "src_r_valid") == 1,
        "read response never returned to the source clock domain",
    )
    _expect(sim, "src_r_id", 0x1, "source R ID mismatch")
    _expect(sim, "src_r_data", 0x12345678, "source R data mismatch")
    _expect(sim, "src_r_resp", 0x2, "source R response mismatch")
    _expect(sim, "src_r_last", 0x1, "source R last mismatch")

    step_drive(sim, engine, "src_r_ready", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "source read-response consume edge not observed")
    _run_until_condition(
        sim,
        sim.time + 80,
        lambda s: _read_int(s, "src_r_valid") == 0,
        "read response never cleared from the source clock domain",
    )


def _check_axi_cdc_overlap_transfer(sim: Simulator, engine: str) -> None:
    _release_axi_cdc_reset(sim, engine)

    step_drive(sim, engine, "src_aw_id", 0x2)
    step_drive(sim, engine, "src_aw_addr", 0x44)
    step_drive(sim, engine, "src_aw_prot", 0x3)
    step_drive(sim, engine, "src_aw_len", 0)
    step_drive(sim, engine, "src_aw_valid", 1)
    step_drive(sim, engine, "src_w_data", 0xCAFEBABE)
    step_drive(sim, engine, "src_w_strb", 0xA)
    step_drive(sim, engine, "src_w_last", 1)
    step_drive(sim, engine, "src_w_valid", 1)
    step_drive(sim, engine, "src_b_ready", 0)
    step_drive(sim, engine, "src_ar_id", 0x1)
    step_drive(sim, engine, "src_ar_addr", 0x88)
    step_drive(sim, engine, "src_ar_prot", 0x5)
    step_drive(sim, engine, "src_ar_len", 0)
    step_drive(sim, engine, "src_ar_valid", 1)
    step_drive(sim, engine, "src_r_ready", 0)
    _settle_axi_cdc_drives(sim, engine)
    _expect(sim, "src_aw_ready", 0x1, "overlap write-address channel should be ready before capture")
    _expect(sim, "src_w_ready", 0x1, "overlap write-data channel should be ready before capture")
    _expect(sim, "src_ar_ready", 0x1, "overlap read-address channel should be ready before capture")

    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "source overlap capture edge not observed")
    step_drive(sim, engine, "src_aw_valid", 0)
    step_drive(sim, engine, "src_w_valid", 0)
    step_drive(sim, engine, "src_ar_valid", 0)
    _settle_axi_cdc_drives(sim, engine)

    _run_until_condition(
        sim,
        sim.time + 160,
        lambda s: (
            _read_int(s, "dst_aw_valid") == 1 and _read_int(s, "dst_w_valid") == 1 and _read_int(s, "dst_ar_valid") == 1
        ),
        "overlap requests never appeared together in the destination clock domain",
    )
    _expect(sim, "dst_aw_id", 0x2, "overlap destination AW ID mismatch")
    _expect(sim, "dst_aw_addr", 0x44, "overlap destination AW address mismatch")
    _expect(sim, "dst_aw_prot", 0x3, "overlap destination AW protection mismatch")
    _expect(sim, "dst_aw_len", 0x0, "overlap destination AW length mismatch")
    _expect(sim, "dst_w_data", 0xCAFEBABE, "overlap destination W data mismatch")
    _expect(sim, "dst_w_strb", 0xA, "overlap destination W strobe mismatch")
    _expect(sim, "dst_w_last", 0x1, "overlap destination W last mismatch")
    _expect(sim, "dst_ar_id", 0x1, "overlap destination AR ID mismatch")
    _expect(sim, "dst_ar_addr", 0x88, "overlap destination AR address mismatch")
    _expect(sim, "dst_ar_prot", 0x5, "overlap destination AR protection mismatch")
    _expect(sim, "dst_ar_len", 0x0, "overlap destination AR length mismatch")

    step_drive(sim, engine, "dst_aw_ready", 1)
    step_drive(sim, engine, "dst_w_ready", 1)
    step_drive(sim, engine, "dst_ar_ready", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 60, "destination overlap consume edge not observed")
    step_drive(sim, engine, "dst_aw_ready", 0)
    step_drive(sim, engine, "dst_w_ready", 0)
    step_drive(sim, engine, "dst_ar_ready", 0)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + 100,
        lambda s: (
            _read_int(s, "dst_aw_valid") == 0 and _read_int(s, "dst_w_valid") == 0 and _read_int(s, "dst_ar_valid") == 0
        ),
        "overlap requests never drained from the destination clock domain",
    )

    _expect(sim, "dst_b_ready", 0x1, "overlap destination B channel should be ready for a response")
    _expect(sim, "dst_r_ready", 0x1, "overlap destination R channel should be ready for a response")
    step_drive(sim, engine, "dst_b_id", 0x2)
    step_drive(sim, engine, "dst_b_resp", 0x1)
    step_drive(sim, engine, "dst_b_valid", 1)
    step_drive(sim, engine, "dst_r_id", 0x1)
    step_drive(sim, engine, "dst_r_data", 0x12345678)
    step_drive(sim, engine, "dst_r_resp", 0x2)
    step_drive(sim, engine, "dst_r_last", 1)
    step_drive(sim, engine, "dst_r_valid", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 60, "destination overlap response capture edge not observed")
    step_drive(sim, engine, "dst_b_valid", 0)
    step_drive(sim, engine, "dst_r_valid", 0)
    _settle_axi_cdc_drives(sim, engine)

    _run_until_condition(
        sim,
        sim.time + 160,
        lambda s: _read_int(s, "src_b_valid") == 1 and _read_int(s, "src_r_valid") == 1,
        "overlap responses never appeared together in the source clock domain",
    )
    _expect(sim, "src_b_id", 0x2, "overlap source B ID mismatch")
    _expect(sim, "src_b_resp", 0x1, "overlap source B response mismatch")
    _expect(sim, "src_r_id", 0x1, "overlap source R ID mismatch")
    _expect(sim, "src_r_data", 0x12345678, "overlap source R data mismatch")
    _expect(sim, "src_r_resp", 0x2, "overlap source R response mismatch")
    _expect(sim, "src_r_last", 0x1, "overlap source R last mismatch")

    step_drive(sim, engine, "src_b_ready", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "source overlap write-response consume edge not observed")
    _settle_axi_cdc_drives(sim, engine)
    _expect(sim, "src_b_valid", 0x0, "overlap source B response should clear after ready")
    _expect(sim, "src_r_valid", 0x1, "overlap source R response should remain pending after B drains")

    step_drive(sim, engine, "src_b_ready", 0)
    step_drive(sim, engine, "src_r_ready", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "source overlap read-response consume edge not observed")
    _run_until_condition(
        sim,
        sim.time + 80,
        lambda s: _read_int(s, "src_r_valid") == 0,
        "overlap source R response never cleared after ready",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_cdc_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_cdc_design(tmp_path)

    sim = _make_axi_cdc_step_sim(design, engine, max_time=320)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_cdc_write_{engine}"):
        _check_axi_cdc_write_transfer(sim, engine)

    sim = _make_axi_cdc_step_sim(design, engine, max_time=320)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_cdc_read_{engine}"):
        _check_axi_cdc_read_transfer(sim, engine)


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_cdc_overlap_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_cdc_design(tmp_path)
    sim = _make_axi_cdc_step_sim(design, engine, max_time=420)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_cdc_overlap_{engine}"):
        _check_axi_cdc_overlap_transfer(sim, engine)


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_cdc_req_fifo_cross_engine(tmp_path, engine):
    design = _parse_axi_cdc_design(tmp_path)
    sim = _make_axi_cdc_req_fifo_step_sim(design, engine, max_time=420)
    _release_axi_cdc_req_fifo_reset(sim, engine)

    step_drive(sim, engine, "dst_ready_i", 0)
    step_drive(sim, engine, "src_push_i", 1)
    step_drive(sim, engine, "src_aw_id", 0x1)
    step_drive(sim, engine, "src_aw_addr", 0x00000100)
    step_drive(sim, engine, "src_aw_prot", 0x2)
    step_drive(sim, engine, "src_aw_len", 0x04)
    step_drive(sim, engine, "src_aw_valid", 1)
    step_drive(sim, engine, "src_w_data", 0x11223344)
    step_drive(sim, engine, "src_w_strb", 0xF)
    step_drive(sim, engine, "src_w_last", 1)
    step_drive(sim, engine, "src_w_valid", 1)
    step_drive(sim, engine, "src_b_ready", 1)
    step_drive(sim, engine, "src_ar_id", 0x2)
    step_drive(sim, engine, "src_ar_addr", 0x00000200)
    step_drive(sim, engine, "src_ar_prot", 0x1)
    step_drive(sim, engine, "src_ar_len", 0x03)
    step_drive(sim, engine, "src_ar_valid", 1)
    step_drive(sim, engine, "src_r_ready", 0)
    _settle_axi_cdc_drives(sim, engine)
    _expect(sim, "src_ready_o", 0x1, "request fifo should accept the first aggregate bundle")
    _run_until_rising_edge(sim, "src_clk_i", sim.time + 40, "first request fifo source edge not observed")
    step_drive(sim, engine, "src_push_i", 0)
    _settle_axi_cdc_drives(sim, engine)

    step_drive(sim, engine, "src_push_i", 1)
    step_drive(sim, engine, "src_aw_id", 0x3)
    step_drive(sim, engine, "src_aw_addr", 0x00000340)
    step_drive(sim, engine, "src_aw_prot", 0x5)
    step_drive(sim, engine, "src_aw_len", 0x01)
    step_drive(sim, engine, "src_aw_valid", 0)
    step_drive(sim, engine, "src_w_data", 0x55667788)
    step_drive(sim, engine, "src_w_strb", 0x3)
    step_drive(sim, engine, "src_w_last", 0)
    step_drive(sim, engine, "src_w_valid", 1)
    step_drive(sim, engine, "src_b_ready", 0)
    step_drive(sim, engine, "src_ar_id", 0x1)
    step_drive(sim, engine, "src_ar_addr", 0x00000480)
    step_drive(sim, engine, "src_ar_prot", 0x7)
    step_drive(sim, engine, "src_ar_len", 0x00)
    step_drive(sim, engine, "src_ar_valid", 1)
    step_drive(sim, engine, "src_r_ready", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + 80,
        lambda s: _read_int(s, "src_ready_o") == 1,
        "request fifo never reopened the second source slot",
    )
    _run_until_rising_edge(sim, "src_clk_i", sim.time + 40, "second request fifo source edge not observed")

    step_drive(sim, engine, "src_push_i", 0)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + 120,
        lambda s: _read_int(s, "src_ready_o") == 0,
        "request fifo never backpressured after two queued aggregate bundles",
    )
    _run_until_condition(
        sim,
        sim.time + 120,
        lambda s: _read_int(s, "dst_valid_o") == 1,
        "request fifo never produced the first aggregate bundle in the destination domain",
    )
    _expect(sim, "dst_aw_id", 0x1, "first destination AW ID mismatch")
    _expect(sim, "dst_aw_addr", 0x00000100, "first destination AW address mismatch")
    _expect(sim, "dst_aw_prot", 0x2, "first destination AW prot mismatch")
    _expect(sim, "dst_aw_len", 0x04, "first destination AW len mismatch")
    _expect(sim, "dst_aw_valid", 1, "first destination AW valid mismatch")
    _expect(sim, "dst_w_data", 0x11223344, "first destination W data mismatch")
    _expect(sim, "dst_w_strb", 0xF, "first destination W strb mismatch")
    _expect(sim, "dst_w_last", 1, "first destination W last mismatch")
    _expect(sim, "dst_w_valid", 1, "first destination W valid mismatch")
    _expect(sim, "dst_b_ready", 1, "first destination B ready mismatch")
    _expect(sim, "dst_ar_id", 0x2, "first destination AR ID mismatch")
    _expect(sim, "dst_ar_addr", 0x00000200, "first destination AR address mismatch")
    _expect(sim, "dst_ar_prot", 0x1, "first destination AR prot mismatch")
    _expect(sim, "dst_ar_len", 0x03, "first destination AR len mismatch")
    _expect(sim, "dst_ar_valid", 1, "first destination AR valid mismatch")
    _expect(sim, "dst_r_ready", 0, "first destination R ready mismatch")

    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 60, "first request fifo destination consume edge not observed")
    _run_until_condition(
        sim,
        sim.time + 120,
        lambda s: _read_int(s, "dst_aw_addr") == 0x00000340 and _read_int(s, "dst_valid_o") == 1,
        "request fifo never advanced to the second aggregate bundle",
    )
    _expect(sim, "dst_aw_id", 0x3, "second destination AW ID mismatch")
    _expect(sim, "dst_aw_addr", 0x00000340, "second destination AW address mismatch")
    _expect(sim, "dst_aw_prot", 0x5, "second destination AW prot mismatch")
    _expect(sim, "dst_aw_len", 0x01, "second destination AW len mismatch")
    _expect(sim, "dst_aw_valid", 0, "second destination AW valid mismatch")
    _expect(sim, "dst_w_data", 0x55667788, "second destination W data mismatch")
    _expect(sim, "dst_w_strb", 0x3, "second destination W strb mismatch")
    _expect(sim, "dst_w_last", 0, "second destination W last mismatch")
    _expect(sim, "dst_w_valid", 1, "second destination W valid mismatch")
    _expect(sim, "dst_b_ready", 0, "second destination B ready mismatch")
    _expect(sim, "dst_ar_id", 0x1, "second destination AR ID mismatch")
    _expect(sim, "dst_ar_addr", 0x00000480, "second destination AR address mismatch")
    _expect(sim, "dst_ar_prot", 0x7, "second destination AR prot mismatch")
    _expect(sim, "dst_ar_len", 0x00, "second destination AR len mismatch")
    _expect(sim, "dst_ar_valid", 1, "second destination AR valid mismatch")
    _expect(sim, "dst_r_ready", 1, "second destination R ready mismatch")

    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 60, "second request fifo destination consume edge not observed")
    _run_until_condition(
        sim,
        sim.time + 120,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "request fifo destination valid never cleared after draining both aggregate bundles",
    )
    _run_until_condition(
        sim,
        sim.time + 120,
        lambda s: _read_int(s, "src_ready_o") == 1,
        "request fifo source ready never recovered after draining both aggregate bundles",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_cdc_resp_fifo_cross_engine(tmp_path, engine):
    design = _parse_axi_cdc_design(tmp_path)
    sim = _make_axi_cdc_resp_fifo_step_sim(design, engine, max_time=420)
    _release_axi_cdc_resp_fifo_reset(sim, engine)

    step_drive(sim, engine, "src_ready_i", 0)
    step_drive(sim, engine, "dst_push_i", 1)
    step_drive(sim, engine, "dst_aw_ready", 1)
    step_drive(sim, engine, "dst_w_ready", 0)
    step_drive(sim, engine, "dst_b_id", 0x1)
    step_drive(sim, engine, "dst_b_resp", 0x2)
    step_drive(sim, engine, "dst_b_valid", 1)
    step_drive(sim, engine, "dst_ar_ready", 1)
    step_drive(sim, engine, "dst_r_id", 0x2)
    step_drive(sim, engine, "dst_r_data", 0x12345678)
    step_drive(sim, engine, "dst_r_resp", 0x1)
    step_drive(sim, engine, "dst_r_last", 1)
    step_drive(sim, engine, "dst_r_valid", 1)
    _settle_axi_cdc_drives(sim, engine)
    _expect(sim, "dst_ready_o", 0x1, "response fifo should accept the first aggregate bundle")
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 40, "first response fifo destination edge not observed")
    step_drive(sim, engine, "dst_push_i", 0)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + 120,
        lambda s: _read_int(s, "src_valid_o") == 1,
        "response fifo never produced the first aggregate bundle in the source domain",
    )
    _expect(sim, "src_aw_ready", 1, "first source AW ready mismatch")
    _expect(sim, "src_w_ready", 0, "first source W ready mismatch")
    _expect(sim, "src_b_id", 0x1, "first source B ID mismatch")
    _expect(sim, "src_b_resp", 0x2, "first source B response mismatch")
    _expect(sim, "src_b_valid", 1, "first source B valid mismatch")
    _expect(sim, "src_ar_ready", 1, "first source AR ready mismatch")
    _expect(sim, "src_r_id", 0x2, "first source R ID mismatch")
    _expect(sim, "src_r_data", 0x12345678, "first source R data mismatch")
    _expect(sim, "src_r_resp", 0x1, "first source R response mismatch")
    _expect(sim, "src_r_last", 1, "first source R last mismatch")
    _expect(sim, "src_r_valid", 1, "first source R valid mismatch")

    step_drive(sim, engine, "src_ready_i", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "first response fifo source consume edge not observed")
    _run_until_condition(
        sim,
        sim.time + 120,
        lambda s: _read_int(s, "src_valid_o") == 0,
        "response fifo source valid never cleared after draining the first aggregate bundle",
    )
    _run_until_condition(
        sim,
        sim.time + 180,
        lambda s: _read_int(s, "dst_ready_o") == 1,
        "response fifo destination ready never recovered after draining the first aggregate bundle",
    )

    step_drive(sim, engine, "src_ready_i", 0)
    step_drive(sim, engine, "dst_push_i", 1)
    step_drive(sim, engine, "dst_aw_ready", 0)
    step_drive(sim, engine, "dst_w_ready", 1)
    step_drive(sim, engine, "dst_b_id", 0x3)
    step_drive(sim, engine, "dst_b_resp", 0x0)
    step_drive(sim, engine, "dst_b_valid", 0)
    step_drive(sim, engine, "dst_ar_ready", 0)
    step_drive(sim, engine, "dst_r_id", 0x1)
    step_drive(sim, engine, "dst_r_data", 0xAABBCCDD)
    step_drive(sim, engine, "dst_r_resp", 0x2)
    step_drive(sim, engine, "dst_r_last", 0)
    step_drive(sim, engine, "dst_r_valid", 1)
    _settle_axi_cdc_drives(sim, engine)
    _expect(sim, "dst_ready_o", 0x1, "response fifo should accept the second aggregate bundle")
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 40, "second response fifo destination edge not observed")
    step_drive(sim, engine, "dst_push_i", 0)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + 120,
        lambda s: _read_int(s, "src_aw_ready") == 0 and _read_int(s, "src_valid_o") == 1,
        "response fifo never delivered the second aggregate bundle",
    )
    _expect(sim, "src_aw_ready", 0, "second source AW ready mismatch")
    _expect(sim, "src_w_ready", 1, "second source W ready mismatch")
    _expect(sim, "src_b_id", 0x3, "second source B ID mismatch")
    _expect(sim, "src_b_resp", 0x0, "second source B response mismatch")
    _expect(sim, "src_b_valid", 0, "second source B valid mismatch")
    _expect(sim, "src_ar_ready", 0, "second source AR ready mismatch")
    _expect(sim, "src_r_id", 0x1, "second source R ID mismatch")
    _expect(sim, "src_r_data", 0xAABBCCDD, "second source R data mismatch")
    _expect(sim, "src_r_resp", 0x2, "second source R response mismatch")
    _expect(sim, "src_r_last", 0, "second source R last mismatch")
    _expect(sim, "src_r_valid", 1, "second source R valid mismatch")

    step_drive(sim, engine, "src_ready_i", 1)
    _settle_axi_cdc_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "second response fifo source consume edge not observed")
    _run_until_condition(
        sim,
        sim.time + 120,
        lambda s: _read_int(s, "src_valid_o") == 0,
        "response fifo source valid never cleared after draining both aggregate bundles",
    )
    _run_until_condition(
        sim,
        sim.time + 120,
        lambda s: _read_int(s, "dst_ready_o") == 1,
        "response fifo destination ready never recovered after draining both aggregate bundles",
    )


def _drive_axi_xbar_idle(sim: Simulator, engine: str) -> None:
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


def _exercise_axi_xbar_top(design, top_name: str, engine: str, vcd_dir, stem_prefix: str) -> None:
    sim = _make_step_sim(design, top_name, engine, max_time=320)
    _drive_axi_xbar_idle(sim, engine)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_route_{engine}"):
        step_drive(sim, engine, "slv0_aw_id", 0x1)
        step_drive(sim, engine, "slv0_aw_addr", 0x000)
        step_drive(sim, engine, "slv0_aw_len", 0)
        step_drive(sim, engine, "slv0_aw_valid", 1)
        step_drive(sim, engine, "slv0_w_data", 0xCAFEBABE)
        step_drive(sim, engine, "slv0_w_strb", 0xF)
        step_drive(sim, engine, "slv0_w_last", 1)
        step_drive(sim, engine, "slv0_w_valid", 1)
        step_drive(sim, engine, "slv0_b_ready", 0)
        step_drive(sim, engine, "slv1_aw_id", 0x2)
        step_drive(sim, engine, "slv1_aw_addr", 0x100)
        step_drive(sim, engine, "slv1_aw_len", 0)
        step_drive(sim, engine, "slv1_aw_valid", 1)
        step_drive(sim, engine, "slv1_w_data", 0x10203040)
        step_drive(sim, engine, "slv1_w_strb", 0xF)
        step_drive(sim, engine, "slv1_w_last", 1)
        step_drive(sim, engine, "slv1_w_valid", 1)
        step_drive(sim, engine, "slv1_b_ready", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_aw_ready", 0x1, "port0 target0 write should be accepted")
        _expect(sim, "slv1_aw_ready", 0x1, "port1 target1 write should be accepted")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "parallel write capture edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv0_aw_valid", 0)
        step_drive(sim, engine, "slv0_w_valid", 0)
        step_drive(sim, engine, "slv1_aw_valid", 0)
        step_drive(sim, engine, "slv1_w_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_b_valid", 0x1, "port0 write response should be pending")
        _expect(sim, "slv0_b_id", 0x1, "port0 write response ID mismatch")
        _expect(sim, "slv0_b_resp", 0x0, "port0 write response code mismatch")
        _expect(sim, "slv1_b_valid", 0x1, "port1 write response should be pending")
        _expect(sim, "slv1_b_id", 0x2, "port1 write response ID mismatch")
        _expect(sim, "slv1_b_resp", 0x0, "port1 write response code mismatch")
        _expect(sim, "target0_data", 0xCAFEBABE, "target0 write data mismatch")
        _expect(sim, "target1_data", 0x10203040, "target1 write data mismatch")
        _expect(sim, "mst0_last_aw_id", 0x1, "target0 widened AW ID mismatch")
        _expect(sim, "mst1_last_aw_id", 0x6, "target1 widened AW ID mismatch")

        step_drive(sim, engine, "slv0_b_ready", 1)
        step_drive(sim, engine, "slv1_b_ready", 1)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "parallel write release edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv0_ar_id", 0x0)
        step_drive(sim, engine, "slv0_ar_addr", 0x100)
        step_drive(sim, engine, "slv0_ar_len", 0)
        step_drive(sim, engine, "slv0_ar_valid", 1)
        step_drive(sim, engine, "slv0_r_ready", 0)
        step_drive(sim, engine, "slv1_ar_id", 0x3)
        step_drive(sim, engine, "slv1_ar_addr", 0x000)
        step_drive(sim, engine, "slv1_ar_len", 0)
        step_drive(sim, engine, "slv1_ar_valid", 1)
        step_drive(sim, engine, "slv1_r_ready", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_ar_ready", 0x1, "port0 target1 read should be accepted")
        _expect(sim, "slv1_ar_ready", 0x1, "port1 target0 read should be accepted")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "parallel read capture edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv0_ar_valid", 0)
        step_drive(sim, engine, "slv1_ar_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_r_valid", 0x1, "port0 read response should be pending")
        _expect(sim, "slv0_r_id", 0x0, "port0 read response ID mismatch")
        _expect(sim, "slv0_r_data", 0x10203040, "port0 read data mismatch")
        _expect(sim, "slv0_r_resp", 0x0, "port0 read response code mismatch")
        _expect(sim, "slv0_r_last", 0x1, "port0 read last mismatch")
        _expect(sim, "slv1_r_valid", 0x1, "port1 read response should be pending")
        _expect(sim, "slv1_r_id", 0x3, "port1 read response ID mismatch")
        _expect(sim, "slv1_r_data", 0xCAFEBABE, "port1 read data mismatch")
        _expect(sim, "slv1_r_resp", 0x0, "port1 read response code mismatch")
        _expect(sim, "slv1_r_last", 0x1, "port1 read last mismatch")
        _expect(sim, "mst0_last_ar_id", 0x7, "target0 widened AR ID mismatch")
        _expect(sim, "mst1_last_ar_id", 0x0, "target1 widened AR ID mismatch")

        step_drive(sim, engine, "slv0_r_ready", 1)
        step_drive(sim, engine, "slv1_r_ready", 1)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "parallel read release edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv0_aw_id", 0x2)
        step_drive(sim, engine, "slv0_aw_addr", 0x200)
        step_drive(sim, engine, "slv0_aw_len", 0)
        step_drive(sim, engine, "slv0_aw_valid", 1)
        step_drive(sim, engine, "slv0_w_data", 0x55AA55AA)
        step_drive(sim, engine, "slv0_w_strb", 0xF)
        step_drive(sim, engine, "slv0_w_last", 1)
        step_drive(sim, engine, "slv0_w_valid", 1)
        step_drive(sim, engine, "slv0_b_ready", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_aw_ready", 0x1, "decode-error write should be accepted")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "decode-error write capture edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv0_aw_valid", 0)
        step_drive(sim, engine, "slv0_w_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_b_valid", 0x1, "decode-error write response should be pending")
        _expect(sim, "slv0_b_id", 0x2, "decode-error write response ID mismatch")
        _expect(sim, "slv0_b_resp", 0x3, "decode-error write response code mismatch")
        _expect(sim, "target0_data", 0xCAFEBABE, "target0 should remain unchanged on decode-error write")
        _expect(sim, "target1_data", 0x10203040, "target1 should remain unchanged on decode-error write")

        step_drive(sim, engine, "slv0_b_ready", 1)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "decode-error write release edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv1_ar_id", 0x1)
        step_drive(sim, engine, "slv1_ar_addr", 0x200)
        step_drive(sim, engine, "slv1_ar_len", 0)
        step_drive(sim, engine, "slv1_ar_valid", 1)
        step_drive(sim, engine, "slv1_r_ready", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv1_ar_ready", 0x1, "decode-error read should be accepted")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "decode-error read capture edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv1_ar_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv1_r_valid", 0x1, "decode-error read response should be pending")
        _expect(sim, "slv1_r_id", 0x1, "decode-error read response ID mismatch")
        _expect(sim, "slv1_r_data", 0xBADCAB1E, "decode-error read data mismatch")
        _expect(sim, "slv1_r_resp", 0x3, "decode-error read response code mismatch")
        _expect(sim, "slv1_r_last", 0x1, "decode-error read last mismatch")

        step_drive(sim, engine, "slv1_r_ready", 1)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "decode-error read release edge not observed")
        _settle_drives(sim, engine)

    sim = _make_step_sim(design, top_name, engine, max_time=320)
    _drive_axi_xbar_idle(sim, engine)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_arb_{engine}"):
        step_drive(sim, engine, "slv0_aw_id", 0x1)
        step_drive(sim, engine, "slv0_aw_addr", 0x000)
        step_drive(sim, engine, "slv0_aw_len", 0)
        step_drive(sim, engine, "slv0_aw_valid", 1)
        step_drive(sim, engine, "slv0_w_data", 0x12345678)
        step_drive(sim, engine, "slv0_w_strb", 0xF)
        step_drive(sim, engine, "slv0_w_last", 1)
        step_drive(sim, engine, "slv0_w_valid", 1)
        step_drive(sim, engine, "slv0_b_ready", 0)
        step_drive(sim, engine, "slv1_aw_id", 0x2)
        step_drive(sim, engine, "slv1_aw_addr", 0x004)
        step_drive(sim, engine, "slv1_aw_len", 0)
        step_drive(sim, engine, "slv1_aw_valid", 1)
        step_drive(sim, engine, "slv1_w_data", 0xDEADBEEF)
        step_drive(sim, engine, "slv1_w_strb", 0xF)
        step_drive(sim, engine, "slv1_w_last", 1)
        step_drive(sim, engine, "slv1_w_valid", 1)
        step_drive(sim, engine, "slv1_b_ready", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_aw_ready", 0x1, "port0 should win first target0 arbitration")
        _expect(sim, "slv1_aw_ready", 0x0, "port1 should stall behind port0 on target0")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "first arbitration capture edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv0_aw_valid", 0)
        step_drive(sim, engine, "slv0_w_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_b_valid", 0x1, "port0 first write response should be pending")
        _expect(sim, "slv1_aw_ready", 0x0, "port1 should remain stalled while target0 response is pending")
        _expect(sim, "target0_data", 0x12345678, "target0 should hold the first write before release")

        step_drive(sim, engine, "slv0_b_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "first arbitration release edge not observed")
        _settle_drives(sim, engine)

        _expect(sim, "slv0_b_valid", 0x0, "port0 first write response should clear")
        _expect(sim, "slv1_aw_ready", 0x1, "port1 should become ready after target0 release")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "second arbitration capture edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv1_aw_valid", 0)
        step_drive(sim, engine, "slv1_w_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv1_b_valid", 0x1, "port1 deferred write response should be pending")
        _expect(sim, "slv1_b_id", 0x2, "port1 deferred write response ID mismatch")
        _expect(sim, "target0_data", 0xDEADBEEF, "target0 should contain the deferred write data")
        _expect(sim, "mst0_last_aw_id", 0x6, "target0 widened AW ID should update for the deferred write")


def _exercise_axi_xbar_typed_read_arbitration(design, engine: str, vcd_dir) -> None:
    sim = _make_step_sim(design, "axi_xbar_typed_exec_tb", engine, max_time=240)
    _drive_axi_xbar_idle(sim, engine)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_xbar_typed_read_arb_{engine}"):
        step_drive(sim, engine, "slv0_ar_id", 0x0)
        step_drive(sim, engine, "slv0_ar_addr", 0x000)
        step_drive(sim, engine, "slv0_ar_len", 0)
        step_drive(sim, engine, "slv0_ar_valid", 1)
        step_drive(sim, engine, "slv0_r_ready", 0)
        step_drive(sim, engine, "slv1_ar_id", 0x3)
        step_drive(sim, engine, "slv1_ar_addr", 0x004)
        step_drive(sim, engine, "slv1_ar_len", 0)
        step_drive(sim, engine, "slv1_ar_valid", 1)
        step_drive(sim, engine, "slv1_r_ready", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_ar_ready", 0x1, "port0 should win first target0 read arbitration")
        _expect(sim, "slv1_ar_ready", 0x0, "port1 should stall behind port0 on target0 read")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "first typed read arbitration capture edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv0_ar_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_r_valid", 0x1, "port0 first read response should be pending")
        _expect(sim, "slv0_r_id", 0x0, "port0 first read response ID mismatch")
        _expect(sim, "slv0_r_data", 0x11111111, "port0 first read data mismatch")
        _expect(sim, "slv0_r_resp", 0x0, "port0 first read response code mismatch")
        _expect(sim, "slv0_r_last", 0x1, "port0 first read last mismatch")
        _expect(sim, "slv1_ar_ready", 0x0, "port1 should remain stalled while target0 read response is pending")
        _expect(sim, "mst0_last_ar_id", 0x0, "target0 widened AR ID should capture the first reader")

        step_drive(sim, engine, "slv0_r_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "first typed read arbitration release edge not observed")
        _settle_drives(sim, engine)

        _expect(sim, "slv0_r_valid", 0x0, "port0 first read response should clear after release")
        _expect(sim, "slv1_ar_ready", 0x1, "port1 should become ready after target0 read release")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "second typed read arbitration capture edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv1_ar_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv1_r_valid", 0x1, "port1 deferred read response should be pending")
        _expect(sim, "slv1_r_id", 0x3, "port1 deferred read response ID mismatch")
        _expect(sim, "slv1_r_data", 0x11111111, "port1 deferred read data mismatch")
        _expect(sim, "slv1_r_resp", 0x0, "port1 deferred read response code mismatch")
        _expect(sim, "slv1_r_last", 0x1, "port1 deferred read last mismatch")
        _expect(sim, "mst0_last_ar_id", 0x7, "target0 widened AR ID should update for the deferred reader")
        _expect(sim, "target0_data", 0x11111111, "target0 data should remain unchanged during read arbitration")
        _expect(sim, "target1_data", 0x22222222, "target1 data should remain untouched during target0 read arbitration")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_xbar_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_xbar_design(tmp_path)
    _exercise_axi_xbar_top(design, "axi_xbar_exec_tb", engine, vcd_dir, "axi_xbar")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_xbar_typed_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_xbar_design(tmp_path)
    _exercise_axi_xbar_top(design, "axi_xbar_typed_exec_tb", engine, vcd_dir, "axi_xbar_typed")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_xbar_typed_read_arbitration_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_xbar_design(tmp_path)
    _exercise_axi_xbar_typed_read_arbitration(design, engine, vcd_dir)


def _exercise_axi_lite_xbar_top(design, top_name: str, engine: str, vcd_dir, stem_prefix: str) -> None:
    target0_init = 0x11111111
    target1_init = 0x22222222
    target0_write = 0x11223344
    target1_write = 0xAABBCCDD
    arbitration_second = 0xDEADBEEF

    sim = _make_step_sim(design, top_name, engine, max_time=260)
    port0 = _make_axi_lite_master(sim, "slv0", timeout_cycles=10)
    port1 = _make_axi_lite_master(sim, "slv1", timeout_cycles=10)
    _settle_drives(sim, engine)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_route_{engine}"):
        assert port0.read(0x000) == target0_init
        assert port1.read(0x100) == target1_init
        assert port0.write(0x100, target1_write) == 0x0
        assert port1.read(0x100) == target1_write
        assert port1.write(0x000, target0_write) == 0x0
        assert port0.read(0x000) == target0_write
        assert port0.write(0x200, 0x55AA55AA, expected_resp=0x3) == 0x3
        with pytest.raises(AXILiteResponseError, match="expected 0x0, got 0x3"):
            port1.read(0x200)

    sim = _make_step_sim(design, top_name, engine, max_time=260)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_arb_{engine}"):
        step_drive(sim, engine, "slv0_aw_addr", 0x000)
        step_drive(sim, engine, "slv0_aw_valid", 1)
        step_drive(sim, engine, "slv0_w_data", 0x12345678)
        step_drive(sim, engine, "slv0_w_strb", 0xF)
        step_drive(sim, engine, "slv0_w_valid", 1)
        step_drive(sim, engine, "slv0_b_ready", 0)
        step_drive(sim, engine, "slv1_aw_addr", 0x004)
        step_drive(sim, engine, "slv1_aw_valid", 1)
        step_drive(sim, engine, "slv1_w_data", arbitration_second)
        step_drive(sim, engine, "slv1_w_strb", 0xF)
        step_drive(sim, engine, "slv1_w_valid", 1)
        step_drive(sim, engine, "slv1_b_ready", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_aw_ready", 0x1, "port0 should win first same-target arbitration")
        _expect(sim, "slv0_w_ready", 0x1, "port0 write data should be accepted first")
        _expect(sim, "slv1_aw_ready", 0x0, "port1 should stall behind port0 for same target")
        _expect(sim, "slv1_w_ready", 0x0, "port1 write data should stall behind port0")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "first arbitration capture edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv0_aw_valid", 0)
        step_drive(sim, engine, "slv0_w_valid", 0)
        _settle_drives(sim, engine)
        _expect(sim, "slv0_b_valid", 0x1, "port0 should hold the first write response")
        _expect(sim, "slv1_aw_ready", 0x0, "port1 should remain stalled while target0 response is pending")

        step_drive(sim, engine, "slv0_b_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "first arbitration response release edge not observed")
        _settle_drives(sim, engine)

        _expect(sim, "slv0_b_valid", 0x0, "port0 response should clear after release")
        _expect(sim, "slv1_aw_ready", 0x1, "port1 should become ready after port0 releases target0")
        _expect(sim, "slv1_w_ready", 0x1, "port1 write data should become ready after release")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "second arbitration capture edge not observed")
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv1_aw_valid", 0)
        step_drive(sim, engine, "slv1_w_valid", 0)
        _settle_drives(sim, engine)
        _expect(sim, "slv1_b_valid", 0x1, "port1 should receive the deferred write response")
        _expect(sim, "target0_data", arbitration_second, "target0 should contain the second write after arbitration")
        _expect(sim, "target1_data", target1_init, "target1 should remain unchanged during target0 arbitration")


def _exercise_axi_lite_xbar_typed_read_arbitration(design, engine: str, vcd_dir) -> None:
    sim = _make_step_sim(design, "axi_lite_xbar_typed_exec_tb", engine, max_time=220)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_xbar_typed_read_arb_{engine}"):
        step_drive(sim, engine, "slv0_ar_addr", 0x000)
        step_drive(sim, engine, "slv0_ar_valid", 1)
        step_drive(sim, engine, "slv0_r_ready", 0)
        step_drive(sim, engine, "slv1_ar_addr", 0x004)
        step_drive(sim, engine, "slv1_ar_valid", 1)
        step_drive(sim, engine, "slv1_r_ready", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_ar_ready", 0x1, "port0 should win first target0 read arbitration")
        _expect(sim, "slv1_ar_ready", 0x0, "port1 should stall behind port0 on target0 read")
        _run_until_rising_edge(
            sim, "clk", sim.time + 20, "first AXI-Lite typed read arbitration capture edge not observed"
        )
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv0_ar_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv0_r_valid", 0x1, "port0 first read response should be pending")
        _expect(sim, "slv0_r_data", 0x11111111, "port0 first read data mismatch")
        _expect(sim, "slv0_r_resp", 0x0, "port0 first read response code mismatch")
        _expect(sim, "slv1_ar_ready", 0x0, "port1 should remain stalled while target0 read response is pending")

        step_drive(sim, engine, "slv0_r_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(
            sim, "clk", sim.time + 20, "first AXI-Lite typed read arbitration release edge not observed"
        )
        _settle_drives(sim, engine)

        _expect(sim, "slv0_r_valid", 0x0, "port0 first read response should clear after release")
        _expect(sim, "slv1_ar_ready", 0x1, "port1 should become ready after target0 read release")
        _run_until_rising_edge(
            sim, "clk", sim.time + 20, "second AXI-Lite typed read arbitration capture edge not observed"
        )
        _settle_drives(sim, engine)

        step_drive(sim, engine, "slv1_ar_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv1_r_valid", 0x1, "port1 deferred read response should be pending")
        _expect(sim, "slv1_r_data", 0x11111111, "port1 deferred read data mismatch")
        _expect(sim, "slv1_r_resp", 0x0, "port1 deferred read response code mismatch")
        _expect(sim, "target0_data", 0x11111111, "target0 data should remain unchanged during read arbitration")
        _expect(sim, "target1_data", 0x22222222, "target1 data should remain untouched during target0 read arbitration")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_xbar_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_xbar_design(tmp_path)
    _exercise_axi_lite_xbar_top(design, "axi_lite_xbar_exec_tb", engine, vcd_dir, "axi_lite_xbar")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_xbar_typed_addr_map_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_xbar_design(tmp_path)
    _exercise_axi_lite_xbar_top(
        design,
        "axi_lite_xbar_typed_exec_tb",
        engine,
        vcd_dir,
        "axi_lite_xbar_typed",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_xbar_typed_read_arbitration_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_xbar_design(tmp_path)
    _exercise_axi_lite_xbar_typed_read_arbitration(design, engine, vcd_dir)


def _exercise_axi_lite_mailbox_top(design, top_name: str, engine: str, vcd_dir, stem_prefix: str) -> None:
    sim = _make_step_sim(design, top_name, engine, max_time=620)
    port0 = _make_axi_lite_master(sim, "slv0", timeout_cycles=8)
    port1 = _make_axi_lite_master(sim, "slv1", timeout_cycles=8)
    _settle_drives(sim, engine)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_{engine}"):
        assert port0.read(0x000 + 0x08) == 0x1
        assert port1.read(0x100 + 0x08) == 0x1
        assert port0.write(0x000 + 0x10, 0x1) == 0x0
        assert port0.write(0x000 + 0x14, 0x1) == 0x0
        assert port1.write(0x100 + 0x10, 0x1) == 0x0
        assert port1.write(0x100 + 0x14, 0x1) == 0x0

        assert port0.write(0x000 + 0x00, 0xAABBCCDD) == 0x0
        assert port0.read(0x000 + 0x08) == 0x1
        assert port1.read(0x100 + 0x08) == 0x0
        assert port1.read(0x100 + 0x04) == 0xAABBCCDD
        assert port1.read(0x100 + 0x08) == 0x1

        assert port1.write(0x100 + 0x00, 0x11223344) == 0x0
        assert port0.read(0x000 + 0x04) == 0x11223344

        assert port1.write(0x100 + 0x1C, 0x4) == 0x0
        with pytest.raises(AXILiteResponseError, match="expected 0x0, got 0x2"):
            port1.read(0x100 + 0x04)
        _expect(sim, "irq1", 1, "port1 irq should assert for enabled error pending")
        assert port1.read(0x100 + 0x20) == 0x4
        assert port1.read(0x100 + 0x0C) == 0x1
        assert port1.write(0x100 + 0x18, 0x4) == 0x0
        assert port1.read(0x100 + 0x20) == 0x0
        _expect(sim, "irq1", 0, "port1 irq should clear after acknowledge")


def _exercise_axi_lite_mailbox_typed_depth(design, engine: str, vcd_dir) -> None:
    sim = _make_step_sim(design, "axi_lite_mailbox_typed_exec_tb", engine, max_time=720)
    port0 = _make_axi_lite_master(sim, "slv0", timeout_cycles=8)
    port1 = _make_axi_lite_master(sim, "slv1", timeout_cycles=8)
    _settle_drives(sim, engine)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_mailbox_typed_depth_{engine}"):
        assert port0.write(0x000 + 0x1C, 0x4) == 0x0
        assert port0.write(0x000 + 0x00, 0xAAAABBBB) == 0x0
        assert port0.write(0x000 + 0x00, 0xCCCCDDDD) == 0x0
        assert port1.read(0x100 + 0x08) == 0x8

        assert port0.write(0x000 + 0x00, 0xEEEEFFFF, expected_resp=0x2) == 0x2
        _expect(sim, "irq0", 1, "port0 irq should assert after typed mailbox overflow")
        assert port0.read(0x000 + 0x20) == 0x4
        assert port0.read(0x000 + 0x0C) == 0x2

        assert port1.read(0x100 + 0x04) == 0xAAAABBBB
        assert port1.read(0x100 + 0x08) == 0x8
        assert port1.read(0x100 + 0x04) == 0xCCCCDDDD
        assert port1.read(0x100 + 0x08) == 0x1

        assert port0.write(0x000 + 0x00, 0x12345678) == 0x0
        assert port0.write(0x000 + 0x24, 0x1) == 0x0
        assert port1.read(0x100 + 0x08) == 0x1
        with pytest.raises(AXILiteResponseError, match="expected 0x0, got 0x2"):
            port1.read(0x100 + 0x04)

        assert port0.write(0x000 + 0x18, 0x4) == 0x0
        assert port0.read(0x000 + 0x20) == 0x0
        _expect(sim, "irq0", 0, "port0 irq should clear after typed mailbox acknowledge")


def _exercise_axi_lite_mailbox_typed_direction_independence(design, engine: str, vcd_dir) -> None:
    sim = _make_step_sim(design, "axi_lite_mailbox_typed_exec_tb", engine, max_time=760)
    port0 = _make_axi_lite_master(sim, "slv0", timeout_cycles=8)
    port1 = _make_axi_lite_master(sim, "slv1", timeout_cycles=8)
    _settle_drives(sim, engine)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_mailbox_typed_dirs_{engine}"):
        assert port0.write(0x000 + 0x00, 0xAAAABBBB) == 0x0
        assert port1.write(0x100 + 0x00, 0xCCCCDDDD) == 0x0

        assert port0.read(0x000 + 0x04) == 0xCCCCDDDD
        assert port1.read(0x100 + 0x04) == 0xAAAABBBB

        assert port0.write(0x000 + 0x00, 0x11112222) == 0x0
        assert port0.write(0x000 + 0x00, 0x33334444) == 0x0
        assert port1.write(0x100 + 0x00, 0x55556666) == 0x0

        assert port0.write(0x000 + 0x24, 0x1) == 0x0
        assert port0.read(0x000 + 0x04) == 0x55556666
        assert port0.read(0x000 + 0x08) == 0x1

        assert port1.read(0x100 + 0x08) == 0x1
        with pytest.raises(AXILiteResponseError, match="expected 0x0, got 0x2"):
            port1.read(0x100 + 0x04)


def _exercise_axi_lite_mailbox_slave_typed(design, top_name: str, engine: str, vcd_dir, stem_prefix: str) -> None:
    sim = _make_step_sim(design, top_name, engine, max_time=320)
    for signal_name, value in [
        ("mbox_w_full", 0),
        ("mbox_w_usage", 0),
        ("mbox_r_data", 0),
        ("mbox_r_empty", 1),
        ("mbox_r_usage", 0),
    ]:
        step_drive(sim, engine, signal_name, value)
    _settle_drives(sim, engine)

    def start_write(addr: int, data: int, *, strb: int = 0xF) -> None:
        step_drive(sim, engine, "slv_aw_addr", addr)
        step_drive(sim, engine, "slv_aw_prot", 0)
        step_drive(sim, engine, "slv_aw_valid", 1)
        step_drive(sim, engine, "slv_w_data", data)
        step_drive(sim, engine, "slv_w_strb", strb)
        step_drive(sim, engine, "slv_w_valid", 1)
        step_drive(sim, engine, "slv_b_ready", 0)
        _settle_drives(sim, engine)

    def finish_write(*, expect_resp: int = 0) -> None:
        _run_until_rising_edge(sim, "clk", sim.time + 20, "mailbox slave write capture edge not observed")
        _settle_drives(sim, engine)
        step_drive(sim, engine, "slv_aw_valid", 0)
        step_drive(sim, engine, "slv_w_valid", 0)
        step_drive(sim, engine, "slv_w_strb", 0)
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_valid", 1, "mailbox slave write response should assert")
        _expect(sim, "slv_b_resp", expect_resp, "mailbox slave write response code mismatch")
        step_drive(sim, engine, "slv_b_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "mailbox slave write release edge not observed")
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_valid", 0, "mailbox slave write response should clear after ready")
        step_drive(sim, engine, "slv_b_ready", 0)
        _settle_drives(sim, engine)

    def start_read(addr: int) -> None:
        step_drive(sim, engine, "slv_ar_addr", addr)
        step_drive(sim, engine, "slv_ar_prot", 0)
        step_drive(sim, engine, "slv_ar_valid", 1)
        step_drive(sim, engine, "slv_r_ready", 0)
        _settle_drives(sim, engine)

    def finish_read(*, expect_resp: int, expect_data: int) -> None:
        _run_until_rising_edge(sim, "clk", sim.time + 20, "mailbox slave read capture edge not observed")
        _settle_drives(sim, engine)
        step_drive(sim, engine, "slv_ar_valid", 0)
        _settle_drives(sim, engine)
        _expect(sim, "slv_r_valid", 1, "mailbox slave read response should assert")
        _expect(sim, "slv_r_resp", expect_resp, "mailbox slave read response code mismatch")
        _expect(sim, "slv_r_data", expect_data, "mailbox slave read data mismatch")
        step_drive(sim, engine, "slv_r_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "mailbox slave read release edge not observed")
        _settle_drives(sim, engine)
        _expect(sim, "slv_r_valid", 0, "mailbox slave read response should clear after ready")
        step_drive(sim, engine, "slv_r_ready", 0)
        _settle_drives(sim, engine)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_{engine}"):
        _expect(sim, "irq", 0, "mailbox slave irq should start low")
        _expect(sim, "clear_irq", 0, "mailbox slave clear_irq should start low")

        start_write(0x100 + 0x00, 0xAABBCCDD)
        _expect(sim, "slv_aw_ready", 1, "mailbox slave write address should be accepted")
        _expect(sim, "slv_w_ready", 1, "mailbox slave write data should be accepted")
        _expect(sim, "mbox_w_data", 0xAABBCCDD, "mailbox slave write data fanout mismatch")
        _expect(sim, "mbox_w_push", 1, "mailbox slave should pulse write push on MBOXW")
        _expect(sim, "mbox_w_flush", 0, "mailbox slave should not flush on MBOXW write")
        finish_write()
        _expect(sim, "mbox_w_push", 0, "mailbox slave write push should return low after handshake")

        step_drive(sim, engine, "mbox_r_data", 0x11223344)
        step_drive(sim, engine, "mbox_r_empty", 0)
        step_drive(sim, engine, "mbox_r_usage", 1)
        _settle_drives(sim, engine)
        start_read(0x100 + 0x04)
        _expect(sim, "slv_ar_ready", 1, "mailbox slave read address should be accepted")
        _expect(sim, "mbox_r_pop", 1, "mailbox slave should pulse read pop on non-empty MBOXR")
        finish_read(expect_resp=0, expect_data=0x11223344)
        _expect(sim, "mbox_r_pop", 0, "mailbox slave read pop should return low after handshake")

        start_write(0x100 + 0x1C, 0x4)
        finish_write()

        step_drive(sim, engine, "mbox_r_empty", 1)
        step_drive(sim, engine, "mbox_r_usage", 0)
        _settle_drives(sim, engine)
        start_read(0x100 + 0x04)
        _expect(sim, "mbox_r_pop", 0, "mailbox slave must not pop from an empty mailbox")
        finish_read(expect_resp=0x2, expect_data=0xFEEDDEAD)
        _expect(sim, "irq", 1, "mailbox slave irq should assert after enabled empty-read error")

        start_write(0x100 + 0x18, 0x4)
        _expect(sim, "clear_irq", 1, "mailbox slave should pulse clear_irq on IRQS acknowledge")
        finish_write()
        _expect(sim, "irq", 0, "mailbox slave irq should clear after acknowledge")
        _expect(sim, "clear_irq", 0, "mailbox slave clear_irq should return low after acknowledge")

        start_write(0x100 + 0x24, 0x3)
        _expect(sim, "mbox_w_flush", 1, "mailbox slave should pulse write flush on CTRL write")
        _expect(sim, "mbox_r_flush", 1, "mailbox slave should pulse read flush on CTRL write")
        finish_write()
        _expect(sim, "mbox_w_flush", 0, "mailbox slave write flush should return low after handshake")
        _expect(sim, "mbox_r_flush", 0, "mailbox slave read flush should return low after handshake")


def _exercise_axi_lite_mailbox_slave_typed_pending_independence(design, engine: str, vcd_dir) -> None:
    sim = _make_step_sim(design, "axi_lite_mailbox_slave_typed_exec_tb", engine, max_time=420)
    for signal_name, value in [
        ("mbox_w_full", 0),
        ("mbox_w_usage", 0),
        ("mbox_r_data", 0x11223344),
        ("mbox_r_empty", 0),
        ("mbox_r_usage", 1),
        ("slv_aw_valid", 0),
        ("slv_w_valid", 0),
        ("slv_b_ready", 0),
        ("slv_ar_valid", 0),
        ("slv_r_ready", 0),
    ]:
        step_drive(sim, engine, signal_name, value)
    _settle_drives(sim, engine)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_mailbox_slave_typed_pending_{engine}"):
        step_drive(sim, engine, "slv_aw_addr", 0x100 + 0x00)
        step_drive(sim, engine, "slv_aw_prot", 0)
        step_drive(sim, engine, "slv_aw_valid", 1)
        step_drive(sim, engine, "slv_w_data", 0xAABBCCDD)
        step_drive(sim, engine, "slv_w_strb", 0xF)
        step_drive(sim, engine, "slv_w_valid", 1)
        step_drive(sim, engine, "slv_ar_addr", 0x100 + 0x04)
        step_drive(sim, engine, "slv_ar_prot", 0)
        step_drive(sim, engine, "slv_ar_valid", 1)
        _settle_drives(sim, engine)

        _expect(sim, "slv_aw_ready", 1, "typed mailbox slave should accept write while no B response is pending")
        _expect(sim, "slv_w_ready", 1, "typed mailbox slave should accept write data while no B response is pending")
        _expect(sim, "slv_ar_ready", 1, "typed mailbox slave should accept read while no R response is pending")
        _expect(sim, "mbox_w_push", 1, "typed mailbox slave should pulse write push during overlapped write")
        _expect(sim, "mbox_r_pop", 1, "typed mailbox slave should pulse read pop during overlapped read")
        _expect(sim, "mbox_w_data", 0xAABBCCDD, "typed mailbox slave overlapped write fanout mismatch")

        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed mailbox slave overlap capture edge not observed")
        _settle_drives(sim, engine)
        step_drive(sim, engine, "slv_aw_valid", 0)
        step_drive(sim, engine, "slv_w_valid", 0)
        step_drive(sim, engine, "slv_ar_valid", 0)
        step_drive(sim, engine, "slv_w_strb", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv_b_valid", 1, "typed mailbox slave write response should be pending after overlap capture")
        _expect(sim, "slv_b_resp", 0, "typed mailbox slave overlapped write should return OKAY")
        _expect(sim, "slv_r_valid", 1, "typed mailbox slave read response should be pending after overlap capture")
        _expect(sim, "slv_r_resp", 0, "typed mailbox slave overlapped read should return OKAY")
        _expect(sim, "slv_r_data", 0x11223344, "typed mailbox slave overlapped read data mismatch")
        _expect(sim, "mbox_w_push", 0, "typed mailbox slave write push should clear after capture")
        _expect(sim, "mbox_r_pop", 0, "typed mailbox slave read pop should clear after capture")

        step_drive(sim, engine, "slv_aw_addr", 0x100 + 0x00)
        step_drive(sim, engine, "slv_aw_prot", 0)
        step_drive(sim, engine, "slv_aw_valid", 1)
        step_drive(sim, engine, "slv_w_data", 0x55667788)
        step_drive(sim, engine, "slv_w_strb", 0xF)
        step_drive(sim, engine, "slv_w_valid", 1)
        step_drive(sim, engine, "slv_ar_addr", 0x100 + 0x04)
        step_drive(sim, engine, "slv_ar_prot", 0)
        step_drive(sim, engine, "slv_ar_valid", 1)
        _settle_drives(sim, engine)

        _expect(sim, "slv_aw_ready", 0, "typed mailbox slave must block writes while B response is pending")
        _expect(sim, "slv_w_ready", 0, "typed mailbox slave must block write data while B response is pending")
        _expect(sim, "slv_ar_ready", 0, "typed mailbox slave must block reads while R response is pending")
        _expect(sim, "mbox_w_push", 0, "typed mailbox slave must not pulse write push while B response is pending")
        _expect(sim, "mbox_r_pop", 0, "typed mailbox slave must not pulse read pop while R response is pending")

        step_drive(sim, engine, "slv_aw_valid", 0)
        step_drive(sim, engine, "slv_w_valid", 0)
        step_drive(sim, engine, "slv_ar_valid", 0)
        step_drive(sim, engine, "slv_w_strb", 0)
        step_drive(sim, engine, "slv_b_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed mailbox slave B-release edge not observed")
        _settle_drives(sim, engine)
        step_drive(sim, engine, "slv_b_ready", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv_b_valid", 0, "typed mailbox slave B response should clear after ready")
        _expect(sim, "slv_r_valid", 1, "typed mailbox slave R response should remain pending after B drain")

        step_drive(sim, engine, "slv_aw_addr", 0x100 + 0x00)
        step_drive(sim, engine, "slv_aw_prot", 0)
        step_drive(sim, engine, "slv_aw_valid", 1)
        step_drive(sim, engine, "slv_w_data", 0x55667788)
        step_drive(sim, engine, "slv_w_strb", 0xF)
        step_drive(sim, engine, "slv_w_valid", 1)
        _settle_drives(sim, engine)

        _expect(sim, "slv_aw_ready", 1, "typed mailbox slave should accept a new write while only R is pending")
        _expect(sim, "slv_w_ready", 1, "typed mailbox slave should accept new write data while only R is pending")
        _expect(sim, "mbox_w_push", 1, "typed mailbox slave should pulse write push while R is pending")
        _expect(sim, "mbox_w_data", 0x55667788, "typed mailbox slave second write fanout mismatch")

        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed mailbox slave second write capture edge not observed")
        _settle_drives(sim, engine)
        step_drive(sim, engine, "slv_aw_valid", 0)
        step_drive(sim, engine, "slv_w_valid", 0)
        step_drive(sim, engine, "slv_w_strb", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv_b_valid", 1, "typed mailbox slave second write response should assert while R is pending")
        _expect(sim, "slv_b_resp", 0, "typed mailbox slave second write should return OKAY")
        _expect(sim, "slv_r_valid", 1, "typed mailbox slave R response should still be pending after second write")
        _expect(sim, "mbox_w_push", 0, "typed mailbox slave second write push should clear after capture")

        step_drive(sim, engine, "slv_r_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed mailbox slave R-release edge not observed")
        _settle_drives(sim, engine)
        step_drive(sim, engine, "slv_r_ready", 0)
        step_drive(sim, engine, "mbox_r_data", 0x99AABBCC)
        step_drive(sim, engine, "mbox_r_empty", 0)
        step_drive(sim, engine, "mbox_r_usage", 1)
        _settle_drives(sim, engine)

        _expect(sim, "slv_r_valid", 0, "typed mailbox slave R response should clear after ready")
        _expect(sim, "slv_b_valid", 1, "typed mailbox slave B response should remain pending after R drain")

        step_drive(sim, engine, "slv_ar_addr", 0x100 + 0x04)
        step_drive(sim, engine, "slv_ar_prot", 0)
        step_drive(sim, engine, "slv_ar_valid", 1)
        _settle_drives(sim, engine)

        _expect(sim, "slv_ar_ready", 1, "typed mailbox slave should accept a new read while only B is pending")
        _expect(sim, "mbox_r_pop", 1, "typed mailbox slave should pulse read pop while B is pending")

        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed mailbox slave second read capture edge not observed")
        _settle_drives(sim, engine)
        step_drive(sim, engine, "slv_ar_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv_r_valid", 1, "typed mailbox slave second read response should assert while B is pending")
        _expect(sim, "slv_r_resp", 0, "typed mailbox slave second read should return OKAY")
        _expect(sim, "slv_r_data", 0x99AABBCC, "typed mailbox slave second read data mismatch")
        _expect(sim, "slv_b_valid", 1, "typed mailbox slave B response should still be pending during second read")
        _expect(sim, "mbox_r_pop", 0, "typed mailbox slave second read pop should clear after capture")

        step_drive(sim, engine, "slv_b_ready", 1)
        step_drive(sim, engine, "slv_r_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed mailbox slave final drain edge not observed")
        _settle_drives(sim, engine)

        _expect(sim, "slv_b_valid", 0, "typed mailbox slave B response should clear after final drain")
        _expect(sim, "slv_r_valid", 0, "typed mailbox slave R response should clear after final drain")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_mailbox_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_mailbox_design(tmp_path)
    _exercise_axi_lite_mailbox_top(design, "axi_lite_mailbox_exec_tb", engine, vcd_dir, "axi_lite_mailbox")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_mailbox_typed_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_mailbox_design(tmp_path)
    _exercise_axi_lite_mailbox_top(
        design,
        "axi_lite_mailbox_typed_exec_tb",
        engine,
        vcd_dir,
        "axi_lite_mailbox_typed",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_mailbox_typed_depth_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_mailbox_design(tmp_path)
    _exercise_axi_lite_mailbox_typed_depth(design, engine, vcd_dir)


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_mailbox_typed_direction_independence_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_mailbox_design(tmp_path)
    _exercise_axi_lite_mailbox_typed_direction_independence(design, engine, vcd_dir)


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_mailbox_slave_typed_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_mailbox_design(tmp_path)
    _exercise_axi_lite_mailbox_slave_typed(
        design,
        "axi_lite_mailbox_slave_typed_exec_tb",
        engine,
        vcd_dir,
        "axi_lite_mailbox_slave_typed",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_mailbox_slave_typed_pending_independence_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_mailbox_design(tmp_path)
    _exercise_axi_lite_mailbox_slave_typed_pending_independence(design, engine, vcd_dir)


def _exercise_axi_fifo_tops(
    design,
    depth0_top: str,
    depth1_top: str,
    engine: str,
    vcd_dir,
    stem_prefix: str,
) -> None:
    sim = _make_step_sim(design, depth0_top, engine, max_time=80)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_depth0_{engine}"):
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

    sim = _make_step_sim(design, depth1_top, engine)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_depth1_{engine}"):
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

        _expect(sim, "slv_aw_ready", 0x1, "depth1 AW should accept into empty fifo")
        _expect(sim, "slv_w_ready", 0x1, "depth1 W should accept into empty fifo")
        _expect(sim, "slv_ar_ready", 0x1, "depth1 AR should accept into empty fifo")
        _expect(sim, "mst_aw_valid", 0x0, "depth1 AW should not appear before capture")
        _expect(sim, "mst_w_valid", 0x0, "depth1 W should not appear before capture")
        _expect(sim, "mst_ar_valid", 0x0, "depth1 AR should not appear before capture")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "depth1 request capture edge not observed")

        step_drive(sim, engine, "slv_aw_valid", 0)
        step_drive(sim, engine, "slv_w_valid", 0)
        step_drive(sim, engine, "slv_ar_valid", 0)
        _settle_drives(sim, engine)

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

        step_drive(sim, engine, "mst_aw_ready", 1)
        step_drive(sim, engine, "mst_w_ready", 1)
        step_drive(sim, engine, "mst_ar_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "depth1 request release edge not observed")
        _settle_drives(sim, engine)

        _expect(sim, "mst_aw_valid", 0x0, "depth1 AW should clear after pop")
        _expect(sim, "mst_w_valid", 0x0, "depth1 W should clear after pop")
        _expect(sim, "mst_ar_valid", 0x0, "depth1 AR should clear after pop")
        _expect(sim, "slv_aw_ready", 0x1, "depth1 AW should recover after pop")
        _expect(sim, "slv_w_ready", 0x1, "depth1 W should recover after pop")
        _expect(sim, "slv_ar_ready", 0x1, "depth1 AR should recover after pop")

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

        _expect(sim, "mst_b_ready", 0x1, "depth1 B should accept into empty fifo")
        _expect(sim, "mst_r_ready", 0x1, "depth1 R should accept into empty fifo")
        _expect(sim, "slv_b_valid", 0x0, "depth1 B should not appear before capture")
        _expect(sim, "slv_r_valid", 0x0, "depth1 R should not appear before capture")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "depth1 response capture edge not observed")

        step_drive(sim, engine, "mst_b_valid", 0)
        step_drive(sim, engine, "mst_r_valid", 0)
        _settle_drives(sim, engine)

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

        step_drive(sim, engine, "slv_b_ready", 1)
        step_drive(sim, engine, "slv_r_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "depth1 response release edge not observed")
        _settle_drives(sim, engine)

        _expect(sim, "slv_b_valid", 0x0, "depth1 B should clear after pop")
        _expect(sim, "slv_r_valid", 0x0, "depth1 R should clear after pop")
        _expect(sim, "mst_b_ready", 0x1, "depth1 B should recover after pop")
        _expect(sim, "mst_r_ready", 0x1, "depth1 R should recover after pop")


def _exercise_axi_fifo_typed_channel_independence(design, engine: str, vcd_dir) -> None:
    sim = _make_step_sim(design, "axi_fifo_depth1_typed_tb", engine, max_time=220)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_fifo_typed_independent_{engine}"):
        step_drive(sim, engine, "mst_aw_ready", 0)
        step_drive(sim, engine, "mst_w_ready", 0)
        step_drive(sim, engine, "mst_ar_ready", 0)
        step_drive(sim, engine, "slv_b_ready", 0)
        step_drive(sim, engine, "slv_r_ready", 0)
        step_drive(sim, engine, "slv_aw_id", 0x1)
        step_drive(sim, engine, "slv_aw_addr", 0x44)
        step_drive(sim, engine, "slv_aw_prot", 0x3)
        step_drive(sim, engine, "slv_aw_valid", 1)
        step_drive(sim, engine, "slv_w_data", 0xCAFEBABE)
        step_drive(sim, engine, "slv_w_strb", 0xA)
        step_drive(sim, engine, "slv_w_last", 1)
        step_drive(sim, engine, "slv_w_valid", 1)
        step_drive(sim, engine, "slv_ar_id", 0x2)
        step_drive(sim, engine, "slv_ar_addr", 0x88)
        step_drive(sim, engine, "slv_ar_prot", 0x5)
        step_drive(sim, engine, "slv_ar_valid", 1)
        _settle_drives(sim, engine)

        _expect(sim, "slv_aw_ready", 0x1, "typed depth1 AW should accept the first request bundle")
        _expect(sim, "slv_w_ready", 0x1, "typed depth1 W should accept the first request bundle")
        _expect(sim, "slv_ar_ready", 0x1, "typed depth1 AR should accept the first request bundle")
        _expect(sim, "mst_b_ready", 0x1, "typed depth1 B channel should start empty")
        _expect(sim, "mst_r_ready", 0x1, "typed depth1 R channel should start empty")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed depth1 first request capture edge not observed")

        step_drive(sim, engine, "slv_aw_valid", 0)
        step_drive(sim, engine, "slv_w_valid", 0)
        step_drive(sim, engine, "slv_ar_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "mst_aw_id", 0x1, "typed depth1 first AW id mismatch")
        _expect(sim, "mst_aw_addr", 0x44, "typed depth1 first AW address mismatch")
        _expect(sim, "mst_aw_prot", 0x3, "typed depth1 first AW prot mismatch")
        _expect(sim, "mst_aw_valid", 0x1, "typed depth1 first AW valid mismatch")
        _expect(sim, "mst_w_data", 0xCAFEBABE, "typed depth1 first W data mismatch")
        _expect(sim, "mst_w_strb", 0xA, "typed depth1 first W strobe mismatch")
        _expect(sim, "mst_w_last", 0x1, "typed depth1 first W last mismatch")
        _expect(sim, "mst_w_valid", 0x1, "typed depth1 first W valid mismatch")
        _expect(sim, "mst_ar_id", 0x2, "typed depth1 first AR id mismatch")
        _expect(sim, "mst_ar_addr", 0x88, "typed depth1 first AR address mismatch")
        _expect(sim, "mst_ar_prot", 0x5, "typed depth1 first AR prot mismatch")
        _expect(sim, "mst_ar_valid", 0x1, "typed depth1 first AR valid mismatch")
        _expect(sim, "slv_aw_ready", 0x0, "typed depth1 AW should backpressure while the request fifo is occupied")
        _expect(sim, "slv_w_ready", 0x0, "typed depth1 W should backpressure while the request fifo is occupied")
        _expect(sim, "slv_ar_ready", 0x0, "typed depth1 AR should backpressure while the request fifo is occupied")
        _expect(
            sim, "mst_b_ready", 0x1, "typed depth1 B fifo should still accept a response while requests are pending"
        )
        _expect(
            sim, "mst_r_ready", 0x1, "typed depth1 R fifo should still accept a response while requests are pending"
        )

        step_drive(sim, engine, "mst_b_id", 0x3)
        step_drive(sim, engine, "mst_b_resp", 0x2)
        step_drive(sim, engine, "mst_b_valid", 1)
        step_drive(sim, engine, "mst_r_id", 0x1)
        step_drive(sim, engine, "mst_r_data", 0x12345678)
        step_drive(sim, engine, "mst_r_resp", 0x1)
        step_drive(sim, engine, "mst_r_last", 1)
        step_drive(sim, engine, "mst_r_valid", 1)
        _settle_drives(sim, engine)

        _expect(sim, "slv_b_valid", 0x0, "typed depth1 B response should stay empty before capture")
        _expect(sim, "slv_r_valid", 0x0, "typed depth1 R response should stay empty before capture")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed depth1 response capture edge not observed")

        step_drive(sim, engine, "mst_b_valid", 0)
        step_drive(sim, engine, "mst_r_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv_b_id", 0x3, "typed depth1 B id mismatch")
        _expect(sim, "slv_b_resp", 0x2, "typed depth1 B response mismatch")
        _expect(sim, "slv_b_valid", 0x1, "typed depth1 B valid mismatch")
        _expect(sim, "slv_r_id", 0x1, "typed depth1 R id mismatch")
        _expect(sim, "slv_r_data", 0x12345678, "typed depth1 R data mismatch")
        _expect(sim, "slv_r_resp", 0x1, "typed depth1 R response mismatch")
        _expect(sim, "slv_r_last", 0x1, "typed depth1 R last mismatch")
        _expect(sim, "slv_r_valid", 0x1, "typed depth1 R valid mismatch")
        _expect(sim, "mst_b_ready", 0x0, "typed depth1 B should backpressure once the response fifo is occupied")
        _expect(sim, "mst_r_ready", 0x0, "typed depth1 R should backpressure once the response fifo is occupied")
        _expect(sim, "mst_aw_valid", 0x1, "typed depth1 AW should remain queued while responses are pending")
        _expect(sim, "mst_w_valid", 0x1, "typed depth1 W should remain queued while responses are pending")
        _expect(sim, "mst_ar_valid", 0x1, "typed depth1 AR should remain queued while responses are pending")

        step_drive(sim, engine, "mst_aw_ready", 1)
        step_drive(sim, engine, "mst_w_ready", 1)
        step_drive(sim, engine, "mst_ar_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed depth1 request release edge not observed")
        _settle_drives(sim, engine)

        _expect(sim, "mst_aw_valid", 0x0, "typed depth1 AW should clear after the request fifo drains")
        _expect(sim, "mst_w_valid", 0x0, "typed depth1 W should clear after the request fifo drains")
        _expect(sim, "mst_ar_valid", 0x0, "typed depth1 AR should clear after the request fifo drains")
        _expect(sim, "slv_aw_ready", 0x1, "typed depth1 AW should reopen even while responses remain pending")
        _expect(sim, "slv_w_ready", 0x1, "typed depth1 W should reopen even while responses remain pending")
        _expect(sim, "slv_ar_ready", 0x1, "typed depth1 AR should reopen even while responses remain pending")
        _expect(sim, "slv_b_valid", 0x1, "typed depth1 B response should remain pending while requests drain")
        _expect(sim, "slv_r_valid", 0x1, "typed depth1 R response should remain pending while requests drain")

        step_drive(sim, engine, "mst_aw_ready", 0)
        step_drive(sim, engine, "mst_w_ready", 0)
        step_drive(sim, engine, "mst_ar_ready", 0)
        step_drive(sim, engine, "slv_aw_id", 0x0)
        step_drive(sim, engine, "slv_aw_addr", 0x104)
        step_drive(sim, engine, "slv_aw_prot", 0x1)
        step_drive(sim, engine, "slv_aw_valid", 1)
        step_drive(sim, engine, "slv_w_data", 0x0BADF00D)
        step_drive(sim, engine, "slv_w_strb", 0xF)
        step_drive(sim, engine, "slv_w_last", 1)
        step_drive(sim, engine, "slv_w_valid", 1)
        step_drive(sim, engine, "slv_ar_id", 0x3)
        step_drive(sim, engine, "slv_ar_addr", 0x208)
        step_drive(sim, engine, "slv_ar_prot", 0x2)
        step_drive(sim, engine, "slv_ar_valid", 1)
        _settle_drives(sim, engine)

        _expect(
            sim, "slv_aw_ready", 0x1, "typed depth1 AW should accept a second request while responses are still pending"
        )
        _expect(
            sim, "slv_w_ready", 0x1, "typed depth1 W should accept a second request while responses are still pending"
        )
        _expect(
            sim, "slv_ar_ready", 0x1, "typed depth1 AR should accept a second request while responses are still pending"
        )
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed depth1 second request capture edge not observed")

        step_drive(sim, engine, "slv_aw_valid", 0)
        step_drive(sim, engine, "slv_w_valid", 0)
        step_drive(sim, engine, "slv_ar_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "mst_aw_id", 0x0, "typed depth1 second AW id mismatch")
        _expect(sim, "mst_aw_addr", 0x104, "typed depth1 second AW address mismatch")
        _expect(sim, "mst_aw_prot", 0x1, "typed depth1 second AW prot mismatch")
        _expect(sim, "mst_aw_valid", 0x1, "typed depth1 second AW valid mismatch")
        _expect(sim, "mst_w_data", 0x0BADF00D, "typed depth1 second W data mismatch")
        _expect(sim, "mst_w_strb", 0xF, "typed depth1 second W strobe mismatch")
        _expect(sim, "mst_w_last", 0x1, "typed depth1 second W last mismatch")
        _expect(sim, "mst_w_valid", 0x1, "typed depth1 second W valid mismatch")
        _expect(sim, "mst_ar_id", 0x3, "typed depth1 second AR id mismatch")
        _expect(sim, "mst_ar_addr", 0x208, "typed depth1 second AR address mismatch")
        _expect(sim, "mst_ar_prot", 0x2, "typed depth1 second AR prot mismatch")
        _expect(sim, "mst_ar_valid", 0x1, "typed depth1 second AR valid mismatch")
        _expect(
            sim, "slv_b_valid", 0x1, "typed depth1 B response should remain pending while the second request is queued"
        )
        _expect(
            sim, "slv_r_valid", 0x1, "typed depth1 R response should remain pending while the second request is queued"
        )

        step_drive(sim, engine, "slv_b_ready", 1)
        step_drive(sim, engine, "slv_r_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed depth1 response release edge not observed")
        _settle_drives(sim, engine)

        _expect(sim, "slv_b_valid", 0x0, "typed depth1 B should clear after the response fifo drains")
        _expect(sim, "slv_r_valid", 0x0, "typed depth1 R should clear after the response fifo drains")
        _expect(sim, "mst_b_ready", 0x1, "typed depth1 B should reopen after the response fifo drains")
        _expect(sim, "mst_r_ready", 0x1, "typed depth1 R should reopen after the response fifo drains")
        _expect(sim, "mst_aw_valid", 0x1, "typed depth1 AW should remain queued while responses drain")
        _expect(sim, "mst_w_valid", 0x1, "typed depth1 W should remain queued while responses drain")
        _expect(sim, "mst_ar_valid", 0x1, "typed depth1 AR should remain queued while responses drain")

        step_drive(sim, engine, "mst_aw_ready", 1)
        step_drive(sim, engine, "mst_w_ready", 1)
        step_drive(sim, engine, "mst_ar_ready", 1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed depth1 final request release edge not observed")
        _settle_drives(sim, engine)

        _expect(sim, "mst_aw_valid", 0x0, "typed depth1 AW should clear after the second request drains")
        _expect(sim, "mst_w_valid", 0x0, "typed depth1 W should clear after the second request drains")
        _expect(sim, "mst_ar_valid", 0x0, "typed depth1 AR should clear after the second request drains")
        _expect(sim, "slv_aw_ready", 0x1, "typed depth1 AW should recover after the second request drains")
        _expect(sim, "slv_w_ready", 0x1, "typed depth1 W should recover after the second request drains")
        _expect(sim, "slv_ar_ready", 0x1, "typed depth1 AR should recover after the second request drains")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_fifo_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_fifo_design(tmp_path)
    _exercise_axi_fifo_tops(design, "axi_fifo_depth0_tb", "axi_fifo_depth1_tb", engine, vcd_dir, "axi_fifo")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_fifo_typed_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_fifo_design(tmp_path)
    _exercise_axi_fifo_tops(
        design,
        "axi_fifo_depth0_typed_tb",
        "axi_fifo_depth1_typed_tb",
        engine,
        vcd_dir,
        "axi_fifo_typed",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_fifo_typed_channel_independence_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_fifo_design(tmp_path)
    _exercise_axi_fifo_typed_channel_independence(design, engine, vcd_dir)


def _exercise_axi_to_axi_lite_top(design, top_name: str, engine: str, vcd_dir, stem_prefix: str) -> None:
    sim = _make_step_sim(design, top_name, engine, max_time=120)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_{engine}"):
        response_driver = _make_axi_lite_response_driver(sim)

        response_driver.set_write_ready(True)
        step_drive(sim, engine, "slv_aw_id", 0x2)
        step_drive(sim, engine, "slv_aw_addr", 0x44)
        step_drive(sim, engine, "slv_aw_prot", 0x3)
        step_drive(sim, engine, "slv_aw_len", 0x0)
        step_drive(sim, engine, "slv_aw_atop", 0x0)
        step_drive(sim, engine, "slv_aw_valid", 1)
        step_drive(sim, engine, "slv_w_data", 0xCAFEBABE)
        step_drive(sim, engine, "slv_w_strb", 0xA)
        step_drive(sim, engine, "slv_w_last", 1)
        step_drive(sim, engine, "slv_w_valid", 1)
        step_drive(sim, engine, "slv_b_ready", 1)
        _settle_drives(sim, engine)

        _expect(sim, "slv_aw_ready", 0x1, "write AW ready mismatch")
        _expect(sim, "slv_w_ready", 0x1, "write W ready mismatch")
        _expect(sim, "mst_aw_addr", 0x44, "write AW address mismatch")
        _expect(sim, "mst_aw_prot", 0x3, "write AW protection mismatch")
        _expect(sim, "mst_aw_valid", 0x1, "write AW valid mismatch")
        _expect(sim, "mst_w_data", 0xCAFEBABE, "write data mismatch")
        _expect(sim, "mst_w_strb", 0xA, "write strobe mismatch")
        _expect(sim, "mst_w_valid", 0x1, "write W valid mismatch")
        _expect(sim, "mst_b_ready", 0x0, "write response should wait for reflected ID")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "write capture edge not observed")

        step_drive(sim, engine, "slv_aw_valid", 0)
        step_drive(sim, engine, "slv_w_valid", 0)
        _settle_drives(sim, engine)
        _expect(sim, "slv_aw_ready", 0x0, "write path should stall while response is pending")
        _expect(sim, "mst_b_ready", 0x1, "write response ready mismatch after ID capture")

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
        step_drive(sim, engine, "slv_ar_id", 0x1)
        step_drive(sim, engine, "slv_ar_addr", 0x88)
        step_drive(sim, engine, "slv_ar_prot", 0x5)
        step_drive(sim, engine, "slv_ar_len", 0x0)
        step_drive(sim, engine, "slv_ar_valid", 1)
        step_drive(sim, engine, "slv_r_ready", 1)
        _settle_drives(sim, engine)

        _expect(sim, "slv_ar_ready", 0x1, "read AR ready mismatch")
        _expect(sim, "mst_ar_addr", 0x88, "read AR address mismatch")
        _expect(sim, "mst_ar_prot", 0x5, "read AR protection mismatch")
        _expect(sim, "mst_ar_valid", 0x1, "read AR valid mismatch")
        _expect(sim, "mst_r_ready", 0x0, "read response should wait for reflected ID")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "read capture edge not observed")

        step_drive(sim, engine, "slv_ar_valid", 0)
        _settle_drives(sim, engine)
        _expect(sim, "slv_ar_ready", 0x0, "read path should stall while response is pending")
        _expect(sim, "mst_r_ready", 0x1, "read response ready mismatch after ID capture")

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


def _exercise_axi_to_axi_lite_typed_pending_independence(design, engine: str, vcd_dir) -> None:
    sim = _make_step_sim(design, "axi_to_axi_lite_typed_exec_tb", engine, max_time=180)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_to_axi_lite_typed_pending_{engine}"):
        response_driver = _make_axi_lite_response_driver(sim)
        response_driver.set_write_ready(True)
        response_driver.set_read_ready(True)

        step_drive(sim, engine, "slv_aw_id", 0x2)
        step_drive(sim, engine, "slv_aw_addr", 0x44)
        step_drive(sim, engine, "slv_aw_prot", 0x3)
        step_drive(sim, engine, "slv_aw_len", 0x0)
        step_drive(sim, engine, "slv_aw_atop", 0x0)
        step_drive(sim, engine, "slv_aw_valid", 1)
        step_drive(sim, engine, "slv_w_data", 0xCAFEBABE)
        step_drive(sim, engine, "slv_w_strb", 0xA)
        step_drive(sim, engine, "slv_w_last", 1)
        step_drive(sim, engine, "slv_w_valid", 1)
        step_drive(sim, engine, "slv_b_ready", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv_aw_ready", 0x1, "typed bridge first write should be accepted")
        _expect(sim, "mst_aw_valid", 0x1, "typed bridge first write AW valid mismatch")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed bridge first write capture edge not observed")

        step_drive(sim, engine, "slv_aw_valid", 0)
        step_drive(sim, engine, "slv_w_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv_aw_ready", 0x0, "typed bridge write path should stall while a write response is pending")
        _expect(sim, "slv_ar_ready", 0x1, "typed bridge read path should remain open while only a write is pending")

        step_drive(sim, engine, "slv_ar_id", 0x1)
        step_drive(sim, engine, "slv_ar_addr", 0x88)
        step_drive(sim, engine, "slv_ar_prot", 0x5)
        step_drive(sim, engine, "slv_ar_len", 0x0)
        step_drive(sim, engine, "slv_ar_valid", 1)
        step_drive(sim, engine, "slv_r_ready", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv_ar_ready", 0x1, "typed bridge first read should be accepted while a write is pending")
        _expect(sim, "mst_ar_valid", 0x1, "typed bridge first read AR valid mismatch")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed bridge first read capture edge not observed")

        step_drive(sim, engine, "slv_ar_valid", 0)
        _settle_drives(sim, engine)

        _expect(
            sim, "slv_aw_ready", 0x0, "typed bridge write path should stay stalled while the first write is pending"
        )
        _expect(sim, "slv_ar_ready", 0x0, "typed bridge read path should stall while a read response is pending")

        step_drive(sim, engine, "slv_b_ready", 1)
        _settle_drives(sim, engine)
        _expect(
            sim, "mst_b_ready", 0x1, "typed bridge first write response should become ready once the slave is ready"
        )

        response_driver.begin_write_response(0x2)
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_id", 0x2, "typed bridge first write response ID mismatch")
        _expect(sim, "slv_b_resp", 0x2, "typed bridge first write response code mismatch")
        _expect(sim, "slv_b_valid", 0x1, "typed bridge first write response valid mismatch")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed bridge first write response consume edge not observed")
        response_driver.end_write_response()
        _settle_drives(sim, engine)

        _expect(sim, "slv_aw_ready", 0x1, "typed bridge write path should reopen after the first write response")
        _expect(
            sim, "slv_ar_ready", 0x0, "typed bridge read path should remain stalled while the first read is pending"
        )

        step_drive(sim, engine, "slv_b_ready", 0)
        step_drive(sim, engine, "slv_aw_id", 0x3)
        step_drive(sim, engine, "slv_aw_addr", 0x104)
        step_drive(sim, engine, "slv_aw_prot", 0x1)
        step_drive(sim, engine, "slv_aw_len", 0x0)
        step_drive(sim, engine, "slv_aw_atop", 0x0)
        step_drive(sim, engine, "slv_aw_valid", 1)
        step_drive(sim, engine, "slv_w_data", 0x0BADF00D)
        step_drive(sim, engine, "slv_w_strb", 0xF)
        step_drive(sim, engine, "slv_w_last", 1)
        step_drive(sim, engine, "slv_w_valid", 1)
        _settle_drives(sim, engine)

        _expect(
            sim, "slv_aw_ready", 0x1, "typed bridge second write should be accepted while the first read is pending"
        )
        _expect(
            sim, "slv_ar_ready", 0x0, "typed bridge read path should remain stalled while the first read is pending"
        )
        _expect(sim, "mst_aw_addr", 0x104, "typed bridge second write AW address mismatch")
        _expect(sim, "mst_aw_prot", 0x1, "typed bridge second write AW prot mismatch")
        _expect(sim, "mst_aw_valid", 0x1, "typed bridge second write AW valid mismatch")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed bridge second write capture edge not observed")

        step_drive(sim, engine, "slv_aw_valid", 0)
        step_drive(sim, engine, "slv_w_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "slv_aw_ready", 0x0, "typed bridge write path should stall while the second write is pending")
        _expect(
            sim, "slv_ar_ready", 0x0, "typed bridge read path should still be stalled while the first read is pending"
        )

        step_drive(sim, engine, "slv_r_ready", 1)
        _settle_drives(sim, engine)
        _expect(sim, "mst_r_ready", 0x1, "typed bridge first read response should become ready once the slave is ready")

        response_driver.begin_read_response(0x12345678, resp=0x1)
        _settle_drives(sim, engine)
        _expect(sim, "slv_r_id", 0x1, "typed bridge first read response ID mismatch")
        _expect(sim, "slv_r_data", 0x12345678, "typed bridge first read response data mismatch")
        _expect(sim, "slv_r_resp", 0x1, "typed bridge first read response code mismatch")
        _expect(sim, "slv_r_last", 0x1, "typed bridge first read response last mismatch")
        _expect(sim, "slv_r_valid", 0x1, "typed bridge first read response valid mismatch")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed bridge first read response consume edge not observed")
        response_driver.end_read_response()
        _settle_drives(sim, engine)

        _expect(sim, "slv_ar_ready", 0x1, "typed bridge read path should reopen after the first read response")
        _expect(
            sim, "slv_aw_ready", 0x0, "typed bridge write path should remain stalled while the second write is pending"
        )

        step_drive(sim, engine, "slv_b_ready", 1)
        _settle_drives(sim, engine)
        _expect(
            sim, "mst_b_ready", 0x1, "typed bridge second write response should become ready once the slave is ready"
        )

        response_driver.begin_write_response(0x0)
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_id", 0x3, "typed bridge second write response ID mismatch")
        _expect(sim, "slv_b_resp", 0x0, "typed bridge second write response code mismatch")
        _expect(sim, "slv_b_valid", 0x1, "typed bridge second write response valid mismatch")
        _run_until_rising_edge(
            sim, "clk", sim.time + 20, "typed bridge second write response consume edge not observed"
        )
        response_driver.end_write_response()
        _settle_drives(sim, engine)

        _expect(sim, "slv_aw_ready", 0x1, "typed bridge write path should reopen after the second write response")
        _expect(
            sim, "slv_ar_ready", 0x1, "typed bridge read path should remain open after the pending operations drain"
        )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_to_axi_lite_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_to_axi_lite_design(tmp_path)
    _exercise_axi_to_axi_lite_top(design, "axi_to_axi_lite_exec_tb", engine, vcd_dir, "axi_to_axi_lite")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_to_axi_lite_typed_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_to_axi_lite_design(tmp_path)
    _exercise_axi_to_axi_lite_top(
        design,
        "axi_to_axi_lite_typed_exec_tb",
        engine,
        vcd_dir,
        "axi_to_axi_lite_typed",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_to_axi_lite_typed_pending_independence_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_to_axi_lite_design(tmp_path)
    _exercise_axi_to_axi_lite_typed_pending_independence(design, engine, vcd_dir)


@pytest.mark.parametrize("engine", ENGINES)
def _exercise_axi_lite_to_axi_top(design, top_name: str, engine: str, vcd_dir, stem_prefix: str) -> None:
    sim = _make_step_sim(design, top_name, engine, max_time=80)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_{engine}"):
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


def _exercise_axi_lite_to_axi_typed_channel_independence(design, engine: str, vcd_dir) -> None:
    sim = _make_step_sim(design, "axi_lite_to_axi_typed_exec_tb", engine, max_time=40)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_to_axi_typed_channels_{engine}"):
        step_drive(sim, engine, "slv_aw_addr", 0x184)
        step_drive(sim, engine, "slv_aw_prot", 0x6)
        step_drive(sim, engine, "slv_aw_cache", 0x9)
        step_drive(sim, engine, "slv_aw_valid", 1)
        step_drive(sim, engine, "slv_w_data", 0x11223344)
        step_drive(sim, engine, "slv_w_strb", 0x0)
        step_drive(sim, engine, "slv_w_valid", 0)
        step_drive(sim, engine, "slv_b_ready", 0)
        step_drive(sim, engine, "slv_ar_addr", 0x248)
        step_drive(sim, engine, "slv_ar_prot", 0x1)
        step_drive(sim, engine, "slv_ar_cache", 0x5)
        step_drive(sim, engine, "slv_ar_valid", 1)
        step_drive(sim, engine, "slv_r_ready", 1)
        step_drive(sim, engine, "mst_aw_ready", 0)
        step_drive(sim, engine, "mst_w_ready", 1)
        step_drive(sim, engine, "mst_b_resp", 0x2)
        step_drive(sim, engine, "mst_b_valid", 1)
        step_drive(sim, engine, "mst_ar_ready", 1)
        step_drive(sim, engine, "mst_r_data", 0x89ABCDEF)
        step_drive(sim, engine, "mst_r_resp", 0x1)
        step_drive(sim, engine, "mst_r_valid", 1)
        _settle_drives(sim, engine)

        _expect(sim, "mst_aw_addr", 0x184, "typed bridge AW address mismatch")
        _expect(sim, "mst_aw_prot", 0x6, "typed bridge AW protection mismatch")
        _expect(sim, "mst_aw_cache", 0x9, "typed bridge AW cache mismatch")
        _expect(sim, "mst_aw_size", 0x2, "typed bridge AW size mismatch")
        _expect(sim, "mst_aw_burst", 0x0, "typed bridge AW burst mismatch")
        _expect(sim, "mst_aw_valid", 0x1, "typed bridge AW valid mismatch")
        _expect(sim, "mst_w_valid", 0x0, "typed bridge W valid should stay low")
        _expect(sim, "mst_ar_addr", 0x248, "typed bridge AR address mismatch")
        _expect(sim, "mst_ar_prot", 0x1, "typed bridge AR protection mismatch")
        _expect(sim, "mst_ar_cache", 0x5, "typed bridge AR cache mismatch")
        _expect(sim, "mst_ar_size", 0x2, "typed bridge AR size mismatch")
        _expect(sim, "mst_ar_burst", 0x0, "typed bridge AR burst mismatch")
        _expect(sim, "mst_ar_valid", 0x1, "typed bridge AR valid mismatch")
        _expect(sim, "slv_aw_ready", 0x0, "typed bridge AW ready should follow only AW backpressure")
        _expect(sim, "slv_w_ready", 0x1, "typed bridge W ready should remain independent")
        _expect(sim, "slv_ar_ready", 0x1, "typed bridge AR ready should remain independent")
        _expect(sim, "slv_b_resp", 0x2, "typed bridge B response mismatch")
        _expect(sim, "slv_b_valid", 0x1, "typed bridge B valid mismatch")
        _expect(sim, "mst_b_ready", 0x0, "typed bridge B ready mismatch")
        _expect(sim, "slv_r_data", 0x89ABCDEF, "typed bridge R data mismatch")
        _expect(sim, "slv_r_resp", 0x1, "typed bridge R response mismatch")
        _expect(sim, "slv_r_valid", 0x1, "typed bridge R valid mismatch")
        _expect(sim, "mst_r_ready", 0x1, "typed bridge R ready mismatch")

        step_drive(sim, engine, "slv_aw_valid", 0)
        step_drive(sim, engine, "slv_w_data", 0x55667788)
        step_drive(sim, engine, "slv_w_strb", 0xD)
        step_drive(sim, engine, "slv_w_valid", 1)
        step_drive(sim, engine, "slv_b_ready", 1)
        step_drive(sim, engine, "slv_ar_addr", 0x30C)
        step_drive(sim, engine, "slv_ar_prot", 0x4)
        step_drive(sim, engine, "slv_ar_cache", 0xA)
        step_drive(sim, engine, "slv_ar_valid", 0)
        step_drive(sim, engine, "slv_r_ready", 0)
        step_drive(sim, engine, "mst_aw_ready", 1)
        step_drive(sim, engine, "mst_w_ready", 0)
        step_drive(sim, engine, "mst_b_resp", 0x0)
        step_drive(sim, engine, "mst_b_valid", 1)
        step_drive(sim, engine, "mst_ar_ready", 0)
        step_drive(sim, engine, "mst_r_data", 0x10203040)
        step_drive(sim, engine, "mst_r_resp", 0x2)
        step_drive(sim, engine, "mst_r_valid", 1)
        _settle_drives(sim, engine)

        _expect(sim, "mst_aw_valid", 0x0, "typed bridge AW valid deassert mismatch")
        _expect(sim, "mst_w_data", 0x55667788, "typed bridge W data mismatch")
        _expect(sim, "mst_w_strb", 0xD, "typed bridge W strobe mismatch")
        _expect(sim, "mst_w_last", 0x1, "typed bridge W last mismatch")
        _expect(sim, "mst_w_valid", 0x1, "typed bridge W valid mismatch")
        _expect(sim, "mst_ar_valid", 0x0, "typed bridge AR valid deassert mismatch")
        _expect(sim, "slv_aw_ready", 0x1, "typed bridge AW ready update mismatch")
        _expect(sim, "slv_w_ready", 0x0, "typed bridge W ready update mismatch")
        _expect(sim, "slv_ar_ready", 0x0, "typed bridge AR ready update mismatch")
        _expect(sim, "slv_b_resp", 0x0, "typed bridge B response update mismatch")
        _expect(sim, "slv_b_valid", 0x1, "typed bridge B valid update mismatch")
        _expect(sim, "mst_b_ready", 0x1, "typed bridge B ready update mismatch")
        _expect(sim, "slv_r_data", 0x10203040, "typed bridge R data update mismatch")
        _expect(sim, "slv_r_resp", 0x2, "typed bridge R response update mismatch")
        _expect(sim, "slv_r_valid", 0x1, "typed bridge R valid update mismatch")
        _expect(sim, "mst_r_ready", 0x0, "typed bridge R ready update mismatch")

        step_drive(sim, engine, "slv_aw_addr", 0x3F0)
        step_drive(sim, engine, "slv_aw_prot", 0x2)
        step_drive(sim, engine, "slv_aw_cache", 0x3)
        step_drive(sim, engine, "slv_aw_valid", 1)
        step_drive(sim, engine, "slv_w_data", 0xA5A55A5A)
        step_drive(sim, engine, "slv_w_strb", 0xF)
        step_drive(sim, engine, "slv_w_valid", 1)
        step_drive(sim, engine, "slv_b_ready", 1)
        step_drive(sim, engine, "slv_ar_addr", 0x044)
        step_drive(sim, engine, "slv_ar_prot", 0x7)
        step_drive(sim, engine, "slv_ar_cache", 0xC)
        step_drive(sim, engine, "slv_ar_valid", 1)
        step_drive(sim, engine, "slv_r_ready", 1)
        step_drive(sim, engine, "mst_aw_ready", 1)
        step_drive(sim, engine, "mst_w_ready", 1)
        step_drive(sim, engine, "mst_b_resp", 0x1)
        step_drive(sim, engine, "mst_b_valid", 0)
        step_drive(sim, engine, "mst_ar_ready", 1)
        step_drive(sim, engine, "mst_r_data", 0x55667788)
        step_drive(sim, engine, "mst_r_resp", 0x0)
        step_drive(sim, engine, "mst_r_valid", 0)
        _settle_drives(sim, engine)

        _expect(sim, "mst_aw_addr", 0x3F0, "typed bridge final AW address mismatch")
        _expect(sim, "mst_aw_prot", 0x2, "typed bridge final AW protection mismatch")
        _expect(sim, "mst_aw_cache", 0x3, "typed bridge final AW cache mismatch")
        _expect(sim, "mst_aw_valid", 0x1, "typed bridge final AW valid mismatch")
        _expect(sim, "mst_w_data", 0xA5A55A5A, "typed bridge final W data mismatch")
        _expect(sim, "mst_w_strb", 0xF, "typed bridge final W strobe mismatch")
        _expect(sim, "mst_w_valid", 0x1, "typed bridge final W valid mismatch")
        _expect(sim, "mst_ar_addr", 0x44, "typed bridge final AR address mismatch")
        _expect(sim, "mst_ar_prot", 0x7, "typed bridge final AR protection mismatch")
        _expect(sim, "mst_ar_cache", 0xC, "typed bridge final AR cache mismatch")
        _expect(sim, "mst_ar_valid", 0x1, "typed bridge final AR valid mismatch")
        _expect(sim, "slv_aw_ready", 0x1, "typed bridge final AW ready mismatch")
        _expect(sim, "slv_w_ready", 0x1, "typed bridge final W ready mismatch")
        _expect(sim, "slv_ar_ready", 0x1, "typed bridge final AR ready mismatch")
        _expect(sim, "slv_b_valid", 0x0, "typed bridge final B valid mismatch")
        _expect(sim, "slv_r_valid", 0x0, "typed bridge final R valid mismatch")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_to_axi_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_to_axi_design(tmp_path)
    _exercise_axi_lite_to_axi_top(design, "axi_lite_to_axi_exec_tb", engine, vcd_dir, "axi_lite_to_axi")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_to_axi_typed_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_to_axi_design(tmp_path)
    _exercise_axi_lite_to_axi_top(
        design,
        "axi_lite_to_axi_typed_exec_tb",
        engine,
        vcd_dir,
        "axi_lite_to_axi_typed",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_to_axi_typed_channel_independence_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_lite_to_axi_design(tmp_path)
    _exercise_axi_lite_to_axi_typed_channel_independence(design, engine, vcd_dir)


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_dw_converter_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_dw_design(tmp_path)

    sim = _make_step_sim(design, "axi_lite_dw_down_tb", engine)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_dw_down_manual_{engine}"):
        driver = _make_axi_lite_request_driver(sim)
        response_driver = _make_axi_lite_response_driver(sim)
        response_driver.set_write_ready(True)
        response_driver.set_read_ready(True)
        driver.begin_write(0x2, 0x61112222, strb=0xC)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer write capture edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _expect(sim, MST_AW_ADDR, 0x0, "downsizer first AW address mismatch")
        _expect(sim, MST_W_DATA, 0x2222, "downsizer first W data mismatch")
        _run_until_high(sim, MST_B_READY, sim.time + 20, "downsizer first B ready not observed")
        response_driver.begin_write_response(0x0)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer first B edge not observed")
        _run_until_high(sim, MST_AW_VALID, sim.time + 20, "downsizer second AW not observed")
        response_driver.end_write_response()
        _settle_drives(sim, engine)
        _expect(sim, MST_AW_ADDR, 0x2, "downsizer second AW address mismatch")
        _expect(sim, MST_W_DATA, 0x6111, "downsizer second W data mismatch")
        _expect(sim, MST_W_STRB, 0x3, "downsizer second W strobe mismatch")
        _run_until_high(sim, MST_B_READY, sim.time + 20, "downsizer second B ready not observed")
        response_driver.begin_write_response(0x2)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "downsizer second B edge not observed")
        _run_until_high(sim, SLV_B_VALID, sim.time + 20, "downsizer slave B response not observed")
        response_driver.end_write_response()
        _settle_drives(sim, engine)
        _expect(sim, SLV_B_VALID, 1, "downsizer should produce a slave B response")
        _expect(sim, SLV_B_RESP, 0x2, "downsizer B aggregation mismatch")

    sim = _make_step_sim(design, "axi_lite_dw_up_tb", engine)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_dw_up_{engine}"):
        driver = _make_axi_lite_request_driver(sim)
        response_driver = _make_axi_lite_response_driver(sim)
        response_driver.set_write_ready(True)
        driver.begin_write(0x2, 0x1EEF, strb=0x3)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "upsizer write capture edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _expect(sim, MST_AW_ADDR, 0x2, "upsizer AW address mismatch")
        _expect(sim, MST_W_DATA, 0x1EEF1EEF, "upsizer W replication mismatch")
        _expect(sim, MST_W_STRB, 0xC, "upsizer W strobe shift mismatch")
        _run_until_high(sim, MST_B_READY, sim.time + 20, "upsizer B ready not observed")
        response_driver.begin_write_response(0x0)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "upsizer B edge not observed")
        _run_until_high(sim, SLV_B_VALID, sim.time + 20, "upsizer B response not observed")
        response_driver.end_write_response()
        _settle_drives(sim, engine)

    sim = _make_step_sim(design, "axi_lite_dw_same_tb", engine)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_dw_same_{engine}"):
        driver = _make_axi_lite_request_driver(sim)
        response_driver = _make_axi_lite_response_driver(sim)
        response_driver.set_write_ready(True)
        driver.begin_write(0x8, 0xCAFEBABE, strb=0xF)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "passthrough write capture edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _expect(sim, MST_AW_ADDR, 0x8, "passthrough AW address mismatch")
        _expect(sim, MST_W_DATA, 0xCAFEBABE, "passthrough W data mismatch")
        _expect(sim, MST_W_STRB, 0xF, "passthrough W strobe mismatch")

    sim = _make_step_sim(design, "axi_lite_dw_typed_up32_128_tb", engine)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_dw_typed_up32_128_{engine}"):
        driver = _make_axi_lite_request_driver(sim)
        response_driver = _make_axi_lite_response_driver(sim)
        response_driver.set_write_ready(True)
        response_driver.set_read_ready(True)
        driver.begin_write(0x8, 0x89ABCDEF, strb=0xF)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->128 write capture edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _expect(sim, "mst_aw_addr", 0x8, "typed 32->128 AW address mismatch")
        _expect(sim, "mst_w_data", WIDE_REPEAT_32, "typed 32->128 W replication mismatch")
        _expect(sim, "mst_w_strb", 0x0F00, "typed 32->128 W strobe shift mismatch")
        _run_until_high(sim, "mst_b_ready", sim.time + 20, "typed 32->128 B ready not observed")
        response_driver.begin_write_response(0x1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->128 B edge not observed")
        _run_until_high(sim, "slv_b_valid", sim.time + 20, "typed 32->128 slave B response not observed")
        response_driver.end_write_response()
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_resp", 0x1, "typed 32->128 slave B response mismatch")

        driver.begin_read(0x8)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->128 read capture edge not observed")
        driver.end_read()
        _settle_drives(sim, engine)
        _run_until_high(sim, "mst_r_ready", sim.time + 25, "typed 32->128 R ready not observed")
        response_driver.begin_read_response(int("112233445566778899AABBCCDDEEFF00", 16), resp=0x2)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->128 R edge not observed")
        _run_until_high(sim, "slv_r_valid", sim.time + 20, "typed 32->128 slave R response not observed")
        response_driver.end_read_response()
        _settle_drives(sim, engine)
        _expect(sim, "slv_r_resp", 0x2, "typed 32->128 slave R response mismatch")
        _expect(sim, "slv_r_data", 0x55667788, "typed 32->128 read lane selection mismatch")

    sim = _make_step_sim(design, "axi_lite_dw_typed_up64_128_tb", engine)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_dw_typed_up64_128_{engine}"):
        driver = _make_axi_lite_request_driver(sim)
        response_driver = _make_axi_lite_response_driver(sim)
        response_driver.set_write_ready(True)
        response_driver.set_read_ready(True)
        driver.begin_write(0x8, 0x0123456789ABCDEF, strb=0xFF)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 64->128 write capture edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _expect(sim, "mst_aw_addr", 0x8, "typed 64->128 AW address mismatch")
        _expect(sim, "mst_w_data", WIDE_REPEAT_64, "typed 64->128 W replication mismatch")
        _expect(sim, "mst_w_strb", 0xFF00, "typed 64->128 W strobe shift mismatch")
        _run_until_high(sim, "mst_b_ready", sim.time + 20, "typed 64->128 B ready not observed")
        response_driver.begin_write_response(0x3)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 64->128 B edge not observed")
        _run_until_high(sim, "slv_b_valid", sim.time + 20, "typed 64->128 slave B response not observed")
        response_driver.end_write_response()
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_resp", 0x3, "typed 64->128 slave B response mismatch")

        driver.begin_read(0x8)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 64->128 read capture edge not observed")
        driver.end_read()
        _settle_drives(sim, engine)
        _run_until_high(sim, "mst_r_ready", sim.time + 25, "typed 64->128 R ready not observed")
        response_driver.begin_read_response(int("0123456789ABCDEFFEEDC0DEBAADF00D", 16), resp=0x2)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 64->128 R edge not observed")
        _run_until_high(sim, "slv_r_valid", sim.time + 20, "typed 64->128 slave R response not observed")
        response_driver.end_read_response()
        _settle_drives(sim, engine)
        _expect(sim, "slv_r_resp", 0x2, "typed 64->128 slave R response mismatch")
        _expect(sim, "slv_r_data", 0x0123456789ABCDEF, "typed 64->128 read lane selection mismatch")

    sim = _make_step_sim(design, "axi_lite_dw_typed_up128_256_tb", engine)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_dw_typed_up128_256_{engine}"):
        driver = _make_axi_lite_request_driver(sim)
        response_driver = _make_axi_lite_response_driver(sim)
        response_driver.set_write_ready(True)
        response_driver.set_read_ready(True)
        driver.begin_write(0x10, WIDE_VALUE_128, strb=0xFFFF)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 128->256 write capture edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _expect(sim, "mst_aw_addr", 0x10, "typed 128->256 AW address mismatch")
        _expect(sim, "mst_w_data", WIDE_REPEAT_128, "typed 128->256 W replication mismatch")
        _expect(sim, "mst_w_strb", 0xFFFF0000, "typed 128->256 W strobe shift mismatch")
        _run_until_high(sim, "mst_b_ready", sim.time + 20, "typed 128->256 B ready not observed")
        response_driver.begin_write_response(0x1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 128->256 B edge not observed")
        _run_until_high(sim, "slv_b_valid", sim.time + 20, "typed 128->256 slave B response not observed")
        response_driver.end_write_response()
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_resp", 0x1, "typed 128->256 slave B response mismatch")

        driver.begin_read(0x10)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 128->256 read capture edge not observed")
        driver.end_read()
        _settle_drives(sim, engine)
        _run_until_high(sim, "mst_r_ready", sim.time + 25, "typed 128->256 R ready not observed")
        response_driver.begin_read_response(WIDE_READ_256, resp=0x2)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 128->256 R edge not observed")
        _run_until_high(sim, "slv_r_valid", sim.time + 20, "typed 128->256 slave R response not observed")
        response_driver.end_read_response()
        _settle_drives(sim, engine)
        _expect(sim, "slv_r_resp", 0x2, "typed 128->256 slave R response mismatch")
        _expect(
            sim,
            "slv_r_data",
            0x112233445566778899AABBCCDDEEFF00,
            "typed 128->256 read lane selection mismatch",
        )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_dw_typed_basic_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_dw_design(tmp_path)
    mst_aw_addr = "i_core.i_dut.mst_aw_addr_int"
    mst_aw_valid = "i_core.i_dut.mst_aw_valid_int"
    mst_w_data = "i_core.i_dut.mst_w_data_int"
    mst_w_strb = "i_core.i_dut.mst_w_strb_int"
    mst_b_ready = "i_core.i_dut.mst_b_ready_int"
    slv_b_valid = "i_core.i_dut.slv_b_valid_int"
    slv_b_resp = "i_core.i_dut.slv_b_resp_int"

    sim = _make_step_sim(design, "axi_lite_dw_typed_down32_16_tb", engine)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_dw_typed_down32_16_{engine}"):
        driver = _make_axi_lite_request_driver(sim)
        response_driver = _make_axi_lite_response_driver(sim)
        response_driver.set_write_ready(True)
        driver.begin_write(0x2, 0x61112222, strb=0xC)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->16 write capture edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _run_until_high(sim, mst_aw_valid, sim.time + 20, "typed 32->16 first AW not observed")
        _expect(sim, mst_aw_addr, 0x0, "typed 32->16 first AW address mismatch")
        _expect(sim, mst_w_data, 0x2222, "typed 32->16 first W data mismatch")
        _run_until_high(sim, mst_b_ready, sim.time + 20, "typed 32->16 first B ready not observed")
        response_driver.begin_write_response(0x0)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->16 first B edge not observed")
        _run_until_high(sim, mst_aw_valid, sim.time + 20, "typed 32->16 second AW not observed")
        response_driver.end_write_response()
        _settle_drives(sim, engine)
        _expect(sim, mst_aw_addr, 0x2, "typed 32->16 second AW address mismatch")
        _expect(sim, mst_w_data, 0x6111, "typed 32->16 second W data mismatch")
        _expect(sim, mst_w_strb, 0x3, "typed 32->16 second W strobe mismatch")
        _run_until_high(sim, mst_b_ready, sim.time + 20, "typed 32->16 second B ready not observed")
        response_driver.begin_write_response(0x2)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->16 second B edge not observed")
        _run_until_high(sim, slv_b_valid, sim.time + 20, "typed 32->16 slave B response not observed")
        _settle_drives(sim, engine)
        _expect(sim, slv_b_resp, 0x2, "typed 32->16 B aggregation mismatch")
        response_driver.end_write_response()
        _settle_drives(sim, engine)

    sim = _make_step_sim(design, "axi_lite_dw_typed_up16_32_tb", engine)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_dw_typed_up16_32_{engine}"):
        driver = _make_axi_lite_request_driver(sim)
        response_driver = _make_axi_lite_response_driver(sim)
        response_driver.set_write_ready(True)
        driver.begin_write(0x2, 0x1EEF, strb=0x3)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 16->32 write capture edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _run_until_high(sim, mst_aw_valid, sim.time + 20, "typed 16->32 AW not observed")
        _expect(sim, mst_aw_addr, 0x2, "typed 16->32 AW address mismatch")
        _expect(sim, mst_w_data, 0x1EEF1EEF, "typed 16->32 W replication mismatch")
        _expect(sim, mst_w_strb, 0xC, "typed 16->32 W strobe shift mismatch")
        _run_until_high(sim, mst_b_ready, sim.time + 20, "typed 16->32 B ready not observed")
        response_driver.begin_write_response(0x0)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 16->32 B edge not observed")
        _run_until_high(sim, slv_b_valid, sim.time + 20, "typed 16->32 slave B response not observed")
        _settle_drives(sim, engine)
        _expect(sim, slv_b_resp, 0x0, "typed 16->32 slave B response mismatch")
        response_driver.end_write_response()
        _settle_drives(sim, engine)

    sim = _make_step_sim(design, "axi_lite_dw_typed_same32_32_tb", engine)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_dw_typed_same32_32_{engine}"):
        driver = _make_axi_lite_request_driver(sim)
        response_driver = _make_axi_lite_response_driver(sim)
        response_driver.set_write_ready(True)
        driver.begin_write(0x8, 0xCAFEBABE, strb=0xF)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed 32->32 write capture edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _expect(sim, mst_aw_addr, 0x8, "typed 32->32 AW address mismatch")
        _expect(sim, mst_w_data, 0xCAFEBABE, "typed 32->32 W data mismatch")
        _expect(sim, mst_w_strb, 0xF, "typed 32->32 W strobe mismatch")


def _exercise_axi_lite_dw_typed_pending_independence(design, engine: str, vcd_dir) -> None:
    sim = _make_step_sim(design, "axi_lite_dw_typed_down32_16_tb", engine, max_time=240)
    driver = _make_axi_lite_request_driver(sim)
    response_driver = _make_axi_lite_response_driver(sim)
    response_driver.set_write_ready(True)
    response_driver.set_read_ready(True)

    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_dw_typed_pending_{engine}"):
        driver.begin_write(0x2, 0x61112222, strb=0xC)
        _settle_drives(sim, engine)
        _expect(sim, "slv_aw_ready", 1, "typed DW first write address should be accepted")
        _expect(sim, "slv_w_ready", 1, "typed DW first write data should be accepted")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW first write capture edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)

        _run_until_high(sim, "mst_aw_valid", sim.time + 20, "typed DW first narrow AW not observed")
        _expect(sim, "mst_aw_addr", 0x0, "typed DW first narrow AW address mismatch")
        _expect(sim, "mst_w_data", 0x2222, "typed DW first narrow W data mismatch")
        _run_until_high(sim, "mst_b_ready", sim.time + 20, "typed DW first narrow B ready not observed")
        response_driver.begin_write_response(0x0)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW first narrow B edge not observed")
        response_driver.end_write_response()
        _settle_drives(sim, engine)

        _run_until_high(sim, "mst_aw_valid", sim.time + 20, "typed DW second narrow AW not observed")
        _expect(sim, "mst_aw_addr", 0x2, "typed DW second narrow AW address mismatch")
        _expect(sim, "mst_w_data", 0x6111, "typed DW second narrow W data mismatch")
        _expect(sim, "mst_w_strb", 0x3, "typed DW second narrow W strobe mismatch")
        _run_until_high(sim, "mst_b_ready", sim.time + 20, "typed DW second narrow B ready not observed")
        response_driver.begin_write_response(0x2)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW second narrow B edge not observed")
        _run_until_high(sim, "slv_b_valid", sim.time + 20, "typed DW aggregated slave B response not observed")
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_resp", 0x2, "typed DW aggregated slave B response mismatch")

        driver.begin_read(0x2)
        _settle_drives(sim, engine)
        _expect(sim, "slv_ar_ready", 1, "typed DW read path should remain open while only B is pending")
        _expect(sim, "slv_aw_ready", 0, "typed DW write path should stay stalled while aggregated B is pending")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW read capture edge not observed")
        driver.end_read()
        _settle_drives(sim, engine)

        _run_until_high(sim, "mst_ar_valid", sim.time + 20, "typed DW first narrow AR not observed")
        _expect(sim, "mst_ar_addr", 0x0, "typed DW first narrow AR address mismatch")
        _expect(sim, "slv_b_valid", 1, "typed DW aggregated B should remain pending while the read starts")
        _run_until_high(sim, "mst_r_ready", sim.time + 20, "typed DW first narrow R ready not observed")
        response_driver.begin_read_response(0x1111, resp=0x0)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW first narrow R edge not observed")
        response_driver.end_read_response()
        _settle_drives(sim, engine)

        _run_until_high(sim, "mst_ar_valid", sim.time + 20, "typed DW second narrow AR not observed")
        _expect(sim, "mst_ar_addr", 0x2, "typed DW second narrow AR address mismatch")
        _run_until_high(sim, "mst_r_ready", sim.time + 20, "typed DW second narrow R ready not observed")
        response_driver.begin_read_response(0x2222, resp=0x1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW second narrow R edge not observed")
        _run_until_high(sim, "slv_r_valid", sim.time + 20, "typed DW aggregated slave R response not observed")
        _settle_drives(sim, engine)
        _expect(sim, "slv_r_resp", 0x1, "typed DW aggregated slave R response mismatch")
        _expect(sim, "slv_r_data", 0x22221111, "typed DW aggregated slave R data mismatch")
        _expect(sim, "slv_b_valid", 1, "typed DW aggregated B should remain pending while aggregated R is valid")
        response_driver.end_read_response()
        _settle_drives(sim, engine)

        driver.set_bready(True)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW B consume edge not observed")
        driver.set_bready(False)
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_valid", 0, "typed DW aggregated B should clear after consume")
        _expect(sim, "slv_r_valid", 1, "typed DW aggregated R should remain pending after B consume")

        driver.begin_write(0x0, 0xAA55CC11, strb=0xD)
        _settle_drives(sim, engine)
        _expect(sim, "slv_aw_ready", 1, "typed DW write path should reopen while only R is pending")
        _expect(sim, "slv_w_ready", 1, "typed DW write data should be accepted while only R is pending")
        _expect(sim, "slv_ar_ready", 0, "typed DW read path should stay stalled while aggregated R is pending")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW second write capture edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)

        _run_until_high(sim, "mst_aw_valid", sim.time + 20, "typed DW third narrow AW not observed")
        _expect(sim, "mst_aw_addr", 0x0, "typed DW third narrow AW address mismatch")
        _expect(sim, "mst_w_data", 0xCC11, "typed DW third narrow W data mismatch")
        _expect(sim, "slv_r_valid", 1, "typed DW aggregated R should remain pending while the next write starts")
        _run_until_high(sim, "mst_b_ready", sim.time + 20, "typed DW third narrow B ready not observed")
        response_driver.begin_write_response(0x0)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW third narrow B edge not observed")
        response_driver.end_write_response()
        _settle_drives(sim, engine)

        _run_until_high(sim, "mst_aw_valid", sim.time + 20, "typed DW fourth narrow AW not observed")
        _expect(sim, "mst_aw_addr", 0x2, "typed DW fourth narrow AW address mismatch")
        _expect(sim, "mst_w_data", 0xAA55, "typed DW fourth narrow W data mismatch")
        _expect(sim, "mst_w_strb", 0x3, "typed DW fourth narrow W strobe mismatch")
        _run_until_high(sim, "mst_b_ready", sim.time + 20, "typed DW fourth narrow B ready not observed")
        response_driver.begin_write_response(0x1)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW fourth narrow B edge not observed")
        _run_until_high(sim, "slv_b_valid", sim.time + 20, "typed DW second aggregated slave B response not observed")
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_resp", 0x1, "typed DW second aggregated slave B response mismatch")
        _expect(sim, "slv_r_valid", 1, "typed DW aggregated R should remain pending while the next B is valid")
        response_driver.end_write_response()
        _settle_drives(sim, engine)

        driver.set_rready(True)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW R consume edge not observed")
        driver.set_rready(False)
        _settle_drives(sim, engine)
        _expect(sim, "slv_r_valid", 0, "typed DW aggregated R should clear after consume")
        _expect(sim, "slv_b_valid", 1, "typed DW second aggregated B should remain pending after R consume")

        driver.set_bready(True)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed DW final B consume edge not observed")
        driver.set_bready(False)
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_valid", 0, "typed DW second aggregated B should clear after final consume")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_dw_typed_pending_independence_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_dw_design(tmp_path)
    _exercise_axi_lite_dw_typed_pending_independence(design, engine, vcd_dir)


def _exercise_axi_lite_regs_tops(
    design,
    basic_top_name: str,
    prot_top_name: str,
    engine: str,
    vcd_dir,
    stem_prefix: str,
) -> None:
    sim = _make_step_sim(design, basic_top_name, engine)
    master = _make_axi_lite_master(sim)
    driver = _make_axi_lite_request_driver(sim)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_basic_{engine}"):
        assert master.read(0x0) == 0x40302010
        assert master.read(0x4) == 0x00006050
        assert master.write(0x0, 0xAA0000FF, strb=0x9) == 0x0
        assert master.read(0x0) == 0xAA3020FF

        step_drive(sim, engine, "reg_d_flat", _set_byte(0, 2, 0xB6))
        step_drive(sim, engine, "reg_load", 0x04)
        driver.begin_write(0x0, 0x00BB0000, strb=0x4)
        _settle_drives(sim, engine)
        _expect(sim, "slv_aw_ready", 0, "direct-load conflict should stall AW")
        _expect(sim, "slv_w_ready", 0, "direct-load conflict should stall W")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "direct-load conflict edge not observed")
        step_drive(sim, engine, "reg_d_flat", 0)
        step_drive(sim, engine, "reg_load", 0)
        _settle_drives(sim, engine)
        _expect(sim, "slv_aw_ready", 1, "AW should recover after direct-load conflict clears")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "conflict-recovery write edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _run_until_high(sim, "slv_b_valid", sim.time + 30, "conflict-recovery B response not observed")
        _expect(sim, "slv_b_resp", 0x0, "conflict-recovery B response mismatch")
        assert master.read(0x0) == 0xAABB20FF

        driver.begin_read(0x4)
        _settle_drives(sim, engine)
        _expect(sim, "rd_active", 0x30, "tail read-active bits mismatch")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "tail read edge not observed")
        driver.end_read()
        _settle_drives(sim, engine)
        _expect(sim, "slv_r_resp", 0x0, "tail read response mismatch")
        _expect(sim, "slv_r_data", 0x00006050, "tail read data mismatch")
        driver.set_rready(True)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "tail read consume edge not observed")
        driver.set_rready(False)
        _settle_drives(sim, engine)

    sim = _make_step_sim(design, prot_top_name, engine)
    master = _make_axi_lite_master(sim)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"{stem_prefix}_prot_{engine}"):
        with pytest.raises(AXILiteResponseError, match="expected 0x0, got 0x2"):
            master.read(0x0)
        with pytest.raises(AXILiteResponseError, match="expected 0x0, got 0x2"):
            master.write(0x0, 0x88776655)
        assert master.write(0x0, 0x88776655, prot=0x3) == 0x0
        assert master.read(0x0, prot=0x3) == 0x88776655


def _exercise_axi_lite_regs_typed_pending_independence(design, engine: str, vcd_dir) -> None:
    sim = _make_step_sim(design, "axi_lite_regs_basic_typed_exec_tb", engine)
    master = _make_axi_lite_master(sim)
    driver = _make_axi_lite_request_driver(sim)
    with _open_vcd_trace(sim, vcd_dir=vcd_dir, stem=f"axi_lite_regs_typed_pending_{engine}"):
        driver.begin_write(0x0, 0xAA55CC11, strb=0xD)
        _settle_drives(sim, engine)
        _expect(sim, "slv_aw_ready", 1, "typed regs first AW should be ready")
        _expect(sim, "slv_w_ready", 1, "typed regs first W should be ready")
        _expect(sim, "wr_active", 0x0D, "typed regs first write-active bits mismatch")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed regs first write edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _run_until_high(sim, "slv_b_valid", sim.time + 20, "typed regs first B response not observed")
        _expect(sim, "slv_b_resp", 0x0, "typed regs first B response mismatch")

        driver.begin_read(0x0)
        _settle_drives(sim, engine)
        _expect(sim, "slv_ar_ready", 1, "typed regs read should bypass pending B")
        _expect(sim, "rd_active", 0x0F, "typed regs read-active bits mismatch")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed regs read edge not observed")
        driver.end_read()
        _settle_drives(sim, engine)
        _run_until_high(sim, "slv_r_valid", sim.time + 20, "typed regs read response not observed")
        _expect(sim, "slv_r_resp", 0x0, "typed regs read response mismatch")
        _expect(sim, "slv_r_data", 0xAA552011, "typed regs read data mismatch")
        _expect(sim, "slv_b_valid", 1, "typed regs B should remain pending while R is valid")

        driver.set_bready(True)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed regs B consume edge not observed")
        driver.set_bready(False)
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_valid", 0, "typed regs B should clear after consume")
        _expect(sim, "slv_r_valid", 1, "typed regs R should remain pending after B consume")

        driver.begin_write(0x4, 0x00007700, strb=0x2)
        _settle_drives(sim, engine)
        _expect(sim, "slv_aw_ready", 1, "typed regs second AW should bypass pending R")
        _expect(sim, "slv_w_ready", 1, "typed regs second W should bypass pending R")
        _expect(sim, "wr_active", 0x20, "typed regs second write-active bits mismatch")
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed regs second write edge not observed")
        driver.end_write()
        _settle_drives(sim, engine)
        _run_until_high(sim, "slv_b_valid", sim.time + 20, "typed regs second B response not observed")
        _expect(sim, "slv_b_resp", 0x0, "typed regs second B response mismatch")
        _expect(sim, "slv_r_valid", 1, "typed regs R should remain pending while second B is valid")

        driver.set_rready(True)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed regs R consume edge not observed")
        driver.set_rready(False)
        _settle_drives(sim, engine)
        _expect(sim, "slv_r_valid", 0, "typed regs R should clear after consume")
        _expect(sim, "slv_b_valid", 1, "typed regs second B should remain pending after R consume")

        driver.set_bready(True)
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "typed regs final B consume edge not observed")
        driver.set_bready(False)
        _settle_drives(sim, engine)
        _expect(sim, "slv_b_valid", 0, "typed regs second B should clear after final consume")
        assert master.read(0x4) == 0x00007750


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_regs_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_regs_design(tmp_path)
    _exercise_axi_lite_regs_tops(
        design,
        "axi_lite_regs_basic_exec_tb",
        "axi_lite_regs_prot_exec_tb",
        engine,
        vcd_dir,
        "axi_lite_regs",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_regs_typed_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_regs_design(tmp_path)
    _exercise_axi_lite_regs_tops(
        design,
        "axi_lite_regs_basic_typed_exec_tb",
        "axi_lite_regs_prot_typed_exec_tb",
        engine,
        vcd_dir,
        "axi_lite_regs_typed",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_regs_typed_pending_independence_cross_engine(tmp_path, engine, vcd_dir):
    design = _parse_axi_regs_design(tmp_path)
    _exercise_axi_lite_regs_typed_pending_independence(design, engine, vcd_dir)
