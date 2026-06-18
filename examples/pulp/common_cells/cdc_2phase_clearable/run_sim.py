"""Run the imported common_cells cdc_2phase_clearable example.

Run from the repository root:

    uv run python examples/pulp/common_cells/cdc_2phase_clearable/run_sim.py
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
    str(SCRIPT_DIR / "rtl" / "cdc_2phase_clearable.sv"),
    str(SCRIPT_DIR / "tb" / "cdc_2phase_clearable_tb_local.sv"),
]
MAX_TIME = 2200
ENGINES = available_engines()
SRC_CLEAR_PAYLOAD = 0x11
SRC_RECOVERY_PAYLOAD = 0x22
DST_CLEAR_PAYLOAD = 0x33
DST_RECOVERY_PAYLOAD = 0x44
ASYNC_RESET_PAYLOAD = 0x55
ASYNC_RECOVERY_PAYLOAD = 0x66
DST_ASYNC_RESET_PAYLOAD = 0x77
DST_ASYNC_RECOVERY_PAYLOAD = 0x88


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
        step_eval_now(sim, "src_clk_i")


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


def _make_step_sim(design, engine: str, top_name: str = "cdc_2phase_clearable_tb_local") -> Simulator:
    top = design.get_module(top_name)
    if top is None:
        raise RuntimeError(f"Top module '{top_name}' not found")

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    for signal_name, value in [
        ("src_clk_i", 0),
        ("dst_clk_i", 0),
        ("src_rst_ni", 0),
        ("dst_rst_ni", 0),
        ("src_clear_i", 0),
        ("dst_clear_i", 0),
        ("src_data_i", 0),
        ("src_valid_i", 0),
        ("dst_ready_i", 0),
    ]:
        step_drive(sim, engine, signal_name, value)
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
    _expect(sim, "src_ready_o", 1, "source should be ready after reset")
    _expect(sim, "dst_valid_o", 0, "destination should be idle after reset")
    _expect(sim, "src_clear_pending_o", 0, "source clear-pending should start low after reset")
    _expect(sim, "dst_clear_pending_o", 0, "destination clear-pending should start low after reset")


def _release_async_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 45)
    _expect(sim, "dst_valid_o", 0, "destination should stay idle immediately after async-reset release")
    _run_until_condition(
        sim,
        260,
        lambda s: _read_int(s, "src_clear_pending_o") == 1,
        "async-reset release never raised source clear-pending",
    )
    _run_until_condition(
        sim,
        360,
        lambda s: _read_int(s, "dst_clear_pending_o") == 1,
        "async-reset release never raised destination clear-pending",
    )
    _run_until_condition(
        sim,
        1180,
        lambda s: (
            _read_int(s, "src_clear_pending_o") == 0
            and _read_int(s, "dst_clear_pending_o") == 0
            and _read_int(s, "src_ready_o") == 1
            and _read_int(s, "dst_valid_o") == 0
        ),
        "async-reset startup clear sequence never completed cleanly",
    )


def _send_transfer(sim: Simulator, engine: str, payload: int, edge_limit: int, label: str) -> None:
    step_drive(sim, engine, "src_data_i", payload)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", edge_limit, f"{label} source write edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine)


def _recover_clean_transfer(
    sim: Simulator,
    engine: str,
    *,
    payload: int,
    limits: tuple[int, int, int],
    label: str,
) -> None:
    send_limit, visible_limit, drain_limit = limits
    _send_transfer(sim, engine, payload, send_limit, label)
    _run_until_condition(
        sim,
        visible_limit,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == payload,
        f"{label} never became visible at the destination",
    )
    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        drain_limit,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        f"{label} never drained from the destination",
    )
    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        drain_limit + 120,
        lambda s: _read_int(s, "src_ready_o") == 1,
        f"{label} never restored source ready after acknowledgement",
    )


def _run_src_clear_scenario(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    _release_reset(sim, engine)

    _send_transfer(sim, engine, SRC_CLEAR_PAYLOAD, 90, "source-clear first transfer")
    _run_until_condition(
        sim,
        180,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == SRC_CLEAR_PAYLOAD,
        "source-clear first transfer never became visible at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should remain blocked while the pre-clear transfer is pending")

    step_drive(sim, engine, "src_clear_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", 220, "source clear edge not observed")
    step_drive(sim, engine, "src_clear_i", 0)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        320,
        lambda s: _read_int(s, "src_clear_pending_o") == 1,
        "source clear never raised src_clear_pending_o",
    )
    _run_until_condition(
        sim,
        420,
        lambda s: _read_int(s, "dst_clear_pending_o") == 1,
        "source clear never propagated pending state into the destination domain",
    )
    _run_until_condition(
        sim,
        760,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "source clear never withdrew the stale destination valid state",
    )
    _run_until_condition(
        sim,
        1180,
        lambda s: (
            _read_int(s, "src_clear_pending_o") == 0
            and _read_int(s, "dst_clear_pending_o") == 0
            and _read_int(s, "src_ready_o") == 1
        ),
        "source clear sequence never completed cleanly",
    )

    _recover_clean_transfer(
        sim,
        engine,
        payload=SRC_RECOVERY_PAYLOAD,
        limits=(1240, 1330, 1385),
        label="post-source-clear recovery transfer",
    )


def _run_dst_clear_scenario(design, engine: str) -> None:
    sim = _make_step_sim(design, engine)
    _release_reset(sim, engine)

    _send_transfer(sim, engine, DST_CLEAR_PAYLOAD, 90, "destination-clear first transfer")
    _run_until_condition(
        sim,
        180,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == DST_CLEAR_PAYLOAD,
        "destination-clear first transfer never became visible at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should remain blocked while the destination-clear transfer is pending")

    step_drive(sim, engine, "dst_clear_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", 220, "destination clear edge not observed")
    step_drive(sim, engine, "dst_clear_i", 0)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        320,
        lambda s: _read_int(s, "dst_clear_pending_o") == 1,
        "destination clear never raised dst_clear_pending_o",
    )
    _run_until_condition(
        sim,
        420,
        lambda s: _read_int(s, "src_clear_pending_o") == 1,
        "destination clear never propagated pending state into the source domain",
    )
    _run_until_condition(
        sim,
        760,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "destination clear never withdrew the visible stalled payload",
    )
    _run_until_condition(
        sim,
        1180,
        lambda s: (
            _read_int(s, "src_clear_pending_o") == 0
            and _read_int(s, "dst_clear_pending_o") == 0
            and _read_int(s, "src_ready_o") == 1
        ),
        "destination clear sequence never completed cleanly",
    )

    _recover_clean_transfer(
        sim,
        engine,
        payload=DST_RECOVERY_PAYLOAD,
        limits=(1240, 1330, 1385),
        label="post-destination-clear recovery transfer",
    )


def _run_async_reset_scenario(design, engine: str) -> None:
    sim = _make_step_sim(design, engine, top_name="cdc_2phase_clearable_async_reset_tb_local")
    _release_async_reset(sim, engine)

    _send_transfer(sim, engine, ASYNC_RESET_PAYLOAD, 1240, "pre-async-reset transfer")
    _run_until_condition(
        sim,
        1330,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == ASYNC_RESET_PAYLOAD,
        "pre-async-reset transfer never became visible at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should remain blocked while the async-reset transfer is pending")

    step_drive(sim, engine, "src_rst_ni", 0)
    _settle_drives(sim, engine)
    step_run_until(sim, sim.time + 24)
    step_drive(sim, engine, "src_rst_ni", 1)
    _settle_drives(sim, engine)

    reset_release_time = sim.time
    _run_until_condition(
        sim,
        reset_release_time + 220,
        lambda s: _read_int(s, "src_clear_pending_o") == 1,
        "source async reset never raised local clear-pending",
    )
    _run_until_condition(
        sim,
        reset_release_time + 320,
        lambda s: _read_int(s, "dst_clear_pending_o") == 1,
        "source async reset never propagated clear-pending into the destination domain",
    )
    _run_until_condition(
        sim,
        reset_release_time + 760,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "source async reset never withdrew the stale destination payload",
    )
    _run_until_condition(
        sim,
        reset_release_time + 1180,
        lambda s: (
            _read_int(s, "src_clear_pending_o") == 0
            and _read_int(s, "dst_clear_pending_o") == 0
            and _read_int(s, "src_ready_o") == 1
            and _read_int(s, "dst_valid_o") == 0
        ),
        "source async reset clear sequence never completed cleanly",
    )

    recovery_start = sim.time
    _recover_clean_transfer(
        sim,
        engine,
        payload=ASYNC_RECOVERY_PAYLOAD,
        limits=(recovery_start + 80, recovery_start + 180, recovery_start + 260),
        label="post-async-reset recovery transfer",
    )

    sim = _make_step_sim(design, engine, top_name="cdc_2phase_clearable_async_reset_tb_local")
    _release_async_reset(sim, engine)

    _send_transfer(sim, engine, DST_ASYNC_RESET_PAYLOAD, 1240, "pre-destination-async-reset transfer")
    _run_until_condition(
        sim,
        1330,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == DST_ASYNC_RESET_PAYLOAD,
        "pre-destination-async-reset transfer never became visible at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should remain blocked while the destination async-reset transfer is pending")

    step_drive(sim, engine, "dst_rst_ni", 0)
    _settle_drives(sim, engine)
    _expect(sim, "dst_valid_o", 0, "destination async reset should clear visible valid state immediately")
    step_run_until(sim, sim.time + 24)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine)

    reset_release_time = sim.time
    _run_until_condition(
        sim,
        reset_release_time + 220,
        lambda s: _read_int(s, "dst_clear_pending_o") == 1,
        "destination async reset never raised local clear-pending",
    )
    _run_until_condition(
        sim,
        reset_release_time + 320,
        lambda s: _read_int(s, "src_clear_pending_o") == 1,
        "destination async reset never propagated clear-pending into the source domain",
    )
    _run_until_condition(
        sim,
        reset_release_time + 1180,
        lambda s: (
            _read_int(s, "src_clear_pending_o") == 0
            and _read_int(s, "dst_clear_pending_o") == 0
            and _read_int(s, "src_ready_o") == 1
            and _read_int(s, "dst_valid_o") == 0
        ),
        "destination async reset clear sequence never completed cleanly",
    )

    recovery_start = sim.time
    _recover_clean_transfer(
        sim,
        engine,
        payload=DST_ASYNC_RECOVERY_PAYLOAD,
        limits=(recovery_start + 80, recovery_start + 180, recovery_start + 260),
        label="post-destination-async-reset recovery transfer",
    )


def _run_engine(design, engine: str) -> int:
    print(f"\nRunning engine={engine}...")
    t0 = time.time()
    try:
        _run_src_clear_scenario(design, engine)
        _run_dst_clear_scenario(design, engine)
        _run_async_reset_scenario(design, engine)
    except Exception as exc:
        print(f"  FAIL cdc_2phase_clearable python {engine} checks: {exc}")
        print(f"  engine={engine} failed in {time.time() - t0:.2f}s")
        return 1

    print(f"  PASS cdc_2phase_clearable python {engine} checks")
    print(f"  engine={engine} passed in {time.time() - t0:.2f}s")
    return 0


def main() -> int:
    missing = [path for path in map(Path, FILES) if not path.exists()]
    if missing:
        print("cdc_2phase_clearable example is scaffolded but not runnable yet.")
        print("Missing files:")
        for path in missing:
            print(f"  - {path.relative_to(SCRIPT_DIR)}")
        return 1

    os.chdir(SCRIPT_DIR)

    print("Parsing cdc_2phase_clearable example...")
    t0 = time.time()
    try:
        design = parse_files(FILES, preprocess=True, cache_dir=SCRIPT_DIR / "_vtc_cdc_2phase_clearable_pcache")
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
