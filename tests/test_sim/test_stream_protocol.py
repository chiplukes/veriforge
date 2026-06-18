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


# --------------------------------------------------------------- example


@pytest.mark.skipif(not STREAM_REGISTER_BENCH.exists(), reason="example not present")
def test_stream_register_bench_example_runs_end_to_end():
    proc = subprocess.run(  # noqa: S603 - trusted path, fixed argv
        [sys.executable, str(STREAM_REGISTER_BENCH)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "stream_register passed" in proc.stdout
