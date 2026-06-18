"""Bytecode VM simulation engine.

Alternative high-performance engine that compiles the model AST to bytecode
at elaboration time and executes it in a tight interpreter loop.

Usage:
    from veriforge.sim import Simulator
    sim = Simulator(module, engine="vm")
"""

from .compiler import CompiledProcess, Compiler
from .interpreter import Interpreter
from .opcodes import Op
from .vm_scheduler import VMScheduler

__all__ = [
    "CompiledProcess",
    "Compiler",
    "Interpreter",
    "Op",
    "VMScheduler",
]
