"""Hardware Construction DSL — build Verilog model objects from Python.

Usage::

    from veriforge.dsl import Module, posedge, negedge, cat, mux

    with Module("counter") as m:
        clk = m.input("clk")
        rst = m.input("rst")
        count = m.output_reg("count", width=8)

        with m.always(posedge(clk)):
            with m.if_(rst):
                count <<= 0
            with m.else_():
                count <<= count + 1

    module = m.build()

    # Emit Verilog
    from veriforge.codegen import emit_module
    print(emit_module(module))

    # Or simulate directly
    from veriforge.sim import Simulator
    sim = Simulator(module)
    sim.run(test_fn, max_time=1000)
"""

from .builder import (
    ashl,
    ashr,
    case_eq,
    case_ne,
    Expr,
    clog2,
    Module,
    Signal,
    cat,
    land,
    lnot,
    lor,
    mux,
    negedge,
    posedge,
    reduce_and,
    reduce_or,
    reduce_xor,
    rep,
    signed,
    sim_time,
    unsigned,
)
from .interface import BoundInterface, Interface

__all__ = [
    "BoundInterface",
    "Expr",
    "Interface",
    "Module",
    "Signal",
    "ashl",
    "ashr",
    "case_eq",
    "case_ne",
    "cat",
    "clog2",
    "land",
    "lnot",
    "lor",
    "mux",
    "negedge",
    "posedge",
    "reduce_and",
    "reduce_or",
    "reduce_xor",
    "rep",
    "signed",
    "sim_time",
    "unsigned",
]
