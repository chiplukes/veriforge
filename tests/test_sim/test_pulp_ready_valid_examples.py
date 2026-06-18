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


def _read_int(sim: Simulator, signal_name: str) -> int:
    raw = sim.read(signal_name)
    try:
        return int(raw)
    except Exception as exc:
        raise RuntimeError(f"{signal_name} is not fully resolved: {raw}") from exc


def _expect(sim: Simulator, signal_name: str, expected: int, message: str) -> None:
    actual = _read_int(sim, signal_name)
    assert actual == expected, f"{message}: expected {expected:#x}, got {actual:#x}"


def _settle_drives(sim: Simulator, engine: str, clock_name: str | None = None) -> None:
    if engine == "reference":
        sim.run(max_time=0)
    elif clock_name is None:
        step_eval_now(sim)
    else:
        step_eval_now(sim, clock_name)


def _run_until_condition(sim: Simulator, limit: int, predicate, message: str) -> None:
    while sim.time < limit:
        if predicate(sim):
            return
        assert sim.run_step(), f"stepped engine stopped before {message}"
    assert predicate(sim), message


def _run_until_rising_edge(sim: Simulator, signal_name: str, limit: int, message: str) -> None:
    previous = _read_int(sim, signal_name)
    while sim.time < limit:
        assert sim.run_step(), f"stepped engine stopped before {message}"
        current = _read_int(sim, signal_name)
        if previous == 0 and current == 1:
            return
        previous = current
    raise AssertionError(message)


