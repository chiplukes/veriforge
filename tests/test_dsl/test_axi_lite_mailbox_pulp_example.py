"""Tests for the Wave D pulp axi_lite_mailbox migration.

The mailbox example exercises two AXI-Lite slave bundles (``slv0`` /
``slv1``) on the same clock domain and verifies the bench-framework
port mirrors the original ``run_sim.py`` end-to-end behavior.
"""

from __future__ import annotations

import compileall
import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.project import parse_files
from veriforge.sim.endpoints import detect_axi_lite_interfaces

REPO_ROOT = Path(__file__).resolve().parents[2]
EX_ROOT = REPO_ROOT / "examples" / "pulp" / "axi" / "axi_lite_mailbox"
RTL = EX_ROOT / "rtl" / "axi_lite_mailbox.sv"
TB = EX_ROOT / "tb" / "axi_lite_mailbox_tb.sv"
BENCH = EX_ROOT / "bench" / "axi_lite_mailbox_bench.py"


@pytest.mark.skipif(not TB.exists(), reason="axi_lite_mailbox example not present")
def test_mailbox_tb_detects_two_axi_lite_slaves():
    design = parse_files([str(RTL), str(TB)], preprocess=True)
    module = design.get_module("axi_lite_mailbox_exec_tb")
    assert module is not None
    bundles = detect_axi_lite_interfaces(module)
    prefixes = sorted(b.prefix for b in bundles)
    assert prefixes == ["slv0", "slv1"]
    for b in bundles:
        assert b.role == "slave"


@pytest.mark.skipif(not TB.exists(), reason="axi_lite_mailbox example not present")
def test_cli_generates_mailbox_scaffold(tmp_path):
    out = tmp_path / "auto_mailbox_bench.py"
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "veriforge",
            "generate-python-testbench",
            "-f",
            str(RTL),
            "-f",
            str(TB),
            "--module",
            "axi_lite_mailbox_exec_tb",
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
    assert "axi_lite_slv0" in text
    assert "axi_lite_slv1" in text
    assert "bench.iface('slv0')" in text
    assert "bench.iface('slv1')" in text
    assert compileall.compile_file(str(out), quiet=1, force=True)


@pytest.mark.skipif(not BENCH.exists(), reason="axi_lite_mailbox bench not present")
def test_mailbox_bench_runs_end_to_end():
    proc = subprocess.run(  # noqa: S603
        [sys.executable, str(BENCH)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert proc.returncode == 0, f"axi_lite_mailbox bench failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "axi_lite_mailbox passed" in proc.stdout
