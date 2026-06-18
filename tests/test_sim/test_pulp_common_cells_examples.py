from __future__ import annotations

import shutil
from pathlib import Path

import pytest

Cython = pytest.importorskip("Cython")

from veriforge.project import parse_files  # noqa: E402
from veriforge.sim.example_runner import display_lines  # noqa: E402
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until  # noqa: E402
from veriforge.sim.testbench import Clock, Simulator  # noqa: E402


_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")


def _engines() -> list[str]:
    engines = ["reference", "vm", "vm-fast"]
    if _has_compiler:
        engines.append("compiled")
    return engines


ENGINES = _engines()
REPO_ROOT = Path(__file__).resolve().parents[2]
POPCOUNT_TOPS = {
    "reference": "popcount_tb_local",
    "vm": "popcount_tb_vm_local",
    "vm-fast": "popcount_tb_vm_local",
    "compiled": "popcount_tb_vm_local",
}
SUB_PER_HASH_TOP = "sub_per_hash_tb_local"
DELTA_COUNTER_TOP = "delta_counter_tb_local"
COUNTER_TOP = "counter_tb_local"
MAX_COUNTER_TOP = "max_counter_tb_local"
CREDIT_COUNTER_TOP = "credit_counter_tb_local"
EDGE_DETECT_TOP = "edge_detect_tb_local"
BINARY_TO_GRAY_TOP = "binary_to_gray_tb_local"
ONEHOT_TO_BIN_TOP = "onehot_to_bin_tb_local"
GRAY_TO_BINARY_TOP = "gray_to_binary_tb_local"
LZC_TOP = "lzc_tb_local"
HEAVISIDE_TOP = "heaviside_tb_local"
READ_TOP = "read_tb_local"
UNREAD_TOP = "unread_tb_local"
CC_ONEHOT_TOP = "cc_onehot_tb_local"
TRIP_COUNTER_TOP = "trip_counter_tb_local"
EXP_BACKOFF_TOP = "exp_backoff_tb_local"
SERIAL_DEGLITCH_TOP = "serial_deglitch_tb_local"
SHIFT_REG_TOP = "shift_reg_tb_local"
PLRU_TREE_TOP = "plru_tree_tb_local"
LFSR_8BIT_TOP = "lfsr_8bit_tb_local"
EDGE_PROPAGATOR_ACK_TOP = "edge_propagator_ack_tb_local"
RING_BUFFER_TOP = "ring_buffer_tb_local"
EDGE_PROPAGATOR_TX_TOP = "edge_propagator_tx_tb_local"
EDGE_PROPAGATOR_RX_TOP = "edge_propagator_rx_tb_local"
ISOCHRONOUS_4PHASE_TOP = "isochronous_4phase_handshake_tb_local"
CDC_RESET_CTRLR_TOP = "cdc_reset_ctrlr_tb_local"
CDC_RESET_CTRLR_ASYNC_TOP = "cdc_reset_ctrlr_async_reset_tb_local"
RSTGEN_BYPASS_TOP = "rstgen_bypass_tb_local"
RSTGEN_TOP = "rstgen_tb_local"
SYNC_TOP = "sync_tb_local"
SYNC_RESET_ONE_TOP = "sync_reset_one_tb_local"
SYNC_WEDGE_TOP = "sync_wedge_tb_local"
ASYNC_ISOLATE_ASSERT_WINDOW = 220
ASYNC_CLEAR_ASSERT_WINDOW = 340
ASYNC_CLEAR_COMPLETE_WINDOW = 820
ASYNC_RELEASE_WINDOW = 420


