"""Implicit nets (IEEE 1364-2005 4.5) on every engine (fixed 2026-11-22, found by
sv-tests' 6.10 run as a corpus).

An undeclared name in a port connection or a continuous assignment's target is
a 1-bit wire. Compiled had no signal for one and read x; reference kept the
port's full width (Icarus pads the port: `d` reads 1 below).
`elaborate.declare_implicit_nets` declares them before flattening.
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_SRC = """\
package p;
  localparam logic K = 1;
endpackage
module top(input i, output d, output e, output f, output [69:0] w, output g);
  import p::*;
  wire a = 1;
  test mod(a, 1'b0, c);
  test named(.a(i), .b(1'b0), .c(n1));
  wide wmod(wc);
  test pkg(K, 1'b0, g);
  assign n2 = ~i;
  assign d = c;
  assign e = n1;
  assign f = n2;
  assign w = wc;
endmodule
module test(input a, input b, output c);
  assign c = a | b;
endmodule
module wide(output [69:0] c);
  assign c = {6'h3f, 64'h1};
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_implicit_nets(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("top"), engine=engine, design=design)
    sim.drive("i", 0)
    sim.settle()
    got = {name: (sim.read(name).val, sim.read(name).mask) for name in ("d", "e", "f", "w", "g")}
    assert got == {"d": (1, 0), "e": (0, 0), "f": (1, 0), "w": (1, 0), "g": (1, 0)}
