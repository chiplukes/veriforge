"""One-line import for DSL user code::

    from veriforge.dsl.prelude import *

Brings in the module builder, the declarative spec layer, and every
expression helper — the names a typical DSL file imports individually.
"""

from .builder import (
    Expr,
    Module,
    Signal,
    WhenChain,
    ashl,
    ashr,
    case_eq,
    case_ne,
    cat,
    clog2,
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
    select,
    signed,
    sim_time,
    unsigned,
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
    "select",
    "signed",
    "sim_time",
    "unsigned",
    "when",
]