def _make_step_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "data_i", 0)
    if "clr" in [port.name for port in top.ports]:
        step_drive(sim, engine, "clr", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 160)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _parse_spill_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "spill_register"
    files = [
        str(example / "rtl" / "spill_register_flushable.sv"),
        str(example / "rtl" / "spill_register.sv"),
        str(example / "tb" / "spill_register_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "spill_pcache")


def _parse_spill_register_flushable_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "spill_register_flushable"
    files = [
        str(example / "rtl" / "spill_register_flushable.sv"),
        str(example / "tb" / "spill_register_flushable_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "spill_register_flushable_pcache")


def _parse_ft_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "fall_through_register"
    files = [
        str(example / "rtl" / "fifo_v3.sv"),
        str(example / "rtl" / "fall_through_register.sv"),
        str(example / "tb" / "ft_reg_tb.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "ft_pcache")


def _parse_stream_register_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_register"
    files = [
        str(example / "rtl" / "stream_register.sv"),
        str(example / "tb" / "stream_register_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_register_pcache")


def _parse_stream_join_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_join"
    files = [
        str(example / "rtl" / "stream_join_dynamic.sv"),
        str(example / "rtl" / "stream_join.sv"),
        str(example / "tb" / "stream_join_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_join_pcache")


def _parse_stream_mux_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_mux"
    files = [
        str(example / "rtl" / "stream_mux.sv"),
        str(example / "tb" / "stream_mux_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_mux_pcache")


def _parse_stream_demux_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_demux"
    files = [
        str(example / "rtl" / "stream_demux.sv"),
        str(example / "tb" / "stream_demux_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_demux_pcache")


def _parse_stream_filter_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_filter"
    files = [
        str(example / "rtl" / "stream_filter.sv"),
        str(example / "tb" / "stream_filter_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_filter_pcache")


def _parse_stream_delay_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_delay"
    files = [
        str(example / "rtl" / "stream_delay.sv"),
        str(example / "tb" / "stream_delay_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_delay_pcache")


def _parse_stream_fifo_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_fifo"
    files = [
        str(example / "rtl" / "fifo_v3.sv"),
        str(example / "rtl" / "stream_fifo.sv"),
        str(example / "tb" / "stream_fifo_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_fifo_pcache")


def _parse_stream_fifo_optimal_wrap_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_fifo_optimal_wrap"
    files = [
        str(example / "rtl" / "spill_register_flushable.sv"),
        str(example / "rtl" / "fifo_v3.sv"),
        str(example / "rtl" / "stream_fifo.sv"),
        str(example / "rtl" / "stream_fifo_optimal_wrap.sv"),
        str(example / "tb" / "stream_fifo_optimal_wrap_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_fifo_optimal_wrap_pcache")


def _parse_passthrough_stream_fifo_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "passthrough_stream_fifo"
    files = [
        str(example / "rtl" / "passthrough_stream_fifo.sv"),
        str(example / "tb" / "passthrough_stream_fifo_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "passthrough_stream_fifo_pcache")


def _parse_stream_throttle_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_throttle"
    files = [
        str(example / "rtl" / "stream_throttle.sv"),
        str(example / "tb" / "stream_throttle_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_throttle_pcache")


def _parse_lossy_valid_to_stream_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "lossy_valid_to_stream"
    files = [
        str(example / "rtl" / "lossy_valid_to_stream.sv"),
        str(example / "tb" / "lossy_valid_to_stream_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "lossy_valid_to_stream_pcache")


def _parse_stream_fork_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_fork"
    files = [
        str(example / "rtl" / "stream_fork.sv"),
        str(example / "tb" / "stream_fork_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_fork_pcache")


def _parse_stream_fork_dynamic_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_fork_dynamic"
    files = [
        str(example / "rtl" / "stream_fork.sv"),
        str(example / "rtl" / "stream_fork_dynamic.sv"),
        str(example / "tb" / "stream_fork_dynamic_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_fork_dynamic_pcache")


def _parse_stream_arbiter_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_arbiter"
    files = [
        str(example / "rtl" / "rr_arb_tree.sv"),
        str(example / "rtl" / "stream_arbiter_flushable.sv"),
        str(example / "rtl" / "stream_arbiter.sv"),
        str(example / "tb" / "stream_arbiter_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_arbiter_pcache")


def _parse_stream_arbiter_flushable_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_arbiter_flushable"
    files = [
        str(example / "rtl" / "rr_arb_tree.sv"),
        str(example / "rtl" / "stream_arbiter_flushable.sv"),
        str(example / "tb" / "stream_arbiter_flushable_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_arbiter_flushable_pcache")


def _parse_typed_stream_xbar_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_xbar_typed"
    files = [
        str(example / "rtl" / "stream_demux.sv"),
        str(example / "rtl" / "spill_register.sv"),
        str(example / "rtl" / "rr_arb_tree.sv"),
        str(example / "rtl" / "stream_xbar.sv"),
        str(example / "tb" / "sxt_tb.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "typed_stream_xbar_pcache")


def _parse_stream_to_mem_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_to_mem"
    files = [
        str(example / "rtl" / "stream_to_mem.sv"),
        str(example / "tb" / "stream_to_mem_tb_vm_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_to_mem_pcache")


def _parse_rr_arb_tree_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "rr_arb_tree"
    files = [
        str(example / "rtl" / "rr_arb_tree.sv"),
        str(example / "tb" / "rr_arb_tree_tb_local.sv"),
        str(example / "tb" / "rr_arb_tree_tb_vm_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "rr_arb_tree_pcache")


def _parse_cdc_fifo_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "cdc_fifo"
    files = [
        str(example / "rtl" / "cdc_2phase.sv"),
        str(example / "rtl" / "cdc_fifo_2phase.sv"),
        str(example / "tb" / "cdc_fifo_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "cdc_fifo_pcache")


def _parse_cdc_2phase_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "cdc_2phase"
    files = [
        str(example / "rtl" / "cdc_2phase.sv"),
        str(example / "tb" / "cdc_2phase_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "cdc_2phase_pcache")


def _parse_cdc_2phase_clearable_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "cdc_2phase_clearable"
    files = [
        str(example / "rtl" / "sync.sv"),
        str(example / "rtl" / "cdc_4phase_ctrl.sv"),
        str(example / "rtl" / "cdc_reset_ctrlr.sv"),
        str(example / "rtl" / "cdc_2phase_clearable.sv"),
        str(example / "tb" / "cdc_2phase_clearable_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "cdc_2phase_clearable_pcache")


def _parse_cdc_fifo_gray_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "cdc_fifo_gray"
    files = [
        str(example / "rtl" / "binary_to_gray.sv"),
        str(example / "rtl" / "gray_to_binary.sv"),
        str(example / "rtl" / "sync.sv"),
        str(example / "rtl" / "spill_register_flushable.sv"),
        str(example / "rtl" / "spill_register.sv"),
        str(example / "rtl" / "cdc_fifo_gray.sv"),
        str(example / "tb" / "cdc_fifo_gray_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "cdc_fifo_gray_pcache")


def _parse_cdc_4phase_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "cdc_4phase"
    files = [
        str(example / "rtl" / "sync.sv"),
        str(example / "rtl" / "spill_register_flushable.sv"),
        str(example / "rtl" / "spill_register.sv"),
        str(example / "rtl" / "cdc_4phase.sv"),
        str(example / "tb" / "cdc_4phase_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "cdc_4phase_pcache")


def _parse_isochronous_spill_register_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "isochronous_spill_register"
    files = [
        str(example / "rtl" / "isochronous_spill_register.sv"),
        str(example / "tb" / "isochronous_spill_register_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "isochronous_spill_register_pcache")


def _parse_stream_omega_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_omega_net"
    files = [
        str(example / "rtl" / "spill_register_flushable.sv"),
        str(example / "rtl" / "spill_register.sv"),
        str(example / "rtl" / "stream_omega_net.sv"),
        str(example / "tb" / "so_tb.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_omega_pcache")


def _parse_fifo_v3_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "fifo_v3"
    files = [
        str(example / "rtl" / "fifo_v3.sv"),
        str(example / "tb" / "fifo_v3_tb_local.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "fifo_v3_pcache")


def _parse_stream_xbar_design(tmp_path: Path):
    example = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_xbar"
    files = [
        str(example / "rtl" / "spill_register_flushable.sv"),
        str(example / "rtl" / "spill_register.sv"),
        str(example / "rtl" / "stream_xbar.sv"),
        str(example / "tb" / "sx_tb.sv"),
    ]
    return parse_files(files, preprocess=True, cache_dir=tmp_path / "stream_xbar_pcache")


def _make_stream_to_mem_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "req_i", 0)
    step_drive(sim, engine, "req_valid_i", 0)
    step_drive(sim, engine, "resp_ready_i", 0)
    step_drive(sim, engine, "mem_req_ready_i", 1)
    step_drive(sim, engine, "mem_resp_i", 0)
    step_drive(sim, engine, "mem_resp_valid_i", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 160)
    _settle_drives(sim, engine)
    step_run_until(sim, 30)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    return sim


def _make_rr_arb_tree_sim(design, engine: str) -> Simulator:
    top = design.get_module("rr_arb_tree_tb_vm_local")
    assert top is not None, "Top module 'rr_arb_tree_tb_vm_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "flush", 0)
    step_drive(sim, engine, "rr", 0)
    step_drive(sim, engine, "req", 0)
    step_drive(sim, engine, "gnt_oup", 0)
    step_drive(sim, engine, "data_bus", 0xD3C2B1A0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 120)
    _settle_drives(sim, engine)
    return sim


def _make_typed_rr_arb_tree_sim(design, engine: str) -> Simulator:
    top = design.get_module("rrt0_tb")
    assert top is not None, "Top module 'rrt0_tb' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "flush", 0)
    step_drive(sim, engine, "rr_i", 0)
    step_drive(sim, engine, "req_i", 0)
    step_drive(sim, engine, "gnt_i", 0)
    step_drive(sim, engine, "data_i", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 120)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    return sim


def _make_cdc_fifo_sim(design, engine: str) -> Simulator:
    top = design.get_module("cdc_fifo_tb_local")
    assert top is not None, "Top module 'cdc_fifo_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "src_clk_i", 0)
    step_drive(sim, engine, "dst_clk_i", 0)
    step_drive(sim, engine, "src_rst_ni", 0)
    step_drive(sim, engine, "dst_rst_ni", 0)
    step_drive(sim, engine, "src_data_i", 0)
    step_drive(sim, engine, "src_valid_i", 0)
    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), 260)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=14), 260)
    _settle_drives(sim, engine, "src_clk_i")
    return sim


def _make_cdc_2phase_sim(design, engine: str) -> Simulator:
    top = design.get_module("cdc_2phase_tb_local")
    assert top is not None, "Top module 'cdc_2phase_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "src_clk_i", 0)
    step_drive(sim, engine, "dst_clk_i", 0)
    step_drive(sim, engine, "src_rst_ni", 0)
    step_drive(sim, engine, "dst_rst_ni", 0)
    step_drive(sim, engine, "src_data_i", 0)
    step_drive(sim, engine, "src_valid_i", 0)
    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), 320)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=14), 320)
    _settle_drives(sim, engine, "src_clk_i")
    return sim


def _make_cdc_2phase_clearable_sim(design, engine: str) -> Simulator:
    return _make_cdc_2phase_clearable_sim_for_top(design, "cdc_2phase_clearable_tb_local", engine)


def _make_cdc_2phase_clearable_sim_for_top(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module '{top_name}' not found"

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
    _settle_drives(sim, engine, "src_clk_i")
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), 2200)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=14), 2200)
    _settle_drives(sim, engine, "src_clk_i")
    return sim


def _make_cdc_fifo_gray_sim(design, engine: str) -> Simulator:
    top = design.get_module("cdc_fifo_gray_tb_local")
    assert top is not None, "Top module 'cdc_fifo_gray_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "src_clk_i", 0)
    step_drive(sim, engine, "dst_clk_i", 0)
    step_drive(sim, engine, "src_rst_ni", 0)
    step_drive(sim, engine, "dst_rst_ni", 0)
    step_drive(sim, engine, "src_data_i", 0)
    step_drive(sim, engine, "src_valid_i", 0)
    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), 280)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=14), 280)
    _settle_drives(sim, engine, "src_clk_i")
    return sim


def _make_cdc_4phase_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "src_clk_i", 0)
    step_drive(sim, engine, "dst_clk_i", 0)
    step_drive(sim, engine, "src_rst_ni", 0)
    step_drive(sim, engine, "dst_rst_ni", 0)
    step_drive(sim, engine, "src_data_i", 0)
    step_drive(sim, engine, "src_valid_i", 0)
    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), 360)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=14), 360)
    _settle_drives(sim, engine, "src_clk_i")
    return sim


def _make_isochronous_spill_register_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "src_clk_i", 0)
    step_drive(sim, engine, "dst_clk_i", 0)
    step_drive(sim, engine, "src_rst_ni", 0)
    step_drive(sim, engine, "dst_rst_ni", 0)
    step_drive(sim, engine, "src_data_i", 0)
    step_drive(sim, engine, "src_valid_i", 0)
    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    sim._schedule_clock_events(Clock(sim.signal("src_clk_i"), period=10), 260)
    sim._schedule_clock_events(Clock(sim.signal("dst_clk_i"), period=20), 260)
    _settle_drives(sim, engine, "src_clk_i")
    return sim


def _make_stream_omega_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

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
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 320)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _make_fifo_v3_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "flush", 0)
    step_drive(sim, engine, "push", 0)
    step_drive(sim, engine, "pop", 0)
    step_drive(sim, engine, "data_i", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 220)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _make_stream_fifo_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "flush", 0)
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "data_i", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 220)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _make_stream_throttle_sim(design, engine: str) -> Simulator:
    top = design.get_module("stream_throttle_tb_local")
    assert top is not None, "Top module 'stream_throttle_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "req_valid_i", 0)
    step_drive(sim, engine, "req_ready_i", 0)
    step_drive(sim, engine, "rsp_valid_i", 0)
    step_drive(sim, engine, "rsp_ready_i", 0)
    step_drive(sim, engine, "credit_i", 2)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 220)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _make_stream_xbar_sim(design, top_name: str, engine: str) -> Simulator:
    top = design.get_module(top_name)
    assert top is not None, f"Top module {top_name!r} not found"

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
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 240)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _make_stream_fork_sim(design, engine: str) -> Simulator:
    top = design.get_module("stream_fork_tb_local")
    assert top is not None, "Top module 'stream_fork_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 220)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _make_stream_fork_dynamic_sim(design, engine: str) -> Simulator:
    top = design.get_module("stream_fork_dynamic_tb_local")
    assert top is not None, "Top module 'stream_fork_dynamic_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "sel_i", 0)
    step_drive(sim, engine, "sel_valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 220)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _make_stream_arbiter_sim(design, engine: str) -> Simulator:
    top = design.get_module("stream_arbiter_tb_local")
    assert top is not None, "Top module 'stream_arbiter_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "inp_data_i", 0)
    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 220)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _make_stream_arbiter_flushable_sim(design, engine: str) -> Simulator:
    top = design.get_module("stream_arbiter_flushable_tb_local")
    assert top is not None, "Top module 'stream_arbiter_flushable_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "clk", 0)
    step_drive(sim, engine, "rst_n", 0)
    step_drive(sim, engine, "flush_i", 0)
    step_drive(sim, engine, "inp_data_i", 0)
    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 220)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    if engine == "reference":
        sim.run(max_time=0)
    return sim


def _release_cdc_fifo_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine, "src_clk_i")
    step_run_until(sim, 45)
    _expect(sim, "src_ready_o", 1, "source should be ready after reset")
    _expect(sim, "dst_valid_o", 0, "destination should be idle after reset")


def _release_cdc_2phase_clearable_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine, "src_clk_i")
    step_run_until(sim, 45)
    _expect(sim, "src_ready_o", 1, "source should be ready after reset")
    _expect(sim, "dst_valid_o", 0, "destination should be idle after reset")
    _expect(sim, "src_clear_pending_o", 0, "source clear-pending should start low after reset")
    _expect(sim, "dst_clear_pending_o", 0, "destination clear-pending should start low after reset")


def _release_cdc_2phase_clearable_async_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine, "src_clk_i")
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


def _release_cdc_4phase_reset(sim: Simulator, engine: str, expected_src_ready: int) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine, "src_clk_i")
    step_run_until(sim, 45)
    _expect(sim, "src_ready_o", expected_src_ready, "unexpected source ready state after reset")
    _expect(sim, "dst_valid_o", 0, "destination should be idle after reset")


def _release_isochronous_spill_register_reset(sim: Simulator, engine: str) -> None:
    step_run_until(sim, 31)
    step_drive(sim, engine, "src_rst_ni", 1)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine, "src_clk_i")
    step_run_until(sim, 36)


def _release_isochronous_spill_register_non_bypass_reset(sim: Simulator, engine: str) -> None:
    _release_isochronous_spill_register_reset(sim, engine)
    _expect(sim, "src_ready_o", 1, "source should be ready after reset")
    _expect(sim, "dst_valid_o", 0, "destination should be idle after reset")


def _wait_for_source_low(sim: Simulator) -> None:
    _run_until_condition(
        sim,
        sim.time + 20,
        lambda s: _read_int(s, "src_clk_i") == 0,
        "source clock never reached a low phase before the next drive",
    )


def _expect_rr_arb_state(
    sim: Simulator,
    exp_req: int,
    exp_idx: int,
    exp_data: int,
    exp_gnt: int,
    label: str,
) -> None:
    _expect(sim, "req_oup", exp_req, f"{label} req_o")
    _expect(sim, "idx_oup", exp_idx, f"{label} idx")
    _expect(sim, "data_oup", exp_data, f"{label} data")
    _expect(sim, "gnt", exp_gnt, f"{label} gnt")


def _pack_omega_inputs(d0: int, d1: int, d2: int, d3: int) -> int:
    return (d3 << 24) | (d2 << 16) | (d1 << 8) | d0


def _pack_stream_arbiter_inputs(d0: int, d1: int, d2: int, d3: int) -> int:
    return (d3 << 24) | (d2 << 16) | (d1 << 8) | d0


def _pack_stream_xbar_inputs(d0: int, d1: int, d2: int) -> int:
    return (d2 << 16) | (d1 << 8) | d0


def _pack_typed_stream_inputs(d0: int, d1: int, d2: int) -> int:
    return (d2 << 24) | (d1 << 12) | d0


def _pack_omega_selects(s0: int, s1: int, s2: int, s3: int) -> int:
    return (s3 << 6) | (s2 << 4) | (s1 << 2) | s0


def _pack_omega_indices(i0: int, i1: int, i2: int, i3: int) -> int:
    return (i3 << 6) | (i2 << 4) | (i1 << 2) | i0


def _pulse_omega_flush(sim: Simulator, engine: str) -> None:
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    step_drive(sim, engine, "flush", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "flush edge not observed")
    step_drive(sim, engine, "flush", 0)
    _settle_drives(sim, engine)


def _fifo_tx(sim: Simulator, engine: str, values: dict[str, int]) -> None:
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


def _check_fifo_reset_state(sim: Simulator) -> None:
    _expect(sim, "empty", 1, "fifo should be empty after reset")
    _expect(sim, "full", 0, "fifo should not be full after reset")
    _expect(sim, "usage", 0, "fifo usage should reset to zero")


@pytest.mark.parametrize("engine", ENGINES)
def test_spill_register_cross_engine(tmp_path, engine):
    design = _parse_spill_design(tmp_path)
    sim = _make_step_sim(design, "spill_reg_tb", engine)

    _expect(sim, "valid_o", 0, "spill register should be empty after reset")
    _expect(sim, "ready_o", 1, "spill register should be ready after reset")

    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x12)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill capture edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "spill register should hold the stalled item")
    _expect(sim, "data_o", 0x12, "spill register first payload mismatch")
    _expect(sim, "ready_o", 1, "spill register should still accept one more item")

    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x34)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second capture edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "data_o", 0x12, "spill register must preserve ordering under backpressure")
    _expect(sim, "ready_o", 0, "spill register should backpressure once both stages are occupied")

    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill drain edge not observed")
    _expect(sim, "valid_o", 1, "spill register should expose the second buffered item after a drain")
    _expect(sim, "data_o", 0x34, "spill register second payload mismatch")


@pytest.mark.parametrize("engine", ENGINES)
def test_spill_register_flushable_cross_engine(tmp_path, engine):
    design = _parse_spill_register_flushable_design(tmp_path)
    sim = _make_stream_fifo_sim(design, "spill_reg_flush_tb", engine)

    _expect(sim, "valid_o", 0, "spill_register_flushable should be empty after reset")
    _expect(sim, "ready_o", 1, "spill_register_flushable should be ready after reset")

    def _spill_register_flushable_tx(values: dict[str, int]) -> None:
        step_drive(sim, engine, "data_i", values.get("data_i", 0))
        step_drive(sim, engine, "valid_i", values.get("valid_i", 0))
        step_drive(sim, engine, "ready_i", values.get("ready_i", 0))
        step_drive(sim, engine, "flush", values.get("flush", 0))
        _settle_drives(sim, engine)
        _run_until_rising_edge(
            sim, "clk", sim.time + 20, "spill_register_flushable next rising clock edge not observed"
        )
        step_drive(sim, engine, "valid_i", 0)
        step_drive(sim, engine, "ready_i", 0)
        step_drive(sim, engine, "flush", 0)
        _settle_drives(sim, engine)

    _spill_register_flushable_tx({"valid_i": 1, "data_i": 0x12})
    _expect(sim, "valid_o", 1, "spill_register_flushable should capture the first stalled item")
    _expect(sim, "data_o", 0x12, "spill_register_flushable first payload mismatch")
    _expect(sim, "ready_o", 1, "spill_register_flushable should still accept one more item")

    _spill_register_flushable_tx({"valid_i": 1, "data_i": 0x34})
    _expect(sim, "valid_o", 1, "spill_register_flushable should remain occupied with two queued items")
    _expect(
        sim,
        "data_o",
        0x12,
        "spill_register_flushable should keep the oldest payload at the output while full",
    )
    _expect(sim, "ready_o", 0, "spill_register_flushable should backpressure once both stages are full")

    _spill_register_flushable_tx({"valid_i": 1, "data_i": 0x56, "flush": 1})
    _expect(sim, "valid_o", 0, "spill_register_flushable flush should clear both buffered stages")
    _expect(sim, "ready_o", 1, "spill_register_flushable flush should restore ready")

    _spill_register_flushable_tx({"valid_i": 1, "data_i": 0x78})
    _expect(sim, "valid_o", 1, "spill_register_flushable should accept a new item after flush")
    _expect(sim, "data_o", 0x78, "spill_register_flushable refill payload mismatch")
    _expect(sim, "ready_o", 1, "spill_register_flushable should reopen with a single queued word")

    _spill_register_flushable_tx({"ready_i": 1})
    _expect(sim, "valid_o", 0, "spill_register_flushable should drain cleanly after refill")
    _expect(sim, "ready_o", 1, "spill_register_flushable should be ready again after draining")

    sim = _make_stream_fifo_sim(design, "spill_reg_flush_bp_tb", engine)
    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "flush", 1)
    step_drive(sim, engine, "data_i", 0x9A)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "spill_register_flushable bypass should pass valid combinationally")
    _expect(sim, "ready_o", 0, "spill_register_flushable bypass should pass ready combinationally")
    _expect(sim, "data_o", 0x9A, "spill_register_flushable bypass should pass data combinationally")

    step_drive(sim, engine, "ready_i", 1)
    step_drive(sim, engine, "flush", 0)
    step_drive(sim, engine, "data_i", 0xBC)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 1, "spill_register_flushable bypass should reopen immediately")
    _expect(sim, "valid_o", 1, "spill_register_flushable bypass should remain transparent")
    _expect(sim, "data_o", 0xBC, "spill_register_flushable bypass should update data immediately")

    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "spill_register_flushable bypass should clear valid immediately")


@pytest.mark.parametrize("engine", ENGINES)
def test_fall_through_register_cross_engine(tmp_path, engine):
    design = _parse_ft_design(tmp_path)
    sim = _make_step_sim(design, "ft_reg_tb", engine)

    _expect(sim, "valid_o", 0, "fall-through register should be empty after reset")
    _expect(sim, "ready_o", 1, "fall-through register should be ready after reset")

    step_drive(sim, engine, "ready_i", 1)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x56)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "fall-through register should assert valid immediately when empty")
    _expect(sim, "ready_o", 1, "fall-through register should keep default ready when empty")
    _expect(sim, "data_o", 0x56, "fall-through register should forward data immediately when empty")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "fall-through handshake edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "accepted pass-through item should leave the register empty")

    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x78)
    _settle_drives(sim, engine)
    _expect(sim, "data_o", 0x78, "stalled empty fall-through register should still expose input data")
    _expect(sim, "ready_o", 1, "stalled empty fall-through register should show default ready before capture")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "fall-through stall capture edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stalled item should remain buffered after capture")
    _expect(sim, "ready_o", 0, "depth-1 fall-through register should backpressure once filled")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_register_cross_engine(tmp_path, engine):
    design = _parse_stream_register_design(tmp_path)
    sim = _make_step_sim(design, "stream_register_tb_local", engine)

    _expect(sim, "valid_o", 0, "stream_register should be empty after reset")
    _expect(sim, "ready_o", 1, "stream_register should be ready after reset")

    step_drive(sim, engine, "ready_i", 1)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x21)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_register should not pass valid combinationally")
    _expect(sim, "ready_o", 1, "empty stream_register should accept input")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_register capture edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_register should present the captured item after one edge")
    _expect(sim, "data_o", 0x21, "stream_register first payload mismatch")

    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x43)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 0, "full stream_register should block overwrite while stalled")
    _expect(sim, "data_o", 0x21, "blocked overwrite should keep the buffered head stable")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_register blocked-write edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "blocked overwrite should leave the stored item valid")
    _expect(sim, "data_o", 0x21, "blocked overwrite should preserve the stored payload")

    step_drive(sim, engine, "ready_i", 1)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x65)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 1, "stream_register should reopen immediately when draining")
    _expect(sim, "data_o", 0x21, "old payload should stay visible before the drain/refill edge")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_register drain/refill edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "drain/refill should keep the stream_register occupied")
    _expect(sim, "data_o", 0x65, "drain/refill should replace the buffered payload")

    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "clr", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_register clear edge not observed")
    step_drive(sim, engine, "clr", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_register clear should empty the register")
    _expect(sim, "ready_o", 1, "stream_register clear should restore ready")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_join_cross_engine(tmp_path, engine):
    design = _parse_stream_join_design(tmp_path)
    top = design.get_module("stream_join_tb_local")
    assert top is not None, "Top module 'stream_join_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)

    _expect(sim, "oup_valid_o", 0, "stream_join should be idle with no valid inputs")
    _expect(sim, "inp_ready_o", 0, "stream_join should not ready any input while idle")

    step_drive(sim, engine, "inp_valid_i", 0b011)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0, "partial-valid join should stay blocked")
    _expect(sim, "inp_ready_o", 0, "partial-valid join should not fan out ready")

    step_drive(sim, engine, "inp_valid_i", 0b111)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 1, "all-valid join should assert output valid")
    _expect(sim, "inp_ready_o", 0, "stalled downstream should block join ready fanout")

    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 1, "join valid should remain high while all inputs are valid")
    _expect(sim, "inp_ready_o", 0b111, "join should fan ready to all inputs at once")

    step_drive(sim, engine, "inp_valid_i", 0b101)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0, "dropping one join input should clear output valid")
    _expect(sim, "inp_ready_o", 0, "dropping one join input should clear ready fanout")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_mux_cross_engine(tmp_path, engine):
    design = _parse_stream_mux_design(tmp_path)
    top = design.get_module("stream_mux_tb_local")
    assert top is not None, "Top module 'stream_mux_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "inp_data_i", 0)
    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "inp_sel_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)

    _expect(sim, "oup_valid_o", 0, "stream_mux should be idle when the selected input is invalid")
    _expect(sim, "inp_ready_o", 0, "stream_mux should not fan out ready while downstream stalls")

    step_drive(sim, engine, "inp_data_i", (0x33 << 16) | (0x22 << 8) | 0x11)
    step_drive(sim, engine, "inp_valid_i", 0b010)
    step_drive(sim, engine, "inp_sel_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0x22, "stream_mux should route selected input 1 data")
    _expect(sim, "oup_valid_o", 1, "stream_mux should route selected input 1 valid")

    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "inp_ready_o", 0b010, "stream_mux should fan ready only to the selected input")

    step_drive(sim, engine, "inp_valid_i", 0b101)
    step_drive(sim, engine, "inp_sel_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0x11, "stream_mux should reroute data immediately when select changes")
    _expect(sim, "oup_valid_o", 1, "stream_mux should reroute valid immediately when select changes")
    _expect(sim, "inp_ready_o", 0b001, "stream_mux should move ready fanout with the selection")

    step_drive(sim, engine, "inp_sel_i", 2)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0x33, "stream_mux should route selected input 2 data")
    _expect(sim, "oup_valid_o", 1, "stream_mux should route selected input 2 valid")
    _expect(sim, "inp_ready_o", 0b100, "stream_mux should fan ready only to input 2")

    step_drive(sim, engine, "inp_valid_i", 0b001)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0, "non-selected valids should not assert stream_mux output valid")
    _expect(sim, "oup_data_o", 0x33, "selected data routing should remain stable when unselected valids change")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_demux_cross_engine(tmp_path, engine):
    design = _parse_stream_demux_design(tmp_path)
    top = design.get_module("stream_demux_tb_local")
    assert top is not None, "Top module 'stream_demux_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_sel_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)

    _expect(sim, "oup_valid_o", 0b000, "stream_demux should be idle when input valid is low")
    _expect(sim, "inp_ready_o", 0, "stream_demux ready should reflect selected output ready")

    step_drive(sim, engine, "oup_ready_i", 0b001)
    _settle_drives(sim, engine)
    _expect(sim, "inp_ready_o", 1, "stream_demux should return selected output 0 ready when idle")

    step_drive(sim, engine, "inp_valid_i", 1)
    step_drive(sim, engine, "oup_sel_i", 1)
    step_drive(sim, engine, "oup_ready_i", 0b001)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0b010, "stream_demux should assert only selected output 1 valid")
    _expect(sim, "inp_ready_o", 0, "stream_demux input ready should follow selected output 1 ready")

    step_drive(sim, engine, "oup_ready_i", 0b010)
    _settle_drives(sim, engine)
    _expect(sim, "inp_ready_o", 1, "stream_demux should return selected output 1 ready")

    step_drive(sim, engine, "oup_sel_i", 2)
    step_drive(sim, engine, "oup_ready_i", 0b100)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0b100, "stream_demux should reroute valid immediately when select changes")
    _expect(sim, "inp_ready_o", 1, "stream_demux should reroute ready immediately when select changes")

    step_drive(sim, engine, "inp_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0b000, "stream_demux should clear valid fanout when input valid drops")
    _expect(sim, "inp_ready_o", 1, "stream_demux ready should still reflect selected output when idle")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_filter_cross_engine(tmp_path, engine):
    design = _parse_stream_filter_design(tmp_path)
    top = design.get_module("stream_filter_tb_local")
    assert top is not None, "Top module 'stream_filter_tb_local' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "drop_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)

    _expect(sim, "valid_o", 0, "stream_filter should be idle when input valid is low")
    _expect(sim, "ready_o", 0, "stream_filter should follow downstream ready in pass-through mode")

    step_drive(sim, engine, "valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_filter should pass valid through when drop is low")
    _expect(sim, "ready_o", 0, "stream_filter should keep ready low while downstream is not ready")

    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_filter should keep valid asserted in pass-through mode")
    _expect(sim, "ready_o", 1, "stream_filter should pass ready through when drop is low")

    step_drive(sim, engine, "drop_i", 1)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_filter should suppress downstream valid when drop is high")
    _expect(sim, "ready_o", 1, "stream_filter should force upstream ready high when drop is high")

    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_filter drop mode should stay invalid when input valid is low")
    _expect(sim, "ready_o", 1, "stream_filter drop mode should keep upstream ready high")

    step_drive(sim, engine, "drop_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_filter should return to pass-through mode when drop clears")
    _expect(sim, "ready_o", 0, "stream_filter should resume following downstream ready when drop clears")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_delay_cross_engine(tmp_path, engine):
    design = _parse_stream_delay_design(tmp_path)

    sim = _make_step_sim(design, "stream_delay_tb_local", engine)
    _expect(sim, "valid_o", 0, "stream_delay should be idle after reset")
    _expect(sim, "ready_o", 0, "stream_delay should not assert ready after reset")

    step_drive(sim, engine, "data_i", 0x34)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_delay should not assert valid immediately")
    _expect(sim, "ready_o", 0, "stream_delay should not assert ready during the delay window")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_delay first delay edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_delay should still be delaying after the first edge")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_delay second delay edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_delay should assert valid after two delay edges")
    _expect(sim, "data_o", 0x34, "stream_delay should preserve the payload through the delay")
    _expect(sim, "ready_o", 0, "stream_delay should keep ready low while the sink stalls")

    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_delay should hold valid until the delayed transfer is accepted")
    _expect(sim, "ready_o", 1, "stream_delay should reflect ready once the sink can accept")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_delay accept edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_delay should return to idle after acceptance")
    _expect(sim, "ready_o", 0, "stream_delay should clear ready again once idle")

    sim = _make_step_sim(design, "stream_delay_tb_local", engine)
    step_drive(sim, engine, "data_i", 0x56)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_delay should not bypass the delay when ready is already high")
    _expect(sim, "ready_o", 0, "stream_delay should keep ready low until the delay expires")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_delay pre-ready first edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_delay should still be delaying after the first pre-ready edge")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_delay pre-ready second edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_delay should assert valid after the same two-edge delay when ready is high")
    _expect(sim, "ready_o", 1, "stream_delay should expose ready once the delayed transfer becomes valid")
    _expect(sim, "data_o", 0x56, "stream_delay should preserve the second payload through the delay")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_fifo_cross_engine(tmp_path, engine):
    design = _parse_stream_fifo_design(tmp_path)

    sim = _make_stream_fifo_sim(design, "stream_fifo_tb_depth3", engine)
    _expect(sim, "usage", 0, "stream_fifo should reset usage to zero")
    _expect(sim, "valid_o", 0, "stream_fifo should be empty after reset")
    _expect(sim, "ready_o", 1, "stream_fifo should be ready after reset")

    def _stream_fifo_tx(values: dict[str, int]) -> None:
        step_drive(sim, engine, "data_i", values.get("data_i", 0))
        step_drive(sim, engine, "valid_i", values.get("valid_i", 0))
        step_drive(sim, engine, "ready_i", values.get("ready_i", 0))
        step_drive(sim, engine, "flush", values.get("flush", 0))
        _settle_drives(sim, engine)
        _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_fifo next rising clock edge not observed")
        step_drive(sim, engine, "valid_i", 0)
        step_drive(sim, engine, "ready_i", 0)
        step_drive(sim, engine, "flush", 0)
        _settle_drives(sim, engine)

    _stream_fifo_tx({"valid_i": 1, "data_i": 0x11})
    _expect(sim, "usage", 1, "stream_fifo depth3 first push should increment usage")
    _expect(sim, "valid_o", 1, "stream_fifo depth3 first push should make output valid")
    _expect(sim, "data_o", 0x11, "stream_fifo depth3 first push should expose the first element")

    _stream_fifo_tx({"valid_i": 1, "data_i": 0x22})
    _expect(sim, "usage", 2, "stream_fifo depth3 second push should increment usage")
    _expect(sim, "data_o", 0x11, "stream_fifo depth3 head should stay stable after second push")

    _stream_fifo_tx({"valid_i": 1, "data_i": 0x33})
    _expect(sim, "usage", 3, "stream_fifo depth3 third push should fill the fifo")
    _expect(sim, "ready_o", 0, "stream_fifo depth3 should backpressure once full")
    _expect(sim, "data_o", 0x11, "stream_fifo depth3 full fifo should retain the oldest head")

    _stream_fifo_tx({"valid_i": 1, "data_i": 0x44})
    _expect(sim, "usage", 3, "stream_fifo depth3 blocked push should not change usage")
    _expect(sim, "ready_o", 0, "stream_fifo depth3 blocked push should keep backpressure asserted")
    _expect(sim, "data_o", 0x11, "stream_fifo depth3 blocked push should preserve the head")

    _stream_fifo_tx({"ready_i": 1})
    _expect(sim, "usage", 2, "stream_fifo depth3 pop should decrement usage")
    _expect(sim, "ready_o", 1, "stream_fifo depth3 pop should reopen the input")
    _expect(sim, "data_o", 0x22, "stream_fifo depth3 pop should advance to the second element")

    _stream_fifo_tx({"valid_i": 1, "ready_i": 1, "data_i": 0x44})
    _expect(sim, "usage", 2, "stream_fifo depth3 simultaneous refill should preserve usage")
    _expect(sim, "data_o", 0x33, "stream_fifo depth3 simultaneous refill should advance the head")

    _stream_fifo_tx({"ready_i": 1})
    _expect(sim, "usage", 1, "stream_fifo depth3 second pop should leave one element")
    _expect(sim, "data_o", 0x44, "stream_fifo depth3 reordered tail should surface after draining")

    _stream_fifo_tx({"flush": 1})
    _expect(sim, "usage", 0, "stream_fifo depth3 flush should clear usage")
    _expect(sim, "valid_o", 0, "stream_fifo depth3 flush should empty the fifo")
    _expect(sim, "ready_o", 1, "stream_fifo depth3 flush should restore ready")

    sim = _make_stream_fifo_sim(design, "stream_fifo_tb_ft_depth3", engine)
    _expect(sim, "usage", 0, "stream_fifo fall-through should reset usage to zero")
    _expect(sim, "valid_o", 0, "stream_fifo fall-through should be empty after reset")
    _expect(sim, "ready_o", 1, "stream_fifo fall-through should be ready after reset")

    step_run_until(sim, 30)
    step_drive(sim, engine, "data_i", 0xA1)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_fifo fall-through should assert valid immediately when empty")
    _expect(sim, "usage", 0, "stream_fifo fall-through pass-through should not pre-increment usage")
    _expect(sim, "data_o", 0xA1, "stream_fifo fall-through should expose input data immediately")
    step_run_until(sim, 36)
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "usage", 0, "stream_fifo fall-through empty pass-through should leave fifo empty")
    _expect(sim, "valid_o", 0, "stream_fifo fall-through empty pass-through should drain immediately")

    step_run_until(sim, 40)
    step_drive(sim, engine, "data_i", 0xB2)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_fifo fall-through should expose a stalled empty push immediately")
    _expect(sim, "ready_o", 1, "stream_fifo fall-through should stay ready before the stalled word is stored")
    _expect(sim, "data_o", 0xB2, "stream_fifo fall-through stalled push should drive the incoming payload")
    step_run_until(sim, 46)
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "usage", 1, "stream_fifo fall-through should store the stalled word after the clock edge")
    _expect(sim, "data_o", 0xB2, "stream_fifo fall-through stored word should remain at the output")

    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_fifo fall-through pop edge not observed")
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "usage", 0, "stream_fifo fall-through pop should drain the stored word")
    _expect(sim, "valid_o", 0, "stream_fifo fall-through pop should return fifo to empty")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_fifo_optimal_wrap_cross_engine(tmp_path, engine):
    design = _parse_stream_fifo_optimal_wrap_design(tmp_path)

    sim = _make_stream_fifo_sim(design, "stream_fifo_optimal_wrap_tb_depth2", engine)
    _expect(sim, "valid_o", 0, "stream_fifo_optimal_wrap depth2 should be empty after reset")
    _expect(sim, "ready_o", 1, "stream_fifo_optimal_wrap depth2 should be ready after reset")

    def _stream_fifo_optimal_wrap_tx(values: dict[str, int]) -> None:
        step_drive(sim, engine, "data_i", values.get("data_i", 0))
        step_drive(sim, engine, "valid_i", values.get("valid_i", 0))
        step_drive(sim, engine, "ready_i", values.get("ready_i", 0))
        step_drive(sim, engine, "flush", values.get("flush", 0))
        _settle_drives(sim, engine)
        _run_until_rising_edge(
            sim,
            "clk",
            sim.time + 20,
            "stream_fifo_optimal_wrap next rising clock edge not observed",
        )
        step_drive(sim, engine, "valid_i", 0)
        step_drive(sim, engine, "ready_i", 0)
        step_drive(sim, engine, "flush", 0)
        _settle_drives(sim, engine)

    step_run_until(sim, 30)
    step_drive(sim, engine, "data_i", 0x11)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "stream_fifo_optimal_wrap depth2 should cut valid combinationally")
    _expect(sim, "ready_o", 1, "stream_fifo_optimal_wrap depth2 should stay ready before the first capture")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_fifo_optimal_wrap depth2 first edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "stream_fifo_optimal_wrap depth2 should present the captured head")
    _expect(sim, "data_o", 0x11, "stream_fifo_optimal_wrap depth2 head payload mismatch")
    _expect(sim, "ready_o", 1, "stream_fifo_optimal_wrap depth2 should still accept a second word")

    _stream_fifo_optimal_wrap_tx({"valid_i": 1, "data_i": 0x22})
    _expect(sim, "valid_o", 1, "stream_fifo_optimal_wrap depth2 should stay valid when full")
    _expect(sim, "data_o", 0x11, "stream_fifo_optimal_wrap depth2 should retain the oldest queued word")
    _expect(sim, "ready_o", 0, "stream_fifo_optimal_wrap depth2 should backpressure once full")

    _stream_fifo_optimal_wrap_tx({"valid_i": 1, "data_i": 0x33})
    _expect(sim, "data_o", 0x11, "stream_fifo_optimal_wrap depth2 blocked push should preserve the head")
    _expect(sim, "ready_o", 0, "stream_fifo_optimal_wrap depth2 blocked push should keep backpressure asserted")

    _stream_fifo_optimal_wrap_tx({"ready_i": 1})
    _expect(sim, "valid_o", 1, "stream_fifo_optimal_wrap depth2 first drain should leave one word queued")
    _expect(sim, "data_o", 0x22, "stream_fifo_optimal_wrap depth2 should drain in order")
    _expect(sim, "ready_o", 1, "stream_fifo_optimal_wrap depth2 first drain should reopen the input")

    _stream_fifo_optimal_wrap_tx({"ready_i": 1})
    _expect(sim, "valid_o", 0, "stream_fifo_optimal_wrap depth2 second drain should empty the wrapper")
    _expect(sim, "ready_o", 1, "stream_fifo_optimal_wrap depth2 should be ready again after draining")

    _stream_fifo_optimal_wrap_tx({"valid_i": 1, "data_i": 0x55})
    _expect(sim, "valid_o", 1, "stream_fifo_optimal_wrap depth2 refill should capture a new word")
    _expect(sim, "data_o", 0x55, "stream_fifo_optimal_wrap depth2 refill payload mismatch")

    _stream_fifo_optimal_wrap_tx({"flush": 1})
    _expect(sim, "valid_o", 0, "stream_fifo_optimal_wrap depth2 flush should clear the spill path")
    _expect(sim, "ready_o", 1, "stream_fifo_optimal_wrap depth2 flush should restore ready")

    sim = _make_stream_fifo_sim(design, "stream_fifo_optimal_wrap_tb_depth3", engine)
    _expect(sim, "usage", 0, "stream_fifo_optimal_wrap depth3 should reset usage to zero")
    _expect(sim, "valid_o", 0, "stream_fifo_optimal_wrap depth3 should be empty after reset")
    _expect(sim, "ready_o", 1, "stream_fifo_optimal_wrap depth3 should be ready after reset")

    _stream_fifo_optimal_wrap_tx({"valid_i": 1, "data_i": 0x11})
    _expect(sim, "usage", 1, "stream_fifo_optimal_wrap depth3 first push should increment usage")
    _expect(sim, "valid_o", 1, "stream_fifo_optimal_wrap depth3 first push should make the output valid")
    _expect(sim, "data_o", 0x11, "stream_fifo_optimal_wrap depth3 first push should expose the head")

    _stream_fifo_optimal_wrap_tx({"valid_i": 1, "data_i": 0x22})
    _expect(sim, "usage", 2, "stream_fifo_optimal_wrap depth3 second push should increment usage")
    _expect(sim, "data_o", 0x11, "stream_fifo_optimal_wrap depth3 second push should keep the first head")

    _stream_fifo_optimal_wrap_tx({"valid_i": 1, "data_i": 0x33})
    _expect(sim, "usage", 3, "stream_fifo_optimal_wrap depth3 third push should fill the fifo path")
    _expect(sim, "ready_o", 0, "stream_fifo_optimal_wrap depth3 should backpressure once full")

    _stream_fifo_optimal_wrap_tx({"ready_i": 1})
    _expect(sim, "usage", 2, "stream_fifo_optimal_wrap depth3 pop should decrement usage")
    _expect(sim, "data_o", 0x22, "stream_fifo_optimal_wrap depth3 pop should advance to the next word")
    _expect(sim, "ready_o", 1, "stream_fifo_optimal_wrap depth3 pop should reopen the input")

    _stream_fifo_optimal_wrap_tx({"flush": 1})
    _expect(sim, "usage", 0, "stream_fifo_optimal_wrap depth3 flush should clear usage")
    _expect(sim, "valid_o", 0, "stream_fifo_optimal_wrap depth3 flush should empty the fifo path")
    _expect(sim, "ready_o", 1, "stream_fifo_optimal_wrap depth3 flush should restore ready")


