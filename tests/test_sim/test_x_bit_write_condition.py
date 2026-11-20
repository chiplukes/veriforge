"""An x written to one bit of a vector, then read as a condition, on every
engine (fixed 2026-11-20).

Compiled's continuous assign to a bit select (``assign e[1] = d``) kept the
bit's old value when the right-hand side was x and only set its mask bit.
Compiled reads a condition from the value alone (x bits are stored as value
0), so ``if (e)`` was taken after ``e[1]`` went 1 -> x, while reads showed x.
Found by reducing ibex: ``ibex_fetch_fifo``'s ``entry_en[i]`` (built per bit
in a generate loop) went x when ``out_valid_o`` was x, and compiled wrote the
FIFO entry that reference kept x.
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

# name -> (declarations, drivers of `e` from `d`, condition read by the flop)
_CASES = {
    "bit": ("logic [3:0] e;", "assign e[1] = d; assign e[0] = 1'b0; assign e[3:2] = 2'b00;", "e"),
    "bit_cond_bit": ("logic [3:0] e;", "assign e[1] = d; assign e[0] = 1'b0; assign e[3:2] = 2'b00;", "e[1]"),
    "bit_gen": (
        "logic [1:0] e;",
        "for (genvar i = 0; i < 2; i++) begin : g assign e[i] = d; end",
        "e",
    ),
    "struct_field": (
        "typedef struct packed { logic [1:0] a; logic f; logic b; } s_t; s_t e;",
        "assign e.f = d; assign e.a = 2'b00; assign e.b = 1'b0;",
        "e",
    ),
    "part": ("logic [3:0] e;", "assign e[2:1] = {d, d}; assign e[0] = 1'b0; assign e[3] = 1'b0;", "e"),
    "indexed": ("logic [3:0] e;", "assign e[1 +: 2] = {d, d}; assign e[0] = 1'b0; assign e[3] = 1'b0;", "e"),
    "wide_bit": ("logic [127:0] e;", "assign e[100] = d; assign e[99:0] = '0; assign e[127:101] = '0;", "e"),
    "comb_bit": ("logic [3:0] e;", "always_comb begin e = 4'd0; e[1] = d; end", "e"),
    "nba_bit": ("logic [3:0] e;", "always_ff @(negedge clk) begin e[1] <= d; e[0] <= 1'b0; e[3:2] <= 2'b0; end", "e"),
}


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("case", sorted(_CASES))
def test_x_bit_is_a_false_condition(engine, case):
    decl, drivers, cond = _CASES[case]
    src = f"""\
module t(input clk, input dv, input u, output logic q, output logic c);
  {decl}
  logic d;
  assign d = dv ? 1'b1 : u;  // (u is never driven: x once dv is 0)
  {drivers}
  always_ff @(posedge clk) if ({cond}) q <= 1'b1; else q <= 1'b0;
  always_comb if ({cond}) c = 1'b1; else c = 1'b0;
endmodule
"""
    design = parse_text(src)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.drive("clk", 1)
    sim.drive("dv", 1)
    sim.settle()
    for level in (0, 1):
        sim.drive("clk", level)
        sim.settle()
    assert sim.read("q") == 1
    sim.drive("dv", 0)
    sim.settle()
    if case != "nba_bit":  # (that `e` goes x only at the next negedge)
        assert sim.read("c") == 0
    for level in (0, 1):
        sim.drive("clk", level)
        sim.settle()
    assert sim.read("q") == 0


_FIFO = """\
module t #(parameter int DEPTH = 2) (input logic clk_i, input logic in_valid_i, input logic [31:0] in_rdata_i,
    output logic out_valid_o, input logic out_ready_i, output logic [DEPTH-1:0][31:0] rdata_q);
  logic [DEPTH - 1:0] entry_en;
  logic pop_fifo;
  assign pop_fifo = out_ready_i & out_valid_o;  // (out_valid_o is never driven)
  for (genvar i = 0; i < DEPTH; i++) begin : g_fifo_next
    assign entry_en[i] = in_valid_i & ~pop_fifo;
  end
  for (genvar i = 0; i < DEPTH; i++) begin : g_fifo_regs
    always_ff @(posedge clk_i) if (entry_en[i]) rdata_q[i] <= in_rdata_i;
  end
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_reduced_ibex_fetch_fifo(engine):
    design = parse_text(_FIFO)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.drive("clk_i", 0)
    sim.drive("out_ready_i", 0)
    sim.drive("in_valid_i", 1)
    sim.drive("in_rdata_i", 0xBE4AC364)
    sim.settle()
    sim.drive("clk_i", 1)
    sim.settle()
    assert sim.read("rdata_q") == 0xBE4AC364_BE4AC364  # (entry_en is 1: both entries written)
    sim.drive("clk_i", 0)
    sim.drive("out_ready_i", 1)  # (pop_fifo and entry_en go x)
    sim.drive("in_rdata_i", 0xAC6959EE)
    sim.settle()
    sim.drive("clk_i", 1)
    sim.settle()
    assert sim.read("rdata_q") == 0xBE4AC364_BE4AC364
