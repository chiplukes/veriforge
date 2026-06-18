"""Shared helpers for example runner scripts."""

from __future__ import annotations

import shutil

from veriforge.sim.testbench import Simulator


def compiled_engine_available() -> bool:
    """Return True when the compiled engine can be used in this environment."""
    if not (shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")):
        return False
    try:
        import Cython  # noqa: F401, PLC0415
    except ImportError:
        return False
    return True


def available_engines() -> tuple[str, ...]:
    """Return the supported example-runner engines for the current machine."""
    if compiled_engine_available():
        return ("reference", "vm", "vm-fast", "compiled")
    return ("reference", "vm", "vm-fast")


def display_lines(sim: Simulator) -> list[str]:
    """Return non-empty stripped display output lines from a simulation run."""
    return [line.strip() for line in sim.display_output if line.strip()]