@pytest.mark.parametrize("engine", ENGINES)
def test_passthrough_stream_fifo_cross_engine(tmp_path, engine):
    design = _parse_passthrough_stream_fifo_design(tmp_path)

    sim = _make_stream_fifo_sim(design, "passthrough_stream_fifo_tb_same_cycle", engine)
    _expect(sim, "valid_o", 0, "passthrough_stream_fifo same-cycle path should be empty after reset")
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo same-cycle path should be ready after reset")

    def _passthrough_tx(values: dict[str, int]) -> None:
        step_drive(sim, engine, "data_i", values.get("data_i", 0))
        step_drive(sim, engine, "valid_i", values.get("valid_i", 0))
        step_drive(sim, engine, "ready_i", values.get("ready_i", 0))
        step_drive(sim, engine, "flush", values.get("flush", 0))
        _settle_drives(sim, engine)
        _run_until_rising_edge(
            sim,
            "clk",
            sim.time + 20,
            "passthrough_stream_fifo next rising clock edge not observed",
        )
        step_drive(sim, engine, "valid_i", 0)
        step_drive(sim, engine, "ready_i", 0)
        step_drive(sim, engine, "flush", 0)
        _settle_drives(sim, engine)

    _passthrough_tx({"valid_i": 1, "data_i": 0x11})
    _passthrough_tx({"valid_i": 1, "data_i": 0x22})
    _passthrough_tx({"valid_i": 1, "data_i": 0x33})
    _expect(sim, "ready_o", 0, "passthrough_stream_fifo same-cycle path should be full after three pushes")
    _expect(sim, "data_o", 0x11, "passthrough_stream_fifo same-cycle full fifo should retain the oldest head")

    step_drive(sim, engine, "data_i", 0x44)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo same-cycle path should allow pop/push on full")
    _expect(sim, "data_o", 0x11, "passthrough_stream_fifo same-cycle exchange should keep the old head pre-edge")
    _run_until_rising_edge(
        sim,
        "clk",
        sim.time + 20,
        "passthrough_stream_fifo same-cycle full exchange edge not observed",
    )
    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 0, "passthrough_stream_fifo same-cycle path should remain full after pop/push")
    _expect(sim, "data_o", 0x22, "passthrough_stream_fifo same-cycle exchange should advance to the next head")

    sim = _make_stream_fifo_sim(design, "passthrough_stream_fifo_tb_no_same_cycle", engine)
    _expect(sim, "valid_o", 0, "passthrough_stream_fifo no-same-cycle path should be empty after reset")
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo no-same-cycle path should be ready after reset")

    _passthrough_tx({"valid_i": 1, "data_i": 0x11})
    _passthrough_tx({"valid_i": 1, "data_i": 0x22})
    _passthrough_tx({"valid_i": 1, "data_i": 0x33})
    _expect(sim, "ready_o", 0, "passthrough_stream_fifo no-same-cycle path should be full after three pushes")
    _expect(sim, "data_o", 0x11, "passthrough_stream_fifo no-same-cycle full fifo should retain the oldest head")

    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 0, "passthrough_stream_fifo no-same-cycle path should stay blocked before the pop edge")
    _expect(sim, "data_o", 0x11, "passthrough_stream_fifo no-same-cycle blocked pop should keep the head pre-edge")
    _run_until_rising_edge(
        sim,
        "clk",
        sim.time + 20,
        "passthrough_stream_fifo no-same-cycle full pop edge not observed",
    )
    step_drive(sim, engine, "ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 1, "passthrough_stream_fifo no-same-cycle path should reopen after a pure pop")
    _expect(sim, "data_o", 0x22, "passthrough_stream_fifo no-same-cycle pure pop should advance the head")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_throttle_cross_engine(tmp_path, engine):
    design = _parse_stream_throttle_design(tmp_path)
    sim = _make_stream_throttle_sim(design, engine)

    def _drive_stream_throttle(
        *,
        credit: int,
        req_valid: int = 0,
        req_ready: int = 0,
        rsp_valid: int = 0,
        rsp_ready: int = 0,
    ) -> None:
        step_drive(sim, engine, "credit_i", credit)
        step_drive(sim, engine, "req_valid_i", req_valid)
        step_drive(sim, engine, "req_ready_i", req_ready)
        step_drive(sim, engine, "rsp_valid_i", rsp_valid)
        step_drive(sim, engine, "rsp_ready_i", rsp_ready)
        _settle_drives(sim, engine)

    _expect(sim, "req_valid_o", 0, "stream_throttle should be idle after reset")
    _expect(sim, "req_ready_o", 0, "stream_throttle should keep ready low when upstream ready is low")

    _drive_stream_throttle(credit=2, req_valid=1, req_ready=0)
    _expect(sim, "req_valid_o", 1, "stream_throttle should pass valid when credit is available")
    _expect(sim, "req_ready_o", 0, "stream_throttle should still reflect downstream ready")

    _drive_stream_throttle(credit=2, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 1, "stream_throttle should pass valid when downstream is ready")
    _expect(sim, "req_ready_o", 1, "stream_throttle should pass ready when credit is available")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_throttle first request edge not observed")

    _drive_stream_throttle(credit=2)
    _expect(sim, "req_valid_o", 0, "stream_throttle should return low when request valid drops")
    _expect(sim, "req_ready_o", 0, "stream_throttle should return ready low when request ready drops")

    _drive_stream_throttle(credit=2, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 1, "stream_throttle should still allow the second request at credit two")
    _expect(sim, "req_ready_o", 1, "stream_throttle should still allow ready for the second request")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_throttle second request edge not observed")

    _drive_stream_throttle(credit=2, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 0, "stream_throttle should block once outstanding requests reach the credit")
    _expect(sim, "req_ready_o", 0, "stream_throttle should deassert ready once the credit is exhausted")

    _drive_stream_throttle(credit=2, req_valid=1, req_ready=1, rsp_valid=1, rsp_ready=1)
    _expect(sim, "req_valid_o", 0, "stream_throttle should stay blocked until a response is clocked in")
    _expect(sim, "req_ready_o", 0, "stream_throttle should stay blocked until a response is clocked in")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_throttle response edge not observed")

    _drive_stream_throttle(credit=2, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 1, "stream_throttle should reopen after one response reduces outstanding count")
    _expect(sim, "req_ready_o", 1, "stream_throttle should reopen ready after one response")

    _drive_stream_throttle(credit=2, req_valid=1, req_ready=1, rsp_valid=1, rsp_ready=1)
    _expect(
        sim,
        "req_valid_o",
        1,
        "stream_throttle should still pass the request during a simultaneous request and response",
    )
    _expect(
        sim,
        "req_ready_o",
        1,
        "stream_throttle should still pass ready during a simultaneous request and response",
    )
    _run_until_rising_edge(
        sim,
        "clk",
        sim.time + 20,
        "stream_throttle simultaneous request/response edge not observed",
    )

    _drive_stream_throttle(credit=2, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 1, "stream_throttle simultaneous request/response should preserve outstanding count")
    _expect(sim, "req_ready_o", 1, "stream_throttle simultaneous request/response should preserve credit availability")

    _drive_stream_throttle(credit=1, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 0, "stream_throttle should block immediately when runtime credit is lowered")
    _expect(sim, "req_ready_o", 0, "stream_throttle should block ready immediately when runtime credit is lowered")

    _drive_stream_throttle(credit=1, req_valid=1, req_ready=1, rsp_valid=1, rsp_ready=1)
    _expect(sim, "req_valid_o", 0, "stream_throttle should stay blocked until the lowered-credit response is accepted")
    _expect(
        sim, "req_ready_o", 0, "stream_throttle should keep ready blocked until the lowered-credit response is accepted"
    )
    _run_until_rising_edge(
        sim,
        "clk",
        sim.time + 20,
        "stream_throttle lowered-credit response edge not observed",
    )

    _drive_stream_throttle(credit=1, req_valid=1, req_ready=1)
    _expect(sim, "req_valid_o", 1, "stream_throttle should reopen once outstanding count drops below lowered credit")
    _expect(
        sim, "req_ready_o", 1, "stream_throttle should reopen ready once outstanding count drops below lowered credit"
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_lossy_valid_to_stream_cross_engine(tmp_path, engine):
    design = _parse_lossy_valid_to_stream_design(tmp_path)
    sim = _make_step_sim(design, "lossy_valid_to_stream_tb_local", engine)

    def _drive_lossy(*, valid: int = 0, ready: int = 0, data: int = 0) -> None:
        step_drive(sim, engine, "valid_i", valid)
        step_drive(sim, engine, "ready_i", ready)
        step_drive(sim, engine, "data_i", data)
        _settle_drives(sim, engine)

    _expect(sim, "valid_o", 0, "lossy_valid_to_stream should be idle after reset")
    _expect(sim, "busy_o", 0, "lossy_valid_to_stream should not be busy after reset")

    _drive_lossy(valid=1, ready=1, data=0x11)
    _expect(sim, "valid_o", 1, "lossy_valid_to_stream should pass through valid when empty and ready")
    _expect(sim, "data_o", 0x11, "lossy_valid_to_stream should pass through the payload when empty and ready")
    _expect(sim, "busy_o", 0, "lossy_valid_to_stream pass-through should not mark the buffer busy")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "lossy_valid_to_stream pass-through edge not observed")
    _drive_lossy()
    _expect(sim, "valid_o", 0, "lossy_valid_to_stream should return idle after a pass-through transfer")
    _expect(sim, "busy_o", 0, "lossy_valid_to_stream should remain not busy after a pass-through transfer")

    _drive_lossy(valid=1, ready=0, data=0x22)
    _expect(sim, "valid_o", 1, "lossy_valid_to_stream should expose a stalled first value immediately")
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream should expose the stalled input payload immediately")
    _expect(sim, "busy_o", 0, "lossy_valid_to_stream should not mark busy until the stalled value is clocked in")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "lossy_valid_to_stream first stalled edge not observed")
    _drive_lossy(ready=0)
    _expect(sim, "valid_o", 1, "lossy_valid_to_stream should keep the first stalled value buffered")
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream buffered first value mismatch")
    _expect(sim, "busy_o", 1, "lossy_valid_to_stream should report busy once a value is buffered")

    _drive_lossy(valid=1, ready=0, data=0x33)
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream second stalled value should not replace the head yet")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "lossy_valid_to_stream second stalled edge not observed")
    _drive_lossy(ready=0)
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream head should remain oldest after filling the second slot")
    _expect(sim, "busy_o", 1, "lossy_valid_to_stream should stay busy after filling the second slot")

    _drive_lossy(valid=1, ready=0, data=0x44)
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream full overwrite should preserve the oldest head")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "lossy_valid_to_stream overwrite edge not observed")
    _drive_lossy(ready=0)
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream full overwrite should still leave the oldest head first")
    _expect(sim, "busy_o", 1, "lossy_valid_to_stream should stay busy after overwriting the newest slot")

    _drive_lossy(ready=1)
    _expect(sim, "valid_o", 1, "lossy_valid_to_stream should keep output valid while draining")
    _expect(sim, "data_o", 0x22, "lossy_valid_to_stream should drain the oldest buffered value first")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "lossy_valid_to_stream first drain edge not observed")
    _drive_lossy(ready=0)
    _expect(sim, "valid_o", 1, "lossy_valid_to_stream should still have one buffered value after first drain")
    _expect(sim, "data_o", 0x44, "lossy_valid_to_stream should expose the overwritten newest value second")
    _expect(sim, "busy_o", 1, "lossy_valid_to_stream should remain busy until the final buffered value drains")

    _drive_lossy(ready=1)
    _expect(
        sim, "data_o", 0x44, "lossy_valid_to_stream final drain should keep the newest buffered value at the output"
    )
    _run_until_rising_edge(sim, "clk", sim.time + 20, "lossy_valid_to_stream final drain edge not observed")
    _drive_lossy()
    _expect(sim, "valid_o", 0, "lossy_valid_to_stream should return idle after draining both buffered values")
    _expect(sim, "busy_o", 0, "lossy_valid_to_stream should clear busy after draining both buffered values")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_fork_cross_engine(tmp_path, engine):
    design = _parse_stream_fork_design(tmp_path)
    sim = _make_stream_fork_sim(design, engine)

    _expect(sim, "valid_o", 0b000, "stream_fork should be idle after reset")
    _expect(sim, "ready_o", 0, "stream_fork should not assert input ready after reset")

    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b111, "stream_fork should fan out valid to every output when a transfer starts")
    _expect(sim, "ready_o", 0, "stream_fork should wait for every output before accepting the input")

    step_drive(sim, engine, "ready_i", 0b001)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b111, "stream_fork should still present all outputs before the first handshake edge")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_fork first partial edge not observed")

    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b110, "stream_fork should remember that output 0 already handshaked")
    _expect(sim, "ready_o", 0, "stream_fork should keep input ready low while outputs remain pending")

    step_drive(sim, engine, "ready_i", 0b100)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b110, "stream_fork should keep only outputs 1 and 2 pending before the next edge")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_fork second partial edge not observed")

    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b010, "stream_fork should remember that only output 1 is still pending")
    _expect(sim, "ready_o", 0, "stream_fork should still block the input before the final output is served")

    step_drive(sim, engine, "ready_i", 0b010)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b010, "stream_fork should present only the last pending output before completion")
    _expect(sim, "ready_o", 1, "stream_fork should accept the input when the last pending output is ready")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_fork final handshake edge not observed")

    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b000, "stream_fork should return to idle after the transaction completes")

    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "ready_i", 0b111)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b111, "stream_fork should restart cleanly for a fully-ready transaction")
    _expect(sim, "ready_o", 1, "stream_fork should accept immediately when every output is ready")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_fork_dynamic_cross_engine(tmp_path, engine):
    design = _parse_stream_fork_dynamic_design(tmp_path)
    sim = _make_stream_fork_dynamic_sim(design, engine)

    _expect(sim, "valid_o", 0b000, "stream_fork_dynamic should be idle after reset")
    _expect(sim, "ready_o", 0, "stream_fork_dynamic should not assert input ready after reset")
    _expect(sim, "sel_ready_o", 0, "stream_fork_dynamic should not assert selector ready after reset")

    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "sel_i", 0b101)
    step_drive(sim, engine, "ready_i", 0b101)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b000, "stream_fork_dynamic should gate outputs until the selector stream is valid")
    _expect(sim, "ready_o", 0, "stream_fork_dynamic should gate input ready until the selector stream is valid")
    _expect(sim, "sel_ready_o", 0, "stream_fork_dynamic should keep selector ready low while selector valid is low")

    step_drive(sim, engine, "sel_valid_i", 1)
    step_drive(sim, engine, "ready_i", 0b010)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b101, "stream_fork_dynamic should fan out valid only to the selected outputs")
    _expect(sim, "ready_o", 0, "non-selected ready bits must not complete the dynamic fork transaction")
    _expect(sim, "sel_ready_o", 0, "selector ready must stay low until the selected subset completes")

    step_drive(sim, engine, "ready_i", 0b001)
    _settle_drives(sim, engine)
    _expect(
        sim,
        "valid_o",
        0b101,
        "stream_fork_dynamic should still present the full selected subset before the first handshake edge",
    )
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_fork_dynamic first partial edge not observed")

    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b100, "stream_fork_dynamic should remember that output 0 already handshaked")
    _expect(sim, "ready_o", 0, "stream_fork_dynamic should stay blocked while one selected output remains pending")

    step_drive(sim, engine, "ready_i", 0b100)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b100, "stream_fork_dynamic should present only the last selected output before completion")
    _expect(sim, "ready_o", 1, "stream_fork_dynamic should accept when the last selected output is ready")
    _expect(sim, "sel_ready_o", 1, "stream_fork_dynamic should accept the selector stream with the data handshake")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_fork_dynamic final masked edge not observed")

    step_drive(sim, engine, "valid_i", 0)
    step_drive(sim, engine, "sel_valid_i", 0)
    step_drive(sim, engine, "ready_i", 0b000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b000, "stream_fork_dynamic should return to idle after the transaction completes")
    _expect(sim, "ready_o", 0, "idle stream_fork_dynamic should not assert input ready")
    _expect(sim, "sel_ready_o", 0, "idle stream_fork_dynamic should not assert selector ready")

    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "sel_i", 0b010)
    step_drive(sim, engine, "sel_valid_i", 1)
    step_drive(sim, engine, "ready_i", 0b010)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b010, "stream_fork_dynamic should route a one-hot mask to the single selected output")
    _expect(sim, "ready_o", 1, "stream_fork_dynamic should accept immediately for a ready single-output mask")
    _expect(sim, "sel_ready_o", 1, "selector ready should track the accepted single-output transaction")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_arbiter_cross_engine(tmp_path, engine):
    design = _parse_stream_arbiter_design(tmp_path)
    sim = _make_stream_arbiter_sim(design, engine)

    _expect(sim, "oup_valid_o", 0, "stream_arbiter should be idle after reset")
    _expect(sim, "inp_ready_o", 0b0000, "stream_arbiter should not assert any input ready after reset")

    step_drive(sim, engine, "inp_data_i", _pack_stream_arbiter_inputs(0xA0, 0xB1, 0xC2, 0xD3))
    step_drive(sim, engine, "inp_valid_i", 0b0101)
    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 1, "stream_arbiter should assert output valid when any requester is active")
    _expect(sim, "oup_data_o", 0xA0, "stream_arbiter should grant input 0 first from reset priority")
    _expect(sim, "inp_ready_o", 0b0001, "stream_arbiter should return ready only to the granted requester")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter first grant edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xC2, "stream_arbiter should rotate to input 2 on the next accepted cycle")
    _expect(sim, "inp_ready_o", 0b0100, "stream_arbiter should move ready to the next granted requester")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter second grant edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xA0, "stream_arbiter should wrap back to input 0 after the second accepted cycle")
    _expect(
        sim, "inp_ready_o", 0b0001, "stream_arbiter should wrap ready back to input 0 after the second accepted cycle"
    )

    step_drive(sim, engine, "inp_valid_i", 0b1110)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 1, "stream_arbiter should keep output valid while requests are pending")
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter should select input 1 after the previous accepted grant")
    _expect(sim, "inp_ready_o", 0b0000, "stream_arbiter should not return ready while the output is stalled")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter stall edge not observed")

    step_drive(sim, engine, "inp_valid_i", 0b1100)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter should hold the selected payload stable while stalled")
    _expect(sim, "oup_valid_o", 1, "stream_arbiter should keep output valid asserted while locked")
    _expect(sim, "inp_ready_o", 0b0000, "stream_arbiter should keep ready low while stalled")

    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter should present the locked payload until acceptance")
    _expect(sim, "inp_ready_o", 0b0010, "stream_arbiter should release ready only to the locked requester")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter locked grant edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xC2, "stream_arbiter should advance to the next active requester after acceptance")
    _expect(sim, "inp_ready_o", 0b0100, "stream_arbiter should move ready to the next requester after acceptance")

    step_drive(sim, engine, "inp_valid_i", 0b1000)
    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xD3, "stream_arbiter should route the lone active requester payload")
    _expect(sim, "oup_valid_o", 1, "stream_arbiter should keep output valid high for a single requester")
    _expect(sim, "inp_ready_o", 0b1000, "stream_arbiter should return ready only to the lone active requester")

    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0, "stream_arbiter should return to idle when no requesters are active")
    _expect(sim, "inp_ready_o", 0b0000, "idle stream_arbiter should not assert any input ready")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_arbiter_flushable_cross_engine(tmp_path, engine):
    design = _parse_stream_arbiter_flushable_design(tmp_path)
    sim = _make_stream_arbiter_flushable_sim(design, engine)

    _expect(sim, "oup_valid_o", 0, "stream_arbiter_flushable should be idle after reset")
    _expect(sim, "inp_ready_o", 0b0000, "stream_arbiter_flushable should not assert any input ready after reset")

    step_drive(sim, engine, "inp_data_i", _pack_stream_arbiter_inputs(0xA0, 0xB1, 0xC2, 0xD3))
    step_drive(sim, engine, "inp_valid_i", 0b0011)
    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 1, "stream_arbiter_flushable should assert output valid for active requesters")
    _expect(sim, "oup_data_o", 0xA0, "stream_arbiter_flushable should grant input 0 first from reset priority")
    _expect(
        sim,
        "inp_ready_o",
        0b0001,
        "stream_arbiter_flushable should return ready only to the granted requester",
    )
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter_flushable first grant edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter_flushable should rotate to input 1 on the next cycle")
    _expect(
        sim,
        "inp_ready_o",
        0b0010,
        "stream_arbiter_flushable should move ready to the next granted requester",
    )

    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter_flushable should hold the selected payload while stalled")
    _expect(sim, "inp_ready_o", 0b0000, "stream_arbiter_flushable should not return ready while stalled")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter_flushable stall edge not observed")

    step_drive(sim, engine, "flush_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter_flushable should not reset until the flush edge occurs")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter_flushable flush edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xA0, "stream_arbiter_flushable flush should restore reset priority")
    _expect(
        sim,
        "oup_valid_o",
        1,
        "stream_arbiter_flushable should immediately present the reset-priority requester",
    )
    _expect(sim, "inp_ready_o", 0b0000, "stream_arbiter_flushable should keep ready low while stalled after flush")

    step_drive(sim, engine, "flush_i", 0)
    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "inp_ready_o", 0b0001, "stream_arbiter_flushable should re-grant input 0 after flush")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stream_arbiter_flushable post-flush grant edge not observed")
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xB1, "stream_arbiter_flushable should advance again after the post-flush grant")
    _expect(
        sim,
        "inp_ready_o",
        0b0010,
        "stream_arbiter_flushable should move ready to input 1 after the post-flush grant",
    )

    step_drive(sim, engine, "inp_valid_i", 0b1000)
    step_drive(sim, engine, "oup_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "oup_data_o", 0xD3, "stream_arbiter_flushable should route the lone active requester payload")
    _expect(
        sim,
        "oup_valid_o",
        1,
        "stream_arbiter_flushable should keep output valid high for a single requester",
    )
    _expect(
        sim,
        "inp_ready_o",
        0b1000,
        "stream_arbiter_flushable should return ready only to the lone active requester",
    )

    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0, "stream_arbiter_flushable should return to idle when no requesters are active")
    _expect(sim, "inp_ready_o", 0b0000, "idle stream_arbiter_flushable should not assert any input ready")


@pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
def test_typed_stream_xbar_cross_engine(tmp_path, engine):
    design = _parse_typed_stream_xbar_design(tmp_path)
    sim = _make_step_sim(design, "sxt0_tb", engine)

    _expect(sim, "valid_o", 0, "typed stream_xbar should be idle after reset")

    step_drive(sim, engine, "ready_i", 0b11)
    step_drive(sim, engine, "data_i", (0x3C2 << 24) | (0x2B1 << 12) | 0x1A0)
    step_drive(sim, engine, "sel_i", 0b100)
    step_drive(sim, engine, "valid_i", 0b101)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 0b101, "typed stream_xbar should only ready the selected active inputs")
    _expect(sim, "valid_o", 0b11, "typed stream_xbar should route two independent outputs")
    _expect(sim, "data_o", 0x3C21A0, "typed stream_xbar payload/meta routing mismatch")
    _expect(sim, "idx_o", 0b1000, "typed stream_xbar idx routing mismatch")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed stream_xbar independent-routing edge not observed")

    step_drive(sim, engine, "data_i", (0x632 << 24) | (0x521 << 12) | 0x410)
    step_drive(sim, engine, "sel_i", 0b000)
    step_drive(sim, engine, "valid_i", 0b011)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b01, "typed stream_xbar should contend on output 0")
    _expect(sim, "data_o", 0x410, "typed stream_xbar should grant input 0 first")
    _expect(sim, "idx_o", 0b0000, "typed stream_xbar should report input 0 first")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed stream_xbar contention edge not observed")

    _expect(sim, "data_o", 0x521, "typed stream_xbar should rotate to input 1")
    _expect(sim, "idx_o", 0b0001, "typed stream_xbar should report input 1 after rotation")


@pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
def test_typed_stream_xbar_flush_reset_cross_engine(tmp_path, engine):
    design = _parse_typed_stream_xbar_design(tmp_path)
    sim = _make_step_sim(design, "sxt0_tb", engine)

    step_drive(sim, engine, "ready_i", 0b01)
    step_drive(sim, engine, "data_i", _pack_typed_stream_inputs(0x1A0, 0x2B1, 0x000))
    step_drive(sim, engine, "sel_i", 0b000)
    step_drive(sim, engine, "valid_i", 0b011)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b01, "typed stream_xbar flush test should drive only output 0 during contention")
    _expect(sim, "data_o", 0x1A0, "typed stream_xbar flush test should grant input 0 first")
    _expect(sim, "idx_o", 0b0000, "typed stream_xbar flush test should report input 0 first")
    _expect(sim, "ready_o", 0b001, "typed stream_xbar flush test should only ready the first contender initially")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed stream_xbar flush pre-rotation edge not observed")
    _expect(sim, "data_o", 0x2B1, "typed stream_xbar flush test should rotate to input 1 before flush")
    _expect(sim, "idx_o", 0b0001, "typed stream_xbar flush test should report input 1 before flush")
    _expect(sim, "ready_o", 0b010, "typed stream_xbar flush test should ready the second contender before flush")

    step_drive(sim, engine, "flush", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed stream_xbar flush edge not observed")
    step_drive(sim, engine, "flush", 0)
    _settle_drives(sim, engine)

    _expect(sim, "data_o", 0x1A0, "typed stream_xbar flush should restore input 0 priority")
    _expect(sim, "idx_o", 0b0000, "typed stream_xbar flush should restore input 0 index")
    _expect(sim, "ready_o", 0b001, "typed stream_xbar flush should restore first-contender ready")


@pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
def test_typed_stream_xbar_spill_cross_engine(tmp_path, engine):
    design = _parse_typed_stream_xbar_design(tmp_path)
    sim = _make_step_sim(design, "sxt1_tb", engine)

    _expect(sim, "valid_o", 0, "typed spill stream_xbar should be idle after reset")

    step_drive(sim, engine, "ready_i", 0b11)
    step_drive(sim, engine, "data_i", (0x3C2 << 24) | (0x2B1 << 12) | 0x1A0)
    step_drive(sim, engine, "sel_i", 0b100)
    step_drive(sim, engine, "valid_i", 0b101)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 0b101, "typed spill stream_xbar should expose ready before the spill stage fills")
    _expect(sim, "valid_o", 0, "typed spill stream_xbar should wait one cycle before producing output")
    _expect(sim, "data_o", 0, "typed spill stream_xbar should hold zero data before the first cycle")
    _expect(sim, "idx_o", 0, "typed spill stream_xbar should hold zero idx before the first cycle")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed spill stream_xbar first edge not observed")

    _expect(sim, "ready_o", 0b101, "typed spill stream_xbar should present both routed items after one cycle")
    _expect(sim, "valid_o", 0b11, "typed spill stream_xbar should output both lanes after the spill stage fills")
    _expect(sim, "data_o", 0x3C21A0, "typed spill stream_xbar first routed payload mismatch")
    _expect(sim, "idx_o", 0b1000, "typed spill stream_xbar first routed idx mismatch")

    step_drive(sim, engine, "data_i", (0x632 << 24) | (0x521 << 12) | 0x410)
    step_drive(sim, engine, "sel_i", 0b000)
    step_drive(sim, engine, "valid_i", 0b011)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 0b001, "typed spill stream_xbar should backpressure input 1 while output 0 is queued")
    _expect(sim, "valid_o", 0b11, "typed spill stream_xbar should keep prior outputs valid until the next edge")
    _expect(sim, "data_o", 0x3C21A0, "typed spill stream_xbar should retain prior data before the next edge")
    _expect(sim, "idx_o", 0b1000, "typed spill stream_xbar should retain prior idx before the next edge")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed spill stream_xbar second edge not observed")

    _expect(sim, "ready_o", 0b010, "typed spill stream_xbar should rotate ready after output 0 advances")
    _expect(sim, "valid_o", 0b01, "typed spill stream_xbar should leave only output 0 valid after contention")
    _expect(sim, "data_o", 0x3C2410, "typed spill stream_xbar should queue input 0 behind the existing output 1 item")
    _expect(sim, "idx_o", 0b1000, "typed spill stream_xbar should keep output 1 idx while output 0 advances")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed spill stream_xbar third edge not observed")

    _expect(sim, "ready_o", 0b001, "typed spill stream_xbar should return ready to input 0 after the queued transfer")
    _expect(sim, "valid_o", 0b01, "typed spill stream_xbar should still only drive output 0 on the queued item")
    _expect(sim, "data_o", 0x3C2521, "typed spill stream_xbar should advance output 0 to the queued input 1 item")
    _expect(sim, "idx_o", 0b1001, "typed spill stream_xbar should advance output 0 idx to input 1")


@pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
def test_typed_stream_xbar_spill_flush_reset_cross_engine(tmp_path, engine):
    design = _parse_typed_stream_xbar_design(tmp_path)
    sim = _make_step_sim(design, "sxt1_tb", engine)

    step_drive(sim, engine, "ready_i", 0b00)
    step_drive(sim, engine, "data_i", _pack_typed_stream_inputs(0x1A0, 0x2B1, 0x000))
    step_drive(sim, engine, "sel_i", 0b000)
    step_drive(sim, engine, "valid_i", 0b011)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b00, "typed spill stream_xbar flush test should stay empty before capture")
    _expect(sim, "ready_o", 0b001, "typed spill stream_xbar flush test should grant the first contender before capture")

    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed spill stream_xbar flush first capture edge not observed")
    _expect(sim, "valid_o", 0b01, "typed spill stream_xbar flush test should expose the first buffered contender")
    _expect(sim, "data_o", 0x1A0, "typed spill stream_xbar flush test should buffer input 0 first")
    _expect(sim, "idx_o", 0b0000, "typed spill stream_xbar flush test should report input 0 first")
    _expect(sim, "ready_o", 0b010, "typed spill stream_xbar flush test should advance ready to the second contender")

    step_drive(sim, engine, "valid_i", 0b000)
    _settle_drives(sim, engine)
    step_drive(sim, engine, "flush", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed spill stream_xbar flush edge not observed")
    step_drive(sim, engine, "flush", 0)
    _settle_drives(sim, engine)

    _expect(sim, "valid_o", 0b01, "typed spill stream_xbar flush should preserve the buffered head item")
    _expect(sim, "data_o", 0x1A0, "typed spill stream_xbar flush should not disturb the buffered head payload")
    _expect(sim, "idx_o", 0b0000, "typed spill stream_xbar flush should not disturb the buffered head index")

    step_drive(sim, engine, "ready_i", 0b01)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed spill stream_xbar flush drain edge not observed")
    _expect(
        sim,
        "valid_o",
        0b00,
        "typed spill stream_xbar flush drain should empty the spill path when no contender is queued",
    )

    step_drive(sim, engine, "ready_i", 0b00)
    step_drive(sim, engine, "data_i", _pack_typed_stream_inputs(0x410, 0x521, 0x000))
    step_drive(sim, engine, "sel_i", 0b000)
    step_drive(sim, engine, "valid_i", 0b011)
    _settle_drives(sim, engine)
    _expect(
        sim, "ready_o", 0b001, "typed spill stream_xbar flush should restore first-contender priority on the next wave"
    )

    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed spill stream_xbar flush replay edge not observed")
    _expect(sim, "valid_o", 0b01, "typed spill stream_xbar flush replay should buffer one contender")
    _expect(sim, "data_o", 0x410, "typed spill stream_xbar flush should grant input 0 first on the next wave")
    _expect(sim, "idx_o", 0b0000, "typed spill stream_xbar flush should report input 0 first on the next wave")


@pytest.mark.parametrize("engine", ENGINES)
def test_typed_spill_register_cross_engine(tmp_path, engine):
    design = _parse_typed_stream_xbar_design(tmp_path)
    sim = _make_step_sim(design, "spt0_tb", engine)

    _expect(sim, "valid_o", 0, "typed spill_register should be empty after reset")
    _expect(sim, "ready_o", 1, "typed spill_register should be ready after reset")

    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x1A0)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed spill_register capture edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "typed spill_register should hold the stalled item")
    _expect(sim, "data_o", 0x1A0, "typed spill_register first payload mismatch")
    _expect(sim, "ready_o", 1, "typed spill_register should still accept one more item")

    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x2B1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed spill_register second capture edge not observed")
    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "data_o", 0x1A0, "typed spill_register must preserve ordering under backpressure")
    _expect(sim, "ready_o", 0, "typed spill_register should backpressure once both stages are occupied")

    step_drive(sim, engine, "ready_i", 1)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed spill_register drain edge not observed")
    _expect(sim, "valid_o", 1, "typed spill_register should expose the second buffered item after a drain")
    _expect(sim, "data_o", 0x2B1, "typed spill_register second payload mismatch")

    sim = _make_step_sim(design, "spt1_tb", engine)
    _expect(sim, "valid_o", 0, "typed spill_register bypass should be empty after reset")
    _expect(sim, "ready_o", 0, "typed spill_register bypass should follow downstream ready after reset")

    step_drive(sim, engine, "ready_i", 0)
    step_drive(sim, engine, "valid_i", 1)
    step_drive(sim, engine, "data_i", 0x3C2)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 1, "typed spill_register bypass should pass valid combinationally")
    _expect(sim, "ready_o", 0, "typed spill_register bypass should pass ready combinationally")
    _expect(sim, "data_o", 0x3C2, "typed spill_register bypass should pass data combinationally")

    step_drive(sim, engine, "ready_i", 1)
    step_drive(sim, engine, "data_i", 0x4D3)
    _settle_drives(sim, engine)
    _expect(sim, "ready_o", 1, "typed spill_register bypass should reopen immediately")
    _expect(sim, "valid_o", 1, "typed spill_register bypass should remain transparent")
    _expect(sim, "data_o", 0x4D3, "typed spill_register bypass should update data immediately")

    step_drive(sim, engine, "valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0, "typed spill_register bypass should clear valid immediately")


@pytest.mark.parametrize("engine", ENGINES)
def test_typed_stream_xbar_demux_cross_engine(tmp_path, engine):
    design = _parse_typed_stream_xbar_design(tmp_path)
    top = design.get_module("dmt0_tb")
    assert top is not None, "Top module 'dmt0_tb' not found"

    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=0)
    step_drive(sim, engine, "inp_valid_i", 0)
    step_drive(sim, engine, "oup_sel_i", 0)
    step_drive(sim, engine, "oup_ready_i", 0)
    _settle_drives(sim, engine)

    _expect(sim, "oup_valid_o", 0b000, "typed stream_xbar demux should be idle when input valid is low")
    _expect(sim, "inp_ready_o", 0, "typed stream_xbar demux ready should reflect selected output ready")

    step_drive(sim, engine, "oup_ready_i", 0b001)
    _settle_drives(sim, engine)
    _expect(sim, "inp_ready_o", 1, "typed stream_xbar demux should return selected output 0 ready when idle")

    step_drive(sim, engine, "inp_valid_i", 1)
    step_drive(sim, engine, "oup_sel_i", 1)
    step_drive(sim, engine, "oup_ready_i", 0b001)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0b010, "typed stream_xbar demux should assert only selected output 1 valid")
    _expect(sim, "inp_ready_o", 0, "typed stream_xbar demux input ready should follow selected output 1 ready")

    step_drive(sim, engine, "oup_ready_i", 0b010)
    _settle_drives(sim, engine)
    _expect(sim, "inp_ready_o", 1, "typed stream_xbar demux should return selected output 1 ready")

    step_drive(sim, engine, "oup_sel_i", 2)
    step_drive(sim, engine, "oup_ready_i", 0b100)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0b100, "typed stream_xbar demux should reroute valid immediately when select changes")
    _expect(sim, "inp_ready_o", 1, "typed stream_xbar demux should reroute ready immediately when select changes")

    step_drive(sim, engine, "inp_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "oup_valid_o", 0b000, "typed stream_xbar demux should clear valid fanout when input valid drops")
    _expect(sim, "inp_ready_o", 1, "typed stream_xbar demux ready should still reflect selected output when idle")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_to_mem_cross_engine(tmp_path, engine):
    design = _parse_stream_to_mem_design(tmp_path)

    sim = _make_stream_to_mem_sim(design, "stream_to_mem_vm_buf0", engine)
    step_drive(sim, engine, "resp_ready_i", 1)
    step_drive(sim, engine, "req_i", 0x1234)
    step_drive(sim, engine, "req_valid_i", 1)
    step_drive(sim, engine, "mem_resp_i", 0x12CB)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "req_ready_o", 1, "buf0 request should be accepted")
    _expect(sim, "mem_req_valid_o", 1, "buf0 memory request should be valid")
    _expect(sim, "resp_valid_o", 1, "buf0 response should be valid")
    _expect(sim, "resp_o", 0x12CB, "buf0 response payload mismatch")
    step_drive(sim, engine, "req_valid_i", 0)
    step_drive(sim, engine, "mem_resp_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 0, "buf0 response should clear after handshake")

    sim = _make_stream_to_mem_sim(design, "stream_to_mem_vm_buf1", engine)
    step_drive(sim, engine, "req_i", 0x0011)
    step_drive(sim, engine, "req_valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "req_ready_o", 1, "buf1 first request should be accepted")
    step_run_until(sim, 40)
    step_drive(sim, engine, "req_valid_i", 0)
    step_run_until(sim, 50)
    step_drive(sim, engine, "mem_resp_i", 0x0111)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 1, "buf1 first response should be visible")
    _expect(sim, "resp_o", 0x0111, "buf1 first response payload mismatch")
    step_drive(sim, engine, "req_i", 0x0022)
    step_drive(sim, engine, "req_valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "req_ready_o", 0, "buf1 second request should stall while response is blocked")
    _expect(sim, "mem_req_valid_o", 0, "buf1 memory request should be blocked while stalled")
    step_run_until(sim, 60)
    step_drive(sim, engine, "mem_resp_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 1, "buf1 buffered response should remain valid")
    _expect(sim, "resp_o", 0x0111, "buf1 buffered response mismatch")
    step_drive(sim, engine, "resp_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "req_ready_o", 1, "buf1 second request should reopen while draining")
    _expect(sim, "mem_req_valid_o", 1, "buf1 second request should reach memory while draining")
    step_run_until(sim, 70)
    step_drive(sim, engine, "req_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 0, "buf1 should have a one-cycle bubble before second response")
    step_run_until(sim, 80)
    step_drive(sim, engine, "mem_resp_i", 0x0122)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 1, "buf1 second response should be visible")
    _expect(sim, "resp_o", 0x0122, "buf1 second response payload mismatch")
    step_drive(sim, engine, "mem_resp_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 0, "buf1 response should clear after second handshake")

    sim = _make_stream_to_mem_sim(design, "stream_to_mem_vm_buf2", engine)
    step_drive(sim, engine, "req_i", 0x0033)
    step_drive(sim, engine, "req_valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "req_ready_o", 1, "buf2 first request should be accepted")
    step_run_until(sim, 40)
    step_drive(sim, engine, "req_i", 0x0044)
    step_drive(sim, engine, "req_valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "req_ready_o", 1, "buf2 second request should be accepted")
    step_run_until(sim, 50)
    step_drive(sim, engine, "req_i", 0x0055)
    step_drive(sim, engine, "req_valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "req_ready_o", 0, "buf2 third request should stall at outstanding limit")
    step_run_until(sim, 60)
    step_drive(sim, engine, "mem_resp_i", 0x0233)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "req_ready_o", 0, "buf2 third request should stall at outstanding limit")
    _expect(sim, "resp_valid_o", 1, "buf2 first response should be visible")
    _expect(sim, "resp_o", 0x0233, "buf2 first response payload mismatch")
    step_run_until(sim, 70)
    step_drive(sim, engine, "mem_resp_i", 0x0244)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 1, "buf2 first buffered response should remain valid")
    _expect(sim, "resp_o", 0x0233, "buf2 first buffered response should stay stable")
    step_run_until(sim, 80)
    step_drive(sim, engine, "mem_resp_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 1, "buf2 buffered responses should remain available")
    _expect(sim, "resp_o", 0x0233, "buf2 head response mismatch before drain")
    step_drive(sim, engine, "resp_ready_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "req_ready_o", 1, "buf2 third request should reopen while draining")
    _expect(sim, "mem_req_valid_o", 1, "buf2 third request should reach memory while draining")
    step_run_until(sim, 90)
    step_drive(sim, engine, "req_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 1, "buf2 second response should now be visible")
    _expect(sim, "resp_o", 0x0244, "buf2 second response payload mismatch")
    step_run_until(sim, 100)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 0, "buf2 should have a one-cycle bubble before third response")
    step_run_until(sim, 110)
    step_drive(sim, engine, "mem_resp_i", 0x0255)
    step_drive(sim, engine, "mem_resp_valid_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 1, "buf2 third response should be visible")
    _expect(sim, "resp_o", 0x0255, "buf2 third response payload mismatch")
    step_drive(sim, engine, "mem_resp_valid_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "resp_valid_o", 0, "buf2 response should clear after final handshake")


@pytest.mark.parametrize("engine", ENGINES)
def test_rr_arb_tree_cross_engine(tmp_path, engine):
    design = _parse_rr_arb_tree_design(tmp_path)
    if engine == "reference":
        top = design.get_module("rr_arb_tree_tb_local")
        assert top is not None, "Top module 'rr_arb_tree_tb_local' not found"
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=120)
        lines = display_lines(sim)
        assert not any("FAIL" in line for line in lines), f"reference rr_arb_tree failed: {lines}"
        assert any("PASS" in line for line in lines), f"reference rr_arb_tree produced no PASS marker: {lines}"
        return

    sim = _make_rr_arb_tree_sim(design, engine)

    step_run_until(sim, 22)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 26)
    _expect_rr_arb_state(sim, 0, 0, 0x00, 0b0000, "idle after reset")

    step_drive(sim, engine, "req", 0b0101)
    step_drive(sim, engine, "gnt_oup", 1)
    _settle_drives(sim, engine)
    _expect_rr_arb_state(sim, 1, 0, 0xA0, 0b0001, "first round robin grant")
    step_run_until(sim, 36)
    _expect_rr_arb_state(sim, 1, 2, 0xC2, 0b0100, "second round robin grant")
    step_run_until(sim, 46)
    _expect_rr_arb_state(sim, 1, 0, 0xA0, 0b0001, "wrapped round robin grant")

    step_drive(sim, engine, "req", 0b0110)
    step_drive(sim, engine, "gnt_oup", 0)
    _settle_drives(sim, engine)
    _expect_rr_arb_state(sim, 1, 1, 0xB1, 0b0000, "lock selection while stalled")
    step_run_until(sim, 56)
    _expect_rr_arb_state(sim, 1, 1, 0xB1, 0b0000, "locked selection remains stable")

    step_drive(sim, engine, "gnt_oup", 1)
    _settle_drives(sim, engine)
    _expect_rr_arb_state(sim, 1, 1, 0xB1, 0b0010, "locked request granted")
    step_run_until(sim, 66)
    _expect_rr_arb_state(sim, 1, 1, 0xB1, 0b0010, "priority state updates after locked grant")
    step_run_until(sim, 76)
    _expect_rr_arb_state(sim, 1, 2, 0xC2, 0b0100, "round robin advances on the next accepted cycle")

    step_drive(sim, engine, "flush", 1)
    step_run_until(sim, 85)
    step_drive(sim, engine, "flush", 0)
    step_drive(sim, engine, "req", 0b0011)
    _settle_drives(sim, engine)
    step_run_until(sim, 86)
    _expect_rr_arb_state(sim, 1, 0, 0xA0, 0b0001, "flush resets priority state")

    step_drive(sim, engine, "req", 0b1000)
    _settle_drives(sim, engine)
    step_run_until(sim, 87)
    _expect_rr_arb_state(sim, 1, 3, 0xD3, 0b1000, "single active requester routes data")


@pytest.mark.parametrize("engine", ENGINES)
def test_typed_rr_arb_tree_cross_engine(tmp_path, engine):
    design = _parse_typed_stream_xbar_design(tmp_path)
    if engine == "reference":
        top = design.get_module("rrt0_tb_local")
        assert top is not None, "Top module 'rrt0_tb_local' not found"
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=120)
        lines = display_lines(sim)
        assert not any("FAIL" in line for line in lines), f"reference typed rr_arb_tree failed: {lines}"
        assert any("PASS" in line for line in lines), f"reference typed rr_arb_tree produced no PASS marker: {lines}"
        return

    sim = _make_typed_rr_arb_tree_sim(design, engine)

    _expect(sim, "req_o", 0, "typed rr_arb_tree should be idle after reset")
    _expect(sim, "idx_o", 0, "typed rr_arb_tree should reset idx")
    _expect(sim, "data_o", 0, "typed rr_arb_tree should reset payload")
    _expect(sim, "gnt_o", 0, "typed rr_arb_tree should reset grants")

    step_drive(sim, engine, "data_i", _pack_typed_stream_inputs(0x1A0, 0x2B1, 0x3C2))
    step_drive(sim, engine, "req_i", 0b101)
    step_drive(sim, engine, "gnt_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "req_o", 1, "typed rr_arb_tree should assert request for active inputs")
    _expect(sim, "idx_o", 0, "typed rr_arb_tree should grant input 0 first")
    _expect(sim, "data_o", 0x1A0, "typed rr_arb_tree should route input 0 payload first")
    _expect(sim, "gnt_o", 0b001, "typed rr_arb_tree should grant only input 0 first")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed rr_arb_tree first grant edge not observed")

    _expect(sim, "idx_o", 2, "typed rr_arb_tree should rotate to input 2")
    _expect(sim, "data_o", 0x3C2, "typed rr_arb_tree should route input 2 payload after rotation")
    _expect(sim, "gnt_o", 0b100, "typed rr_arb_tree should grant only input 2 after rotation")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed rr_arb_tree wrapped grant edge not observed")

    _expect(sim, "idx_o", 0, "typed rr_arb_tree should wrap back to input 0")
    _expect(sim, "data_o", 0x1A0, "typed rr_arb_tree should wrap payload routing")
    _expect(sim, "gnt_o", 0b001, "typed rr_arb_tree should wrap grant routing")

    step_drive(sim, engine, "req_i", 0b110)
    step_drive(sim, engine, "gnt_i", 0)
    _settle_drives(sim, engine)
    _expect(sim, "idx_o", 1, "typed rr_arb_tree should lock to input 1 while stalled")
    _expect(sim, "data_o", 0x2B1, "typed rr_arb_tree should preserve locked payload while stalled")
    _expect(sim, "gnt_o", 0b000, "typed rr_arb_tree should not grant while downstream stalls")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed rr_arb_tree stall edge not observed")

    _expect(sim, "idx_o", 1, "typed rr_arb_tree should keep locked idx stable")
    _expect(sim, "data_o", 0x2B1, "typed rr_arb_tree should keep locked payload stable")
    _expect(sim, "gnt_o", 0b000, "typed rr_arb_tree should keep grant low while stalled")

    step_drive(sim, engine, "gnt_i", 1)
    _settle_drives(sim, engine)
    _expect(sim, "idx_o", 1, "typed rr_arb_tree should keep the locked requester selected when ready returns")
    _expect(sim, "data_o", 0x2B1, "typed rr_arb_tree should keep the locked payload selected when ready returns")
    _expect(sim, "gnt_o", 0b010, "typed rr_arb_tree should grant the locked requester when ready")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed rr_arb_tree locked grant edge not observed")

    _expect(sim, "idx_o", 1, "typed rr_arb_tree should retain the granted requester for the current cycle")
    _expect(sim, "data_o", 0x2B1, "typed rr_arb_tree should retain the granted payload for the current cycle")
    _expect(sim, "gnt_o", 0b010, "typed rr_arb_tree should retain the current grant for the current cycle")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed rr_arb_tree post-grant advance edge not observed")

    _expect(sim, "idx_o", 2, "typed rr_arb_tree should advance after the locked grant")
    _expect(sim, "data_o", 0x3C2, "typed rr_arb_tree should route the next requester after unlock")
    _expect(sim, "gnt_o", 0b100, "typed rr_arb_tree should grant the next requester after unlock")

    step_drive(sim, engine, "flush", 1)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "typed rr_arb_tree flush edge not observed")
    step_drive(sim, engine, "flush", 0)
    step_drive(sim, engine, "req_i", 0b011)
    _settle_drives(sim, engine)
    _expect(sim, "idx_o", 0, "typed rr_arb_tree flush should restore priority to input 0")
    _expect(sim, "data_o", 0x1A0, "typed rr_arb_tree flush should restore input 0 payload")
    _expect(sim, "gnt_o", 0b001, "typed rr_arb_tree flush should restore input 0 grant")


@pytest.mark.parametrize("engine", ENGINES)
def test_cdc_fifo_cross_engine(tmp_path, engine):
    design = _parse_cdc_fifo_design(tmp_path)

    sim = _make_cdc_fifo_sim(design, engine)
    _release_cdc_fifo_reset(sim, engine)

    step_drive(sim, engine, "src_data_i", 0x11)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 90, "source write edge not observed for first transfer")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        160,
        lambda s: _read_int(s, "dst_valid_o") == 1,
        "first transfer never became visible at the destination",
    )
    _expect(sim, "dst_data_o", 0x11, "destination data mismatch for first transfer")
    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        210,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "first transfer never drained from the destination",
    )
    _expect(sim, "src_ready_o", 1, "source should remain ready after one transfer")

    sim = _make_cdc_fifo_sim(design, engine)
    _release_cdc_fifo_reset(sim, engine)
    step_drive(sim, engine, "src_data_i", 0x11)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 90, "source write edge not observed for first queued item")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    step_drive(sim, engine, "src_data_i", 0x22)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 130, "source write edge not observed for second queued item")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        130,
        lambda s: _read_int(s, "src_ready_o") == 0,
        "fifo never reported full after two queued items",
    )
    _run_until_condition(
        sim,
        170,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x11,
        "first queued item never appeared at the destination",
    )
    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        230,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x22,
        "second queued item never surfaced after draining the first",
    )
    _run_until_condition(
        sim,
        260,
        lambda s: _read_int(s, "src_ready_o") == 1,
        "source ready never reopened after draining the fifo",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_cdc_2phase_cross_engine(tmp_path, engine):
    design = _parse_cdc_2phase_design(tmp_path)

    sim = _make_cdc_2phase_sim(design, engine)
    _release_cdc_fifo_reset(sim, engine)

    step_drive(sim, engine, "src_data_i", 0x11)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 90, "source write edge not observed for first transfer")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _expect(sim, "src_ready_o", 0, "source should backpressure while the first transfer is in flight")
    _run_until_condition(
        sim,
        160,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x11,
        "first transfer never became visible at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should remain blocked until the destination acknowledges")

    step_drive(sim, engine, "src_data_i", 0x22)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 190, "blocked source attempt edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _expect(sim, "src_ready_o", 0, "blocked source attempt should not reopen ready")
    _expect(sim, "dst_data_o", 0x11, "blocked source attempt should not overwrite the in-flight payload")

    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        220,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "first transfer never drained from the destination",
    )
    _run_until_condition(
        sim,
        260,
        lambda s: _read_int(s, "src_ready_o") == 1,
        "source ready never reopened after the first acknowledgement",
    )
    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine, "src_clk_i")

    step_drive(sim, engine, "src_data_i", 0x33)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 280, "source write edge not observed for second transfer")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        310,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x33,
        "second transfer never became visible at the destination",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_cdc_2phase_clearable_cross_engine(tmp_path, engine):
    design = _parse_cdc_2phase_clearable_design(tmp_path)

    sim = _make_cdc_2phase_clearable_sim(design, engine)
    _release_cdc_2phase_clearable_reset(sim, engine)

    step_drive(sim, engine, "src_data_i", 0x11)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 90, "source-clear first transfer write edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        180,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x11,
        "source-clear first transfer never became visible at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should remain blocked while the pre-clear transfer is pending")

    step_drive(sim, engine, "src_clear_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 220, "source clear edge not observed")
    step_drive(sim, engine, "src_clear_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
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

    step_drive(sim, engine, "src_data_i", 0x22)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 1240, "post-source-clear recovery write edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        1330,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x22,
        "post-source-clear recovery transfer never became visible at the destination",
    )
    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        1385,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "post-source-clear recovery transfer never drained from the destination",
    )
    step_drive(sim, engine, "dst_ready_i", 0)
    _settle_drives(sim, engine, "src_clk_i")

    sim = _make_cdc_2phase_clearable_sim(design, engine)
    _release_cdc_2phase_clearable_reset(sim, engine)

    step_drive(sim, engine, "src_data_i", 0x33)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 90, "destination-clear first transfer write edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        180,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x33,
        "destination-clear first transfer never became visible at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should remain blocked while the destination-clear transfer is pending")

    step_drive(sim, engine, "dst_clear_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "dst_clk_i", 220, "destination clear edge not observed")
    step_drive(sim, engine, "dst_clear_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
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

    step_drive(sim, engine, "src_data_i", 0x44)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 1240, "post-destination-clear recovery write edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        1330,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x44,
        "post-destination-clear recovery transfer never became visible at the destination",
    )
    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        1385,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "post-destination-clear recovery transfer never drained from the destination",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_cdc_2phase_clearable_async_reset_cross_engine(tmp_path, engine):
    design = _parse_cdc_2phase_clearable_design(tmp_path)

    sim = _make_cdc_2phase_clearable_sim_for_top(design, "cdc_2phase_clearable_async_reset_tb_local", engine)
    _release_cdc_2phase_clearable_async_reset(sim, engine)

    step_drive(sim, engine, "src_data_i", 0x55)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 1240, "pre-async-reset transfer write edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        1330,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x55,
        "pre-async-reset transfer never became visible at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should remain blocked while the async-reset transfer is pending")

    step_drive(sim, engine, "src_rst_ni", 0)
    _settle_drives(sim, engine, "src_clk_i")
    step_run_until(sim, sim.time + 24)
    step_drive(sim, engine, "src_rst_ni", 1)
    _settle_drives(sim, engine, "src_clk_i")

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
    step_drive(sim, engine, "src_data_i", 0x66)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(
        sim,
        "src_clk_i",
        recovery_start + 80,
        "post-async-reset recovery transfer write edge not observed",
    )
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        recovery_start + 180,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x66,
        "post-async-reset recovery transfer never became visible at the destination",
    )
    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        recovery_start + 260,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "post-async-reset recovery transfer never drained from the destination",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_cdc_2phase_clearable_dst_async_reset_cross_engine(tmp_path, engine):
    design = _parse_cdc_2phase_clearable_design(tmp_path)

    sim = _make_cdc_2phase_clearable_sim_for_top(design, "cdc_2phase_clearable_async_reset_tb_local", engine)
    _release_cdc_2phase_clearable_async_reset(sim, engine)

    step_drive(sim, engine, "src_data_i", 0x77)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 1240, "pre-destination-async-reset transfer write edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        1330,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x77,
        "pre-destination-async-reset transfer never became visible at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should remain blocked while the destination async-reset transfer is pending")

    step_drive(sim, engine, "dst_rst_ni", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _expect(sim, "dst_valid_o", 0, "destination async reset should clear visible valid state immediately")
    step_run_until(sim, sim.time + 24)
    step_drive(sim, engine, "dst_rst_ni", 1)
    _settle_drives(sim, engine, "src_clk_i")

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
    step_drive(sim, engine, "src_data_i", 0x88)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(
        sim,
        "src_clk_i",
        recovery_start + 80,
        "post-destination-async-reset recovery transfer write edge not observed",
    )
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        recovery_start + 180,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x88,
        "post-destination-async-reset recovery transfer never became visible at the destination",
    )
    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        recovery_start + 260,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "post-destination-async-reset recovery transfer never drained from the destination",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_cdc_fifo_gray_cross_engine(tmp_path, engine):
    design = _parse_cdc_fifo_gray_design(tmp_path)

    sim = _make_cdc_fifo_gray_sim(design, engine)
    _release_cdc_fifo_reset(sim, engine)

    step_drive(sim, engine, "src_data_i", 0x11)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 90, "source write edge not observed for first transfer")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        160,
        lambda s: _read_int(s, "dst_valid_o") == 1,
        "first transfer never became visible at the destination",
    )
    _expect(sim, "dst_data_o", 0x11, "destination data mismatch for first transfer")

    step_drive(sim, engine, "src_data_i", 0x22)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 200, "source write edge not observed for second queued item")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        200,
        lambda s: _read_int(s, "src_ready_o") == 0,
        "gray fifo never reported full after two queued items",
    )

    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        250,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x22,
        "second queued item never surfaced after draining the first",
    )
    _run_until_condition(
        sim,
        280,
        lambda s: _read_int(s, "src_ready_o") == 1,
        "source ready never reopened after draining the gray fifo",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_cdc_4phase_cross_engine(tmp_path, engine):
    design = _parse_cdc_4phase_design(tmp_path)

    sim = _make_cdc_4phase_sim(design, "cdc_4phase_tb_local", engine)
    _release_cdc_4phase_reset(sim, engine, expected_src_ready=1)

    step_drive(sim, engine, "src_data_i", 0x11)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 90, "decoupled source write edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        180,
        lambda s: (
            _read_int(s, "src_ready_o") == 1 and _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x11
        ),
        "decoupled source never reopened while the stalled destination held the first payload",
    )

    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        230,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "decoupled first payload never drained after destination acknowledgement",
    )

    step_drive(sim, engine, "src_data_i", 0x22)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 250, "decoupled second source write edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        320,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x22,
        "decoupled second payload never surfaced after draining the first",
    )
    _run_until_condition(
        sim,
        340,
        lambda s: _read_int(s, "src_ready_o") == 1,
        "decoupled source ready never recovered after draining",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_cdc_4phase_nondecoupled_cross_engine(tmp_path, engine):
    design = _parse_cdc_4phase_design(tmp_path)

    sim = _make_cdc_4phase_sim(design, "cdc_4phase_nondecoupled_tb_local", engine)
    _release_cdc_4phase_reset(sim, engine, expected_src_ready=1)

    step_drive(sim, engine, "src_data_i", 0x33)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 90, "non-decoupled source write edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _expect(sim, "src_ready_o", 0, "non-decoupled source should stay blocked while the destination stalls")

    step_drive(sim, engine, "src_data_i", 0x44)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        180,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x33,
        "non-decoupled destination never presented the first stalled payload",
    )
    _expect(sim, "src_ready_o", 0, "non-decoupled source should remain blocked before destination acknowledgement")
    _expect(sim, "dst_data_o", 0x33, "blocked non-decoupled source update should not overwrite the stalled payload")

    step_drive(sim, engine, "src_valid_i", 0)
    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        250,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "non-decoupled stalled payload never drained after destination acknowledgement",
    )
    _run_until_condition(
        sim,
        280,
        lambda s: _read_int(s, "src_ready_o") == 1,
        "non-decoupled source ready never pulsed after the acknowledgement returned",
    )

    step_drive(sim, engine, "src_data_i", 0x55)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 300, "non-decoupled second source write edge not observed")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        340,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x55,
        "non-decoupled second payload never appeared after the first handshake completed",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_isochronous_spill_register_cross_engine(tmp_path, engine):
    design = _parse_isochronous_spill_register_design(tmp_path)

    sim = _make_isochronous_spill_register_sim(design, "isochronous_spill_register_tb_local", engine)
    _release_isochronous_spill_register_non_bypass_reset(sim, engine)

    _wait_for_source_low(sim)
    step_drive(sim, engine, "src_data_i", 0x11)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 60, "source write edge not observed for first queued item")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _expect(sim, "src_ready_o", 1, "source should still have one free slot after the first write")

    _wait_for_source_low(sim)
    step_drive(sim, engine, "src_data_i", 0x22)
    step_drive(sim, engine, "src_valid_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_rising_edge(sim, "src_clk_i", 80, "source write edge not observed for second queued item")
    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        90,
        lambda s: _read_int(s, "src_ready_o") == 0,
        "spill register never reported full after two queued items",
    )
    _run_until_condition(
        sim,
        120,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x11,
        "first queued item never appeared at the destination",
    )
    _expect(sim, "src_ready_o", 0, "source should stay blocked while both entries remain occupied")

    step_drive(sim, engine, "dst_ready_i", 1)
    _settle_drives(sim, engine, "src_clk_i")
    _run_until_condition(
        sim,
        170,
        lambda s: _read_int(s, "dst_valid_o") == 1 and _read_int(s, "dst_data_o") == 0x22,
        "second queued item never surfaced after draining the first",
    )
    _run_until_condition(
        sim,
        190,
        lambda s: _read_int(s, "src_ready_o") == 1,
        "source ready never reopened after draining one entry",
    )
    _run_until_condition(
        sim,
        220,
        lambda s: _read_int(s, "dst_valid_o") == 0,
        "destination valid never cleared after draining the second entry",
    )

    sim = _make_isochronous_spill_register_sim(design, "isochronous_spill_register_bypass_tb_local", engine)
    _release_isochronous_spill_register_reset(sim, engine)
    step_drive(sim, engine, "dst_ready_i", 0)
    step_drive(sim, engine, "src_valid_i", 1)
    step_drive(sim, engine, "src_data_i", 0x5A)
    _settle_drives(sim, engine, "src_clk_i")
    _expect(sim, "src_ready_o", 0, "bypass mode should pass destination ready combinationally")
    _expect(sim, "dst_valid_o", 1, "bypass mode should pass source valid combinationally")
    _expect(sim, "dst_data_o", 0x5A, "bypass mode should pass source data combinationally")

    step_drive(sim, engine, "dst_ready_i", 1)
    step_drive(sim, engine, "src_data_i", 0xA5)
    _settle_drives(sim, engine, "src_clk_i")
    _expect(sim, "src_ready_o", 1, "bypass mode should reopen immediately")
    _expect(sim, "dst_valid_o", 1, "bypass mode should remain transparent while valid is asserted")
    _expect(sim, "dst_data_o", 0xA5, "bypass mode should update data immediately")

    step_drive(sim, engine, "src_valid_i", 0)
    _settle_drives(sim, engine, "src_clk_i")
    _expect(sim, "dst_valid_o", 0, "bypass mode should clear valid immediately")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_omega_net_cross_engine(tmp_path, engine):
    design = _parse_stream_omega_design(tmp_path)

    sim = _make_stream_omega_sim(design, "so0_tb", engine)
    _expect(sim, "valid_o", 0, "stream_omega_net no-spill should be idle after reset")
    _expect(sim, "ready_o", 0, "stream_omega_net no-spill should not assert ready without valids")

    step_drive(sim, engine, "ready_i", 0b1111)
    step_drive(sim, engine, "data_i", _pack_omega_inputs(0xA0, 0xB1, 0xC2, 0xD3))
    step_drive(sim, engine, "sel_i", _pack_omega_selects(0, 2, 1, 3))
    step_drive(sim, engine, "valid_i", 0b1111)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b1111, "stream_omega_net no-spill should route all four outputs independently")
    _expect(sim, "data_o", 0xD3B1C2A0, "stream_omega_net no-spill payload routing mismatch")
    _expect(sim, "idx_o", _pack_omega_indices(0, 2, 1, 3), "stream_omega_net no-spill idx routing mismatch")
    _expect(sim, "ready_o", 0b1111, "stream_omega_net no-spill should ready every selected input")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "independent-routing edge not observed")

    step_drive(sim, engine, "ready_i", 0b0001)
    step_drive(sim, engine, "data_i", _pack_omega_inputs(0x10, 0x21, 0x32, 0x43))
    step_drive(sim, engine, "sel_i", _pack_omega_selects(0, 0, 3, 3))
    step_drive(sim, engine, "valid_i", 0b0011)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b0001, "stream_omega_net no-spill should drive only output 0 for first-stage contention")
    _expect(
        sim, "data_o", 0x00000010, "stream_omega_net no-spill should choose input 0 first in first-stage contention"
    )
    _expect(sim, "idx_o", _pack_omega_indices(0, 0, 0, 0), "stream_omega_net no-spill should report input 0 first")
    _expect(sim, "ready_o", 0b0001, "stream_omega_net no-spill should ready only the first-stage winner")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "first-stage contention edge not observed")

    _expect(
        sim, "data_o", 0x00000021, "stream_omega_net no-spill should rotate to input 1 on the next first-stage grant"
    )
    _expect(
        sim,
        "idx_o",
        _pack_omega_indices(1, 0, 0, 0),
        "stream_omega_net no-spill should report input 1 after first-stage rotation",
    )
    _expect(sim, "ready_o", 0b0010, "stream_omega_net no-spill should ready the second first-stage contender")

    _pulse_omega_flush(sim, engine)

    step_drive(sim, engine, "ready_i", 0b0001)
    step_drive(sim, engine, "data_i", _pack_omega_inputs(0x54, 0x65, 0x76, 0x87))
    step_drive(sim, engine, "sel_i", _pack_omega_selects(0, 3, 0, 3))
    step_drive(sim, engine, "valid_i", 0b0101)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b0001, "stream_omega_net no-spill should drive only output 0 for second-stage contention")
    _expect(
        sim, "data_o", 0x00000054, "stream_omega_net no-spill should choose input 0 first in second-stage contention"
    )
    _expect(
        sim,
        "idx_o",
        _pack_omega_indices(0, 0, 0, 0),
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
        _pack_omega_indices(2, 0, 0, 0),
        "stream_omega_net no-spill should report input 2 after second-stage rotation",
    )
    _expect(sim, "ready_o", 0b0100, "stream_omega_net no-spill should ready the second-stage contender after rotation")

    _pulse_omega_flush(sim, engine)

    step_drive(sim, engine, "ready_i", 0b0001)
    step_drive(sim, engine, "data_i", _pack_omega_inputs(0x54, 0x65, 0x76, 0x87))
    step_drive(sim, engine, "sel_i", _pack_omega_selects(0, 3, 0, 3))
    step_drive(sim, engine, "valid_i", 0b0101)
    _settle_drives(sim, engine)
    _expect(sim, "data_o", 0x00000054, "stream_omega_net no-spill flush should restore second-stage priority")
    _expect(
        sim,
        "idx_o",
        _pack_omega_indices(0, 0, 0, 0),
        "stream_omega_net no-spill flush should restore second-stage input index",
    )

    sim = _make_stream_omega_sim(design, "so1_tb", engine)
    step_drive(sim, engine, "ready_i", 0b0000)
    step_drive(sim, engine, "data_i", _pack_omega_inputs(0x44, 0x00, 0x00, 0x00))
    step_drive(sim, engine, "sel_i", _pack_omega_selects(0, 0, 0, 0))
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
        sim,
        "idx_o",
        _pack_omega_indices(0, 0, 0, 0),
        "stream_omega_net spill should preserve source index while stalled",
    )
    _expect(sim, "valid_o", 0b0001, "stream_omega_net spill should keep output valid until ready")

    step_drive(sim, engine, "ready_i", 0b0001)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill drain edge not observed")
    _expect(sim, "valid_o", 0b0000, "stream_omega_net spill should drain once the sink is ready")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_omega_net_spill_rotation_cross_engine(tmp_path, engine):
    design = _parse_stream_omega_design(tmp_path)

    sim = _make_stream_omega_sim(design, "so1_tb", engine)
    step_drive(sim, engine, "ready_i", 0b0000)
    step_drive(sim, engine, "data_i", _pack_omega_inputs(0x10, 0x21, 0x00, 0x00))
    step_drive(sim, engine, "sel_i", _pack_omega_selects(0, 0, 0, 0))
    step_drive(sim, engine, "valid_i", 0b0011)
    _settle_drives(sim, engine)

    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill rotation first capture edge not observed")
    step_drive(sim, engine, "valid_i", 0b0010)
    _settle_drives(sim, engine)

    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill rotation first output edge not observed")
    _expect(sim, "valid_o", 0b0001, "stream_omega_net spill should surface the first contender first")
    _expect(sim, "data_o", 0x00000010, "stream_omega_net spill first contender payload mismatch")
    _expect(
        sim,
        "idx_o",
        _pack_omega_indices(0, 0, 0, 0),
        "stream_omega_net spill first contender index mismatch",
    )

    step_drive(sim, engine, "ready_i", 0b0001)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill rotation drain edge not observed")
    step_drive(sim, engine, "ready_i", 0b0000)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b0001, "stream_omega_net spill should rotate to the waiting contender")
    _expect(sim, "data_o", 0x00000021, "stream_omega_net spill second contender payload mismatch")
    _expect(
        sim,
        "idx_o",
        _pack_omega_indices(1, 0, 0, 0),
        "stream_omega_net spill second contender index mismatch",
    )

    step_drive(sim, engine, "valid_i", 0b0000)
    _settle_drives(sim, engine)
    step_drive(sim, engine, "ready_i", 0b0001)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill rotation second drain edge not observed")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill rotation empty edge not observed")
    _expect(sim, "valid_o", 0b0000, "stream_omega_net spill should empty after the second contender drains")

    _pulse_omega_flush(sim, engine)

    step_drive(sim, engine, "ready_i", 0b0000)
    step_drive(sim, engine, "data_i", _pack_omega_inputs(0x54, 0x65, 0x00, 0x00))
    step_drive(sim, engine, "sel_i", _pack_omega_selects(0, 0, 0, 0))
    step_drive(sim, engine, "valid_i", 0b0011)
    _settle_drives(sim, engine)

    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill flush reset capture edge not observed")
    step_drive(sim, engine, "valid_i", 0b0010)
    _settle_drives(sim, engine)

    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill flush reset output edge not observed")
    _expect(sim, "valid_o", 0b0001, "stream_omega_net spill flush should restore first contender priority")
    _expect(sim, "data_o", 0x00000054, "stream_omega_net spill flush should restore first contender payload")
    _expect(
        sim,
        "idx_o",
        _pack_omega_indices(0, 0, 0, 0),
        "stream_omega_net spill flush should restore first contender index",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_omega_net_spill_second_stage_rotation_cross_engine(tmp_path, engine):
    design = _parse_stream_omega_design(tmp_path)

    sim = _make_stream_omega_sim(design, "so1_tb", engine)
    step_drive(sim, engine, "ready_i", 0b0000)
    step_drive(sim, engine, "data_i", _pack_omega_inputs(0x54, 0x00, 0x76, 0x00))
    step_drive(sim, engine, "sel_i", _pack_omega_selects(0, 3, 0, 3))
    step_drive(sim, engine, "valid_i", 0b0101)
    _settle_drives(sim, engine)

    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second-stage first capture edge not observed")
    step_drive(sim, engine, "valid_i", 0b0100)
    _settle_drives(sim, engine)

    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second-stage first output edge not observed")
    _expect(sim, "valid_o", 0b0001, "stream_omega_net spill should surface the first second-stage contender first")
    _expect(sim, "data_o", 0x00000054, "stream_omega_net spill first second-stage contender payload mismatch")
    _expect(
        sim,
        "idx_o",
        _pack_omega_indices(0, 0, 0, 0),
        "stream_omega_net spill first second-stage contender index mismatch",
    )

    step_drive(sim, engine, "ready_i", 0b0001)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second-stage first drain edge not observed")
    step_drive(sim, engine, "ready_i", 0b0000)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second-stage rotated output edge not observed")
    _expect(sim, "valid_o", 0b0001, "stream_omega_net spill should rotate to the waiting second-stage contender")
    _expect(sim, "data_o", 0x00000076, "stream_omega_net spill second-stage contender payload mismatch")
    _expect(
        sim,
        "idx_o",
        _pack_omega_indices(2, 0, 0, 0),
        "stream_omega_net spill second-stage contender index mismatch",
    )

    step_drive(sim, engine, "valid_i", 0b0000)
    _settle_drives(sim, engine)
    step_drive(sim, engine, "ready_i", 0b0001)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second-stage second drain edge not observed")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second-stage empty edge not observed")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second-stage final empty edge not observed")
    _expect(sim, "valid_o", 0b0000, "stream_omega_net spill should empty after the second-stage contender drains")

    _pulse_omega_flush(sim, engine)

    step_drive(sim, engine, "ready_i", 0b0000)
    step_drive(sim, engine, "data_i", _pack_omega_inputs(0x88, 0x00, 0x99, 0x00))
    step_drive(sim, engine, "sel_i", _pack_omega_selects(0, 3, 0, 3))
    step_drive(sim, engine, "valid_i", 0b0101)
    _settle_drives(sim, engine)

    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second-stage flush capture edge not observed")
    step_drive(sim, engine, "valid_i", 0b0100)
    _settle_drives(sim, engine)

    _run_until_rising_edge(sim, "clk", sim.time + 20, "spill second-stage flush output edge not observed")
    _expect(sim, "valid_o", 0b0001, "stream_omega_net spill flush should restore first second-stage contender priority")
    _expect(
        sim, "data_o", 0x00000088, "stream_omega_net spill flush should restore first second-stage contender payload"
    )
    _expect(
        sim,
        "idx_o",
        _pack_omega_indices(0, 0, 0, 0),
        "stream_omega_net spill flush should restore first second-stage contender index",
    )


