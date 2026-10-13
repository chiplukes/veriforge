"""Writes of narrower values into >64-bit signals and memory elements
(expected values from Icarus).

The compiled engine copied a "signal slice" RHS at the destination's width,
reading past the end of the source (`w[80:10] = b` of a 1-bit `b`: x and
garbage above bit 0; `m[1][80:10] = n[7:0]` leaked `n[11:8]`), took a bare
signed identifier into a wide memory element as a zero-extended concat, and
computed non-slice RHSs of >64-bit memory range writes with the narrow
(64-bit) emitter.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_CASES = [
    ("w = b;", 0x1, 0x0),
    ("w[80:10] = b;", 0x400, 0x0),
    ("w[80:10] <= b;", 0x400, 0x0),
    ("w[80:10] = n[7:0];", 0x2F000, 0x0),
    ("w[j +: 70] = b;", 0x8, 0x0),
    ("w = n[3:0];", 0xC, 0x0),
    ("m[1] = b;", 0x0, 0x1),
    ("m[1][80:10] = b;", 0x0, 0x400),
    ("m[1][80:10] <= n[7:0];", 0x0, 0x2F000),
    ("m[j[0]] = n[7:0];", 0x0, 0xBC),
    ("w[80:10] = sb;", 0x1FFFFFFFFFFFFFFFFE800, 0x0),
    ("m[1] = sb;", 0x0, 0xFFFFFFFFFFFFFFFFFFFFFFFFA),
    ("w = sb;", 0xFFFFFFFFFFFFFFFFFFFFFFFFA, 0x0),
    ("m[j[0]] <= sb;", 0x0, 0xFFFFFFFFFFFFFFFFFFFFFFFFA),
    ("m[1] = {sb};", 0x0, 0xA),
    ("w[99:0] = n;", 0xABC, 0x0),
    ("m[1] = n;", 0x0, 0xABC),
    ("m[1][99:0] <= sb;", 0x0, 0xFFFFFFFFFFFFFFFFFFFFFFFFA),
    ("m[1][j +: 70] = b;", 0x0, 0x8),
    ("m[j[0]][j +: 70] <= n[7:0];", 0x0, 0x5E0),
    ("w[j +: 70] <= n[7:0];", 0x5E0, 0x0),
    ("m[1][j +: 70] = sb;", 0x0, 0x1FFFFFFFFFFFFFFFFD0),
    ("w[90:0] = n[11:4];", 0xAB, 0x0),
    ("m[1][90:0] = n[11:4];", 0x0, 0xAB),
]


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize(("stmt", "w", "m"), _CASES)
def test_narrow_into_wide(engine, stmt, w, m):
    source = f"""module t(input clk, input b, input signed [3:0] sb, input [11:0] n, input [3:0] j,
             output [99:0] o, output [99:0] om);
  reg [99:0] w; reg [99:0] m [0:1];
  assign o = w; assign om = m[1];
  always @(posedge clk) begin w = 100'h0; m[1] = 100'h0; {stmt} end
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    for name, value in (("b", 1), ("sb", 0b1010), ("n", 0xABC), ("j", 3), ("clk", 0)):
        sim.drive(name, value)
    sim.settle()
    sim.drive("clk", 1)
    sim.settle()
    o, om = sim.read("o"), sim.read("om")
    assert (o.val, o.mask, om.val, om.mask) == (w, 0, m, 0)
