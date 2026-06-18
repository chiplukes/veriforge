"""Tests for the Wave D-9 pulp axi_xbar migration.

The axi_xbar example exercises a 2x2 AXI4 crossbar with fixed address map.
The bench drives slave ports manually and verifies routing, decode errors,
and write arbitration.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.project import parse_files
from veriforge.analysis.clock_reset import extract_clocks_resets_hier

REPO_ROOT = Path(__file__).resolve().parents[2]
EX_ROOT = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_xbar"
TB = EX_ROOT / "tb" / "axi_xbar_tb.sv"
BENCH = EX_ROOT / "bench" / "axi_xbar_bench.py"
RTL_FILES = [
    str(EX_ROOT / "rtl" / "axi_pkg.sv"),
    str(EX_ROOT / "rtl" / "axi_xbar.sv"),
    str(TB),
]


@pytest.mark.skipif(not TB.exists(), reason="axi_xbar example not present")
def test_axi_xbar_tb_detects_single_clock_domain():
    design = parse_files(RTL_FILES, preprocess=True)
    module = design.get_module("axi_xbar_exec_tb")
    assert module is not None
    info = extract_clocks_resets_hier(module, design)
    clock_names = {c.name for c in info.clocks}
    assert "clk" in clock_names, f"clk not detected; got {clock_names}"


@pytest.mark.skipif(not BENCH.exists(), reason="axi_xbar bench not present")
def test_axi_xbar_bench_all_exercises_pass():
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(BENCH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, f"axi_xbar bench failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "parallel routes passed" in proc.stdout
    assert "decode errors passed" in proc.stdout
    assert "arbitration passed" in proc.stdout