@pytest.mark.parametrize("engine", ENGINES)
def test_fifo_v3_cross_engine(tmp_path, engine):
    design = _parse_fifo_v3_design(tmp_path)

    sim = _make_fifo_v3_sim(design, "fifo_v3_tb_depth3", engine)
    _check_fifo_reset_state(sim)
    _fifo_tx(sim, engine, {"push": 1, "data_i": 0x11})
    _expect(sim, "usage", 1, "depth3 first push should increment usage")
    _expect(sim, "empty", 0, "depth3 first push should make fifo non-empty")
    _expect(sim, "data_o", 0x11, "depth3 first push should expose first element")
    _fifo_tx(sim, engine, {"push": 1, "data_i": 0x22})
    _expect(sim, "usage", 2, "depth3 second push should increment usage")
    _expect(sim, "data_o", 0x11, "depth3 head should stay stable after second push")
    _fifo_tx(sim, engine, {"push": 1, "data_i": 0x33})
    _expect(sim, "usage", 3, "depth3 third push should fill the fifo")
    _expect(sim, "full", 1, "depth3 fifo should report full after three pushes")
    _expect(sim, "data_o", 0x11, "depth3 full fifo should retain oldest head")
    _fifo_tx(sim, engine, {"push": 1, "data_i": 0x44})
    _expect(sim, "usage", 3, "depth3 blocked push should not change usage")
    _expect(sim, "full", 1, "depth3 blocked push should keep fifo full")
    _expect(sim, "data_o", 0x11, "depth3 blocked push should not disturb the head")
    _fifo_tx(sim, engine, {"pop": 1})
    _expect(sim, "usage", 2, "depth3 pop should decrement usage")
    _expect(sim, "full", 0, "depth3 pop should clear full")
    _expect(sim, "data_o", 0x22, "depth3 pop should advance to the second element")
    _fifo_tx(sim, engine, {"push": 1, "pop": 1, "data_i": 0x44})
    _expect(sim, "usage", 2, "depth3 simultaneous push/pop should preserve usage")
    _expect(sim, "data_o", 0x33, "depth3 simultaneous push/pop should advance the head")
    _fifo_tx(sim, engine, {"pop": 1})
    _expect(sim, "usage", 1, "depth3 second pop should leave one element")
    _expect(sim, "data_o", 0x44, "depth3 reordered tail should surface after draining")
    _fifo_tx(sim, engine, {"flush": 1})
    _expect(sim, "usage", 0, "depth3 flush should clear usage")
    _expect(sim, "empty", 1, "depth3 flush should empty the fifo")
    _expect(sim, "full", 0, "depth3 flush should clear full")

    sim = _make_fifo_v3_sim(design, "fifo_v3_tb_ft_depth3", engine)
    _check_fifo_reset_state(sim)
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
    _fifo_tx(sim, engine, {"push": 1, "data_i": 0xB2})
    _expect(sim, "usage", 1, "fall-through depth3 push should store when not popped")
    _expect(sim, "data_o", 0xB2, "fall-through depth3 stored element should remain visible")
    _fifo_tx(sim, engine, {"pop": 1})
    _expect(sim, "usage", 0, "fall-through depth3 pop should drain stored element")
    _expect(sim, "empty", 1, "fall-through depth3 pop should return fifo to empty")

    sim = _make_fifo_v3_sim(design, "fifo_v3_tb_depth1", engine)
    _check_fifo_reset_state(sim)
    _fifo_tx(sim, engine, {"push": 1, "data_i": 0x71})
    _expect(sim, "usage", 1, "depth1 push should fill the fifo")
    _expect(sim, "full", 1, "depth1 fifo should report full after one push")
    _expect(sim, "data_o", 0x71, "depth1 head should match the stored word")
    _fifo_tx(sim, engine, {"push": 1, "data_i": 0x72})
    _expect(sim, "usage", 1, "depth1 blocked push should keep usage stable")
    _expect(sim, "data_o", 0x71, "depth1 blocked push should preserve the head")
    _fifo_tx(sim, engine, {"pop": 1})
    _expect(sim, "usage", 0, "depth1 pop should empty the fifo")
    _expect(sim, "empty", 1, "depth1 pop should assert empty")
    _expect(sim, "full", 0, "depth1 pop should clear full")

    sim = _make_fifo_v3_sim(design, "fifo_v3_tb_ft_depth1", engine)
    _check_fifo_reset_state(sim)
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
    _fifo_tx(sim, engine, {"pop": 1})
    _expect(sim, "usage", 0, "fall-through depth1 pop should empty the fifo")
    _expect(sim, "empty", 1, "fall-through depth1 pop should assert empty")


