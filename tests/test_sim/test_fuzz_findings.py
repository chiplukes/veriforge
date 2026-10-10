"""Bugs found by the expanded fuzzer (2026-10-09, notes/plans/
fuzzer_coverage_2026-10.md), reduced to Icarus-checked regressions."""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

# 2-state types: start at 0, x/z assigned become 0 (no engine modeled them)
_TWO = """module t(input clk);
  int i; byte b; bit [3:0] bt; longint l; shortint s; integer g; reg [3:0] r;
  bit [3:0] bt2 = 4'b1x0z;
  always @(posedge clk) begin
    $display("%0d %0d %b %0d %0d %0d %b %b", i, b, bt, l, s, g, r, bt2);
    i = 'x; b = 8'bx1; bt = 4'b1xz0; r = 4'b1xx0;  // (z prints as x here: one x/z encoding)
    $display("%0d %0d %b %b", i, b, bt, r);
    i = r + 1;
    $display("%0d", i);
  end
endmodule
"""
_TWO_EXPECTED = ["0 0 0000 0 0 x xxxx 1000", "0 1 1000 1xx0", "0"]

# values wider than 512 bits (vm-fast computed them wrong silently)
_WIDE512 = """module t(input clk);
  reg signed [125:0] i6 = {6'h2a, 120'h1234_5678_9abc_def0_1357_9bdf_2468};
  wire [6:0] o11 = ~^$signed({32'd2{{32'd3{i6}}}});
  wire [6:0] o12 = ^{i6, i6, i6, i6, i6};
  wire [9:0] o13 = {5{i6}} >> 600;
  always @(posedge clk) $display("%b %b %b", o11, o12, o13);
endmodule
"""
_WIDE512_EXPECTED = ["0000001 0000000 1000110100"]

# user-function result widened in a ternary inside a concatenation (reference)
_TC = """module t(input clk);
  reg c = 1; reg [7:0] x = 8'hff; reg [7:0] y = 8'h01;
  wire [64:0] o1 = {1'b0, 1'b1, c ? 2'(x) : 31'(y)};
  wire [64:0] o2 = {1'b0, 1'b1, (c ? x[1:0] : y[0])};
  wire [64:0] o3 = {1'b1, 2'(x)};
  always @(posedge clk) $display("%h %h %h", o1, o2, o3);
endmodule
"""
_TC_EXPECTED = ["00000000080000003 00000000000000007 00000000000000007"]

# integer function returns / integer task ports (were unsigned, 1-bit ports)
_FINT = """module t(input clk);
  function integer g(input integer x, input [3:0] y); g = x - y; endfunction
  task automatic tk(input integer n, output integer m); m = n * -2; endtask
  integer r; reg [63:0] w; reg c;
  always @(posedge clk) begin
    w = g(-7, 4'd3);
    c = g(1, 4'd5) < 0;
    tk(-3, r);
    $display("%h %b %0d %0d", w, c, r, g(-100, 4'd1) >>> 2);
  end
endmodule
"""
_FINT_EXPECTED = ["fffffffffffffff6 1 6 -26"]

# compiled: >64-bit **, <<<, XNOR were unsupported/wrong; signed-parameter casts zero-extended (all)
_WIDE_OPS = """module t(input clk);
  reg [99:0] a = {36'h9, 64'hffff_0000_1234_5678}; reg signed [99:0] sa = -100'sd7;
  reg [3:0] e = 4'd5; reg [3:0] ex = 4'bx; reg [69:0] w70 = {6'h2a, 64'h1};
  parameter signed [14:0] P = 15'sd14651;
  wire [99:0] p1 = a ** 2'd2;
  wire [99:0] p2 = a ** e;
  wire signed [99:0] p3 = sa ** 3'sd3;
  wire [99:0] p8 = a ** ex;
  wire [99:0] p9 = 2 ** 7'd90;
  wire [99:0] s1 = w70 <<< 7;
  wire [44:0] x1 = {w70[3] ~^ 124'd17899840811320458329098487409344238289, 1'b1};
  wire [14:0] c1 = 12'(P);
  always @(posedge clk) $display("%h %h %h %h %h %h %h %b", p1, p2, p3, p8, p9, s1, x1, c1);
endmodule
"""
_WIDE_OPS_EXPECTED = [
    "26c169cf7545b66dc1df4d840 0e6897d30334ba049de078000 ffffffffffffffffffffffea9 xxxxxxxxxxxxxxxxxxxxxxxxx 0040000000000000000000000 0000015000000000000000080 0514ead85a5d 111100100111011"
]

# >>> of an unsigned operand sign-filled on every engine
_UNSIGNED_ASHR = """module t(input clk);
  reg [126:0] i4 = {1'b1, 126'h3}; reg [7:0] u = 8'h80;
  wire [63:0] o1 = {26'd0, i4 >>> 65};
  wire [63:0] o2 = i4 >>> 65;
  wire [7:0] o3 = {u >>> 1};
  wire [15:0] o4 = {8'd0, u >>> 1};
  always @(posedge clk) $display("%h %h %h %h", o1, o2, o3, o4);
endmodule
"""
_UNSIGNED_ASHR_EXPECTED = ["2000000000000000 2000000000000000 40 0040"]

