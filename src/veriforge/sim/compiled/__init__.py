"""Compiled Cython simulation engine.

Generates design-specific .pyx files at elaboration time, compiles them
to native extensions, and imports them for maximum simulation throughput.

Usage:
    from veriforge.sim import Simulator
    sim = Simulator(module, engine="compiled")
"""

from .codegen import CythonCodegen
from .compiled_scheduler import CompiledScheduler
from .compiler import CythonCompiler

__all__ = [
    "CompiledScheduler",
    "CythonCodegen",
    "CythonCompiler",
]
