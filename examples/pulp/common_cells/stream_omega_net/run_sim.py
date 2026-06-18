"""Run the imported common_cells stream_omega_net example.

Run from the repository root:

    uv run python examples/pulp/common_cells/stream_omega_net/run_sim.py
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
TB_FILE = SCRIPT_DIR / "tb" / "so_tb.sv"
FILES = [
    str(RTL_DIR / "spill_register_flushable.sv"),
    str(RTL_DIR / "spill_register.sv"),
    str(RTL_DIR / "stream_omega_net.sv"),
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


def _pack_inputs(d0: int, d1: int, d2: int, d3: int) -> int:
    return (d3 << 24) | (d2 << 16) | (d1 << 8) | d0


def _pack_selects(s0: int, s1: int, s2: int, s3: int) -> int:
    return (s3 << 6) | (s2 << 4) | (s1 << 2) | s0


def _pack_indices(i0: int, i1: int, i2: int, i3: int) -> int:
    return (i3 << 6) | (i2 << 4) | (i1 << 2) | i0


def _pulse_flush(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    step_drive(sim, engine, "flush", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "flush edge not observed")
    step_drive(sim, engine, "flush", 0)
    _settle_drives(sim, engine)


def _check_nospill(design, engine: str) -> None:
    sim = _make_step_sim(design, "so0_tb", engine)

    _expect(sim, "valid_o", 0, "stream_omega_net no-spill should be idle after reset")
    _expect(sim, "ready_o", 0, "stream_omega_net no-spill should not assert ready without valids")

    step_drive(sim, engine, "ready_i", 0b1111)
    step_drive(sim, engine, "data_i", _pack_inputs(0xA0, 0xB1, 0xC2, 0xD3))
    step_drive(sim, engine, "sel_i", _pack_selects(0, 2, 1, 3))
    step_drive(sim, engine, "valid_i", 0b1111)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b1111, "stream_omega_net no-spill should route all four outputs independently")
    _expect(sim, "data_o", 0xD3B1C2A0, "stream_omega_net no-spill payload routing mismatch")
    _expect(sim, "idx_o", _pack_indices(0, 2, 1, 3), "stream_omega_net no-spill idx routing mismatch")
    _expect(sim, "ready_o", 0b1111, "stream_omega_net no-spill should ready every selected input")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "independent-routing edge not observed")

    step_drive(sim, engine, "ready_i", 0b0001)
    step_drive(sim, engine, "data_i", _pack_inputs(0x10, 0x21, 0x32, 0x43))
    step_drive(sim, engine, "sel_i", _pack_selects(0, 0, 3, 3))
    step_drive(sim, engine, "valid_i", 0b0011)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b0001, "stream_omega_net no-spill should drive only output 0 for first-stage contention")
    _expect(
        sim, "data_o", 0x00000010, "stream_omega_net no-spill should choose input 0 first in first-stage contention"
    )
    _expect(sim, "idx_o", _pack_indices(0, 0, 0, 0), "stream_omega_net no-spill should report input 0 first")
    _expect(sim, "ready_o", 0b0001, "stream_omega_net no-spill should ready only the first-stage winner")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "first-stage contention edge not observed")

    _expect(
        sim, "data_o", 0x00000021, "stream_omega_net no-spill should rotate to input 1 on the next first-stage grant"
    )
    _expect(
        sim,
        "idx_o",
        _pack_indices(1, 0, 0, 0),
        "stream_omega_net no-spill should report input 1 after first-stage rotation",
    )
    _expect(sim, "ready_o", 0b0010, "stream_omega_net no-spill should ready the second first-stage contender")

    _pulse_flush(sim, engine)

    step_drive(sim, engine, "ready_i", 0b0001)
    step_drive(sim, engine, "data_i", _pack_inputs(0x54, 0x65, 0x76, 0x87))
    step_drive(sim, engine, "sel_i", _pack_selects(0, 3, 0, 3))
    step_drive(sim, engine, "valid_i", 0b0101)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b0001, "stream_omega_net no-spill should drive only output 0 for second-stage contention")
    _expect(
        sim, "data_o", 0x00000054, "stream_omega_net no-spill should choose input 0 first in second-stage contention"
    )
    _expect(
        sim,
        "idx_o",
        _pack_indices(0, 0, 0, 0),
        "stream_omega_net no-spill should report input 0 first in second-stage contention",
    )
    _expect(sim, "ready_o", 0b0001, "stream_omega_net no-spill should ready the first second-stage contender")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "second-stage contention edge not observed")

    _expect(
        sim, "data_o", 0x00000076, "stream_omega_net no-spill should rotate to input 2 on the next second-stage grant"
    )
    _expect(
        sim,
        "idx_o",
        _pack_indices(2, 0, 0, 0),
        "stream_omega_net no-spill should report input 2 after second-stage rotation",
    )
    _expect(sim, "ready_o", 0b0100, "stream_omega_net no-spill should ready the second-stage contender after rotation")

    _pulse_flush(sim, engine)

    step_drive(sim, engine, "ready_i", 0b0001)
    step_drive(sim, engine, "data_i", _pack_inputs(0x54, 0x65, 0x76, 0x87))
    step_drive(sim, engine, "sel_i", _pack_selects(0, 3, 0, 3))
    step_drive(sim, engine, "valid_i", 0b0101)
    _settle_drives(sim, engine)
    _expect(sim, "data_o", 0x00000054, "stream_omega_net no-spill flush should restore second-stage priority")
    _expect(
        sim,
        "idx_o",
        _pack_indices(0, 0, 0, 0),
        "stream_omega_net no-spill flush should restore second-stage input index",
    )


def _check_spill(design, engine: str) -> None:
    sim = _make_step_sim(design, "so1_tb", engine)

    step_drive(sim, engine, "ready_i", 0b0000)
    step_drive(sim, engine, "data_i", _pack_inputs(0x44, 0x00, 0x00, 0x00))
    step_drive(sim, engine, "sel_i", _pack_selects(0, 0, 0, 0))
    step_drive(sim, engine, "valid_i", 0b0001)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b0000, "stream_omega_net spill outputs should stay empty before capture")
    _expect(sim, "ready_o", 0b0001, "stream_omega_net spill should initially accept the routed input")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill first capture edge not observed")
    _expect(sim, "valid_o", 0b0000, "stream_omega_net spill should still be internal after the first stage capture")
    step_drive(sim, engine, "valid_i", 0b0000)
    _settle_drives(sim, engine)

    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second capture edge not observed")
    _expect(
        sim, "valid_o", 0b0001, "stream_omega_net spill should surface the routed item after the second stage capture"
    )
    _expect(sim, "data_o", 0x00000044, "stream_omega_net spill should preserve payload while stalled")
    _expect(
        sim, "idx_o", _pack_indices(0, 0, 0, 0), "stream_omega_net spill should preserve source index while stalled"
    )

    _expect(sim, "valid_o", 0b0001, "stream_omega_net spill should keep output valid until ready")

    step_drive(sim, engine, "ready_i", 0b0001)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill drain edge not observed")
    _expect(sim, "valid_o", 0b0000, "stream_omega_net spill should drain once the sink is ready")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _check_nospill(design, engine)
        _check_spill(design, engine)
    except Exception as exc:
        print(f"  FAIL stream_omega_net python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS stream_omega_net python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [Path(path) for path in FILES if not Path(path).exists()]
    if missing:
        print("stream_omega_net example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing stream_omega_net example...")
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
