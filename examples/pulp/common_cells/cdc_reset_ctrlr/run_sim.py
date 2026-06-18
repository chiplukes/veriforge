"""Run the imported common_cells cdc_reset_ctrlr example.

Run from the repository root:

    uv run python examples/pulp/common_cells/cdc_reset_ctrlr/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "sync.sv"),
    str(SCRIPT_DIR / "rtl" / "cdc_4phase_ctrl.sv"),
    str(SCRIPT_DIR / "rtl" / "cdc_reset_ctrlr.sv"),
    str(SCRIPT_DIR / "tb" / "cdc_reset_ctrlr_tb_local.sv"),
]
MAX_TIME = 2400
ENGINES = available_engines()
ASYNC_ISOLATE_ASSERT_WINDOW = 220
ASYNC_CLEAR_ASSERT_WINDOW = 340
ASYNC_CLEAR_COMPLETE_WINDOW = 820
ASYNC_RELEASE_WINDOW = 420


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
        step_eval_now(sim, "a_clk_i")


def _run_until_condition(sim: Simulator, target_time: int, predicate, message: str) -> None:
    while sim.time < target_time:
        if predicate(sim):
            return
        if not sim.run_step():
            raise RuntimeError(f"stepped engine stopped before {message}")
    if not predicate(sim):
        raise RuntimeError(message)


def _run_until_rising_edge(sim: Simulator, signal_name: str, target_time: int, message: str) -> None:
    previous = _read_int(sim, signal_name)
    while sim.time < target_time:
        if not sim.run_step():
            raise RuntimeError(f"stepped engine stopped before {message}")
        current = _read_int(sim, signal_name)
        if previous == 0 and current == 1:
            return
        previous = current
    raise RuntimeError(message)


def _make_step_sim(design, engine: str, top_name: str = "cdc_reset_ctrlr_tb_local") -> Simulator:
    top = design.get_module(top_name)
    if top is None:
        raise RuntimeError(f"Top module '{top_name}' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    for signal_name, value in [
        ("a_clk_i", 0),
        ("b_clk_i", 0),
        ("a_rst_ni", 0),
        ("b_rst_ni", 0),
        ("a_clear_i", 0),
        ("b_clear_i", 0),
        ("a_clear_ack_i", 0),
        ("b_clear_ack_i", 0),
        ("a_isolate_ack_i", 0),
        ("b_isolate_ack_i", 0),
    ]:
        step_drive(sim, engine, signal_name, value)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("a_clk_i"), period=10), MAX_TIME)
    sim._schedule_clock_events(Clock(sim.signal("b_clk_i"), period=14), MAX_TIME)
    _settle_drives(sim, engine)
    return sim


def _release_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "a_rst_ni", 1)
    step_drive(sim, engine, "b_rst_ni", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 45)
    _expect(sim, "a_isolate_o", 0, "a side should start idle after reset release")
    _expect(sim, "a_clear_o", 0, "a side clear should start low after reset release")
    _expect(sim, "b_isolate_o", 0, "b side should start idle after reset release")
    _expect(sim, "b_clear_o", 0, "b side clear should start low after reset release")


def _complete_symmetric_round(sim: Simulator, engine: str, *, label: str) -> None:
    _run_until_condition(
        sim,
        sim.time + ASYNC_ISOLATE_ASSERT_WINDOW,
        lambda s: _read_int(s, "a_isolate_o") == 1 and _read_int(s, "b_isolate_o") == 1,
        f"{label} isolate phase never asserted on both sides",
    )
    _expect(sim, "a_clear_o", 0, f"{label} a-side clear should stay low before isolate acknowledgements")
    _expect(sim, "b_clear_o", 0, f"{label} b-side clear should stay low before isolate acknowledgements")

    step_drive(sim, engine, "a_isolate_ack_i", 1)
    step_drive(sim, engine, "b_isolate_ack_i", 1)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + ASYNC_CLEAR_ASSERT_WINDOW,
        lambda s: _read_int(s, "a_clear_o") == 1 and _read_int(s, "b_clear_o") == 1,
        f"{label} clear phase never started after isolate acknowledgements",
    )
    _expect(sim, "a_isolate_o", 1, f"{label} a-side isolate should stay high during clear")
    _expect(sim, "b_isolate_o", 1, f"{label} b-side isolate should stay high during clear")

    step_drive(sim, engine, "a_clear_ack_i", 1)
    step_drive(sim, engine, "b_clear_ack_i", 1)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + ASYNC_CLEAR_COMPLETE_WINDOW,
        lambda s: _read_int(s, "a_clear_o") == 0 and _read_int(s, "b_clear_o") == 0,
        f"{label} clear phase never completed after clear acknowledgements",
    )
    _run_until_condition(
        sim,
        sim.time + ASYNC_RELEASE_WINDOW,
        lambda s: _read_int(s, "a_isolate_o") == 0 and _read_int(s, "b_isolate_o") == 0,
        f"{label} isolate phase never released after post-clear",
    )

    step_drive(sim, engine, "a_isolate_ack_i", 0)
    step_drive(sim, engine, "b_isolate_ack_i", 0)
    step_drive(sim, engine, "a_clear_ack_i", 0)
    step_drive(sim, engine, "b_clear_ack_i", 0)
    _settle_drives(sim, engine)


def _release_async_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "a_rst_ni", 1)
    step_drive(sim, engine, "b_rst_ni", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 45)
    _complete_symmetric_round(sim, engine, label="startup async reset")


def _run_clear_round(
    sim: Simulator,
    engine: str,
    *,
    side: str,
    label: str,
) -> None:
    other_side = "b" if side == "a" else "a"
    trigger_signal = f"{side}_clear_i"
    trigger_clock = f"{side}_clk_i"
    local_isolate = f"{side}_isolate_o"
    remote_isolate = f"{other_side}_isolate_o"
    local_clear = f"{side}_clear_o"
    remote_clear = f"{other_side}_clear_o"
    local_isolate_ack = f"{side}_isolate_ack_i"
    remote_isolate_ack = f"{other_side}_isolate_ack_i"
    local_clear_ack = f"{side}_clear_ack_i"
    remote_clear_ack = f"{other_side}_clear_ack_i"

    step_drive(sim, engine, trigger_signal, 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, trigger_clock, sim.time + 30, f"{label} trigger edge not observed")
    step_drive(sim, engine, trigger_signal, 0)
    _settle_drives(sim, engine)

    _run_until_condition(
        sim,
        sim.time + 70,
        lambda s: _read_int(s, local_isolate) == 1,
        f"{label} local isolate never asserted",
    )
    _expect(sim, local_clear, 0, f"{label} local clear should stay low before isolate acknowledgements")
    _expect(sim, remote_clear, 0, f"{label} remote clear should stay low before isolate acknowledgements")

    _run_until_condition(
        sim,
        sim.time + 90,
        lambda s: _read_int(s, remote_isolate) == 1,
        f"{label} remote isolate never asserted",
    )
    _expect(sim, local_clear, 0, f"{label} local clear should still be low before isolate acknowledgements")
    _expect(sim, remote_clear, 0, f"{label} remote clear should still be low before isolate acknowledgements")

    step_drive(sim, engine, local_isolate_ack, 1)
    step_drive(sim, engine, remote_isolate_ack, 1)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + 220,
        lambda s: _read_int(s, local_clear) == 1 and _read_int(s, remote_clear) == 1,
        f"{label} clear phase never started after isolate acknowledgements",
    )
    _expect(sim, local_isolate, 1, f"{label} local isolate should stay high during clear")
    _expect(sim, remote_isolate, 1, f"{label} remote isolate should stay high during clear")

    step_drive(sim, engine, local_clear_ack, 1)
    step_drive(sim, engine, remote_clear_ack, 1)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + 220,
        lambda s: _read_int(s, local_clear) == 0 and _read_int(s, remote_clear) == 0,
        f"{label} clear phase never completed after clear acknowledgements",
    )
    _run_until_condition(
        sim,
        sim.time + 220,
        lambda s: _read_int(s, local_isolate) == 0 and _read_int(s, remote_isolate) == 0,
        f"{label} isolate phase never released after post-clear",
    )

    step_drive(sim, engine, local_isolate_ack, 0)
    step_drive(sim, engine, remote_isolate_ack, 0)
    step_drive(sim, engine, local_clear_ack, 0)
    step_drive(sim, engine, remote_clear_ack, 0)
    _settle_drives(sim, engine)


def _run_engine_checks(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    _release_reset(sim, engine)
    _run_clear_round(
        sim,
        engine,
        side="a",
        label="a-side clear request",
    )
    _run_clear_round(
        sim,
        engine,
        side="b",
        label="b-side clear request",
    )

    sim = _make_step_sim(design, engine, top_name="cdc_reset_ctrlr_async_reset_tb_local")
    _release_async_reset(sim, engine)

    step_drive(sim, engine, "a_rst_ni", 0)
    _settle_drives(sim, engine)
    _expect(sim, "a_isolate_o", 1, "a-side async reset should assert isolate immediately")
    _expect(sim, "a_clear_o", 0, "a-side async reset should not assert clear immediately")
    step_run_until(sim, sim.time + 24)
    step_drive(sim, engine, "a_rst_ni", 1)
    _settle_drives(sim, engine)
    _complete_symmetric_round(sim, engine, label="a-side async reset")

    step_drive(sim, engine, "b_rst_ni", 0)
    _settle_drives(sim, engine)
    _expect(sim, "b_isolate_o", 1, "b-side async reset should assert isolate immediately")
    _expect(sim, "b_clear_o", 0, "b-side async reset should not assert clear immediately")
    step_run_until(sim, sim.time + 24)
    step_drive(sim, engine, "b_rst_ni", 1)
    _settle_drives(sim, engine)
    _complete_symmetric_round(sim, engine, label="b-side async reset")


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _run_engine_checks(design, engine)
    except Exception as exc:
        print(f"  FAIL cdc_reset_ctrlr python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS cdc_reset_ctrlr python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [path for path in map(Path, FILES) if not path.exists()]
    if missing:
        print("cdc_reset_ctrlr example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing cdc_reset_ctrlr example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_cdc_reset_ctrlr_pcache")
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
