"""Tests for the planner's port-name fallback for clock/reset detection.

Wave B: modules that delegate sequential logic to child cells (e.g.,
``stream_fifo`` -> ``fifo_v3``) have no ``always`` blocks of their own
and so :func:`extract_clocks_resets` returns nothing. The planner
falls back to canonical Pulp/AXI/AHB/APB port-name detection so it can
still bind interfaces.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.sim.bench import Testbench
from veriforge.sim.bench.planner import (
    _naming_fallback_clocks_resets,
    build_plan,
)
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

REPO_ROOT = Path(__file__).resolve().parents[2]
PULP = REPO_ROOT / "examples" / "pulp" / "common_cells"

STREAM_FIFO = PULP / "stream_fifo"
SPILL_REGISTER = PULP / "spill_register"
STREAM_FILTER = PULP / "stream_filter"


def _design_from(*sv_paths: Path):
    text = "\n".join(p.read_text() for p in sv_paths)
    parser = verilog_parser(start="source_text")
    return tree_to_design(parser.build_tree(text=text))


# --------------------------------------------------------------- fallback


def test_naming_fallback_recognizes_clk_i_and_rst_ni():
    src = """
    module dut (
        input  wire        clk_i,
        input  wire        rst_ni,
        input  wire        valid_i,
        output wire        ready_o,
        input  wire [7:0]  data_i,
        output wire        valid_o,
        input  wire        ready_i,
        output wire [7:0]  data_o
    );
    endmodule
    """
    parser = verilog_parser(start="source_text")
    mod = tree_to_design(parser.build_tree(src)).modules[0]
    info = _naming_fallback_clocks_resets(mod)
    assert [c.name for c in info.clocks] == ["clk_i"]
    assert info.clocks[0].edge == "posedge"
    assert [r.name for r in info.resets] == ["rst_ni"]
    rst = info.resets[0]
    assert rst.active_low is True
    assert rst.style == "async"
    assert rst.clock == "clk_i"


def test_naming_fallback_recognizes_active_high_rst():
    src = """
    module dut (
        input  wire        clk,
        input  wire        rst,
        output wire [7:0]  q
    );
    endmodule
    """
    parser = verilog_parser(start="source_text")
    mod = tree_to_design(parser.build_tree(src)).modules[0]
    info = _naming_fallback_clocks_resets(mod)
    assert info.clocks[0].name == "clk"
    assert info.resets[0].name == "rst"
    assert info.resets[0].active_low is False


def test_naming_fallback_returns_empty_when_no_clock_port():
    src = """
    module dut (
        input  wire        valid_i,
        output wire        ready_o,
        input  wire [7:0]  data_i
    );
    endmodule
    """
    parser = verilog_parser(start="source_text")
    mod = tree_to_design(parser.build_tree(src)).modules[0]
    info = _naming_fallback_clocks_resets(mod)
    assert info.clocks == []
    assert info.resets == []


# --------------------------------------------------------------- planner


def test_build_plan_fallback_for_stream_fifo():
    design = _design_from(
        STREAM_FIFO / "rtl" / "fifo_v3.sv",
        STREAM_FIFO / "rtl" / "stream_fifo.sv",
    )
    mod = design.get_module("stream_fifo")
    plan = build_plan(mod)
    assert [d.name for d in plan.domains] == ["clk_i"]
    assert plan.domains[0].reset is not None
    assert plan.domains[0].reset.name == "rst_ni"
    bindings = {b.prefix: b for b in plan.interfaces}
    assert set(bindings.keys()) == {"in", "out"}
    assert all(b.confidence == "sole-domain" for b in bindings.values())
    assert all(b.domain_name == "clk_i" for b in bindings.values())


def test_build_plan_fallback_for_spill_register():
    design = _design_from(
        SPILL_REGISTER / "rtl" / "spill_register_flushable.sv",
        SPILL_REGISTER / "rtl" / "spill_register.sv",
    )
    mod = design.get_module("spill_register")
    plan = build_plan(mod)
    assert [d.name for d in plan.domains] == ["clk_i"]


def test_clockless_module_uses_combinational_domain():
    """``stream_filter`` is purely combinational → synthetic combinational domain."""
    design = _design_from(STREAM_FILTER / "rtl" / "stream_filter.sv")
    mod = design.get_module("stream_filter")
    plan = build_plan(mod, strict=False)
    # Combinational domain is created; interfaces bind to it.
    assert len(plan.domains) == 1
    assert plan.domains[0].name == "__combinational__"
    assert len(plan.interfaces) == 2


# --------------------------------------------------------------- runtime


def test_stream_fifo_round_trip_via_bench():
    design = _design_from(
        STREAM_FIFO / "rtl" / "fifo_v3.sv",
        STREAM_FIFO / "rtl" / "stream_fifo.sv",
    )
    mod = design.get_module("stream_fifo")
    bench = Testbench(mod, engine="reference", design=design)
    payload = [0x11, 0x22, 0x33, 0x44, 0x55]
    with bench.run():
        bench.iface("in").write(payload)
        bench.iface("out").expect_sequence(payload, timeout=400)


def test_spill_register_round_trip_via_bench():
    design = _design_from(
        SPILL_REGISTER / "rtl" / "spill_register_flushable.sv",
        SPILL_REGISTER / "rtl" / "spill_register.sv",
    )
    mod = design.get_module("spill_register")
    bench = Testbench(mod, engine="reference", design=design)
    payload = [0xDE, 0xAD, 0xBE, 0xEF]
    with bench.run():
        bench.iface("in").write(payload)
        bench.iface("out").expect_sequence(payload, timeout=200)


# --------------------------------------------------------------- examples


@pytest.mark.parametrize(
    ("script", "marker"),
    [
        (
            STREAM_FIFO / "bench" / "stream_fifo_bench.py",
            "stream_fifo passed",
        ),
        (
            SPILL_REGISTER / "bench" / "spill_register_bench.py",
            "spill_register passed",
        ),
    ],
)
def test_pulp_bench_example_runs_end_to_end(script: Path, marker: str):
    if not script.exists():
        pytest.skip(f"example missing: {script}")
    proc = subprocess.run(  # noqa: S603 - trusted path, fixed argv
        [sys.executable, str(script)],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert marker in proc.stdout
