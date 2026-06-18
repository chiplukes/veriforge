"""Tests for the Wave D pulp axi_lite_xbar migration.

The xbar example exercises a 2x2 AXI-Lite crossbar with cross-port
routing and decode-error handling. The TB top exposes only the two
master-side AXI-Lite slaves; the underlying memory targets are
internal observables.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.project import parse_files
from veriforge.sim.endpoints import detect_axi_lite_interfaces

REPO_ROOT = Path(__file__).resolve().parents[2]
EX_ROOT = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_lite_xbar"
TB = EX_ROOT / "tb" / "axi_lite_xbar_tb.sv"
BENCH = EX_ROOT / "bench" / "axi_lite_xbar_bench.py"
FILES = [
    EX_ROOT / "rtl" / "axi_pkg.sv",
    EX_ROOT / "rtl" / "axi_lite_xbar.sv",
    TB,
]


@pytest.mark.skipif(not TB.exists(), reason="axi_lite_xbar example not present")
def test_xbar_tb_detects_two_axi_lite_slaves():
    design = parse_files([str(p) for p in FILES], preprocess=True)
    module = design.get_module("axi_lite_xbar_exec_tb")
    assert module is not None
    bundles = detect_axi_lite_interfaces(module)
    assert sorted(b.prefix for b in bundles) == ["slv0", "slv1"]
    for b in bundles:
        assert b.role == "slave"


@pytest.mark.skipif(not BENCH.exists(), reason="axi_lite_xbar bench not present")
def test_xbar_bench_runs_end_to_end():
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(BENCH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, f"axi_lite_xbar bench failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "axi_lite_xbar passed" in proc.stdout