@pytest.mark.parametrize("engine", ENGINES)
def test_stream_xbar_cross_engine(tmp_path, engine):
    design = _parse_stream_xbar_design(tmp_path)

    sim = _make_stream_xbar_sim(design, "sx0_tb", engine)
    _expect(sim, "valid_o", 0, "stream_xbar no-spill should be idle after reset")
    _expect(sim, "ready_o", 0, "stream_xbar no-spill should not assert input ready until valids exist")

    step_drive(sim, engine, "ready_i", 0b11)
    step_drive(sim, engine, "data_i", _pack_stream_xbar_inputs(0xA0, 0xB1, 0xC2))
    step_drive(sim, engine, "sel_i", 0b100)
    step_drive(sim, engine, "valid_i", 0b101)
    _settle_drives(sim, engine)
    _expect(sim, "valid_o", 0b11, "stream_xbar no-spill should route two independent outputs simultaneously")
    _expect(sim, "data_o", 0xC2A0, "stream_xbar no-spill payload routing mismatch for independent outputs")
    _expect(sim, "idx_o", 0b1000, "stream_xbar no-spill idx routing mismatch for independent outputs")
    _expect(sim, "ready_o", 0b101, "stream_xbar no-spill should ready both selected inputs")
    _run_until_rising_edge(sim, "clk", sim.time + 20, "independent-output handshake edge not observed")

    step_drive(sim, engine, "data_i", _pack_stream_xbar_inputs(0x10, 0x21, 0x32))
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

    sim = _make_stream_xbar_sim(design, "sx1_tb", engine)
    step_drive(sim, engine, "ready_i", 0b00)
    step_drive(sim, engine, "data_i", _pack_stream_xbar_inputs(0x44, 0x55, 0x66))
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
