"""A reset driven before start-up is a starting value, through port wiring too.

`drive("rst_n", 0)` before the first `run()`/`settle()` sets a starting value,
not an edge (known_issues.md, "Start-up"). Its continuous fan-out -- the port
net `u.rst_n` of a submodule, or `wire r2 = rst_n` -- is part of the same
starting state, so `always @(posedge clk or negedge rst_n)` in the submodule
must not fire at start-up either. It used to fire on the submodule's port net
on vm, vm-fast and compiled after `run(max_time=0)`, and on vm and vm-fast
after `settle()`, but not on reference: 69 of 87 mismatches in the first
corpus run (tools/corpus_check.py, nearly every pulp module with an async
reset). An `initial` block driving the reset at t=0 is an event, and does fire
it (as in Icarus).
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SUB = """\
module sub(input clk, input rst_n, output reg [2:0] q);
  always @(posedge clk or negedge rst_n) if (!rst_n) q <= 3'd0; else q <= q + 3'd1;
endmodule
"""

_TOP = (
    _SUB
    + """\
module t(input clk, input rst_n, output [2:0] q, output [2:0] q2, output reg [2:0] q3, output reg seen);
  wire r2 = rst_n;
  sub u (.clk(clk), .rst_n(rst_n), .q(q));
  sub u2 (.clk(clk), .rst_n(r2), .q(q2));
  always @(posedge clk or negedge rst_n) if (!rst_n) q3 <= 3'd0; else q3 <= q3 + 3'd1;
  always @* seen = !r2;
endmodule
"""
)

_OUTS = ("q", "q2", "q3")


def _sim(engine: str, src: str = _TOP) -> Simulator:
    design = parse_text(src)
    return Simulator(design.get_module("t"), engine=engine, design=design)


def _vals(sim: Simulator) -> dict[str, Value]:
    return {n: sim.read(n) for n in _OUTS}


@pytest.mark.parametrize("start", ["settle", "run"])
@pytest.mark.parametrize("engine", ENGINES)
def test_pre_start_reset_is_not_an_edge_through_ports(engine, start):
    sim = _sim(engine)
    sim.drive("clk", Value(0))
    sim.drive("rst_n", Value(0))
    if start == "run":
        sim.run(max_time=0)
    sim.settle()
    assert all(v.mask == 0b111 for v in _vals(sim).values()), _vals(sim)  # no async reset at start-up
    assert sim.read("seen") == Value(1)  # (the port net's reader still woke)

    sim.drive("clk", Value(1))
    sim.settle()  # the first posedge takes the reset branch
    assert _vals(sim) == dict.fromkeys(_OUTS, Value(0, width=3))
    sim.drive("clk", Value(0))
    sim.settle()
    sim.drive("rst_n", Value(1))
    sim.settle()
    sim.drive("clk", Value(1))
    sim.settle()
    assert _vals(sim) == dict.fromkeys(_OUTS, Value(1, width=3))
    sim.drive("rst_n", Value(0))  # a negedge after start-up: async reset on every path
    sim.settle()
    assert _vals(sim) == dict.fromkeys(_OUTS, Value(0, width=3))


@pytest.mark.parametrize("engine", ENGINES)
def test_initial_block_reset_through_port_fires(engine):
    src = (
        _SUB
        + """\
module t(input clk, output [2:0] q, output [2:0] q2, output [2:0] q3);
  reg rst_n;
  wire r2 = rst_n;
  initial rst_n = 1'b0;
  sub u (.clk(clk), .rst_n(rst_n), .q(q));
  sub u2 (.clk(clk), .rst_n(r2), .q(q2));
  sub u3 (.clk(clk), .rst_n(1'b1), .q(q3));
endmodule
"""
    )
    sim = _sim(engine, src)
    sim.drive("clk", Value(0))
    sim.run(max_time=0)
    sim.settle()
    assert sim.read("q") == Value(0, width=3)  # an initial block's write is a t=0 event (as in Icarus)
    assert sim.read("q2") == Value(0, width=3)
    assert sim.read("q3").mask == 0b111  # a tie-off's starting value is not
