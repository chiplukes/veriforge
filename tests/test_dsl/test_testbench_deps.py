"""Tests for the SV dependency-discovery helper.

Exercises both the in-process Python API and the CLI ``--auto-deps``
flag against a real multi-file Pulp cell (``stream_fifo`` →
``stream_fifo.sv`` + ``fifo_v3.sv``).
"""

from __future__ import annotations

import compileall
import subprocess
import sys
from pathlib import Path

import pytest

from veriforge.dsl.testbench_deps import discover_sv_dependencies

REPO_ROOT = Path(__file__).resolve().parents[2]
STREAM_FIFO_RTL = REPO_ROOT / "examples" / "pulp" / "common_cells" / "stream_fifo" / "rtl"
STREAM_FIFO = STREAM_FIFO_RTL / "stream_fifo.sv"
FIFO_V3 = STREAM_FIFO_RTL / "fifo_v3.sv"


@pytest.mark.skipif(not STREAM_FIFO.exists(), reason="pulp stream_fifo example not present")
def test_discover_finds_fifo_v3_dependency():
    deps, design = discover_sv_dependencies(STREAM_FIFO)
    dep_resolved = {p.resolve() for p in deps}
    assert FIFO_V3.resolve() in dep_resolved, f"expected fifo_v3.sv among deps, got {dep_resolved}"
    # Both modules should be present in the merged Design.
    names = {m.name for m in design.modules}
    assert {"stream_fifo", "fifo_v3"}.issubset(names), names


@pytest.mark.skipif(not STREAM_FIFO.exists(), reason="pulp stream_fifo example not present")
def test_discover_explicit_search_dirs(tmp_path):
    # Pointing at an empty dir should yield zero deps but still parse the DUT.
    deps, design = discover_sv_dependencies(STREAM_FIFO, search_dirs=[tmp_path])
    assert deps == []
    assert design.get_module("stream_fifo") is not None


@pytest.mark.skipif(not STREAM_FIFO.exists(), reason="pulp stream_fifo example not present")
def test_cli_auto_deps_generates_runnable_scaffold(tmp_path):
    out = tmp_path / "auto_sf_bench.py"
    proc = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "veriforge",
            "generate-python-testbench",
            "-f",
            str(STREAM_FIFO),
            "--module",
            "stream_fifo",
            "--enhanced",
            "--style",
            "bench",
            "--auto-deps",
            "--no-strict",
            "--iface-domain",
            "in=clk_i",
            "--iface-domain",
            "out=clk_i",
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
    # DEPS list should be emitted with the discovered child file.
    assert "DEPS = [" in text
    assert "fifo_v3.sv" in text
    # Loader uses the multi-file parse helper.
    assert "parse_files([*DEPS, DUT_PATH])" in text
    # Build_bench must forward design= so the simulator can elaborate the child.
    assert "Testbench(dut, design=design" in text
    # Output is valid Python.
    assert compileall.compile_file(str(out), quiet=1, force=True)

    # And it actually runs end-to-end against the in-process simulator.
    run = subprocess.run(  # noqa: S603
        [sys.executable, str(out)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert run.returncode == 0, f"generated scaffold failed:\nSTDOUT:\n{run.stdout}\nSTDERR:\n{run.stderr}"
    assert "received out:" in run.stdout
