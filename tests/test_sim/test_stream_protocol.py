"""Tests for the generic ready/valid stream protocol (Pulp-style)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.sim.bench import StreamProxy, Testbench
from veriforge.sim.endpoints import (
    detect_interfaces,
    detect_stream_interfaces,
)
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
STREAM_REGISTER_RTL = (
    REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_register" / "rtl" / "stream_register.sv"
)
STREAM_REGISTER_BENCH = (
    REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_register" / "bench" / "stream_register_bench.py"
)
STREAM_DELAY_RTL = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_delay" / "rtl" / "stream_delay.sv"
STREAM_DELAY_BENCH = (
    REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_delay" / "bench" / "stream_delay_bench.py"
)
STREAM_FILTER_RTL = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_filter" / "rtl" / "stream_filter.sv"
STREAM_FILTER_BENCH = (
    REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_filter" / "bench" / "stream_filter_bench.py"
)
PASSTHROUGH_FIFO_BENCH = (
    REPO_ROOT
    / "examples"
    / "pulp"
    / "common_cells"
    / "passthrough_stream_fifo"
    / "bench"
    / "passthrough_stream_fifo_bench.py"
)
SPILL_FLUSHABLE_BENCH = (
    REPO_ROOT
    / "examples"
    / "pulp"
    / "common_cells"
    / "spill_register_flushable"
    / "bench"
    / "spill_register_flushable_bench.py"
)
FIFO_OPTIMAL_BENCH = (
    REPO_ROOT
    / "examples"
    / "pulp"
    / "common_cells"
    / "stream_fifo_optimal_wrap"
    / "bench"
    / "stream_fifo_optimal_wrap_bench.py"
)
FALL_THROUGH_BENCH = (
    REPO_ROOT
    / "examples"
    / "pulp"
    / "common_cells"
    / "fall_through_register"
    / "bench"
    / "fall_through_register_bench.py"
)
ISOCHRONOUS_SPILL_BENCH = (
    REPO_ROOT
    / "examples"
    / "pulp"
    / "common_cells"
    / "isochronous_spill_register"
    / "bench"
    / "isochronous_spill_register_bench.py"
)
CDC_FIFO_BENCH = REPO_ROOT / "examples" / "pulp" / "common_cells" / "cdc_fifo" / "bench" / "cdc_fifo_bench.py"
CDC_FIFO_GRAY_BENCH = (
    REPO_ROOT / "examples" / "pulp" / "common_cells" / "cdc_fifo_gray" / "bench" / "cdc_fifo_gray_bench.py"
)
CDC_4PHASE_BENCH = REPO_ROOT / "examples" / "pulp" / "common_cells" / "cdc_4phase" / "bench" / "cdc_4phase_bench.py"
CDC_2PHASE_BENCH = REPO_ROOT / "examples" / "pulp" / "common_cells" / "cdc_2phase" / "bench" / "cdc_2phase_bench.py"
CDC_2PHASE_CLEARABLE_RTL = (
    REPO_ROOT / "examples" / "pulp" / "common_cells" / "cdc_2phase_clearable" / "rtl" / "cdc_2phase_clearable.sv"
)
CDC_2PHASE_CLEARABLE_BENCH = (
    REPO_ROOT
    / "examples"
    / "pulp"
    / "common_cells"
    / "cdc_2phase_clearable"
    / "bench"
    / "cdc_2phase_clearable_bench.py"
)
STREAM_ROUTING_BENCHES = [
    (
        REPO_ROOT / "examples" / "pulp" / "common_cells" / name / "bench" / f"{name}_bench.py",
        f"{name} passed",
    )
    for name in (
        "stream_mux",
        "stream_demux",
        "stream_join",
        "stream_arbiter",
        "stream_arbiter_flushable",
        "stream_fork",
        "stream_fork_dynamic",
        "stream_xbar",
        "stream_omega_net",
    )
]
LOSSY_STREAM_BENCH = (
    REPO_ROOT
    / "examples"
    / "pulp"
    / "common_cells"
    / "lossy_valid_to_stream"
    / "bench"
    / "lossy_valid_to_stream_bench.py"
)
STREAM_CONTROL_BENCHES = [
    (
        REPO_ROOT / "examples" / "pulp" / "common_cells" / name / "bench" / f"{name}_bench.py",
        f"{name} passed",
    )
    for name in ("stream_throttle", "stream_to_mem")
]


def _parse(path: Path):
    parser = verilog_parser(start="source_text")
    tree = parser.build_tree(text=path.read_text())
    return tree_to_design(tree)


# --------------------------------------------------------------- detection


def test_detect_anonymous_stream_bundles_in_stream_register():
    design = _parse(STREAM_REGISTER_RTL)
    mod = design.get_module("stream_register")
    bundles = detect_stream_interfaces(mod)
    pairs = [(b.prefix, b.role) for b in bundles]
    assert sorted(pairs) == [("in", "slave"), ("out", "master")]

    by_prefix = {b.prefix: b for b in bundles}
    assert by_prefix["in"].signal_names() == {
        "valid": "valid_i",
        "ready": "ready_o",
        "data": "data_i",
    }
    assert by_prefix["out"].signal_names() == {
        "valid": "valid_o",
        "ready": "ready_i",
        "data": "data_o",
    }


def test_stream_detection_does_not_swallow_clock_or_reset_ports():
    design = _parse(STREAM_REGISTER_RTL)
    mod = design.get_module("stream_register")
    bundles = detect_stream_interfaces(mod)
    all_signals = {name for b in bundles for name in b.signal_names().values()}
    # Clocks, resets, and module-level control inputs MUST NOT be pulled
    # into a stream bundle.
    assert "clk_i" not in all_signals
    assert "rst_ni" not in all_signals
    assert "clr_i" not in all_signals
    assert "testmode_i" not in all_signals


def test_stream_detection_keeps_clear_control_outside_cdc_stream():
    design = _parse(CDC_2PHASE_CLEARABLE_RTL)
    mod = design.get_module("cdc_2phase_clearable")
    bundles = detect_stream_interfaces(mod)
    by_prefix = {bundle.prefix: bundle.signal_names() for bundle in bundles}
    assert by_prefix["src"] == {"valid": "src_valid_i", "ready": "src_ready_o", "data": "src_data_i"}
    assert by_prefix["dst"] == {"valid": "dst_valid_o", "ready": "dst_ready_i", "data": "dst_data_o"}


def test_stream_detection_skipped_when_axis_naming_present():
    """A module that already uses tvalid/tready does NOT get a stream bundle on top."""
    src = """
    module dut (
        input  wire        clk,
        input  wire        rstn,
        input  wire        s_axis_tvalid,
        output wire        s_axis_tready,
        input  wire [7:0]  s_axis_tdata,
        input  wire        s_axis_tlast
    );
        always @(posedge clk) begin
            if (s_axis_tvalid) begin end
        end
    endmodule
    """
    parser = verilog_parser(start="source_text")
    tree = parser.build_tree(src)
    mod = tree_to_design(tree).modules[0]
    bundles = detect_interfaces(mod)
    assert len(bundles) == 1
    assert bundles[0].protocol == "axi_stream"


# --------------------------------------------------------------- runtime


def test_stream_proxy_round_trip_through_stream_register():
    design = _parse(STREAM_REGISTER_RTL)
    mod = design.get_module("stream_register")
    bench = Testbench(mod, engine="reference")
    with bench.run():
        src = bench.iface("in")
        snk = bench.iface("out")
        assert isinstance(src, StreamProxy)
        assert isinstance(snk, StreamProxy)
        src.write([0x11, 0x22, 0x33, 0x44])
        snk.expect_sequence([0x11, 0x22, 0x33, 0x44], timeout=200)


def test_stream_proxy_get_returns_data_and_sideband_dict():
    design = _parse(STREAM_REGISTER_RTL)
    mod = design.get_module("stream_register")
    bench = Testbench(mod, engine="reference")
    with bench.run():
        bench.iface("in").put(0xAB)
        data, sideband = bench.iface("out").get(timeout=50)
        assert data == 0xAB
        # No extra same-direction signals beyond data → empty sideband.
        assert sideband == {}


def test_stream_proxy_role_misuse_raises():
    design = _parse(STREAM_REGISTER_RTL)
    mod = design.get_module("stream_register")
    bench = Testbench(mod, engine="reference")
    with bench.run():
        with pytest.raises(RuntimeError, match="sink"):
            bench.iface("out").put(0)
        with pytest.raises(RuntimeError, match="source"):
            bench.iface("in").get(timeout=1)


@pytest.mark.parametrize("engine", ["reference", "vm", "vm-fast"])
def test_stream_delay_payload_round_trip(engine):
    design = _parse(STREAM_DELAY_RTL)
    mod = design.get_module("stream_delay")
    assert mod is not None
    bench = Testbench(mod, engine=engine, design=design)
    bindings = {binding.prefix: dict(binding.signals) for binding in bench.plan.interfaces}
    assert bindings["in"]["data"] == "payload_i"
    assert bindings["out"]["data"] == "payload_o"

    payloads = [0x00, 0x34, 0x56, 0xA5, 0xFF]
    with bench.run():
        bench.reset_all()
        bench.iface("in").write(payloads)
        bench.iface("out").expect_sequence(payloads, timeout=100)
        bench.iface("in").wait_drain(timeout=100)


@pytest.mark.parametrize("engine", ["reference", "vm", "vm-fast"])
def test_stream_filter_pass_and_drop_via_proxy(engine):
    design = _parse(STREAM_FILTER_RTL)
    mod = design.get_module("stream_filter")
    assert mod is not None
    bench = Testbench(mod, engine=engine, design=design)
    with bench.run():
        source = bench.iface("in")
        sink = bench.iface("out")
        source.put(sideband={"drop": 0})
        sink.get(timeout=5)
        source.put(sideband={"drop": 1})
        source.wait_drain(timeout=5)
        assert sink.pending() == 0
        source.put(sideband={"drop": 0})
        sink.get(timeout=5)
        bench.step(2)
        assert sink.pending() == 0


# --------------------------------------------------------------- example


def _run_bench_script(path: Path, expected: str, *, engine: str = "reference") -> None:
    proc = subprocess.run(  # noqa: S603 - trusted path, fixed argv
        [sys.executable, str(path), "--engine", engine],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert expected in proc.stdout


@pytest.mark.skipif(not STREAM_REGISTER_BENCH.exists(), reason="example not present")
def test_stream_register_bench_example_runs_end_to_end():
    _run_bench_script(STREAM_REGISTER_BENCH, "stream_register passed")


def test_stream_delay_bench_example_runs_end_to_end():
    _run_bench_script(STREAM_DELAY_BENCH, "stream_delay passed")


def test_stream_filter_bench_example_runs_end_to_end():
    _run_bench_script(STREAM_FILTER_BENCH, "stream_filter passed")


@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_passthrough_fifo_bench_example_runs_end_to_end(engine):
    _run_bench_script(PASSTHROUGH_FIFO_BENCH, "passthrough_stream_fifo passed", engine=engine)


@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_spill_register_flushable_bench_example_runs_end_to_end(engine):
    _run_bench_script(SPILL_FLUSHABLE_BENCH, "spill_register_flushable passed", engine=engine)


@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_stream_fifo_optimal_wrap_bench_example_runs_end_to_end(engine):
    _run_bench_script(FIFO_OPTIMAL_BENCH, "stream_fifo_optimal_wrap passed", engine=engine)


@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_fall_through_register_bench_example_runs_end_to_end(engine):
    _run_bench_script(FALL_THROUGH_BENCH, "fall_through_register passed", engine=engine)


@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_isochronous_spill_register_bench_example_runs_end_to_end(engine):
    _run_bench_script(ISOCHRONOUS_SPILL_BENCH, "isochronous_spill_register passed", engine=engine)


@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_cdc_fifo_bench_example_runs_end_to_end(engine):
    _run_bench_script(CDC_FIFO_BENCH, "cdc_fifo passed", engine=engine)


@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_cdc_fifo_gray_bench_example_runs_end_to_end(engine):
    _run_bench_script(CDC_FIFO_GRAY_BENCH, "cdc_fifo_gray passed", engine=engine)


@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_cdc_4phase_bench_example_runs_end_to_end(engine):
    _run_bench_script(CDC_4PHASE_BENCH, "cdc_4phase passed", engine=engine)


@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_cdc_2phase_bench_example_runs_end_to_end(engine):
    _run_bench_script(CDC_2PHASE_BENCH, "cdc_2phase passed", engine=engine)


@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_cdc_2phase_clearable_bench_example_runs_end_to_end(engine):
    _run_bench_script(CDC_2PHASE_CLEARABLE_BENCH, "cdc_2phase_clearable passed", engine=engine)


@pytest.mark.parametrize("path,expected", STREAM_ROUTING_BENCHES)
@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_stream_routing_bench_examples_run_end_to_end(path, expected, engine):
    _run_bench_script(path, expected, engine=engine)


@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_lossy_valid_to_stream_bench_example_runs_end_to_end(engine):
    _run_bench_script(LOSSY_STREAM_BENCH, "lossy_valid_to_stream passed", engine=engine)


@pytest.mark.parametrize("path,expected", STREAM_CONTROL_BENCHES)
@pytest.mark.parametrize("engine", ["reference", "vm-fast"])
def test_stream_control_bench_examples_run_end_to_end(path, expected, engine):
    _run_bench_script(path, expected, engine=engine)
