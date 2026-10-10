"""``$display``-family formatting across engines (found 2026-10-08; see
sim/display_format.py). Expected output is Icarus Verilog's.

Before: every engine printed a value containing x as a lone ``x``, never
padded ``%h``/``%b``/``%o``/``%d`` to the value's width, printed signed
values unsigned and dropped arguments left over after a format string;
compiled also ignored x bits and truncated wide arguments to 64 bits (and
left a negative signed declaration initializer x), and vm-fast printed a
wide argument as 0.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_SRC = """module t(input clk);
  reg [7:0] a = 8'h05; reg [7:0] x8 = 8'bxxxx0101;
  reg [9:0] m = 10'b1x_xx11_0x0x; reg [7:0] ax = 8'hxx; reg [7:0] p = 8'b0000_00x1;
  reg signed [7:0] s = -8'sd5; reg [31:0] i = 7; integer k = -3;
  reg [99:0] w = {4'b1010, 96'h1}; reg [99:0] wx = {4'b1x10, 96'h1}; reg signed [99:0] ws = -100'sd12345678901234567890;
  reg [64:0] w65 = {1'b1, 64'd0};
  always @(posedge clk) begin
    $display("[%h][%0h][%5h][%05h][%1h]", a, a, a, a, a);
    $display("[%b][%0b][%12b][%012b]", a, a, a, a);
    $display("[%o][%0o][%5o]", a, a, a);
    $display("[%d][%0d][%5d][%05d][%1d]", a, a, a, a, a);
    $display("[%d][%0d][%d][%0d][%d][%d]", s, s, i, i, k, k);
    $display("[%h][%h][%h][%h]", x8, m, ax, p);
    $display("[%o][%o]", x8, m);
    $display("[%d][%d][%d][%0d][%5d]", x8, ax, p, ax, ax);
    $display("[%b][%0b]", m, p);
    $display("[%0h][%0o][%0b][%0d]", 8'h00, 8'h00, 8'h00, 8'h00);
    $display("[%0h][%0b]", ax, x8);
    $display("[%h][%d][%0d][%o]", w, w, w, w);
    $display("[%h][%d][%b]", wx, wx, w65);
    $display("[%d][%0d][%h]", ws, ws, ws);
    $display(a, s, k);
    $display("a=", a, " s=", s);
    $display("v=%0d", a, s, "|", x8);
    $display("%0d %d %h %d", $signed(a), -s, s, s + 8'sd1);
  end
endmodule
"""

_EXPECTED = [
    "[05][5][   05][00005][05]",
    "[00000101][101][    00000101][000000000101]",
    "[005][5][  005]",
    "[  5][5][    5][00005][5]",
    "[  -5][-5][         7][7][         -3][         -3]",
    "[x5][XXX][xx][0X]",
    "[xX5][1x6X]",
    "[  X][  x][  X][x][    x]",
    "[1xxx110x0x][x1]",
    "[0][0][0][0]",
    "[xx][xxxx0101]",
    "[a000000000000000000000001][ 792281625142643375935439503361][792281625142643375935439503361][1200000000000000000000000000000001]",
    "[X000000000000000000000001][                              X][10000000000000000000000000000000000000000000000000000000000000000]",
    "[          -12345678901234567890][-12345678901234567890][fffffffff54ab567314e0f52e]",
    "  5  -5         -3",
    "a=  5 s=  -5",
    "v=5  -5|  X",
    "5    5 fb   -4",
]


@pytest.mark.parametrize("engine", ENGINES)
def test_display_formats(engine):
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_SRC), source_file="fmt.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=8)
    assert list(sim.display_output) == _EXPECTED


_NEG_INIT = """module t;
  reg [7:0] r = -3'b001; reg [7:0] q = ~3'b001; reg signed [15:0] s = -8'sd3; reg [7:0] u = -1;
  initial #1 $display("%h %h %h %h", r, q, s, u);
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_declaration_initializer_context_width(engine):
    """Reference evaluated declaration initializers self-determined
    (``-3'b001`` -> 07), compiled left sized negations x."""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_NEG_INIT), source_file="neg.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=10)
    assert list(sim.display_output) == ["ff fe fffd ff"]


