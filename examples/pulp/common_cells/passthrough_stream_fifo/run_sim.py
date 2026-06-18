"""Run the imported common_cells passthrough_stream_fifo example.

Run from the repository root:

    uv run python examples/pulp/common_cells/passthrough_stream_fifo/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "passthrough_stream_fifo.sv"),
    str(SCRIPT_DIR / "tb" / "passthrough_stream_fifo_tb_local.sv"),
]
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
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "data_i", 0)
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
    step_drive(sim, engine, "valid_i", values.get("valid_i", 0))
    step_drive(sim, engine, "ready_i", values.get("ready_i", 0))
    step_drive(sim, engine, "flush", values.get("flush", 0))
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "next rising clock edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "flush", 0)
    _settle_drives(sim, engine)


def _check_reset_state(sim: Simulator) -> None:
    _expect(sim, "valid_o", 0, "passthrough_stream_fifo should be empty after reset")
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo should be ready after reset")


def _run_same_cycle(design, engine: str) -> None:
    sim = _make_step_sim(design, "passthrough_stream_fifo_tb_same_cycle", engine)
    _check_reset_state(sim)

    _tx(sim, engine, {"valid_i": 1, "data_i": 0x11})
    _expect(sim, "valid_o", 1, "passthrough_stream_fifo same-cycle first push should make output valid")
    _expect(sim, "data_o", 0x11, "passthrough_stream_fifo same-cycle first push payload mismatch")

    _tx(sim, engine, {"valid_i": 1, "data_i": 0x22})
    _expect(sim, "data_o", 0x11, "passthrough_stream_fifo same-cycle second push should keep the head")

    _tx(sim, engine, {"valid_i": 1, "data_i": 0x33})
    _expect(sim, "ready_o", 0, "passthrough_stream_fifo same-cycle path should report full after three pushes")
    _expect(sim, "data_o", 0x11, "passthrough_stream_fifo same-cycle full fifo should retain the oldest head")

    step_drive(sim, engine, "data_i", 0x44)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo same-cycle path should reopen for pop/push on full")
    _expect(sim, "valid_o", 1, "passthrough_stream_fifo same-cycle path should keep the output valid on full")
    _expect(sim, "data_o", 0x11, "passthrough_stream_fifo same-cycle full exchange should keep the old head")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "passthrough_stream_fifo same-cycle edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 0, "passthrough_stream_fifo same-cycle queue should stay full after pop/push")
    _expect(sim, "data_o", 0x22, "passthrough_stream_fifo same-cycle exchange should advance to the next head")

    _tx(sim, engine, {"ready_i": 1})
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo same-cycle first drain should reopen the input")
    _expect(sim, "data_o", 0x33, "passthrough_stream_fifo same-cycle first drain should preserve order")

    _tx(sim, engine, {"ready_i": 1})
    _expect(sim, "valid_o", 1, "passthrough_stream_fifo same-cycle second drain should leave one word queued")
    _expect(sim, "data_o", 0x44, "passthrough_stream_fifo same-cycle queued replacement should drain last")

    _tx(sim, engine, {"ready_i": 1})
    _expect(sim, "valid_o", 0, "passthrough_stream_fifo same-cycle third drain should empty the fifo")
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo same-cycle third drain should restore ready")

    _tx(sim, engine, {"valid_i": 1, "data_i": 0x55})
    _expect(sim, "valid_o", 1, "passthrough_stream_fifo same-cycle refill should capture a new word")
    _expect(sim, "data_o", 0x55, "passthrough_stream_fifo same-cycle refill payload mismatch")

    _tx(sim, engine, {"flush": 1})
    _expect(sim, "valid_o", 0, "passthrough_stream_fifo same-cycle flush should empty the fifo")
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo same-cycle flush should restore ready")


def _run_no_same_cycle(design, engine: str) -> None:
    sim = _make_step_sim(design, "passthrough_stream_fifo_tb_no_same_cycle", engine)
    _check_reset_state(sim)

    _tx(sim, engine, {"valid_i": 1, "data_i": 0x11})
    _tx(sim, engine, {"valid_i": 1, "data_i": 0x22})
    _tx(sim, engine, {"valid_i": 1, "data_i": 0x33})
    _expect(sim, "ready_o", 0, "passthrough_stream_fifo no-same-cycle path should report full after three pushes")
    _expect(sim, "data_o", 0x11, "passthrough_stream_fifo no-same-cycle full fifo should retain the head")

    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 0, "passthrough_stream_fifo no-same-cycle path should stay blocked before the pop edge")
    _expect(sim, "valid_o", 1, "passthrough_stream_fifo no-same-cycle path should keep output valid on full")
    _expect(sim, "data_o", 0x11, "passthrough_stream_fifo no-same-cycle blocked full pop should keep the head")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "passthrough_stream_fifo no-same-cycle edge not observed")
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo no-same-cycle path should reopen after a pure pop")
    _expect(sim, "data_o", 0x22, "passthrough_stream_fifo no-same-cycle pure pop should advance the head")

    _tx(sim, engine, {"valid_i": 1, "data_i": 0x44})
    _expect(sim, "ready_o", 0, "passthrough_stream_fifo no-same-cycle refill should return to full")
    _expect(sim, "data_o", 0x22, "passthrough_stream_fifo no-same-cycle refill should not bypass the head")

    _tx(sim, engine, {"ready_i": 1})
    _expect(sim, "data_o", 0x33, "passthrough_stream_fifo no-same-cycle first drain should preserve order")

    _tx(sim, engine, {"ready_i": 1})
    _expect(sim, "data_o", 0x44, "passthrough_stream_fifo no-same-cycle replacement word should drain last")

    _tx(sim, engine, {"ready_i": 1})
    _expect(sim, "valid_o", 0, "passthrough_stream_fifo no-same-cycle final drain should empty the fifo")
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo no-same-cycle final drain should restore ready")

    _tx(sim, engine, {"valid_i": 1, "data_i": 0x66})
    _tx(sim, engine, {"flush": 1})
    _expect(sim, "valid_o", 0, "passthrough_stream_fifo no-same-cycle flush should empty the fifo")
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo no-same-cycle flush should restore ready")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _run_same_cycle(design, engine)
        _run_no_same_cycle(design, engine)
    except Exception as exc:
        print(f"  FAIL passthrough_stream_fifo python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS passthrough_stream_fifo python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("passthrough_stream_fifo example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing passthrough_stream_fifo example...")
    t0 = time.time()
    try:
        design = parse_files(
            FILES,
            preprocess=True,
            cache_dir=SCRIPT_DIR / "_vtc_passthrough_stream_fifo_pcache",
        )
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
