"""Run the adapted AXI CDC example.

Run from the repository root:

    uv run python examples/pulp/axi/axi_cdc/run_sim.py
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
TB_FILE = SCRIPT_DIR / "tb" / "axi_cdc_tb.sv"
FILES = [
    str(RTL_DIR / "axi_cdc.sv"),
    str(RTL_DIR / "cdc_2phase.sv"),
    str(RTL_DIR / "cdc_fifo_2phase.sv"),
    str(TB_FILE),
]
MAX_TIME = 320
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


def _settle_drives(sim: Simulator, engine: str, clock_name: str = "src_clk_i") -> None:
    if engine == "reference":
        sim.run(max_time=0)
    else:
        step_eval_now(sim, clock_name)


def _run_until_condition(sim: Simulator, limit: int, predicate, message: str) -> None:
    while sim.time < limit:
        if predicate(sim):
            return
        if not sim.run_step():
            raise RuntimeError(f"stepped engine stopped before {message}")
    if not predicate(sim):
        raise RuntimeError(message)


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
    top = design.get_module("axi_cdc_exec_tb")
    if top is None:
        raise RuntimeError("Top module 'axi_cdc_exec_tb' not found")

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
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), MAX_TIME)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=14), MAX_TIME)
    _settle_drives(sim, engine)
    return sim


def _release_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 45)
    _expect(sim, "src_aw_ready", 1, "write-address channel should be ready after reset")
    _expect(sim, "src_w_ready", 1, "write-data channel should be ready after reset")
    _expect(sim, "src_ar_ready", 1, "read-address channel should be ready after reset")
    _expect(sim, "src_b_valid", 0, "write response channel should be idle after reset")
    _expect(sim, "src_r_valid", 0, "read response channel should be idle after reset")
    _expect(sim, "dst_aw_valid", 0, "destination AW channel should be idle after reset")
    _expect(sim, "dst_w_valid", 0, "destination W channel should be idle after reset")
    _expect(sim, "dst_ar_valid", 0, "destination AR channel should be idle after reset")


def _exercise_write_transfer(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    _release_reset(sim, engine)

    step_drive(sim, engine, "src_aw_id", 0x2)
    step_drive(sim, engine, "src_aw_addr", 0x44)
    step_drive(sim, engine, "src_aw_prot", 0x3)
    step_drive(sim, engine, "src_aw_len", 0)
    step_drive(sim, engine, "src_aw_valid", 1)
    step_drive(sim, engine, "src_w_data", 0xCAFEBABE)
    step_drive(sim, engine, "src_w_strb", 0xA)
    step_drive(sim, engine, "src_w_last", 1)
    step_drive(sim, engine, "src_w_valid", 1)
    _settle_drives(sim, engine)
    _expect(sim, "src_aw_ready", 1, "source AW should be ready before the first transfer")
    _expect(sim, "src_w_ready", 1, "source W should be ready before the first transfer")

    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "source write capture edge not observed")
    step_drive(sim, engine, "src_aw_valid", 0)
    step_drive(sim, engine, "src_w_valid", 0)
    _settle_drives(sim, engine)

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
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 50, "destination write consume edge not observed")
    step_drive(sim, engine, "dst_aw_ready", 0)
    step_drive(sim, engine, "dst_w_ready", 0)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + 80,
        lambda s: _read_int(s, "dst_aw_valid") == 0 and _read_int(s, "dst_w_valid") == 0,
        "destination write request never drained",
    )

    _expect(sim, "dst_b_ready", 1, "destination B channel should be ready for a response")
    step_drive(sim, engine, "dst_b_id", 0x2)
    step_drive(sim, engine, "dst_b_resp", 0x1)
    step_drive(sim, engine, "dst_b_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 50, "destination write-response capture edge not observed")
    step_drive(sim, engine, "dst_b_valid", 0)
    _settle_drives(sim, engine)

    _run_until_condition(
        sim,
        sim.time + 140,
        lambda s: _read_int(s, "src_b_valid") == 1,
        "write response never returned to the source clock domain",
    )
    _expect(sim, "src_b_id", 0x2, "source B ID mismatch")
    _expect(sim, "src_b_resp", 0x1, "source B response mismatch")

    step_drive(sim, engine, "src_b_ready", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "source write-response consume edge not observed")
    _run_until_condition(
        sim,
        sim.time + 80,
        lambda s: _read_int(s, "src_b_valid") == 0,
        "write response never cleared from the source clock domain",
    )


def _exercise_read_transfer(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    _release_reset(sim, engine)

    step_drive(sim, engine, "src_ar_id", 0x1)
    step_drive(sim, engine, "src_ar_addr", 0x88)
    step_drive(sim, engine, "src_ar_prot", 0x5)
    step_drive(sim, engine, "src_ar_len", 0)
    step_drive(sim, engine, "src_ar_valid", 1)
    _settle_drives(sim, engine)
    _expect(sim, "src_ar_ready", 1, "source AR should be ready before the first transfer")

    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "source read capture edge not observed")
    step_drive(sim, engine, "src_ar_valid", 0)
    _settle_drives(sim, engine)

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
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 50, "destination read consume edge not observed")
    step_drive(sim, engine, "dst_ar_ready", 0)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + 80,
        lambda s: _read_int(s, "dst_ar_valid") == 0,
        "destination read request never drained",
    )

    _expect(sim, "dst_r_ready", 1, "destination R channel should be ready for a response")
    step_drive(sim, engine, "dst_r_id", 0x1)
    step_drive(sim, engine, "dst_r_data", 0x12345678)
    step_drive(sim, engine, "dst_r_resp", 0x2)
    step_drive(sim, engine, "dst_r_last", 1)
    step_drive(sim, engine, "dst_r_valid", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", sim.time + 50, "destination read-response capture edge not observed")
    step_drive(sim, engine, "dst_r_valid", 0)
    _settle_drives(sim, engine)

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
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", sim.time + 60, "source read-response consume edge not observed")
    _run_until_condition(
        sim,
        sim.time + 80,
        lambda s: _read_int(s, "src_r_valid") == 0,
        "read response never cleared from the source clock domain",
    )


def main() -> int:
    design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_axi_cdc_pcache")
    if design.get_module("axi_cdc") is None:
        raise RuntimeError("Imported typed module 'axi_cdc' was not parsed")

    start = time.perf_counter()
    try:
        for engine in ENGINES:
            _exercise_write_transfer(design, engine)
            _exercise_read_transfer(design, engine)
    except Exception:
        print("axi_cdc run failed", file=sys.stderr)
        traceback.print_exc()
        return 1

    elapsed = time.perf_counter() - start
    print(f"axi_cdc passed on {', '.join(ENGINES)} in {elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
