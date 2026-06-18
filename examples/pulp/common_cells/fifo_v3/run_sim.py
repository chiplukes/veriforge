"""Run the imported common_cells fifo_v3 example.

Run from the repository root:

    uv run python examples/pulp/common_cells/fifo_v3/run_sim.py
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
RTL_FILE = SCRIPT_DIR / "rtl" / "fifo_v3.sv"
TB_FILE = SCRIPT_DIR / "tb" / "fifo_v3_tb_local.sv"
FILES = [str(RTL_FILE), str(TB_FILE)]
MAX_TIME = 220
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


def _make_step_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    if top is None:
        raise RuntimeError(f"Top module {top_name!r} not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "flush", 0)
    step_drive(sim, engine, "push", 0)
    step_drive(sim, engine, "pop", 0)
    step_drive(sim, engine, "data_i", 0)
    if engine == "reference":
        sim.run(max_time=0)
    else:
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


def _tx(sim: Simulator, engine: str, values: dict[str, int]) -> None:
    step_drive(sim, engine, "data_i", values.get("data_i", 0))
    step_drive(sim, engine, "push", values.get("push", 0))
    step_drive(sim, engine, "pop", values.get("pop", 0))
    step_drive(sim, engine, "flush", values.get("flush", 0))
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "next rising clock edge not observed")
    step_drive(sim, engine, "push", 0)
    step_drive(sim, engine, "pop", 0)
    step_drive(sim, engine, "flush", 0)
    _settle_drives(sim, engine)


def _check_reset_state(sim: Simulator) -> None:
    _expect(sim, "empty", 1, "fifo should be empty after reset")
    _expect(sim, "full", 0, "fifo should not be full after reset")
    _expect(sim, "usage", 0, "fifo usage should reset to zero")


def _run_depth3(design, engine: str) -> None:
    sim = _make_step_sim(design, "fifo_v3_tb_depth3", engine)
    _check_reset_state(sim)

    _tx(sim, engine, {"push": 1, "data_i": 0x11})
    _expect(sim, "usage", 1, "depth3 first push should increment usage")
    _expect(sim, "empty", 0, "depth3 first push should make fifo non-empty")
    _expect(sim, "data_o", 0x11, "depth3 first push should expose first element")

    _tx(sim, engine, {"push": 1, "data_i": 0x22})
    _expect(sim, "usage", 2, "depth3 second push should increment usage")
    _expect(sim, "data_o", 0x11, "depth3 head should stay stable after second push")

    _tx(sim, engine, {"push": 1, "data_i": 0x33})
    _expect(sim, "usage", 3, "depth3 third push should fill the fifo")
    _expect(sim, "full", 1, "depth3 fifo should report full after three pushes")
    _expect(sim, "data_o", 0x11, "depth3 full fifo should retain oldest head")

    _tx(sim, engine, {"push": 1, "data_i": 0x44})
    _expect(sim, "usage", 3, "depth3 blocked push should not change usage")
    _expect(sim, "full", 1, "depth3 blocked push should keep fifo full")
    _expect(sim, "data_o", 0x11, "depth3 blocked push should not disturb the head")

    _tx(sim, engine, {"pop": 1})
    _expect(sim, "usage", 2, "depth3 pop should decrement usage")
    _expect(sim, "full", 0, "depth3 pop should clear full")
    _expect(sim, "data_o", 0x22, "depth3 pop should advance to the second element")

    _tx(sim, engine, {"push": 1, "pop": 1, "data_i": 0x44})
    _expect(sim, "usage", 2, "depth3 simultaneous push/pop should preserve usage")
    _expect(sim, "data_o", 0x33, "depth3 simultaneous push/pop should advance the head")

    _tx(sim, engine, {"pop": 1})
    _expect(sim, "usage", 1, "depth3 second pop should leave one element")
    _expect(sim, "data_o", 0x44, "depth3 reordered tail should surface after draining")

    _tx(sim, engine, {"flush": 1})
    _expect(sim, "usage", 0, "depth3 flush should clear usage")
    _expect(sim, "empty", 1, "depth3 flush should empty the fifo")
    _expect(sim, "full", 0, "depth3 flush should clear full")


def _run_ft_depth3(design, engine: str) -> None:
    sim = _make_step_sim(design, "fifo_v3_tb_ft_depth3", engine)
    _check_reset_state(sim)

    step_run_until(sim, 30)
    step_drive(sim, engine, "data_i", 0xA1)
    step_drive(sim, engine, "push", 1)
    step_drive(sim, engine, "pop", 1)
    _settle_drives(sim, engine)
    _expect(sim, "empty", 0, "fall-through depth3 should make empty deassert immediately on push")
    _expect(sim, "usage", 0, "fall-through depth3 pass-through should not pre-increment usage")
    _expect(sim, "data_o", 0xA1, "fall-through depth3 should expose input data immediately")
    step_run_until(sim, 36)
    step_drive(sim, engine, "push", 0)
    step_drive(sim, engine, "pop", 0)
    _settle_drives(sim, engine)
    _expect(sim, "usage", 0, "fall-through depth3 empty pass-through should leave fifo empty")
    _expect(sim, "empty", 1, "fall-through depth3 empty pass-through should drain immediately")

    _tx(sim, engine, {"push": 1, "data_i": 0xB2})
    _expect(sim, "usage", 1, "fall-through depth3 push should store when not popped")
    _expect(sim, "data_o", 0xB2, "fall-through depth3 stored element should remain visible")

    _tx(sim, engine, {"pop": 1})
    _expect(sim, "usage", 0, "fall-through depth3 pop should drain stored element")
    _expect(sim, "empty", 1, "fall-through depth3 pop should return fifo to empty")


def _run_depth1(design, engine: str) -> None:
    sim = _make_step_sim(design, "fifo_v3_tb_depth1", engine)
    _check_reset_state(sim)

    _tx(sim, engine, {"push": 1, "data_i": 0x71})
    _expect(sim, "usage", 1, "depth1 push should fill the fifo")
    _expect(sim, "full", 1, "depth1 fifo should report full after one push")
    _expect(sim, "data_o", 0x71, "depth1 head should match the stored word")

    _tx(sim, engine, {"push": 1, "data_i": 0x72})
    _expect(sim, "usage", 1, "depth1 blocked push should keep usage stable")
    _expect(sim, "data_o", 0x71, "depth1 blocked push should preserve the head")

    _tx(sim, engine, {"pop": 1})
    _expect(sim, "usage", 0, "depth1 pop should empty the fifo")
    _expect(sim, "empty", 1, "depth1 pop should assert empty")
    _expect(sim, "full", 0, "depth1 pop should clear full")


def _run_ft_depth1(design, engine: str) -> None:
    sim = _make_step_sim(design, "fifo_v3_tb_ft_depth1", engine)
    _check_reset_state(sim)

    step_run_until(sim, 30)
    step_drive(sim, engine, "data_i", 0xC3)
    step_drive(sim, engine, "push", 1)
    _settle_drives(sim, engine)
    _expect(sim, "empty", 0, "fall-through depth1 should expose a pushed word immediately")
    _expect(sim, "full", 0, "fall-through depth1 should not look full before the clock edge")
    _expect(sim, "data_o", 0xC3, "fall-through depth1 should drive input data directly when empty")
    step_run_until(sim, 36)
    step_drive(sim, engine, "push", 0)
    _settle_drives(sim, engine)
    _expect(sim, "usage", 1, "fall-through depth1 should store the word after the clock edge")
    _expect(sim, "full", 1, "fall-through depth1 should become full after storing one word")
    _expect(sim, "data_o", 0xC3, "fall-through depth1 stored word should remain at the output")

    _tx(sim, engine, {"pop": 1})
    _expect(sim, "usage", 0, "fall-through depth1 pop should empty the fifo")
    _expect(sim, "empty", 1, "fall-through depth1 pop should assert empty")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _run_depth3(design, engine)
        _run_ft_depth3(design, engine)
        _run_depth1(design, engine)
        _run_ft_depth1(design, engine)
    except Exception as exc:
        print(f"  FAIL fifo_v3 python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS fifo_v3 python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("fifo_v3 example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing fifo_v3 example...")
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