def _parse_popcount_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "popcount"
    files = [
        str(example / "rtl" / "popcount.sv"),
        str(example / "tb" / "popcount_tb_local.sv"),
        str(example / "tb" / "popcount_tb_vm_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "popcount_pcache")


def _parse_sub_per_hash_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "sub_per_hash"
    files = [
        str(example / "rtl" / "sub_per_hash.sv"),
        str(example / "tb" / "sub_per_hash_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "sub_per_hash_pcache")


def _parse_delta_counter_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "delta_counter"
    files = [
        str(example / "rtl" / "delta_counter.sv"),
        str(example / "tb" / "delta_counter_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "delta_counter_pcache")


def _parse_counter_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "counter"
    files = [
        str(example / "rtl" / "delta_counter.sv"),
        str(example / "rtl" / "counter.sv"),
        str(example / "tb" / "counter_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "counter_pcache")


def _parse_max_counter_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "max_counter"
    files = [
        str(example / "rtl" / "delta_counter.sv"),
        str(example / "rtl" / "max_counter.sv"),
        str(example / "tb" / "max_counter_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "max_counter_pcache")


def _parse_credit_counter_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "credit_counter"
    files = [
        str(example / "rtl" / "credit_counter.sv"),
        str(example / "tb" / "credit_counter_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "credit_counter_pcache")


def _parse_edge_detect_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "edge_detect"
    files = [
        str(example / "rtl" / "sync.sv"),
        str(example / "rtl" / "sync_wedge.sv"),
        str(example / "rtl" / "edge_detect.sv"),
        str(example / "tb" / "edge_detect_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "edge_detect_pcache")


def _parse_binary_to_gray_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "binary_to_gray"
    files = [
        str(example / "rtl" / "binary_to_gray.sv"),
        str(example / "tb" / "binary_to_gray_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "binary_to_gray_pcache")


def _parse_onehot_to_bin_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "onehot_to_bin"
    files = [
        str(example / "rtl" / "onehot_to_bin.sv"),
        str(example / "tb" / "onehot_to_bin_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "onehot_to_bin_pcache")


def _parse_gray_to_binary_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "gray_to_binary"
    files = [
        str(example / "rtl" / "gray_to_binary.sv"),
        str(example / "tb" / "gray_to_binary_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "gray_to_binary_pcache")


def _parse_lzc_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "lzc"
    files = [
        str(example / "rtl" / "lzc.sv"),
        str(example / "tb" / "lzc_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "lzc_pcache")


def _parse_heaviside_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "heaviside"
    files = [
        str(example / "rtl" / "heaviside.sv"),
        str(example / "tb" / "heaviside_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "heaviside_pcache")


def _parse_read_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "read"
    files = [
        str(example / "rtl" / "read.sv"),
        str(example / "tb" / "read_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "read_pcache")


def _parse_unread_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "unread"
    files = [
        str(example / "rtl" / "unread.sv"),
        str(example / "tb" / "unread_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "unread_pcache")


def _parse_cc_onehot_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "cc_onehot"
    files = [
        str(example / "rtl" / "cc_onehot.sv"),
        str(example / "tb" / "cc_onehot_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "cc_onehot_pcache")


def _parse_trip_counter_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "trip_counter"
    files = [
        str(example / "rtl" / "delta_counter.sv"),
        str(example / "rtl" / "trip_counter.sv"),
        str(example / "tb" / "trip_counter_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "trip_counter_pcache")


def _parse_exp_backoff_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "exp_backoff"
    files = [
        str(example / "rtl" / "exp_backoff.sv"),
        str(example / "tb" / "exp_backoff_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "exp_backoff_pcache")


def _parse_serial_deglitch_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "serial_deglitch"
    files = [
        str(example / "rtl" / "serial_deglitch.sv"),
        str(example / "tb" / "serial_deglitch_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "serial_deglitch_pcache")


def _parse_shift_reg_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "shift_reg"
    files = [
        str(example / "rtl" / "shift_reg_gated.sv"),
        str(example / "rtl" / "shift_reg.sv"),
        str(example / "tb" / "shift_reg_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "shift_reg_pcache")


def _parse_plru_tree_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "plru_tree"
    files = [
        str(example / "rtl" / "plru_tree.sv"),
        str(example / "tb" / "plru_tree_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "plru_tree_pcache")


def _parse_lfsr_8bit_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "lfsr_8bit"
    files = [
        str(example / "rtl" / "lfsr_8bit.sv"),
        str(example / "tb" / "lfsr_8bit_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "lfsr_8bit_pcache")


def _parse_edge_propagator_ack_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "edge_propagator_ack"
    files = [
        str(example / "rtl" / "sync.sv"),
        str(example / "rtl" / "sync_wedge.sv"),
        str(example / "rtl" / "edge_propagator_ack.sv"),
        str(example / "tb" / "edge_propagator_ack_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "edge_propagator_ack_pcache")


def _parse_ring_buffer_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "ring_buffer"
    files = [
        str(example / "rtl" / "ring_buffer.sv"),
        str(example / "tb" / "ring_buffer_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "ring_buffer_pcache")


def _parse_edge_propagator_tx_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "edge_propagator_tx"
    files = [
        str(example / "rtl" / "edge_propagator_tx.sv"),
        str(example / "tb" / "edge_propagator_tx_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "edge_propagator_tx_pcache")


def _parse_edge_propagator_rx_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "edge_propagator_rx"
    files = [
        str(example / "rtl" / "sync.sv"),
        str(example / "rtl" / "sync_wedge.sv"),
        str(example / "rtl" / "edge_propagator_rx.sv"),
        str(example / "tb" / "edge_propagator_rx_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "edge_propagator_rx_pcache")


def _parse_isochronous_4phase_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "isochronous_4phase_handshake"
    files = [
        str(example / "rtl" / "isochronous_4phase_handshake.sv"),
        str(example / "tb" / "isochronous_4phase_handshake_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "isochronous_4phase_pcache")


def _parse_cdc_reset_ctrlr_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "cdc_reset_ctrlr"
    files = [
        str(example / "rtl" / "sync.sv"),
        str(example / "rtl" / "cdc_4phase_ctrl.sv"),
        str(example / "rtl" / "cdc_reset_ctrlr.sv"),
        str(example / "tb" / "cdc_reset_ctrlr_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "cdc_reset_ctrlr_pcache")


def _parse_rstgen_bypass_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "rstgen_bypass"
    files = [
        str(example / "rtl" / "tc_clk_mux2.sv"),
        str(example / "rtl" / "rstgen_bypass.sv"),
        str(example / "tb" / "rstgen_bypass_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "rstgen_bypass_pcache")


def _parse_rstgen_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "rstgen"
    files = [
        str(example / "rtl" / "tc_clk_mux2.sv"),
        str(example / "rtl" / "rstgen_bypass.sv"),
        str(example / "rtl" / "rstgen.sv"),
        str(example / "tb" / "rstgen_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "rstgen_pcache")


def _parse_sync_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "sync"
    files = [
        str(example / "rtl" / "sync.sv"),
        str(example / "tb" / "sync_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "sync_pcache")


def _parse_sync_wedge_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "sync_wedge"
    files = [
        str(example / "rtl" / "sync.sv"),
        str(example / "rtl" / "sync_wedge.sv"),
        str(example / "tb" / "sync_wedge_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "sync_wedge_pcache")


def _run_pass_marker_test(design, engine: str, top_name: str, max_time: int) -> None:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=max_time)
    lines = display_lines(sim)
    assert not any("FAIL" in line for line in lines), f"{top_name} failed on {engine}: {lines}"
    assert any("PASS" in line for line in lines), f"{top_name} produced no PASS marker on {engine}: {lines}"


def _read_int(sim: Simulator, signal_name: str) -> int:
    raw = sim.read(signal_name)
    try:
        return int(raw)
    except Exception as exc:
        raise RuntimeError(f"{signal_name} is not fully resolved: {raw}") from exc


def _expect(sim: Simulator, signal_name: str, expected: int, message: str) -> None:
    actual = _read_int(sim, signal_name)
    assert actual == expected, f"{message}: expected {expected:#x}, got {actual:#x}"


def _settle_drives(sim: Simulator, engine: str, clock_name: str = "src_clk_i") -> None:
    if engine == "reference":
        sim.run(max_time=0)
    else:
        step_eval_now(sim, clock_name)


def _run_until_condition(sim: Simulator, target_time: int, predicate, message: str) -> None:
    while sim.time < target_time:
        if predicate(sim):
            return
        assert sim.run_step(), f"stepped engine stopped before {message}"
    assert predicate(sim), message


def _run_until_rising_edge(sim: Simulator, signal_name: str, target_time: int, message: str) -> None:
    previous = _read_int(sim, signal_name)
    while sim.time < target_time:
        assert sim.run_step(), f"stepped engine stopped before {message}"
        current = _read_int(sim, signal_name)
        if previous == 0 and current == 1:
            return
        previous = current
    raise AssertionError(message)


def _make_isochronous_4phase_sim(design, engine: str) -> Simulator:
    top = design.get_module(ISOCHRONOUS_4PHASE_TOP)
    assert top is not None, f"Top module {ISOCHRONOUS_4PHASE_TOP!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "src_clk_i", 0)
    step_drive(sim, engine, "dst_clk_i", 0)
    step_drive(sim, engine, "src_rst_ni", 0)
    step_drive(sim, engine, "dst_rst_ni", 0)
    step_drive(sim, engine, "src_valid_i", 0)
    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), 220)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=20), 220)
    _settle_drives(sim, engine)
    return sim


def _release_isochronous_4phase_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 36)
    _expect(sim, "src_ready_o", 1, "source should be ready after reset")
    _expect(sim, "dst_valid_o", 0, "destination should be idle after reset")


def _make_cdc_reset_ctrlr_sim(design, engine: str) -> Simulator:
    return _make_cdc_reset_ctrlr_sim_for_top(design, CDC_RESET_CTRLR_TOP, engine)


def _make_cdc_reset_ctrlr_sim_for_top(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

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
    sim._schedule_clock_events(Clock(sim.signal("a_clk_i"), period=10), 2400)
    sim._schedule_clock_events(Clock(sim.signal("b_clk_i"), period=14), 2400)
    _settle_drives(sim, engine)
    return sim


def _release_cdc_reset_ctrlr_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "a_rst_ni", 1)
    step_drive(sim, engine, "b_rst_ni", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 45)
    _expect(sim, "a_isolate_o", 0, "a side should start idle after reset release")
    _expect(sim, "a_clear_o", 0, "a side clear should start low after reset release")
    _expect(sim, "b_isolate_o", 0, "b side should start idle after reset release")
    _expect(sim, "b_clear_o", 0, "b side clear should start low after reset release")


def _release_cdc_reset_ctrlr_async_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "a_rst_ni", 1)
    step_drive(sim, engine, "b_rst_ni", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 45)
    _run_until_condition(
        sim,
        sim.time + ASYNC_ISOLATE_ASSERT_WINDOW,
        lambda s: _read_int(s, "a_isolate_o") == 1 and _read_int(s, "b_isolate_o") == 1,
        "startup async reset never asserted isolate on both sides",
    )
    _expect(sim, "a_clear_o", 0, "startup async reset a-side clear should stay low before isolate acknowledgements")
    _expect(sim, "b_clear_o", 0, "startup async reset b-side clear should stay low before isolate acknowledgements")
    step_drive(sim, engine, "a_isolate_ack_i", 1)
    step_drive(sim, engine, "b_isolate_ack_i", 1)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + ASYNC_CLEAR_ASSERT_WINDOW,
        lambda s: _read_int(s, "a_clear_o") == 1 and _read_int(s, "b_clear_o") == 1,
        "startup async reset never reached clear on both sides",
    )
    step_drive(sim, engine, "a_clear_ack_i", 1)
    step_drive(sim, engine, "b_clear_ack_i", 1)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + ASYNC_CLEAR_COMPLETE_WINDOW,
        lambda s: _read_int(s, "a_clear_o") == 0 and _read_int(s, "b_clear_o") == 0,
        "startup async reset clear phase never completed",
    )
    _run_until_condition(
        sim,
        sim.time + ASYNC_RELEASE_WINDOW,
        lambda s: _read_int(s, "a_isolate_o") == 0 and _read_int(s, "b_isolate_o") == 0,
        "startup async reset isolate phase never released",
    )
    step_drive(sim, engine, "a_isolate_ack_i", 0)
    step_drive(sim, engine, "b_isolate_ack_i", 0)
    step_drive(sim, engine, "a_clear_ack_i", 0)
    step_drive(sim, engine, "b_clear_ack_i", 0)
    _settle_drives(sim, engine)


def _make_rstgen_bypass_sim(design, engine: str) -> Simulator:
    top = design.get_module(RSTGEN_BYPASS_TOP)
    assert top is not None, f"Top module {RSTGEN_BYPASS_TOP!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk_i", 0)
    step_drive(sim, engine, "rst_ni", 1)
    step_drive(sim, engine, "rst_test_mode_ni", 1)
    step_drive(sim, engine, "test_mode_i", 0)
    _settle_drives(sim, engine, "clk_i")
    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine, "clk_i")
    sim._schedule_clock_events(Clock(sim.signal("clk_i"), period=10), 120)
    _settle_drives(sim, engine, "clk_i")
    return sim


def _make_rstgen_sim(design, engine: str) -> Simulator:
    top = design.get_module(RSTGEN_TOP)
    assert top is not None, f"Top module {RSTGEN_TOP!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk_i", 0)
    step_drive(sim, engine, "rst_ni", 1)
    step_drive(sim, engine, "test_mode_i", 0)
    _settle_drives(sim, engine, "clk_i")
    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine, "clk_i")
    sim._schedule_clock_events(Clock(sim.signal("clk_i"), period=10), 170)
    _settle_drives(sim, engine, "clk_i")
    return sim


def _make_sync_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk_i", 0)
    step_drive(sim, engine, "rst_ni", 1)
    step_drive(sim, engine, "serial_i", 0)
    _settle_drives(sim, engine, "clk_i")
    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine, "clk_i")
    sim._schedule_clock_events(Clock(sim.signal("clk_i"), period=10), 180)
    _settle_drives(sim, engine, "clk_i")
    return sim


def _make_sync_wedge_sim(design, engine: str) -> Simulator:
    top = design.get_module(SYNC_WEDGE_TOP)
    assert top is not None, f"Top module {SYNC_WEDGE_TOP!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk_i", 0)
    step_drive(sim, engine, "rst_ni", 1)
    step_drive(sim, engine, "en_i", 1)
    step_drive(sim, engine, "serial_i", 0)
    _settle_drives(sim, engine, "clk_i")
    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine, "clk_i")
    sim._schedule_clock_events(Clock(sim.signal("clk_i"), period=10), 190)
    _settle_drives(sim, engine, "clk_i")
    return sim


@pytest.mark.parametrize("engine", ENGINES)
def test_popcount_cross_engine(tmp_path, engine):
    design = _parse_popcount_design(tmp_path)
    _run_pass_marker_test(design, engine, POPCOUNT_TOPS[engine], 100)


@pytest.mark.parametrize("engine", ENGINES)
def test_sub_per_hash_cross_engine(tmp_path, engine):
    design = _parse_sub_per_hash_design(tmp_path)
    _run_pass_marker_test(design, engine, SUB_PER_HASH_TOP, 20)


@pytest.mark.parametrize("engine", ENGINES)
def test_delta_counter_cross_engine(tmp_path, engine):
    design = _parse_delta_counter_design(tmp_path)
    _run_pass_marker_test(design, engine, DELTA_COUNTER_TOP, 120)


@pytest.mark.parametrize("engine", ENGINES)
def test_counter_cross_engine(tmp_path, engine):
    design = _parse_counter_design(tmp_path)
    _run_pass_marker_test(design, engine, COUNTER_TOP, 120)


@pytest.mark.parametrize("engine", ENGINES)
def test_max_counter_cross_engine(tmp_path, engine):
    design = _parse_max_counter_design(tmp_path)
    _run_pass_marker_test(design, engine, MAX_COUNTER_TOP, 160)


@pytest.mark.parametrize("engine", ENGINES)
def test_credit_counter_cross_engine(tmp_path, engine):
    design = _parse_credit_counter_design(tmp_path)
    _run_pass_marker_test(design, engine, CREDIT_COUNTER_TOP, 160)


@pytest.mark.parametrize("engine", ENGINES)
def test_edge_detect_cross_engine(tmp_path, engine):
    design = _parse_edge_detect_design(tmp_path)
    _run_pass_marker_test(design, engine, EDGE_DETECT_TOP, 140)


@pytest.mark.parametrize("engine", ENGINES)
def test_binary_to_gray_cross_engine(tmp_path, engine):
    design = _parse_binary_to_gray_design(tmp_path)
    _run_pass_marker_test(design, engine, BINARY_TO_GRAY_TOP, 20)


@pytest.mark.parametrize("engine", ENGINES)
def test_onehot_to_bin_cross_engine(tmp_path, engine):
    design = _parse_onehot_to_bin_design(tmp_path)
    _run_pass_marker_test(design, engine, ONEHOT_TO_BIN_TOP, 20)


@pytest.mark.parametrize("engine", ENGINES)
def test_gray_to_binary_cross_engine(tmp_path, engine):
    design = _parse_gray_to_binary_design(tmp_path)
    _run_pass_marker_test(design, engine, GRAY_TO_BINARY_TOP, 20)


@pytest.mark.parametrize("engine", ENGINES)
def test_lzc_cross_engine(tmp_path, engine):
    design = _parse_lzc_design(tmp_path)
    _run_pass_marker_test(design, engine, LZC_TOP, 20)


@pytest.mark.parametrize("engine", ENGINES)
def test_heaviside_cross_engine(tmp_path, engine):
    design = _parse_heaviside_design(tmp_path)
    _run_pass_marker_test(design, engine, HEAVISIDE_TOP, 20)


@pytest.mark.parametrize("engine", ENGINES)
def test_read_cross_engine(tmp_path, engine):
    design = _parse_read_design(tmp_path)
    _run_pass_marker_test(design, engine, READ_TOP, 20)


@pytest.mark.parametrize("engine", ENGINES)
def test_unread_cross_engine(tmp_path, engine):
    design = _parse_unread_design(tmp_path)
    _run_pass_marker_test(design, engine, UNREAD_TOP, 20)


@pytest.mark.parametrize("engine", ENGINES)
def test_cc_onehot_cross_engine(tmp_path, engine):
    design = _parse_cc_onehot_design(tmp_path)
    _run_pass_marker_test(design, engine, CC_ONEHOT_TOP, 20)


@pytest.mark.parametrize("engine", ENGINES)
def test_trip_counter_cross_engine(tmp_path, engine):
    design = _parse_trip_counter_design(tmp_path)
    _run_pass_marker_test(design, engine, TRIP_COUNTER_TOP, 120)


@pytest.mark.parametrize("engine", ENGINES)
def test_exp_backoff_cross_engine(tmp_path, engine):
    design = _parse_exp_backoff_design(tmp_path)
    _run_pass_marker_test(design, engine, EXP_BACKOFF_TOP, 120)


@pytest.mark.parametrize("engine", ENGINES)
def test_serial_deglitch_cross_engine(tmp_path, engine):
    design = _parse_serial_deglitch_design(tmp_path)
    _run_pass_marker_test(design, engine, SERIAL_DEGLITCH_TOP, 140)


@pytest.mark.parametrize("engine", ENGINES)
def test_shift_reg_cross_engine(tmp_path, engine):
    design = _parse_shift_reg_design(tmp_path)
    _run_pass_marker_test(design, engine, SHIFT_REG_TOP, 140)


@pytest.mark.parametrize("engine", ENGINES)
def test_plru_tree_cross_engine(tmp_path, engine):
    design = _parse_plru_tree_design(tmp_path)
    _run_pass_marker_test(design, engine, PLRU_TREE_TOP, 120)


@pytest.mark.parametrize("engine", ENGINES)
def test_lfsr_8bit_cross_engine(tmp_path, engine):
    design = _parse_lfsr_8bit_design(tmp_path)
    _run_pass_marker_test(design, engine, LFSR_8BIT_TOP, 120)


@pytest.mark.parametrize("engine", ENGINES)
def test_edge_propagator_ack_cross_engine(tmp_path, engine):
    design = _parse_edge_propagator_ack_design(tmp_path)
    _run_pass_marker_test(design, engine, EDGE_PROPAGATOR_ACK_TOP, 400)


@pytest.mark.parametrize("engine", ENGINES)
def test_ring_buffer_cross_engine(tmp_path, engine):
    design = _parse_ring_buffer_design(tmp_path)
    _run_pass_marker_test(design, engine, RING_BUFFER_TOP, 220)


@pytest.mark.parametrize("engine", ENGINES)
def test_edge_propagator_tx_cross_engine(tmp_path, engine):
    design = _parse_edge_propagator_tx_design(tmp_path)
    _run_pass_marker_test(design, engine, EDGE_PROPAGATOR_TX_TOP, 200)


@pytest.mark.parametrize("engine", ENGINES)
def test_edge_propagator_rx_cross_engine(tmp_path, engine):
    design = _parse_edge_propagator_rx_design(tmp_path)
    _run_pass_marker_test(design, engine, EDGE_PROPAGATOR_RX_TOP, 200)


@pytest.mark.parametrize("engine", ENGINES)
def test_isochronous_4phase_cross_engine(tmp_path, engine):
    design = _parse_isochronous_4phase_design(tmp_path)
    sim = _make_isochronous_4phase_sim(design, engine)
    _release_isochronous_4phase_reset(sim, engine)

    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", 60, "source request edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "src_ready_o", 0, "source ready should drop after a request")
    _expect(sim, "dst_valid_o", 0, "destination should not see a request before a destination edge")

    _run_until_condition(
        sim,
        90,
        lambda s: _read_int(s, "dst_valid_o") == 1,
        "destination never observed the first request",
    )
    _expect(sim, "src_ready_o", 0, "source should remain blocked while the first request is pending")

    _run_until_rising_edge(sim, "dst_clk_i", 110, "second destination edge not observed while stalled")
    _expect(sim, "dst_valid_o", 1, "destination valid should hold while not ready")

    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "dst_clk_i", 130, "destination acknowledge edge not observed")
    _expect(sim, "dst_valid_o", 0, "destination valid should clear after acknowledgement")

    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        160,
        lambda s: _read_int(s, "src_ready_o") == 1,
        "source ready never reopened after the first acknowledgement",
    )

    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "src_clk_i", 180, "second source request edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "src_ready_o", 0, "second request should drop source ready again")

    _run_until_condition(
        sim,
        210,
        lambda s: _read_int(s, "dst_valid_o") == 1,
        "destination never observed the second request",
    )
    _run_until_rising_edge(sim, "dst_clk_i", 220, "second destination acknowledge edge not observed")
    _expect(sim, "dst_valid_o", 0, "second request should clear after destination acknowledgement")


def _run_cdc_reset_ctrlr_round(
    sim: Simulator,
    engine: str,
    *,
    trigger_signal: str,
    trigger_clock: str,
    local_isolate: str,
    remote_isolate: str,
    local_clear: str,
    remote_clear: str,
    local_isolate_ack: str,
    remote_isolate_ack: str,
    local_clear_ack: str,
    remote_clear_ack: str,
    label: str,
) -> None:
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


def _run_cdc_reset_ctrlr_async_round(sim: Simulator, engine: str, *, side: str, label: str) -> None:
    if side == "a":
        reset_signal = "a_rst_ni"
        local_isolate = "a_isolate_o"
        local_clear = "a_clear_o"
    else:
        reset_signal = "b_rst_ni"
        local_isolate = "b_isolate_o"
        local_clear = "b_clear_o"

    step_drive(sim, engine, reset_signal, 0)
    _settle_drives(sim, engine)
    _expect(sim, local_isolate, 1, f"{label} should assert local isolate immediately")
    _expect(sim, local_clear, 0, f"{label} should not assert local clear immediately")
    step_run_until(sim, sim.time + 24)
    step_drive(sim, engine, reset_signal, 1)
    _settle_drives(sim, engine)

    _run_until_condition(
        sim,
        sim.time + ASYNC_ISOLATE_ASSERT_WINDOW,
        lambda s: _read_int(s, "a_isolate_o") == 1 and _read_int(s, "b_isolate_o") == 1,
        f"{label} never asserted isolate on both sides",
    )
    step_drive(sim, engine, "a_isolate_ack_i", 1)
    step_drive(sim, engine, "b_isolate_ack_i", 1)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + ASYNC_CLEAR_ASSERT_WINDOW,
        lambda s: _read_int(s, "a_clear_o") == 1 and _read_int(s, "b_clear_o") == 1,
        f"{label} never reached clear on both sides",
    )
    step_drive(sim, engine, "a_clear_ack_i", 1)
    step_drive(sim, engine, "b_clear_ack_i", 1)
    _settle_drives(sim, engine)
    _run_until_condition(
        sim,
        sim.time + ASYNC_CLEAR_COMPLETE_WINDOW,
        lambda s: _read_int(s, "a_clear_o") == 0 and _read_int(s, "b_clear_o") == 0,
        f"{label} clear phase never completed",
    )
    _run_until_condition(
        sim,
        sim.time + ASYNC_RELEASE_WINDOW,
        lambda s: _read_int(s, "a_isolate_o") == 0 and _read_int(s, "b_isolate_o") == 0,
        f"{label} isolate phase never released",
    )
    step_drive(sim, engine, "a_isolate_ack_i", 0)
    step_drive(sim, engine, "b_isolate_ack_i", 0)
    step_drive(sim, engine, "a_clear_ack_i", 0)
    step_drive(sim, engine, "b_clear_ack_i", 0)
    _settle_drives(sim, engine)


@pytest.mark.parametrize("engine", ENGINES)
def test_cdc_reset_ctrlr_cross_engine(tmp_path, engine):
    design = _parse_cdc_reset_ctrlr_design(tmp_path)
    sim = _make_cdc_reset_ctrlr_sim(design, engine)
    _release_cdc_reset_ctrlr_reset(sim, engine)

    _run_cdc_reset_ctrlr_round(
        sim,
        engine,
        trigger_signal="a_clear_i",
        trigger_clock="a_clk_i",
        local_isolate="a_isolate_o",
        remote_isolate="b_isolate_o",
        local_clear="a_clear_o",
        remote_clear="b_clear_o",
        local_isolate_ack="a_isolate_ack_i",
        remote_isolate_ack="b_isolate_ack_i",
        local_clear_ack="a_clear_ack_i",
        remote_clear_ack="b_clear_ack_i",
        label="a-side clear request",
    )
    _run_cdc_reset_ctrlr_round(
        sim,
        engine,
        trigger_signal="b_clear_i",
        trigger_clock="b_clk_i",
        local_isolate="b_isolate_o",
        remote_isolate="a_isolate_o",
        local_clear="b_clear_o",
        remote_clear="a_clear_o",
        local_isolate_ack="b_isolate_ack_i",
        remote_isolate_ack="a_isolate_ack_i",
        local_clear_ack="b_clear_ack_i",
        remote_clear_ack="a_clear_ack_i",
        label="b-side clear request",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_cdc_reset_ctrlr_async_reset_cross_engine(tmp_path, engine):
    design = _parse_cdc_reset_ctrlr_design(tmp_path)
    sim = _make_cdc_reset_ctrlr_sim_for_top(design, CDC_RESET_CTRLR_ASYNC_TOP, engine)
    _release_cdc_reset_ctrlr_async_reset(sim, engine)

    _run_cdc_reset_ctrlr_async_round(sim, engine, side="a", label="a-side async reset")
    _run_cdc_reset_ctrlr_async_round(sim, engine, side="b", label="b-side async reset")


@pytest.mark.parametrize("engine", ENGINES)
def test_rstgen_bypass_cross_engine(tmp_path, engine):
    design = _parse_rstgen_bypass_design(tmp_path)
    sim = _make_rstgen_bypass_sim(design, engine)

    _expect(sim, "rst_no", 0, "functional reset should hold rst_no low initially")
    _expect(sim, "init_no", 0, "functional reset should hold init_no low initially")

    step_run_until(sim, 31)
    step_drive(sim, engine, "rst_ni", 1)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "rst_no", 0, "synchronized reset output should stay low immediately after release")
    _expect(sim, "init_no", 0, "synchronized init output should stay low immediately after release")

    step_run_until(sim, 60)
    _expect(sim, "rst_no", 0, "rst_no should still be low before the final sync stage fills")
    _expect(sim, "init_no", 0, "init_no should still be low before the final sync stage fills")
    _run_until_condition(
        sim,
        80,
        lambda s: _read_int(s, "rst_no") == 1 and _read_int(s, "init_no") == 1,
        "outputs never asserted after the synchronized release window",
    )

    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "rst_no", 0, "functional reset reassertion should clear rst_no immediately")
    _expect(sim, "init_no", 0, "functional reset reassertion should clear init_no immediately")

    step_drive(sim, engine, "test_mode_i", 1)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "rst_no", 1, "test mode should bypass rst_no immediately from rst_test_mode_ni")
    _expect(sim, "init_no", 1, "test mode should force init_no high immediately")

    step_drive(sim, engine, "rst_test_mode_ni", 0)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "rst_no", 0, "test-mode reset low should clear rst_no immediately")
    _expect(sim, "init_no", 1, "init_no should stay high in test mode even when rst_test_mode_ni is low")

    step_drive(sim, engine, "rst_test_mode_ni", 1)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "rst_no", 1, "test-mode reset high should restore rst_no immediately")
    _expect(sim, "init_no", 1, "init_no should remain high while test mode stays enabled")

    step_drive(sim, engine, "test_mode_i", 0)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "rst_no", 0, "leaving test mode should return rst_no to the functional reset path")
    _expect(sim, "init_no", 0, "leaving test mode should return init_no to the functional reset path")


@pytest.mark.parametrize("engine", ENGINES)
def test_rstgen_cross_engine(tmp_path, engine):
    design = _parse_rstgen_design(tmp_path)
    sim = _make_rstgen_sim(design, engine)

    _expect(sim, "rst_no", 0, "functional reset should hold rst_no low initially")
    _expect(sim, "init_no", 0, "functional reset should hold init_no low initially")

    step_run_until(sim, 31)
    step_drive(sim, engine, "rst_ni", 1)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "rst_no", 0, "synchronized reset output should stay low immediately after release")
    _expect(sim, "init_no", 0, "synchronized init output should stay low immediately after release")

    step_run_until(sim, 60)
    _expect(sim, "rst_no", 0, "rst_no should still be low before the final sync stage fills")
    _expect(sim, "init_no", 0, "init_no should still be low before the final sync stage fills")
    _run_until_condition(
        sim,
        80,
        lambda s: _read_int(s, "rst_no") == 1 and _read_int(s, "init_no") == 1,
        "outputs never asserted after the synchronized release window",
    )

    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "rst_no", 0, "functional reset reassertion should clear rst_no immediately")
    _expect(sim, "init_no", 0, "functional reset reassertion should clear init_no immediately")

    step_drive(sim, engine, "test_mode_i", 1)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "rst_no", 0, "test mode should still reflect rst_ni on rst_no while reset is asserted")
    _expect(sim, "init_no", 1, "test mode should force init_no high even while reset stays asserted")

    step_drive(sim, engine, "rst_ni", 1)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "rst_no", 1, "test mode should bypass rst_no immediately from rst_ni")
    _expect(sim, "init_no", 1, "test mode should keep init_no high after reset release")

    step_run_until(sim, 130)
    _expect(sim, "rst_no", 1, "rst_no should stay high while test mode remains enabled")
    _expect(sim, "init_no", 1, "init_no should stay high while test mode remains enabled")

    step_drive(sim, engine, "test_mode_i", 0)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "rst_no", 1, "leaving test mode should keep rst_no high after the sync path refills")
    _expect(sim, "init_no", 1, "leaving test mode should keep init_no high after the sync path refills")


@pytest.mark.parametrize("engine", ENGINES)
def test_sync_cross_engine(tmp_path, engine):
    design = _parse_sync_design(tmp_path)

    sim = _make_sync_sim(design, SYNC_TOP, engine)
    _expect(sim, "serial_o", 0, "default reset value should drive serial_o low under reset")

    step_run_until(sim, 31)
    step_drive(sim, engine, "rst_ni", 1)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "serial_o", 0, "default reset case should stay low immediately after release")

    _run_until_condition(
        sim,
        sim.time + 20,
        lambda s: _read_int(s, "clk_i") == 0,
        "clock never reached a low phase before the rising-latency drive",
    )
    step_drive(sim, engine, "serial_i", 1)
    _settle_drives(sim, engine, "clk_i")
    _run_until_rising_edge(sim, "clk_i", 60, "first rising sample edge not observed")
    _expect(sim, "serial_o", 0, "stage 1 should not reach the output immediately")
    _run_until_rising_edge(sim, "clk_i", 80, "second rising sample edge not observed")
    _expect(sim, "serial_o", 0, "stage 2 should not reach the output immediately")
    _run_until_rising_edge(sim, "clk_i", 120, "third rising sample edge not observed")
    _expect(sim, "serial_o", 1, "three-stage synchronizer should propagate a rising input on the third edge")

    _run_until_condition(
        sim,
        sim.time + 20,
        lambda s: _read_int(s, "clk_i") == 0,
        "clock never reached a low phase before the falling-latency drive",
    )
    step_drive(sim, engine, "serial_i", 0)
    _settle_drives(sim, engine, "clk_i")
    _run_until_rising_edge(sim, "clk_i", 140, "first falling sample edge not observed")
    _expect(sim, "serial_o", 1, "output should hold high for the first falling sample edge")
    _run_until_rising_edge(sim, "clk_i", 160, "second falling sample edge not observed")
    _expect(sim, "serial_o", 1, "output should hold high for the second falling sample edge")
    _run_until_rising_edge(sim, "clk_i", 180, "third falling sample edge not observed")
    _expect(sim, "serial_o", 0, "three-stage synchronizer should propagate a falling input on the third edge")

    sim = _make_sync_sim(design, SYNC_RESET_ONE_TOP, engine)
    _expect(sim, "serial_o", 1, "RESET_VALUE=1 should drive serial_o high under reset")

    step_run_until(sim, 31)
    step_drive(sim, engine, "rst_ni", 1)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "serial_o", 1, "RESET_VALUE=1 case should stay high immediately after release")
    _run_until_rising_edge(sim, "clk_i", 60, "first drain edge not observed")
    _expect(sim, "serial_o", 1, "RESET_VALUE=1 should hold high on the first drain edge")
    _run_until_rising_edge(sim, "clk_i", 80, "second drain edge not observed")
    _expect(sim, "serial_o", 1, "RESET_VALUE=1 should hold high on the second drain edge")
    _run_until_rising_edge(sim, "clk_i", 120, "third drain edge not observed")
    _expect(sim, "serial_o", 0, "RESET_VALUE=1 should drain to zero on the third edge when serial_i stays low")

    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "serial_o", 1, "async reset reassertion should immediately restore RESET_VALUE=1")


@pytest.mark.parametrize("engine", ENGINES)
def test_sync_wedge_cross_engine(tmp_path, engine):
    design = _parse_sync_wedge_design(tmp_path)
    sim = _make_sync_wedge_sim(design, engine)

    _expect(sim, "serial_o", 0, "reset should clear the sampled serial output")
    _expect(sim, "r_edge_o", 0, "reset should clear the rising-edge pulse")
    _expect(sim, "f_edge_o", 0, "reset should clear the falling-edge pulse")

    step_run_until(sim, 31)
    step_drive(sim, engine, "rst_ni", 1)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "serial_o", 0, "release should not change serial_o immediately")
    _expect(sim, "r_edge_o", 0, "release should not create a rising-edge pulse")
    _expect(sim, "f_edge_o", 0, "release should not create a falling-edge pulse")

    _run_until_condition(
        sim,
        sim.time + 20,
        lambda s: _read_int(s, "clk_i") == 0,
        "clock never reached a low phase before the rising drive",
    )
    step_drive(sim, engine, "serial_i", 1)
    _settle_drives(sim, engine, "clk_i")
    _run_until_rising_edge(sim, "clk_i", 60, "first rising sample edge not observed")
    _expect(sim, "r_edge_o", 0, "first synchronized stage should not pulse immediately")
    _expect(sim, "serial_o", 0, "serial_o should stay low through the first sample edge")
    _run_until_rising_edge(sim, "clk_i", 80, "second rising sample edge not observed")
    _expect(sim, "r_edge_o", 1, "second synchronized stage should produce the rising-edge pulse")
    _expect(sim, "serial_o", 0, "serial_o should update one cycle after the rising pulse")
    _expect(sim, "f_edge_o", 0, "rising transition should not create a falling-edge pulse")
    _run_until_rising_edge(sim, "clk_i", 100, "third rising sample edge not observed")
    _expect(sim, "r_edge_o", 0, "rising-edge pulse should clear on the following sample edge")
    _expect(sim, "serial_o", 1, "serial_o should go high after the rising pulse cycle")

    step_drive(sim, engine, "en_i", 0)
    _settle_drives(sim, engine, "clk_i")
    _run_until_rising_edge(sim, "clk_i", 120, "disabled hold edge not observed")
    _expect(sim, "serial_o", 1, "disabled hold should preserve the sampled high level")
    _expect(sim, "r_edge_o", 0, "disabled hold should not emit a rising-edge pulse")
    _expect(sim, "f_edge_o", 0, "disabled hold should not emit a falling-edge pulse")
    step_drive(sim, engine, "en_i", 1)
    _settle_drives(sim, engine, "clk_i")

    _run_until_condition(
        sim,
        sim.time + 20,
        lambda s: _read_int(s, "clk_i") == 0,
        "clock never reached a low phase before the falling drive",
    )
    step_drive(sim, engine, "serial_i", 0)
    _settle_drives(sim, engine, "clk_i")
    _run_until_rising_edge(sim, "clk_i", 140, "first falling sample edge not observed")
    _expect(sim, "f_edge_o", 0, "first falling sample should not pulse immediately")
    _expect(sim, "serial_o", 1, "serial_o should stay high through the first falling sample")
    _run_until_rising_edge(sim, "clk_i", 160, "second falling sample edge not observed")
    _expect(sim, "f_edge_o", 1, "second synchronized stage should produce the falling-edge pulse")
    _expect(sim, "serial_o", 1, "serial_o should still be high during the falling pulse cycle")
    _expect(sim, "r_edge_o", 0, "falling transition should not create a rising-edge pulse")
    _run_until_rising_edge(sim, "clk_i", 180, "third falling sample edge not observed")
    _expect(sim, "f_edge_o", 0, "falling-edge pulse should clear on the following sample edge")
    _expect(sim, "serial_o", 0, "serial_o should return low after the falling pulse cycle")

    step_drive(sim, engine, "rst_ni", 0)
    _settle_drives(sim, engine, "clk_i")
    _expect(sim, "serial_o", 0, "async reset reassertion should clear serial_o immediately")
    _expect(sim, "r_edge_o", 0, "async reset reassertion should clear r_edge_o immediately")
    _expect(sim, "f_edge_o", 0, "async reset reassertion should clear f_edge_o immediately")
