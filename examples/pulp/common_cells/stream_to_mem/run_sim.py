"""Run the imported common_cells stream_to_mem example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_to_mem/run_sim.py
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.example_runner import available_engines, display_lines
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator


SCRIPT_DIR = Path(__file__).resolve().parent
RTL_FILE = SCRIPT_DIR / "rtl" / "stream_to_mem.sv"
TB_FILE = SCRIPT_DIR / "tb" / "stream_to_mem_tb_local.sv"
VM_TB_FILE = SCRIPT_DIR / "tb" / "stream_to_mem_tb_vm_local.sv"
FILES = [str(RTL_FILE), str(TB_FILE), str(VM_TB_FILE)]
MAX_TIME = 200
ENGINES = available_engines()


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    if engine in {"vm", "compiled"}:
        return _run_step_engine(design, engine)

    top = design.get_module("stream_to_mem_tb_local")
    if top is None:
        raise RuntimeError("Top module 'stream_to_mem_tb_local' not found")

    t0 = time.time()
    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=MAX_TIME)
    elapsed = time.time() - t0

    lines = display_lines(sim)
    for line in lines:
        print(f"  {line}")

    if any("FAIL" in line for line in lines):
        print(f"  engine={engine} failed in {elapsed:.2f}s")
        return 1

    if not any("PASS" in line for line in lines):
        print(f"  engine={engine} produced no PASS marker in {elapsed:.2f}s")
        return 1

    print(f"  engine={engine} passed in {elapsed:.2f}s at sim time {sim.time}")
    return 0


def _expect(sim: Simulator, signal_name: str, expected: int, message: str) -> None:
    actual = sim.read(signal_name)
    if actual != expected:
        raise RuntimeError(f"{message}: expected {expected:#x}, got {actual}")


def _make_step_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    if top is None:
        raise RuntimeError(f"Top module {top_name!r} not found")

    sim = Simulator(top, engine=engine, design=design)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), MAX_TIME)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "req_i", 0)
    step_drive(sim, engine, "req_valid_i", 0)
    step_drive(sim, engine, "resp_ready_i", 0)
    step_drive(sim, engine, "mem_req_ready_i", 1)
    step_drive(sim, engine, "mem_resp_i", 0)
    step_drive(sim, engine, "mem_resp_valid_i", 0)
    step_eval_now(sim)
    step_run_until(sim, 30)
    step_drive(sim, engine, "rst_n", 1)
    step_eval_now(sim)
    return sim


def _run_step_buf0(design, engine: str) -> None:
    sim = _make_step_sim(design, "stream_to_mem_vm_buf0", engine)

    step_drive(sim, engine, "resp_ready_i", 1)
    step_drive(sim, engine, "req_i", 0x1234)
    step_drive(sim, engine, "req_valid_i", 1)
    step_drive(sim, engine, "mem_resp_i", 0x12CB)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    step_eval_now(sim)

    _expect(sim, "req_ready_o", 1, "buf0 request should be accepted")
    _expect(sim, "mem_req_valid_o", 1, "buf0 memory request should be valid")
    _expect(sim, "resp_valid_o", 1, "buf0 response should be valid")
    _expect(sim, "resp_o", 0x12CB, "buf0 response payload mismatch")

    step_drive(sim, engine, "req_valid_i", 0)
    step_drive(sim, engine, "mem_resp_valid_i", 0)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 0, "buf0 response should clear after handshake")


def _run_step_buf1(design, engine: str) -> None:
    sim = _make_step_sim(design, "stream_to_mem_vm_buf1", engine)

    step_drive(sim, engine, "req_i", 0x0011)
    step_drive(sim, engine, "req_valid_i", 1)
    step_eval_now(sim)
    _expect(sim, "req_ready_o", 1, "buf1 first request should be accepted")
    step_run_until(sim, 40)
    step_drive(sim, engine, "req_valid_i", 0)

    step_run_until(sim, 50)
    step_drive(sim, engine, "mem_resp_i", 0x0111)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 1, "buf1 first response should be visible")
    _expect(sim, "resp_o", 0x0111, "buf1 first response payload mismatch")

    step_drive(sim, engine, "req_i", 0x0022)
    step_drive(sim, engine, "req_valid_i", 1)
    step_eval_now(sim)
    _expect(sim, "req_ready_o", 0, "buf1 second request should stall while response is blocked")
    _expect(sim, "mem_req_valid_o", 0, "buf1 memory request should be blocked while stalled")

    step_run_until(sim, 60)
    step_drive(sim, engine, "mem_resp_valid_i", 0)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 1, "buf1 buffered response should remain valid")
    _expect(sim, "resp_o", 0x0111, "buf1 buffered response mismatch")

    step_drive(sim, engine, "resp_ready_i", 1)
    step_eval_now(sim)
    _expect(sim, "req_ready_o", 1, "buf1 second request should reopen while draining")
    _expect(sim, "mem_req_valid_o", 1, "buf1 second request should reach memory while draining")

    step_run_until(sim, 70)
    step_drive(sim, engine, "req_valid_i", 0)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 0, "buf1 should have a one-cycle bubble before second response")

    step_run_until(sim, 80)
    step_drive(sim, engine, "mem_resp_i", 0x0122)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 1, "buf1 second response should be visible")
    _expect(sim, "resp_o", 0x0122, "buf1 second response payload mismatch")

    step_drive(sim, engine, "mem_resp_valid_i", 0)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 0, "buf1 response should clear after second handshake")


def _run_step_buf2(design, engine: str) -> None:
    sim = _make_step_sim(design, "stream_to_mem_vm_buf2", engine)

    step_drive(sim, engine, "req_i", 0x0033)
    step_drive(sim, engine, "req_valid_i", 1)
    step_eval_now(sim)
    _expect(sim, "req_ready_o", 1, "buf2 first request should be accepted")
    step_run_until(sim, 40)

    step_drive(sim, engine, "req_i", 0x0044)
    step_drive(sim, engine, "req_valid_i", 1)
    step_eval_now(sim)
    _expect(sim, "req_ready_o", 1, "buf2 second request should be accepted")
    step_run_until(sim, 50)

    step_drive(sim, engine, "req_i", 0x0055)
    step_drive(sim, engine, "req_valid_i", 1)
    step_eval_now(sim)
    _expect(sim, "req_ready_o", 0, "buf2 third request should stall at outstanding limit")

    step_run_until(sim, 60)
    step_drive(sim, engine, "mem_resp_i", 0x0233)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    step_eval_now(sim)
    _expect(sim, "req_ready_o", 0, "buf2 third request should stall at outstanding limit")
    _expect(sim, "resp_valid_o", 1, "buf2 first response should be visible")
    _expect(sim, "resp_o", 0x0233, "buf2 first response payload mismatch")

    step_run_until(sim, 70)
    step_drive(sim, engine, "mem_resp_i", 0x0244)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 1, "buf2 first buffered response should remain valid")
    _expect(sim, "resp_o", 0x0233, "buf2 first buffered response should stay stable")

    step_run_until(sim, 80)
    step_drive(sim, engine, "mem_resp_valid_i", 0)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 1, "buf2 buffered responses should remain available")
    _expect(sim, "resp_o", 0x0233, "buf2 head response mismatch before drain")

    step_drive(sim, engine, "resp_ready_i", 1)
    step_eval_now(sim)
    _expect(sim, "req_ready_o", 1, "buf2 third request should reopen while draining")
    _expect(sim, "mem_req_valid_o", 1, "buf2 third request should reach memory while draining")

    step_run_until(sim, 90)
    step_drive(sim, engine, "req_valid_i", 0)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 1, "buf2 second response should now be visible")
    _expect(sim, "resp_o", 0x0244, "buf2 second response payload mismatch")

    step_run_until(sim, 100)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 0, "buf2 should have a one-cycle bubble before third response")

    step_run_until(sim, 110)
    step_drive(sim, engine, "mem_resp_i", 0x0255)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 1, "buf2 third response should be visible")
    _expect(sim, "resp_o", 0x0255, "buf2 third response payload mismatch")

    step_drive(sim, engine, "mem_resp_valid_i", 0)
    step_eval_now(sim)
    _expect(sim, "resp_valid_o", 0, "buf2 response should clear after final handshake")


def _run_step_engine(design, engine: str) -> int:
    t0 = time.time()
    try:
        _run_step_buf0(design, engine)
        _run_step_buf1(design, engine)
        _run_step_buf2(design, engine)
    except Exception as exc:
        print(f"  FAIL {engine} python checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_to_mem python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [path for path in (RTL_FILE, TB_FILE, VM_TB_FILE) if not path.exists()]
    if missing:
        print("stream_to_mem example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_to_mem example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / ".pcache")
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

    return status


if __name__ == "__main__":
    sys.exit(main())
