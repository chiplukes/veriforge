"""Short-circuit evaluation (IEEE 1800 11.4.7): a function call in an
untaken `?:` branch, or in the right operand of a `&&`/`||` decided by its
left operand, must not run (its side effects must not happen). An
ambiguous (x) condition evaluates both branches and merges them. vm,
vm-fast and compiled evaluated every operand; reference didn't short-circuit
`||`/`&&`. Expected outputs are from Icarus."""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_BASIC = """module t(input clk);
  integer n = 0, m = 0, k = 0;
  reg c = 1;
  function integer f(input integer x); begin n = n + 1; f = x + n; end endfunction
  function integer g(input integer x); begin m = m + 10; g = x * 2 + m; end endfunction
  integer r1, r2, r3, r4;
  always @(posedge clk) begin
    r1 = c ? f(1) : g(2);
    r2 = !c ? f(3) : g(4);
    r3 = (c && f(5) > 0) ? 1 : 0;
    r4 = (c || g(6) > 0) ? 1 : 0;
    k = k + (c ? f(7) + f(8) : 0);
    $display("%0d %0d %0d %0d %0d %0d %0d", r1, r2, r3, r4, n, m, k);
    c = ~c;
  end
endmodule
"""
_BASIC_EXPECTED = ["2 18 1 1 4 10 22", "24 8 0 1 5 30 22", "7 48 1 1 9 40 54"]

# x conditions, nesting, a wide result, and a ternary inside a function body
_MIXED = """module t(input clk);
  integer n = 0, m = 0;
  reg [1:0] c = 2'b0x;
  reg x1 = 1'bx;
  reg [99:0] w;
  reg [7:0] q;
  function integer f(input integer x); begin n = n + 1; f = x + n; end endfunction
  function [7:0] g(input [7:0] x); begin m = m + 1; g = x ^ m; end endfunction
  function integer h(input integer s); h = s ? f(s) : g(8'd3); endfunction
  always @(posedge clk) begin
    q = c ? g(8'h0f) : g(8'hf0);          // c = 0x: ambiguous -> both, merge
    $display("a %b %0d %0d", q, n, m);
    q = (x1 && g(1)) ? 8'd1 : 8'd2;       // x && .. evaluates g
    $display("b %0d %0d %0d", q, n, m);
    q = (x1 || g(2)) ? 8'd1 : 8'd2;       // x || .. evaluates g
    $display("c %0d %0d %0d", q, n, m);
    w = c[1] ? {f(1), g(1), 36'd0} : {g(2), f(2), 36'd5};
    $display("d %h %0d %0d", w, n, m);
    q = h(0) + h(1);
    $display("e %0d %0d %0d", q, n, m);
    q = (c[1] ? (f(1) > 0 ? g(1) : g(2)) : (c[1] || f(3) > 0 ? g(3) : g(4)));
    $display("f %0d %0d %0d", q, n, m);
    c = c + 1;
    if (c == 2'b11) c = 2'b10;
  end
endmodule
"""
_MIXED_EXPECTED = [
    "a xxxxxx10 0 2",
    "b X 0 3",
    "c 1 0 4",
    "d 0000000700000003000000005 1 5",
    "e 8 2 6",
    "f 4 3 7",
    "a xxxxxxx1 3 9",
    "b X 3 10",
    "c 1 3 11",
    "d 0000000x00000X0X00000000X 5 13",
    "e 20 6 14",
    "f X 8 16",
]

# the condition is evaluated before the call it guards: compiled hoisted the
# call ahead of the statement, so `r9 == 3 ? fimp(..) : 0` (fimp changes r9)
# read r9 after the call
_ORDER = """module t(input clk);
  int r9 = 1;
  reg c = 1;
  reg [1:0] o5, o6, o7;
  function automatic signed [0:0] fimp (input signed [2:0] a1);
    begin r9 = r9 + a1; fimp = 1'b1; end
  endfunction
  always @(posedge clk) begin
    o5 = (r9 && fimp(3'sd2)) ^ r9[1];
    o6 = (r9 == 3 ? fimp(-3'sd3) : 1'b0) + r9;
    o7 = (r9 || fimp(3'sd1)) ? r9 : 2'd3;
    $display("%b %b %b %0d", o5, o6, o7, r9);
  end
endmodule
"""
_ORDER_EXPECTED = ["00 01 01 1"]

# the same on compiled's wide path (a statement wider than 64 bits)
_ORDER_WIDE = """module t(input clk);
  int r11 = 0;
  reg [127:0] w = {64'd1, 64'd2};
  reg [99:0] o8, o9;
  function automatic signed [32:0] fimp (input [15:0] a1);
    begin r11 = r11 + a1; fimp = 0; end
  endfunction
  always @(posedge clk) begin
    o8 = (r11 || fimp(w[15:0] + w[127:112])) ^ {w, 4'd7};
    o9 = (r11 == 0 ? fimp(16'd1) : w) ^ {w, 4'd7};
    $display("%h %h %0d", o8, o9, r11);
  end
endmodule
"""
_ORDER_WIDE_EXPECTED = [
    "0000000100000000000000027 0000000110000000000000025 2",
    "0000000100000000000000026 0000000110000000000000025 2",
]

_CASES = {
    "basic": (_BASIC, _BASIC_EXPECTED, 28),
    "mixed": (_MIXED, _MIXED_EXPECTED, 18),
    "order": (_ORDER, _ORDER_EXPECTED, 8),
    "order_wide": (_ORDER_WIDE, _ORDER_WIDE_EXPECTED, 18),
}


@pytest.mark.parametrize("case", sorted(_CASES))
@pytest.mark.parametrize("engine", ENGINES)
def test_short_circuit(engine, case):
    src, expected, max_time = _CASES[case]
    design = tree_to_design(verilog_parser(start="source_text").build_tree(src), source_file=f"sc_{case}.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=max_time)
    assert list(sim.display_output) == expected
