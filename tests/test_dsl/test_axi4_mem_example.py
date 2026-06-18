"""Tests for the Wave C-3 AXI4 bench-codegen and example.

Pins:

* The flat-port `axi_mem_flat.sv` example DUT auto-detects as
  ``s_axi (axi4, role=slave)``, and the generated ``bench``-style
  scaffold emits an ``axi4_<prefix>(bench)`` stub that calls
  ``iface.write`` / ``iface.read`` on the bound :class:`AXI4Proxy`.
* The hand-authored example bench at ``examples/pulp/axi/axi_mem_flat/
  bench/axi_mem_flat_bench.py`` runs end-to-end on the reference
  simulator and exits 0.
"""

from __future__ import annotations

import compileall
import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.project import parse_file
from veriforge.sim.endpoints import detect_axi4_interfaces

REPO_ROOT = Path(__file__).resolve().parents[2]
EX_ROOT = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_mem_flat"
DUT = EX_ROOT / "rtl_flat" / "axi_mem_flat.sv"
BENCH = EX_ROOT / "bench" / "axi_mem_flat_bench.py"


@pytest.mark.skipif(not DUT.exists(), reason="axi_mem_flat example not present")
def test_flat_axi4_detected_as_slave_bundle():
    design = parse_file(DUT)
    module = design.get_module("axi_mem_flat")
    assert module is not None
    bundles = detect_axi4_interfaces(module)
    assert len(bundles) == 1, [b.prefix for b in bundles]
    bundle = bundles[0]
    assert bundle.prefix == "s_axi"
    assert bundle.role == "slave"


@pytest.mark.skipif(not DUT.exists(), reason="axi_mem_flat example not present")
def test_cli_auto_deps_generates_axi4_stub(tmp_path):
    out = tmp_path / "auto_axi4_bench.py"
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "veriforge",
            "generate-python-testbench",
            "-f",
            str(DUT),
            "--module",
            "axi_mem_flat",
            "--enhanced",
            "--style",
            "bench",
            "--auto-deps",
            "--no-strict",
            "-o",
            str(out),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    text = out.read_text(encoding="utf-8")
    # Scaffold should pick up the AXI4 slave bundle and emit the proxy stub.
    assert "axi4_s_axi" in text
    assert "iface = bench.iface('s_axi')" in text
    assert "iface.write(0x0, [0xDEADBEEF])" in text
    assert "iface.read(0x0, length=1)" in text
    assert compileall.compile_file(str(out), quiet=1, force=True)


@pytest.mark.skipif(not BENCH.exists(), reason="axi_mem_flat hand bench not present")
def test_axi_mem_flat_bench_runs_end_to_end():
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(BENCH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, f"axi_mem_flat bench failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "axi_mem_flat passed" in proc.stdout
