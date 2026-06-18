from ._version import __version__

# Import the all functions from main and sub modules
from .verilog_parser import *

# Convenience re-exports so that the most common names are available directly
# from the top-level package, e.g. ``from veriforge import parse_file``.
from .project import parse_directory, parse_file, parse_files  # cm:a1f3b2
from .scaffold import build_testbench
from .sim.bench import Testbench, compile_native  # cm:9c4d7e
from .sim.endpoints import (
    AXI4Master,
    AXILiteMaster,
    AXIStreamSink,
    AXIStreamSource,
    MemBusMaster,
    PauseGenerator,
    detect_interfaces,
)
from .sim.testbench import Clock, Simulator, Value

__all__ = [
    "AXI4Master",
    "AXILiteMaster",
    "AXIStreamSink",
    "AXIStreamSource",
    "Clock",
    "MemBusMaster",
    "PauseGenerator",
    "Simulator",
    "Testbench",
    "Value",
    "build_testbench",
    "compile_native",
    "detect_interfaces",
    "parse_directory",
    "parse_file",
    "parse_files",
]
