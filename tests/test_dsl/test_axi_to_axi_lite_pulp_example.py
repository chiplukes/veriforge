"""Tests for the Wave D-4 pulp axi_to_axi_lite migration.

The bridge example exposes an AXI-Lite slave (``slv``) and an AXI-Lite
master (``mst``). Because the bridge has internal ``aw_pending_q`` /
``ar_pending_q`` flops that gate the slave-side handshake, the bench
drives ``slv`` with manual ``step_drive`` pokes (mirroring the original
pulp ``run_sim.py``) and consumes ``mst`` with the auto-tick
AXILiteResponder.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.project import parse_files
from veriforge.sim.endpoints import detect_axi_lite_interfaces

REPO_ROOT = Path(__file__).resolve().parents[2]
EX_ROOT = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_to_axi_lite"
RTL_PKG = EX_ROOT / "rtl" / "axi_pkg.sv"
RTL = EX_ROOT / "rtl" / "axi_to_axi_lite.sv"
TB = EX_ROOT / "tb" / "axi_to_axi_lite_tb.sv"
BENCH = EX_ROOT / "bench" / "axi_to_axi_lite_bench.py"


@pytest.mark.skipif(not TB.exists(), reason="axi_to_axi_lite example not present")
def test_axi_to_axi_lite_tb_detects_slave_and_master():
    design = parse_files([str(RTL_PKG), str(RTL), str(TB)], preprocess=True)
    module = design.get_module("axi_to_axi_lite_exec_tb")
    assert module is not None
    bundles = detect_axi_lite_interfaces(module)
    by_prefix = {b.prefix: b for b in bundles}
    assert "slv" in by_prefix and "mst" in by_prefix
    assert by_prefix["slv"].role == "slave"
    assert by_prefix["mst"].role == "master"


@pytest.mark.skipif(not BENCH.exists(), reason="axi_to_axi_lite bench not present")
def test_axi_to_axi_lite_bench_runs_end_to_end():
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(BENCH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, f"axi_to_axi_lite bench failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "axi_to_axi_lite passed" in proc.stdout
