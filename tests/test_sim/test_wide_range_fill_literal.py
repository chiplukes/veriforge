"""An unbased unsized fill (`'0`, `'1`, `'x`, `'z`) assigned to a range of a wide
(>64-bit) signal fills that range.

Compiled refused to build such a design ("range-select ... assignment has an RHS shape
not yet supported for a destination wider than 64 bits"): its wide literal path read
the literal through `Value.from_verilog`, which has no fill syntax. Found writing the
regression test for ibex_counter's `assign counter_val_upd_o[63:CounterWidth] = '0;`.
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
module t #(parameter W = 70) (input clk, input [99:0] d,
    output logic [99:0] q0, output logic [99:0] q1, output logic [99:0] qx, output logic [99:0] r1,
    output logic [99:0] n1, output logic [149:0] w1);
  assign q0[W-1:0] = d[W-1:0];
  assign q0[99:W] = '0;
  assign q1[W-1:0] = d[W-1:0];
  assign q1[99:W] = '1;
  assign qx[W-1:0] = d[W-1:0];
  assign qx[99:W] = 'x;
  always @* begin r1 = d; r1[99:W] = '1; end
  always @(posedge clk) begin n1 <= d; n1[99:W] <= '1; end
  assign w1[149:10] = '1;
  assign w1[9:0] = '0;
endmodule
"""

_D = 0x1234_5678_9ABC_DEF0_1357_9BDF_5
_LOW = (1 << 70) - 1
_HIGH = ((1 << 100) - 1) & ~_LOW


@pytest.mark.parametrize("engine", ENGINES)
def test_fill_literal_into_wide_range(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.drive("clk", Value(0))
    sim.drive("d", Value(_D, width=100))
    sim.run(max_time=0)
    sim.settle()
    assert sim.read("q0") == Value(_D & _LOW, width=100)
    assert sim.read("q1") == Value((_D & _LOW) | _HIGH, width=100)
    assert sim.read("qx") == Value(_D & _LOW, width=100, mask=_HIGH)
    assert sim.read("r1") == Value((_D & _LOW) | _HIGH, width=100)
    assert sim.read("w1") == Value(((1 << 150) - 1) & ~0x3FF, width=150)
    sim.drive("clk", Value(1))
    sim.settle()
    assert sim.read("n1") == Value((_D & _LOW) | _HIGH, width=100)
