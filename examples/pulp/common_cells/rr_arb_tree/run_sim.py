"""Run the imported common_cells rr_arb_tree example.

Run from the repository root:

    uv run python examples/pulp/common_cells/rr_arb_tree/run_sim.py
"""

from __future__ import annotations

import os
import sys
import time
import traceback
from pathlib import Path

from veriforge.project import parse_files
from veriforge.sim.example_runner import available_engines, display_lines
from veriforge.sim.step_harness import step_run_until
from veriforge.sim.testbench import Clock, Simulator


SCRIPT_DIR = Path(__file__).resolve().parent
RTL_FILE = SCRIPT_DIR / "rtl" / "rr_arb_tree.sv"
TB_FILE = SCRIPT_DIR / "tb" / "rr_arb_tree_tb_local.sv"
VM_TB_FILE = SCRIPT_DIR / "tb" / "rr_arb_tree_tb_vm_local.sv"
FILES = [str(RTL_FILE), str(TB_FILE), str(VM_TB_FILE)]
MAX_TIME = 120
ENGINES = available_engines()


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    if engine in {"vm", "vm-fast", "compiled"}:
        return _run_step_engine(design, engine)

    top = design.get_module("rr_arb_tree_tb_local")
    if top is None:
        raise RuntimeError("Top module 'rr_arb_tree_tb_local' not found")

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


def _expect_state(sim: Simulator, exp_req: int, exp_idx: int, exp_data: int, exp_gnt: int, label: str) -> None:
    _expect(sim, "req_oup", exp_req, f"{label} req_o")
    _expect(sim, "idx_oup", exp_idx, f"{label} idx")
    _expect(sim, "data_oup", exp_data, f"{label} data")
    _expect(sim, "gnt", exp_gnt, f"{label} gnt")


def _make_step_sim(design, engine: str) -> Simulator:
    top = design.get_module("rr_arb_tree_tb_vm_local")
    if top is None:
        raise RuntimeError("Top module 'rr_arb_tree_tb_vm_local' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.schedule_clock(Clock(sim.signal("clk"), period=10), MAX_TIME)
    sim.drive("clk", 0)
    sim.drive("rst_n", 0)
    sim.drive("flush", 0)
    sim.drive("rr", 0)
    sim.drive("req", 0)
    sim.drive("gnt_oup", 0)
    sim.drive("data_bus", 0xD3C2B1A0)
    sim.settle()
    return sim


def _run_step_engine(design, engine: str) -> int:
    t0 = time.time()
    try:
        sim = _make_step_sim(design, engine)

        step_run_until(sim, 22)
        sim.drive("rst_n", 1)
        sim.settle()
        step_run_until(sim, 26)
        _expect_state(sim, 0, 0, 0x00, 0b0000, "idle after reset")

        sim.drive("req", 0b0101)
        sim.drive("gnt_oup", 1)
        sim.settle()
        _expect_state(sim, 1, 0, 0xA0, 0b0001, "first round robin grant")
        step_run_until(sim, 36)
        _expect_state(sim, 1, 2, 0xC2, 0b0100, "second round robin grant")
        step_run_until(sim, 46)
        _expect_state(sim, 1, 0, 0xA0, 0b0001, "wrapped round robin grant")

        sim.drive("req", 0b0110)
        sim.drive("gnt_oup", 0)
        sim.settle()
        _expect_state(sim, 1, 1, 0xB1, 0b0000, "lock selection while stalled")
        step_run_until(sim, 56)
        _expect_state(sim, 1, 1, 0xB1, 0b0000, "locked selection remains stable")

        sim.drive("gnt_oup", 1)
        sim.settle()
        _expect_state(sim, 1, 1, 0xB1, 0b0010, "locked request granted")
        step_run_until(sim, 66)
        _expect_state(sim, 1, 1, 0xB1, 0b0010, "priority state updates after locked grant")
        step_run_until(sim, 76)
        _expect_state(sim, 1, 2, 0xC2, 0b0100, "round robin advances on the next accepted cycle")

        sim.drive("flush", 1)
        step_run_until(sim, 85)
        sim.drive("flush", 0)
        sim.drive("req", 0b0011)
        sim.settle()
        step_run_until(sim, 86)
        _expect_state(sim, 1, 0, 0xA0, 0b0001, "flush resets priority state")

        sim.drive("req", 0b1000)
        sim.settle()
        step_run_until(sim, 87)
        _expect_state(sim, 1, 3, 0xD3, 0b1000, "single active requester routes data")

        step_run_until(sim, 95)
        sim.drive("req", 0)
        sim.drive("gnt_oup", 0)
        sim.settle()
        step_run_until(sim, 96)
        _expect_state(sim, 0, 0, 0x00, 0b0000, "returns idle")
    except Exception as exc:
        print(f"  FAIL {engine} python checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS rr_arb_tree python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [path for path in (RTL_FILE, TB_FILE, VM_TB_FILE) if not path.exists()]
    if missing:
        print("rr_arb_tree example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing rr_arb_tree example...")
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

    if "compiled" not in ENGINES:
        print("\nCompiled engine skipped: Cython or a supported C compiler is not available.")

    return status


if __name__ == "__main__":
    sys.exit(main())