# 2026-10-09: ``$displayh/b/o``/``$writeh/b/o`` (leftover arguments in
# that radix; they were treated as plain $display), ``%e/%f/%g`` with
# width/precision (unimplemented: printed literally), signed ``%d`` of
# function/task ports and locals, and vm's ``$write`` (each was its own
# line). Expected output is Icarus's.
_RADIX = """module t;
  function automatic [7:0] f(input signed [7:0] a);
    reg signed [7:0] loc;
    integer n;
    begin loc = a; n = -5; $display("f: %d %d %d", a, loc, n); f = a; end
  endfunction
  task automatic tk(input signed [3:0] b);
    reg signed [3:0] l2;
    begin l2 = b; $display("tk: %d %d", b, l2); end
  endtask
  reg [7:0] a = 8'h5a; reg [11:0] w = 12'habc; integer i = 7; reg [7:0] y; reg [3:0] x4 = 4'b1x01;
  reg [7:0] x8 = 8'bx; reg signed [7:0] s = -8'sd3;
  initial begin
    #1;
    $displayh(a, " ", w);
    $displayb(a, x4);
    $displayo(w, " ", i);
    $writeh(a); $write("|"); $writeb(x4); $display;
    y = f(-8'sd3); tk(-4'sd2);
    $display("[%f][%e][%g][%0.2f][%10.3f][%.1e]", i, i, a, i, a, w);
    $display("[%f][%e][%g][%010.3f][%f]", x8, x8, x8, s, s);
  end
endmodule
"""
_RADIX_EXPECTED = [
    "5a abc",
    "010110101x01",
    "5274 00000000007",
    "5a|1x01",
    "f:   -3   -3          -5",
    "tk: -2 -2",
    "[7.000000][7.000000e+00][90][7.00][    90.000][2.7e+03]",
    "[0.000000][0.000000e+00][0][-00003.000][-3.000000]",
]


@pytest.mark.parametrize("engine", ENGINES)
def test_radix_tasks_real_formats_and_local_signedness(engine):
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_RADIX), source_file="radix.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=10)
    assert list(sim.display_output) == _RADIX_EXPECTED


# The same formats from a clocked process (compiled's native code path), and
# %m in a named block.
_RADIX_NATIVE = """module t(input clk);
  reg [7:0] a = 8'h5a; reg [11:0] w = 12'habc; integer i = 7; reg [3:0] x4 = 4'b1x01;
  reg [7:0] x8 = 8'bx; reg signed [7:0] s = -8'sd3; reg [99:0] big = {4'h9, 96'h0};
  always @(posedge clk) begin : blk
    $displayh(a, " ", w);
    $displayb(a, x4);
    $writeh(a); $write("|"); $writeb(x4); $display;
    $display("%m [%f][%e][%g][%0.2f][%10.3f][%.1e]", i, i, a, i, a, w);
    $display("[%f][%e][%g][%010.3f][%f][%g]", x8, x8, x8, s, s, big);
  end
endmodule
"""
_RADIX_NATIVE_EXPECTED = [
    "5a abc",
    "010110101x01",
    "5a|1x01",
    "t.blk [7.000000][7.000000e+00][90][7.00][    90.000][2.7e+03]",
    "[0.000000][0.000000e+00][0][-00003.000][-3.000000][7.13053e+29]",
]


@pytest.mark.parametrize("engine", ENGINES)
def test_radix_and_real_formats_native(engine):
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_RADIX_NATIVE), source_file="rnat.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=8)
    assert list(sim.display_output) == _RADIX_NATIVE_EXPECTED


# %m: the hierarchical scope (instance path, named block, function/task) --
# it used to print a "<module>" placeholder.
_SCOPE = """module t;
  sub u_a();
  initial begin : top_blk
    #2 $display("%m");
  end
endmodule
module sub;
  function automatic integer f(input integer x); begin $display("in f: %m"); f = x; end endfunction
  task automatic tk; $display("in tk: %m"); endtask
  inner u_in();
  integer r;
  initial begin
    #1 $display("sub: %m 100%%m");
    begin : named
      $display("named: %m");
    end
    r = f(1); tk;
  end
endmodule
module inner;
  initial #3 $display("inner: %m");
endmodule
"""
_SCOPE_EXPECTED = [
    "sub: t.u_a 100%m",
    "named: t.u_a.named",
    "in f: t.u_a.f",
    "in tk: t.u_a.tk",
    "t.top_blk",
    "inner: t.u_a.u_in",
]


@pytest.mark.parametrize("engine", ENGINES)
def test_scope_name_format(engine):
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_SCOPE), source_file="scope.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=10)
    assert list(sim.display_output) == _SCOPE_EXPECTED
