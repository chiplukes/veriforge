"""Tests for the Wave D-10 pulp axi_lite_dw_converter migration.

Five converter variants are exercised:
  - downsize 32->16, upsize 16->32, passthrough 32->32,
    typed upsize 32->128, typed upsize 64->128.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.project import parse_files
from veriforge.sim.bench.planner import build_plan

REPO_ROOT = Path(__file__).resolve().parents[2]
EX_ROOT = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_lite_dw_converter"
TB = EX_ROOT / "tb" / "axi_lite_dw_tb.sv"
BENCH = EX_ROOT / "bench" / "axi_lite_dw_bench.py"
RTL_FILES = [
    str(EX_ROOT / "rtl" / "axi_pkg.sv"),
    str(EX_ROOT / "rtl" / "axi_lite_dw_converter.sv"),
    str(TB),
]


@pytest.mark.skipif(not TB.exists(), reason="axi_lite_dw_converter example not present")
def test_axi_lite_dw_tb_detects_single_clock_domain():
    # The DUT uses struct ports so extract_clocks_resets_hier finds no always blocks.
    # build_plan falls back to port-name heuristics (clk/rst_n) which succeed.
    design = parse_files(RTL_FILES, preprocess=True)
    module = design.get_module("axi_lite_dw_down_tb")
    assert module is not None
    plan = build_plan(module, design=design)
    clock_names = {d.clock.name for d in plan.domains}
    assert "clk" in clock_names, f"clk not detected; got {clock_names}"


@pytest.mark.skipif(not BENCH.exists(), reason="axi_lite_dw bench not present")
def test_axi_lite_dw_bench_all_variants_pass():
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(BENCH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, f"axi_lite_dw bench failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "downsize (32->16) passed" in proc.stdout
    assert "upsize (16->32) passed" in proc.stdout
    assert "passthrough (32->32) passed" in proc.stdout
    assert "typed upsize (32->128) passed" in proc.stdout
    assert "typed upsize (64->128) passed" in proc.stdout
