"""Nonblocking assignments outside a clocked block (fixed 2026-11-22, found by
sv-tests' 10.4.2 run as a corpus).

- vm/vm-fast/compiled never applied a timing-free initial block's NBA
  (`initial q <= 0;`) in a design with no later event: vm's NBA region only ran
  for an event, compiled's reference-executed initial blocks kept the queue.
- compiled never committed an NBA in a design without a clocked process (the
  apply block was only generated with one), and committed a combinational
  block's NBA one settle late (the delta loop stopped with it pending).
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_INITIAL = """\
module top(output logic a, output logic [7:0] b, output logic [7:0] m1, output logic [69:0] w, output logic [7:0] c);
  logic [7:0] m [2];
  logic [69:0] q;
  initial a <= 1;
  initial begin b = 1; b <= 5; c = b; end
  initial m[1] <= 8'h33;
  initial q <= {6'h3f, 64'h1};
  assign m1 = m[1];
  assign w = ~q;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_initial_block_nba_is_applied(engine):
    design = parse_text(_INITIAL)
    sim = Simulator(design.get_module("top"), engine=engine, design=design)
    sim.run(max_time=0)
    assert (int(sim.read("a")), int(sim.read("b")), int(sim.read("c")), int(sim.read("m1"))) == (1, 5, 1, 0x33)
    w = sim.read("w")
    assert (w.val, w.mask) == (((1 << 70) - 1) ^ ((0x3F << 64) | 1), 0)


_COMB = """\
module top(input clk, input [7:0] i, input [69:0] wi, output logic [7:0] a, output logic [7:0] c,
    output logic [69:0] w, output logic [7:0] f);
  logic [7:0] b, q;
  always @* b <= i + 1;
  always @* a <= b * 2;
  always_comb w <= ~wi;
  always_comb c <= i;
  always_ff @(posedge clk) q <= i;
  always @* f <= q + 1;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_combinational_nba_settles(engine):
    design = parse_text(_COMB)
    sim = Simulator(design.get_module("top"), engine=engine, design=design)
    for i, wi in ((5, 3), (9, 1 << 69)):
        sim.drive("clk", 0)
        sim.drive("i", i)
        sim.drive("wi", wi)
        sim.settle()
        assert (int(sim.read("a")), int(sim.read("c"))) == ((i + 1) * 2, i)
        w = sim.read("w")
        assert (w.val, w.mask) == (((1 << 70) - 1) ^ wi, 0)
        sim.drive("clk", 1)
        sim.settle()
        assert int(sim.read("f")) == i + 1
