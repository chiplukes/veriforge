"""Tests for the Wave D-8 pulp axi_cdc migration.

The axi_cdc example exercises a two-asynchronous-clock-domain AXI4 CDC bridge.
The bench drives signals manually using separate src/dst clock domains.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.project import parse_files
from veriforge.analysis.clock_reset import extract_clocks_resets_hier

REPO_ROOT = Path(__file__).resolve().parents[2]
EX_ROOT = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_cdc"
TB = EX_ROOT / "tb" / "axi_cdc_tb.sv"
BENCH = EX_ROOT / "bench" / "axi_cdc_bench.py"
RTL_FILES = [
    str(EX_ROOT / "rtl" / "axi_cdc.sv"),
    str(EX_ROOT / "rtl" / "cdc_2phase.sv"),
    str(EX_ROOT / "rtl" / "cdc_fifo_2phase.sv"),
    str(TB),
]


@pytest.mark.skipif(not TB.exists(), reason="axi_cdc example not present")
def test_axi_cdc_tb_detects_two_clock_domains():
    design = parse_files(RTL_FILES, preprocess=True)
    module = design.get_module("axi_cdc_exec_tb")
    assert module is not None
    info = extract_clocks_resets_hier(module, design)
    clock_names = {c.name for c in info.clocks}
    assert "src_clk_i" in clock_names, f"src_clk_i not detected; got {clock_names}"
    assert "dst_clk_i" in clock_names, f"dst_clk_i not detected; got {clock_names}"


@pytest.mark.skipif(not BENCH.exists(), reason="axi_cdc bench not present")
def test_axi_cdc_bench_runs_end_to_end():
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(BENCH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, f"axi_cdc bench failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "axi_cdc write transfer passed" in proc.stdout
    assert "axi_cdc read transfer passed" in proc.stdout
