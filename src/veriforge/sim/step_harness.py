"""Helpers for Python-driven stepped simulation on VM and compiled engines."""

from __future__ import annotations

from typing import Any

from veriforge.sim.testbench import Simulator


def step_drive(sim: Simulator, engine: str, signal_name: str, value: Any) -> None:
    """Drive a signal and mark it dirty for the VM interpreter when needed."""
    sim.drive(signal_name, value)
    if engine in ("vm", "vm-fast"):
        sched = sim._sched
        sid = sched.compiler.signal_map.get(signal_name)
        if sid is not None:
            sched.interpreter.dirty.add(sid)


def step_eval_now(sim: Simulator, clock_name: str = "clk") -> None:
    """Propagate pending drives through combinational logic at the current time.

    Deprecated: call ``sim.settle()`` directly.  The ``clock_name`` argument
    is accepted for backward compatibility but is no longer used.
    """
    sim.settle()


def step_run_until(sim: Simulator, target_time: int) -> None:
    """Advance stepped simulation until the requested time is reached."""
    while sim.time < target_time:
        if not sim.run_step():
            raise RuntimeError(f"Stepped engine stopped before reaching t={target_time}")
