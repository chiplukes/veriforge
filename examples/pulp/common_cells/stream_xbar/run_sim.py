"""Run the imported common_cells stream_xbar example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_xbar/run_sim.py
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
TB_FILE = SCRIPT_DIR / "tb" / "sx_tb.sv"
FILES = [
    str(RTL_DIR / "spill_register_flushable.sv"),
    str(RTL_DIR / "spill_register.sv"),
    str(RTL_DIR / "stream_xbar.sv"),
    str(TB_FILE),
]
MAX_TIME = 240
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
    step_drive(sim, engine, "data_i", 0)
    step_drive(sim, engine, "sel_i", 0)
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
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


def _pack_inputs(d0: int, d1: int, d2: int) -> int:
    return (d2 << 16) | (d1 << 8) | d0


def _check_nospill(design, engine: str) -> None:
    sim = _make_step_sim(design, "sx0_tb", engine)

    _expect(sim, "valid_o", 0, "stream_xbar no-spill should be idle after reset")
    _expect(sim, "ready_o", 0, "stream_xbar no-spill should not assert input ready until valids exist")

    step_drive(sim, engine, "ready_i", 0b11)
    step_drive(sim, engine, "data_i", _pack_inputs(0xA0, 0xB1, 0xC2))
    step_drive(sim, engine, "sel_i", 0b100)
    step_drive(sim, engine, "valid_i", 0b101)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b11, "stream_xbar no-spill should route two independent outputs simultaneously")
    _expect(sim, "data_o", 0xC2A0, "stream_xbar no-spill payload routing mismatch for independent outputs")
    _expect(sim, "idx_o", 0b1000, "stream_xbar no-spill idx routing mismatch for independent outputs")
    _expect(sim, "ready_o", 0b101, "stream_xbar no-spill should ready both selected inputs")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "independent-output handshake edge not observed")

    step_drive(sim, engine, "data_i", _pack_inputs(0x10, 0x21, 0x32))
    step_drive(sim, engine, "sel_i", 0b000)
    step_drive(sim, engine, "valid_i", 0b011)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b01, "stream_xbar no-spill should only drive output 0 during contention")
    _expect(sim, "data_o", 0x0010, "stream_xbar no-spill should choose input 0 first on output 0")
    _expect(sim, "idx_o", 0b0000, "stream_xbar no-spill should report input 0 as first winner")
    _expect(sim, "ready_o", 0b001, "stream_xbar no-spill should only ready the granted contending input")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "first contention edge not observed")

    _expect(sim, "data_o", 0x0021, "stream_xbar no-spill should rotate to input 1 on the next grant")
    _expect(sim, "idx_o", 0b0001, "stream_xbar no-spill should report input 1 after round-robin rotation")
    _expect(sim, "ready_o", 0b010, "stream_xbar no-spill should ready the second contender after rotation")

    step_drive(sim, engine, "flush", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "flush edge not observed")
    step_drive(sim, engine, "flush", 0)
    _settle_drives(sim, engine)

    _expect(sim, "data_o", 0x0010, "stream_xbar no-spill flush should reset the output 0 round-robin pointer")
    _expect(sim, "idx_o", 0b0000, "stream_xbar no-spill flush should reset the granted input index")


def _check_spill(design, engine: str) -> None:
    sim = _make_step_sim(design, "sx1_tb", engine)

    step_drive(sim, engine, "ready_i", 0b00)
    step_drive(sim, engine, "data_i", _pack_inputs(0x44, 0x55, 0x66))
    step_drive(sim, engine, "sel_i", 0b000)
    step_drive(sim, engine, "valid_i", 0b011)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b00, "stream_xbar spill outputs should stay empty before the first capture edge")
    _expect(sim, "ready_o", 0b001, "stream_xbar spill should only grant the first contender before capture")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill first capture edge not observed")
    _expect(sim, "valid_o", 0b01, "stream_xbar spill should buffer the first granted output")
    _expect(sim, "data_o", 0x0044, "stream_xbar spill should buffer the first payload")
    _expect(sim, "idx_o", 0b0000, "stream_xbar spill should buffer the first index")
    _expect(sim, "ready_o", 0b010, "stream_xbar spill should reopen to the second contender while only a is full")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second capture edge not observed")
    _expect(sim, "valid_o", 0b01, "stream_xbar spill should still present only one output on the contended port")
    _expect(sim, "data_o", 0x0044, "stream_xbar spill should preserve the head payload while stalled")
    _expect(sim, "ready_o", 0b000, "stream_xbar spill should backpressure once both spill stages are occupied")

    step_drive(sim, engine, "ready_i", 0b01)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill drain edge not observed")
    _expect(sim, "valid_o", 0b01, "stream_xbar spill should keep output 0 valid after draining the first buffered item")
    _expect(sim, "data_o", 0x0055, "stream_xbar spill should surface the second buffered contender after drain")
    _expect(sim, "idx_o", 0b0001, "stream_xbar spill should surface the second contender index after drain")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _check_nospill(design, engine)
        _check_spill(design, engine)
    except Exception as exc:
        print(f"  FAIL stream_xbar python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_xbar python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_xbar example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_xbar example...")
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