# compiled: the x mask of >>> wasn't shifted
_ASHR_X = """module t(input clk);
  reg [7:0] u = 8'bx100_0000; reg signed [7:0] s = 8'sbx100_0000; reg [99:0] w = {1'bx, 99'h1};
  wire [7:0] o1 = u >>> 2; wire [7:0] o2 = s >>> 2; wire [99:0] o3 = w >>> 3; wire [15:0] o4 = u >>> 2;
  always @(posedge clk) $display("%b %b %h %b", o1, o2, o3, o4);
endmodule
"""
_ASHR_X_EXPECTED = ["00x10000 xxx10000 X000000000000000000000000 0000000000x10000"]

# compiled: reg [99:0] r = -1 set only 32 bits
_WIDE_MINUS_ONE = """module t(input clk);
  reg signed [99:0] m1 = -1; reg signed [99:0] m2 = -100'sd1; reg [99:0] m3 = -1; reg signed [99:0] m4;
  initial m4 = -1;
  always @(posedge clk) $display("%h %h %h %h", m1, m2, m3, m4);
endmodule
"""
_WIDE_MINUS_ONE_EXPECTED = [
    "fffffffffffffffffffffffff fffffffffffffffffffffffff fffffffffffffffffffffffff fffffffffffffffffffffffff"
]

_CASES = {
    "wide_ops": (_WIDE_OPS, _WIDE_OPS_EXPECTED, 8),
    "unsigned_ashr": (_UNSIGNED_ASHR, _UNSIGNED_ASHR_EXPECTED, 8),
    "ashr_x": (_ASHR_X, _ASHR_X_EXPECTED, 8),
    "wide_minus_one": (_WIDE_MINUS_ONE, _WIDE_MINUS_ONE_EXPECTED, 8),
    "two": (_TWO, _TWO_EXPECTED, 8),
    "wide512": (_WIDE512, _WIDE512_EXPECTED, 8),
    "tc": (_TC, _TC_EXPECTED, 8),
    "fint": (_FINT, _FINT_EXPECTED, 8),
}


@pytest.mark.parametrize("case", sorted(_CASES))
@pytest.mark.parametrize("engine", ENGINES)
def test_fuzz_finding(engine, case):
    src, expected, max_time = _CASES[case]
    design = tree_to_design(verilog_parser(start="source_text").build_tree(src), source_file=f"{case}.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=max_time + 20)
    assert list(sim.display_output)[: len(expected)] == expected


_TWO_STATE_PORTS = """module t(input clk, input [7:0] din, output bit [7:0] bo);
  bit [99:0] w; byte b; int i; shortint s; int unsigned u; reg [7:0] xr;
  function automatic int f(input [7:0] a); int loc; begin loc = loc + a; f = loc; end endfunction
  assign bo = din ^ 8'bx0x0_1111;
  always @(posedge clk) begin
    w = {4'hx, 96'h5};
    b <= 8'b1x1z_0101;
    i[3:0] = 4'bxx11;
    s = xr;
    u = -1;
    $display("%h %b %0d %0d %0d %0d %b", w, b, i, s, u, f(8'd3), bo);
  end
endmodule
"""
_TWO_STATE_PORTS_EXPECTED = [
    "0000000000000000000000005 00000000 3 0 4294967295 3 01010101",
    "0000000000000000000000005 10100101 3 0 4294967295 3 01010101",
    "0000000000000000000000005 10100101 3 0 4294967295 3 01010101",
]


@pytest.mark.parametrize("engine", ENGINES)
def test_two_state_ports_functions_and_wide(engine):
    """A `bit` output port, a wide `bit` vector, NBA and part-select
    writes, `int unsigned`, and an `int` function local (starts at 0 on
    each call)."""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_TWO_STATE_PORTS), source_file="two2.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.drive("din", 0x5A)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=28)
    assert list(sim.display_output) == _TWO_STATE_PORTS_EXPECTED


# 0->x / x->0 are edges (IEEE 1364 9.7.2): reference ignored 0->x, compiled
# ignored the mask entirely (0->x and x->0 never fired).
_X_EDGES = """module t; reg clk; reg [3:0] d = 0; reg [3:0] q = 0; integer n = 0;
  always @(posedge clk) begin q <= d; n = n + 1; end
  initial begin
    clk = 0; #1 d = 1; clk = 1'bx; #1 $display("0->x: %0d %0d", n, q);
    d = 2; clk = 1; #1 $display("x->1: %0d %0d", n, q);
    clk = 0; #1 d = 3; clk = 1'bz; #1 $display("0->z: %0d %0d", n, q);
    d = 4; clk = 1'bx; #1 $display("z->x: %0d %0d", n, q);
    d = 5; clk = 1; #1 $display("x->1: %0d %0d", n, q);
  end
endmodule
"""
_X_EDGES_EXPECTED = ["0->x: 1 1", "x->1: 2 2", "0->z: 3 3", "z->x: 3 3", "x->1: 4 5"]


@pytest.mark.parametrize("engine", ENGINES)
def test_x_clock_edges(engine):
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_X_EDGES), source_file="xedge.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=12)
    assert list(sim.display_output) == _X_EDGES_EXPECTED
