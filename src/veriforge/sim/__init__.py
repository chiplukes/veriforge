"""Verilog Simulation Engine — pure-Python event-driven simulator.

Usage:
    from veriforge.sim import Simulator, Clock, Value
    from veriforge.sim import VcdWriter
    from veriforge.sim import IcarusCosim  # cross-simulator validation
"""

from .cosim import CosimResult, CycleMismatch, IcarusCosim, find_icarus, record_vcd
from .evaluator import EvalContext, ExpressionEvaluator
from .executor import StatementExecutor
from .scheduler import Scheduler
from .testbench import (
    Clock,
    SignalHandle,
    Simulator,
)
from .trace import attach_vcd
from .value import Value
from .vcd import VcdWriter

__all__ = [
    "Clock",
    "CosimResult",
    "CycleMismatch",
    "EvalContext",
    "ExpressionEvaluator",
    "IcarusCosim",
    "Scheduler",
    "SignalHandle",
    "Simulator",
    "StatementExecutor",
    "Value",
    "VcdWriter",
    "attach_vcd",
    "find_icarus",
    "record_vcd",
]
