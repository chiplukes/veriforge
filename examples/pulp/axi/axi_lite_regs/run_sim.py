"""Run the adapted AXI-Lite register-block example.

Run from the repository root:

    uv run python examples/pulp/axi/axi_lite_regs/run_sim.py
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
TB_FILE = SCRIPT_DIR / "tb" / "axi_lite_regs_tb.sv"
FILES = [
    str(RTL_DIR / "axi_pkg.sv"),
    str(RTL_DIR / "axi_lite_regs.sv"),
    str(TB_FILE),
]
MAX_TIME = 420
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


def _set_byte(flat_value: int, byte_index: int, byte_value: int) -> int:
    mask = 0xFF << (byte_index * 8)
    return (flat_value & ~mask) | ((byte_value & 0xFF) << (byte_index * 8))


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
        "slv_aw_prot",
        "slv_aw_valid",
        "slv_w_data",
        "slv_w_strb",
        "slv_w_valid",
        "slv_b_ready",
        "slv_ar_addr",
        "slv_ar_prot",
        "slv_ar_valid",
        "slv_r_ready",
        "reg_d_flat",
        "reg_load",
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


def _accept_b(sim: Simulator, engine: str, expected_resp: int, message_prefix: str) -> None:
    _run_until_high(sim, "slv_b_valid", sim.time + 30, f"{message_prefix} B response not observed")
    _expect(sim, "slv_b_resp", expected_resp, f"{message_prefix} B response mismatch")
    step_drive(sim, engine, "slv_b_ready", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, f"{message_prefix} B consume edge not observed")
    step_drive(sim, engine, "slv_b_ready", 0)
    _settle_drives(sim, engine)


def _accept_r(sim: Simulator, engine: str, expected_resp: int, expected_data: int, message_prefix: str) -> None:
    _run_until_high(sim, "slv_r_valid", sim.time + 30, f"{message_prefix} R response not observed")
    _expect(sim, "slv_r_resp", expected_resp, f"{message_prefix} R response mismatch")
    _expect(sim, "slv_r_data", expected_data, f"{message_prefix} R data mismatch")
    step_drive(sim, engine, "slv_r_ready", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, f"{message_prefix} R consume edge not observed")
    step_drive(sim, engine, "slv_r_ready", 0)
    _settle_drives(sim, engine)


def _issue_read(
    sim: Simulator, engine: str, addr: int, prot: int, expected_resp: int, expected_data: int, message_prefix: str
) -> None:
    step_drive(sim, engine, "slv_ar_addr", addr)
    step_drive(sim, engine, "slv_ar_prot", prot)
    step_drive(sim, engine, "slv_ar_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, f"{message_prefix} read edge not observed")
    step_drive(sim, engine, "slv_ar_valid", 0)
    _settle_drives(sim, engine)
    _accept_r(sim, engine, expected_resp, expected_data, message_prefix)


def _check_basic(design, engine: str) -> None:
    sim = _make_step_sim(design, "axi_lite_regs_basic_exec_tb", engine)

    _issue_read(sim, engine, 0x0, 0x0, 0x0, 0x40302010, "basic reset word-0")
    _issue_read(sim, engine, 0x4, 0x0, 0x0, 0x00006050, "basic reset tail")

    reg_d_flat = _set_byte(0, 2, 0xA5)
    step_drive(sim, engine, "reg_d_flat", reg_d_flat)
    step_drive(sim, engine, "reg_load", 0x04)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "direct-load edge not observed")
    step_drive(sim, engine, "reg_d_flat", 0)
    step_drive(sim, engine, "reg_load", 0)
    _settle_drives(sim, engine)
    _issue_read(sim, engine, 0x0, 0x0, 0x0, 0x40A52010, "direct-load verify")

    reg_d_flat = _set_byte(0, 2, 0xB6)
    step_drive(sim, engine, "reg_d_flat", reg_d_flat)
    step_drive(sim, engine, "reg_load", 0x04)
    step_drive(sim, engine, "slv_aw_addr", 0x0)
    step_drive(sim, engine, "slv_aw_prot", 0x0)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0x00BB0000)
    step_drive(sim, engine, "slv_w_strb", 0x4)
    step_drive(sim, engine, "slv_w_valid", 1)
    _settle_drives(sim, engine)
    _expect(sim, "slv_aw_ready", 0, "conflicting direct load should stall AW")
    _expect(sim, "slv_w_ready", 0, "conflicting direct load should stall W")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "conflicting direct-load edge not observed")
    step_drive(sim, engine, "reg_d_flat", 0)
    step_drive(sim, engine, "reg_load", 0)
    _settle_drives(sim, engine)
    _expect(sim, "slv_aw_ready", 1, "AW should recover after direct-load conflict clears")
    _expect(sim, "slv_w_ready", 1, "W should recover after direct-load conflict clears")
    _expect(sim, "wr_active", 0x04, "write-active bit should target byte 2")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "conflict-recovery write edge not observed")
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)
    _settle_drives(sim, engine)
    _accept_b(sim, engine, 0x0, "conflict-recovery write")
    _issue_read(sim, engine, 0x0, 0x0, 0x0, 0x40BB2010, "conflict-recovery verify")

    step_drive(sim, engine, "slv_aw_addr", 0x0)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0x11223344)
    step_drive(sim, engine, "slv_w_strb", 0x3)
    step_drive(sim, engine, "slv_w_valid", 1)
    _settle_drives(sim, engine)
    _expect(sim, "wr_active", 0x03, "mixed write should flag both targeted bytes")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "mixed write edge not observed")
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)
    _settle_drives(sim, engine)
    _accept_b(sim, engine, 0x0, "mixed read-only write")
    _issue_read(sim, engine, 0x0, 0x0, 0x0, 0x40BB2044, "mixed read-only verify")

    step_drive(sim, engine, "slv_ar_addr", 0x0)
    step_drive(sim, engine, "slv_ar_prot", 0x0)
    step_drive(sim, engine, "slv_ar_valid", 1)
    _settle_drives(sim, engine)
    _expect(sim, "rd_active", 0x0F, "read-active bits for first word mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "word-0 read edge not observed")
    step_drive(sim, engine, "slv_ar_valid", 0)
    _settle_drives(sim, engine)
    _accept_r(sim, engine, 0x0, 0x40BB2044, "word-0 read")

    step_drive(sim, engine, "slv_ar_addr", 0x4)
    step_drive(sim, engine, "slv_ar_valid", 1)
    _settle_drives(sim, engine)
    _expect(sim, "rd_active", 0x30, "read-active bits for tail word mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "tail read edge not observed")
    step_drive(sim, engine, "slv_ar_valid", 0)
    _settle_drives(sim, engine)
    _accept_r(sim, engine, 0x0, 0x00006050, "tail read")

    step_drive(sim, engine, "slv_aw_addr", 0x4)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0xAAAAAAAA)
    step_drive(sim, engine, "slv_w_strb", 0x1)
    step_drive(sim, engine, "slv_w_valid", 1)
    _settle_drives(sim, engine)
    _expect(sim, "wr_active", 0x10, "read-only write should flag byte 4")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "read-only write edge not observed")
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)
    _settle_drives(sim, engine)
    _accept_b(sim, engine, 0x2, "read-only write")
    _issue_read(sim, engine, 0x0, 0x0, 0x0, 0x40BB2044, "read-only write state verify")

    _issue_read(sim, engine, 0x8, 0x0, 0x2, 0xBA5E1E55, "out-of-range read")


def _check_protection(design, engine: str) -> None:
    sim = _make_step_sim(design, "axi_lite_regs_prot_exec_tb", engine)

    _expect(sim, "reg_q_flat", 0x44332211, "protection reset value mismatch")
    _issue_read(sim, engine, 0x0, 0x0, 0x2, 0xBA5E1E55, "unauthorized read")

    step_drive(sim, engine, "slv_aw_addr", 0x0)
    step_drive(sim, engine, "slv_aw_prot", 0x0)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0xCAFEBABE)
    step_drive(sim, engine, "slv_w_strb", 0xF)
    step_drive(sim, engine, "slv_w_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "unauthorized write edge not observed")
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)
    _settle_drives(sim, engine)
    _accept_b(sim, engine, 0x2, "unauthorized write")
    _expect(sim, "reg_q_flat", 0x44332211, "unauthorized write should not change state")

    step_drive(sim, engine, "slv_aw_addr", 0x0)
    step_drive(sim, engine, "slv_aw_prot", 0x3)
    step_drive(sim, engine, "slv_aw_valid", 1)
    step_drive(sim, engine, "slv_w_data", 0xCAFEBABE)
    step_drive(sim, engine, "slv_w_strb", 0xF)
    step_drive(sim, engine, "slv_w_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "authorized write edge not observed")
    step_drive(sim, engine, "slv_aw_valid", 0)
    step_drive(sim, engine, "slv_w_valid", 0)
    _settle_drives(sim, engine)
    _accept_b(sim, engine, 0x0, "authorized write")
    _expect(sim, "reg_q_flat", 0xCAFEBABE, "authorized write should update state")
    _issue_read(sim, engine, 0x0, 0x3, 0x0, 0xCAFEBABE, "authorized read")


def main() -> int:
    start = time.perf_counter()
    pcache = SCRIPT_DIR / ".pcache_axi_lite_regs"
    design = parse_files(FILES, preprocess=True, cache_dir=pcache)
    failures = []

    for engine in ENGINES:
        print(f"== Engine: {engine} ==")
        try:
            _check_basic(design, engine)
            _check_protection(design, engine)
        except Exception as exc:
            failures.append((engine, exc))
            print(f"FAIL [{engine}] {exc}")
            traceback.print_exc()
        else:
            print(f"PASS [{engine}] basic + protection checks")

    elapsed = time.perf_counter() - start
    print(f"Completed in {elapsed:.2f}s")

    if failures:
        print("Failures:")
        for engine, exc in failures:
            print(f"- {engine}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
