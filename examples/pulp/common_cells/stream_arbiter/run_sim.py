"""Run the imported common_cells stream_arbiter example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_arbiter/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "stream_arbiter.sv"),
    str(SCRIPT_DIR / "tb" / "stream_arbiter_tb_local.sv"),
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
    top = design.get_module("stream_arbiter_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_arbiter_tb_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
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


def _check_round_robin(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "inp_data_i", _pack_inputs(0xA0, 0xB1, 0xC2, 0xD3))
    step_drive(sim, engine, "inp_valid_i", 0b0101)
    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 1, "stream_arbiter should assert output valid when any input is valid")
    _expect(sim, "oup_data_o", 0xA0, "stream_arbiter should grant input 0 first from reset priority")
    _expect(sim, "inp_ready_o", 0b0001, "stream_arbiter should return ready only to the granted input")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter first grant edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xC2, "stream_arbiter should rotate to input 2 on the next accepted cycle")
    _expect(sim, "inp_ready_o", 0b0100, "stream_arbiter should move ready to the next granted input")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter second grant edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xA0, "stream_arbiter should wrap back to input 0 after the second accepted cycle")
    _expect(
        sim, "inp_ready_o", 0b0001, "stream_arbiter should wrap ready back to input 0 after the second accepted cycle"
    )


def _check_stall_lock(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "inp_valid_i", 0b1110)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 1, "stream_arbiter should keep output valid while a request is pending")
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter should select input 1 after the previous accepted grant")
    _expect(sim, "inp_ready_o", 0b0000, "stream_arbiter should not return ready while the output is stalled")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter stall edge not observed")

    step_drive(sim, engine, "inp_valid_i", 0b1100)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter should hold the selected payload stable while stalled")
    _expect(sim, "oup_valid_o", 1, "stream_arbiter should keep output valid asserted while locked")
    _expect(sim, "inp_ready_o", 0b0000, "stream_arbiter should keep ready low for every input while stalled")

    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter should present the locked payload until acceptance")
    _expect(sim, "inp_ready_o", 0b0010, "stream_arbiter should release ready only to the locked requester")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter locked grant edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xC2, "stream_arbiter should advance to the next active requester after acceptance")
    _expect(sim, "inp_ready_o", 0b0100, "stream_arbiter should move ready to the next requester after acceptance")


def _check_single_request_and_idle(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "inp_valid_i", 0b1000)
    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xD3, "stream_arbiter should route the lone active requester payload")
    _expect(sim, "oup_valid_o", 1, "stream_arbiter should keep output valid high for a single active requester")
    _expect(sim, "inp_ready_o", 0b1000, "stream_arbiter should return ready only to the lone active requester")

    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0, "stream_arbiter should return to idle when no requesters are active")
    _expect(sim, "inp_ready_o", 0b0000, "idle stream_arbiter should not assert any input ready")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        sim = _make_step_sim(design, engine)

        _expect(sim, "oup_valid_o", 0, "stream_arbiter should be idle after reset")
        _expect(sim, "inp_ready_o", 0b0000, "stream_arbiter should not assert input ready after reset")

        _check_round_robin(sim, engine)
        _check_stall_lock(sim, engine)
        _check_single_request_and_idle(sim, engine)
    except Exception as exc:
        print(f"  FAIL stream_arbiter python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_arbiter python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_arbiter example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_arbiter example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_stream_arbiter_pcache")
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
