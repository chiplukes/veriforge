"""Run the imported common_cells stream_arbiter_flushable example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_arbiter_flushable/run_sim.py
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
FILES = [
    str(SCRIPT_DIR / "rtl" / "rr_arb_tree.sv"),
    str(SCRIPT_DIR / "rtl" / "stream_arbiter_flushable.sv"),
    str(SCRIPT_DIR / "tb" / "stream_arbiter_flushable_tb_local.sv"),
]
MAX_TIME = 220
ENGINES = available_engines()


def _pack_inputs(d0: int, d1: int, d2: int, d3: int) -> int:
    return (d3 << 24) | (d2 << 16) | (d1 << 8) | d0


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


def _make_step_sim(design, engine: str) -> Simulator:
    top = design.get_module("stream_arbiter_flushable_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_arbiter_flushable_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "flush_i", 0)
    step_drive(sim, engine, "inp_data_i", 0)
    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
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


def _check_round_robin_start(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "inp_data_i", _pack_inputs(0xA0, 0xB1, 0xC2, 0xD3))
    step_drive(sim, engine, "inp_valid_i", 0b0011)
    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 1, "stream_arbiter_flushable should assert output valid for active requesters")
    _expect(sim, "oup_data_o", 0xA0, "stream_arbiter_flushable should grant input 0 first from reset priority")
    _expect(
        sim,
        "inp_ready_o",
        0b0001,
        "stream_arbiter_flushable should return ready only to the granted requester",
    )
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter_flushable first grant edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter_flushable should rotate to input 1 on the next cycle")
    _expect(
        sim,
        "inp_ready_o",
        0b0010,
        "stream_arbiter_flushable should move ready to the next granted requester",
    )


def _check_flush_reset(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter_flushable should hold the selected payload while stalled")
    _expect(sim, "inp_ready_o", 0b0000, "stream_arbiter_flushable should not return ready while stalled")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter_flushable stall edge not observed")

    step_drive(sim, engine, "flush_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter_flushable should not reset until the flush edge occurs")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter_flushable flush edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xA0, "stream_arbiter_flushable flush should restore reset priority")
    _expect(sim, "oup_valid_o", 1, "stream_arbiter_flushable should immediately present the reset-priority requester")
    _expect(
        sim, "inp_ready_o", 0b0000, "stream_arbiter_flushable should keep ready low while still stalled after flush"
    )

    step_drive(sim, engine, "flush_i", 0)
    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "inp_ready_o", 0b0001, "stream_arbiter_flushable should re-grant input 0 after flush")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter_flushable post-flush grant edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter_flushable should advance again after the post-flush grant")
    _expect(
        sim,
        "inp_ready_o",
        0b0010,
        "stream_arbiter_flushable should move ready to input 1 after the post-flush grant",
    )


def _check_single_request_and_idle(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "inp_valid_i", 0b1000)
    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xD3, "stream_arbiter_flushable should route the lone active requester payload")
    _expect(sim, "oup_valid_o", 1, "stream_arbiter_flushable should keep output valid high for a single requester")
    _expect(
        sim,
        "inp_ready_o",
        0b1000,
        "stream_arbiter_flushable should return ready only to the lone active requester",
    )

    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0, "stream_arbiter_flushable should return to idle when no requesters are active")
    _expect(sim, "inp_ready_o", 0b0000, "idle stream_arbiter_flushable should not assert any input ready")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_step_sim(design, engine)

        _expect(sim, "oup_valid_o", 0, "stream_arbiter_flushable should be idle after reset")
        _expect(sim, "inp_ready_o", 0b0000, "stream_arbiter_flushable should not assert input ready after reset")

        _check_round_robin_start(sim, engine)
        _check_flush_reset(sim, engine)
        _check_single_request_and_idle(sim, engine)
    except Exception as exc:
        print(f"  FAIL stream_arbiter_flushable python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_arbiter_flushable python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_arbiter_flushable example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_arbiter_flushable example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_stream_arbiter_flushable_pcache")
    except Exception:
        traceback.print_exc()
        return 1

    print(f"  parsed {len(design.modules)} modules in {time.time() - t0:.2f}s")

    status = 0
    for engine in ENGINES:
        try:
            status |= _run_engine(design, engine)
        except Exception:
            traceback.print_exc()
            status = 1

    if "compiled" not in ENGINES:
        print("\nCompiled engine skipped: Cython or a supported C compiler is not available.")

    return status


if __name__ == "__main__":
    sys.exit(main())
