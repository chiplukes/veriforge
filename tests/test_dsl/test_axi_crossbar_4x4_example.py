"""Slow end-to-end check for the high-level 4x4 AXI4 crossbar bench."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.sim.vm.vm_scheduler import _HAS_CYTHON

REPO_ROOT = Path(__file__).resolve().parents[2]
BENCH = REPO_ROOT / "examples" / "axi" / "axi_crossbar_4x4" / "bench" / "axi_crossbar_4x4_bench.py"


@pytest.mark.slow
@pytest.mark.skipif(not _HAS_CYTHON, reason="large crossbar bench requires native VM extension")
def test_axi_crossbar_4x4_bench_routes_all_ports():
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(BENCH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, f"4x4 AXI crossbar bench failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "axi_crossbar_4x4 passed: all 16 source→sink routes verified" in proc.stdout
