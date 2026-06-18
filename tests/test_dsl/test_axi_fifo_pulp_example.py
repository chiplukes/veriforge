"""Tests for the Wave D-7 pulp axi_fifo migration.

The axi_fifo example exercises AXI4 signal-level passthrough through a
configurable-depth buffer. The bench drives signals manually because
the test verifies passthrough timing rather than transactional
semantics.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.project import parse_files
from veriforge.sim.endpoints import detect_axi_lite_interfaces

REPO_ROOT = Path(__file__).resolve().parents[2]
EX_ROOT = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_fifo"
RTL = EX_ROOT / "rtl" / "axi_fifo.sv"
TB = EX_ROOT / "tb" / "axi_fifo_tb.sv"
BENCH = EX_ROOT / "bench" / "axi_fifo_bench.py"


@pytest.mark.skipif(not TB.exists(), reason="axi_fifo example not present")
def test_axi_fifo_depth0_tb_detects_slave_and_master():
    design = parse_files([str(RTL), str(TB)], preprocess=True)
    module = design.get_module("axi_fifo_depth0_tb")
    assert module is not None
    bundles = detect_axi_lite_interfaces(module)
    by_prefix = {b.prefix: b for b in bundles}
    assert "slv" in by_prefix and "mst" in by_prefix
    assert by_prefix["slv"].role == "slave"
    assert by_prefix["mst"].role == "master"


@pytest.mark.skipif(not BENCH.exists(), reason="axi_fifo bench not present")
def test_axi_fifo_bench_runs_end_to_end():
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(BENCH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, f"axi_fifo bench failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "axi_fifo depth0 passed" in proc.stdout
    assert "axi_fifo depth1 passed" in proc.stdout
