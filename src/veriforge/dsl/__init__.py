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
    cast,
    Expr,
    clog2,
    Module,
    Signal,
    WhenChain,
    cat,
    land,
    lit,
    lnot,
    lor,
    mux,
    negedge,
    posedge,
    reduce_and,
    reduce_nand,
    reduce_nor,
    reduce_or,
    reduce_xnor,
    reduce_xor,
    rep,
    select,
    signed,
    sim_time,
    unsigned,
    unsupported,
    when,
)
from .interface import BoundInterface, Interface
from .spec import In, Inout, ModuleSpec, Out, OutReg, Param, Reg, Wire

__all__ = [
    "BoundInterface",
    "Expr",
    "In",
    "Inout",
    "Interface",
    "Module",
    "ModuleSpec",
    "Out",
    "OutReg",
    "Param",
    "Reg",
    "Signal",
    "WhenChain",
    "Wire",
    "ashl",
    "ashr",
    "case_eq",
    "case_ne",
    "cast",
    "cat",
    "clog2",
    "land",
    "lit",
    "lnot",
    "lor",
    "mux",
    "negedge",
    "posedge",
    "reduce_and",
    "reduce_nand",
    "reduce_nor",
    "reduce_or",
    "reduce_xnor",
    "reduce_xor",
    "rep",
    "select",
    "signed",
    "sim_time",
    "unsigned",
    "unsupported",
    "when",
]
