"""Indexed reference propagation keeps declaration-order behavior."""

from copy import deepcopy
from functools import lru_cache

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


@lru_cache(maxsize=8)
def _module(source):
    return tree_to_design(verilog_parser(start="source_text").build_tree(source)).modules[0]


def _run(source, engine):
    sim = Simulator(deepcopy(_module(source)), engine=engine)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=95)
    return {
        name: (value.val, value.mask, value.width) for name in sim._sched.signal_names() for value in [sim.read(name)]
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_reference_index_preserves_forward_and_reverse_chain(reverse):
    assigns = ["assign a=q+1;", "assign b=a+2;", "assign c=b+3;"]
    if reverse:
        assigns.reverse()
    source = (
        "module chain(input clk, output reg [7:0] q, output wire [7:0] c);"
        "wire [7:0] a,b;" + " ".join(assigns) + " initial q=0; always @(posedge clk) q<=q+1; endmodule"
    )
    reference = _run(source, "reference")
    assert reference == _run(source, "vm")
    assert reference["c"][0] == 16


def test_reference_index_preserves_reverse_order_diamond_and_unknowns():
    source = """module diamond(input clk, output reg [7:0] q,
        output wire [7:0] a,b,c);
        assign c=a+b;
        assign b=q+8'hx1;
        assign a=q+1;
        initial q=0;
        always @(posedge clk) q<=q+1;
    endmodule"""
    assert _run(source, "reference") == _run(source, "vm")


def test_reference_index_dense_candidate_fallback():
    outputs = " ".join(f"wire [7:0] out{i}; assign out{i}=q+8'd{i};" for i in range(32))
    source = (
        "module dense(input clk, output reg [7:0] q);"
        + outputs
        + " initial q=0; always @(posedge clk) q<=q+1; endmodule"
    )
    reference = _run(source, "reference")
    assert reference == _run(source, "vm")
    assert reference["out31"][0] == 41
