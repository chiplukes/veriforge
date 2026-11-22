"""A name with no declaration reads as a 1-bit x on every engine (fixed
2026-11-20).

Compiled read it as 0 (its mask fallback was 0), reference/vm/vm-fast as x.
The ibex reducer deleted a declaration (``err_q[i] <= err_d``) and that
difference became the divergence it reduced to, in place of the real one.
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_SRC = """\
module t(input clk, output logic [1:0] q, output [1:0] a, output logic [1:0] p, output logic c,
    output [100:0] w, output [3:0] n, output logic [129:0] r, output [7:0] s);
  always_ff @(posedge clk) q[0] <= nd0;
  always_ff @(posedge clk) q[1] <= 1'b1;
  assign a = {1'b0, nd1};
  always_comb p = {nd2, 1'b1};
  always_comb if (nd3) c = 1'b1; else c = 1'b0;
  assign w = {nd4, 100'h5};
  assign n = ~nd5;
  always_ff @(posedge clk) r <= {nd6, 129'h1_0000_0000_0000_0000_0000_0000_0000_0003};
  assign s = nd7 + 8'd1;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_undeclared_name_reads_x(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    for level in (0, 1, 0, 1):
        sim.drive("clk", level)
        sim.settle()
    assert str(sim.read("q")) == "2'b1x"
    assert str(sim.read("a")) == "2'b0x"
    assert str(sim.read("p")) == "2'bx1"
    assert sim.read("c") == 0  # (an x condition takes the else branch)
    w = sim.read("w")
    assert (w.val, w.mask) == (5, 1 << 100)
    n = sim.read("n")
    assert (n.val >> 1, n.mask) == (0b111, 1)  # (~ of a zero-extended 1-bit x)
    r = sim.read("r")
    assert (r.val, r.mask) == ((1 << 128) | 3, 1 << 129)
    assert sim.read("s").mask == 0xFF
