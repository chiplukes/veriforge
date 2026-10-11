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

# 2-state memories (byte/int/bit) start at 0
_TWO_STATE_MEMORY = """module t(input clk);
  byte m [0:3]; int w [0:1]; bit [99:0] bw [0:1]; reg [7:0] r [0:1]; reg [1:0] k = 0;
  always @(posedge clk) begin
    $display("%0d %0d %0d %h %b", m[1], m[k], w[0], bw[1], r[0]);
    m[2] = 8'bx1x1_0000; bw[1] = {4'hx, 96'h5};
    $display("%b %h", m[2], bw[1]);
  end
endmodule
"""
_TWO_STATE_MEMORY_EXPECTED = ["0 0 0 0000000000000000000000000 xxxxxxxx", "01010000 0000000000000000000000005"]

# vm: a signed task argument wasn't sign-extended into a wider port
_TASK_SIGNED_ARG = """module t(input clk);
  reg signed [6:0] o7; reg signed [6:0] k = -7'sd3; reg [32:0] q4, q5;
  task automatic tp(input [32:0] a1, output [32:0] q); q = a1; endtask
  always @(posedge clk) begin
    tp(o7, q4); tp(k, q5);
    $display("%h %h", q4, q5);
  end
endmodule
"""
_TASK_SIGNED_ARG_EXPECTED = ["xxxxxxxxx 1fffffffd"]

# compiled: an x task argument was bound as 0
_TASK_X_ARG = """module t(input clk);
  reg [6:0] o7; reg [47:0] r1; reg [32:0] q4; integer li;
  task automatic tk(input [32:0] a1, output [47:0] q); integer l1; begin l1 = a1[22:10]; q = {a1, l1[14:0]}; end endtask
  task automatic tp(input [32:0] a1, output [32:0] q); q = a1; endtask
  always @(posedge clk) begin
    tk(o7, r1);
    tp(o7, q4);
    $display("%h %h", r1, q4);
  end
endmodule
"""
_TASK_X_ARG_EXPECTED = ["000000XxX000 0000000Xx"]

# compiled: unsized literals >= 2**31 were 32-bit (4294967295 read as -1 in a wide signed target)
_UNSIZED_LITERAL_WIDE = """module t(input clk);
  logic signed [102:0] r10, r11, r12; reg [63:0] r13; reg signed [63:0] r14;
  always @(posedge clk) begin
    r10 <= 4294967295; r11 = 2147483648; r12 = 4294967295 + 1; r13 = 4294967295; r14 <= 4294967295;
    $display("%h %h %h %h %h", r10, r11, r12, r13, r14);
  end
endmodule
"""
_UNSIZED_LITERAL_WIDE_EXPECTED = [
    "xxxxxxxxxxxxxxxxxxxxxxxxxx 00000000000000000080000000 00000000000000000100000000 00000000ffffffff xxxxxxxxxxxxxxxx",
    "000000000000000000ffffffff 00000000000000000080000000 00000000000000000100000000 00000000ffffffff 00000000ffffffff",
]

# a nested cast's signedness (1'(12'(s)) of a signed s) was lost
_NESTED_CAST_SIGN = """module t(input clk);
  reg signed [31:0] i5 = 32'sd1; reg c = 1; reg signed [92:0] p2 = 93'sd5;
  wire [30:0] o1 = c ? -1'(12'(i5)) : p2;
  wire signed [30:0] o2 = -1'(12'(i5));
  wire [30:0] o3 = c ? -$signed(i5[0]) : p2;
  function automatic signed [0:0] f(input signed [0:0] a); f = a; endfunction
  wire [30:0] o4 = c ? -f(i5[0]) : p2;
  always @(posedge clk) $display("%h %h %h %h", o1, o2, o3, o4);
endmodule
"""
_NESTED_CAST_SIGN_EXPECTED = ["00000001 00000001 00000001 00000001"]

# a ternary inside an unsigned expression: its signed branches zero-extend (context signedness wins; they sign-extended on every engine)
_TERNARY_CONTEXT_SIGN = """module t(input clk);
  reg signed [15:0] i5 = 16'shda2a; reg c = 1; reg u = 0;
  wire [31:0] o1 = -(c ? i5 : 16'sd3) | 33'(u);
  wire [31:0] o2 = -i5 | 33'(u);
  wire [31:0] o3 = -(c ? i5 : 16'sd3) | {1'b0, u};
  wire [31:0] o4 = (c ? -i5 : 16'sd3) | 33'(u);
  always @(posedge clk) $display("%h %h %h %h", o1, o2, o3, o4);
endmodule
"""
_TERNARY_CONTEXT_SIGN_EXPECTED = ["ffff25d6 ffff25d6 ffff25d6 ffff25d6"]

# a loop rewriting a few signals filled vm-fast's per-process dirty list (one
# entry per write) and dropped later entries: the child's output never
# reached the port connection after the first evaluation
_DIRTY_LOOP = """module t(input clk);
  reg [79:0] i1 = 80'h9000000;
  wire [30:0] o5;
  c u4 (.i5(i1[27:24]), .o7(o5));
  always @(posedge clk) begin
    $display("%0d", o5);
    i1 <= i1 + 80'h1000000;
  end
endmodule
module c (input [3:0] i5, output reg [30:0] o7);
  integer k;
  reg w6;
  always @(*) begin
    for (k = 0; k < 8; k = k + 1) w6 = k[0];
    o7 = i5;
  end
endmodule
"""
_DIRTY_LOOP_EXPECTED = ["9", "10", "11"]

# a wide intermediate built from function locals: compiled didn't emit its
# wide helpers (the pre-scan saw the locals before renaming) -- build error
_FUNC_WIDE_LOCAL = """module t(input clk);
  function automatic signed [32:0] f1 (input [0:0] a1, input [11:0] a2);
    integer l1;
    integer l2;
    begin
      l1 = 3'(a2[32'd11]);
      l2 = a2 * a2[32'd6:32'd1];
      f1 = {32'd3{l1, a2, l2}};
    end
  endfunction
  task automatic tk(input [11:0] a, output [40:0] y); reg [7:0] q; begin q = a[7:0]; y = {5{q}} ^ {a, a, a, a}; end endtask
  reg [40:0] y;
  always @(posedge clk) begin
    tk(12'h9a5, y);
    $display("%h %h %h", f1(1, 12'habc), f1(0, 12'h0f3), y);
  end
endmodule
"""
_FUNC_WIDE_LOCAL_EXPECTED = ["000014208 10000361b 0fc003ffc00"]

# `*` is max(L(i), L(j)) wide, not the sum: reference/vm/vm-fast printed
# `a * b` of two 8-bit regs as 16 bits, and vm/vm-fast kept the carry in
# `(a * b) >> 4`
_MUL_WIDTH = """module t(input clk);
  reg [7:0] a = 8'hff, b = 8'h02; reg [3:0] e = 4'd3; reg [3:0] n = 4'd9;
  always @(posedge clk) begin
    $display("%h %h %h", a * b, (a * b) >> 4, {a * b});
    $display("%h %h %h", n ** e, (n ** e) >> 2, {n ** e});
    $display("%0d %0d %0d", $bits(a * b), $bits(n ** e), $bits(a * e));
    $display("%h %h", (a * e) >> 4, {a * 1'b1, 4'h0});
  end
endmodule
"""
_MUL_WIDTH_EXPECTED = ["fe 0f fe", "9 2 9", "8 4 8", "0f ff0"]

# `` of an x signal is never x, `` of one is all x (compiled)
_QUERY_X = """module t(input clk);
  parameter W = 12;
  localparam D = W * 3;
  reg [W-1:0] a;
  reg [D-1:0] b;
  reg [W*3-1:0] c;
  reg [7:0] m [0:3];
  always @(posedge clk) begin
    $display("%0d %0d %0d %0d %0d", $bits(a), $bits(b), $bits(c), $bits(m[1]), $bits({a, b}));
    $display("%0d %0d %0d %0d %0d %0d", $size(a), $left(b), $right(c), $high(a), $low(a), $clog2(a));
  end
endmodule
"""
_QUERY_X_EXPECTED = ["12 36 36 8 48", "12 35 0 11 0 x"]

# the base of `**` wider than the destination was truncated to the
# destination width first, dropping its x bits (vm, vm-fast, compiled)
_POW_WIDE_BASE = """module t(input clk);
  reg [127:0] i2; reg c = 1; reg [3:0] n;
  wire [14:0] o9 = {i2[9], c, 73'h2f9532a17c7a14276e} ** 2'd2;
  wire [14:0] o10 = {1'bx, 73'h2f9532a17c7a14276e} ** 2'd2;
  wire [14:0] o11 = {1'bx, 3'h5} ** 2'd2;
  wire [14:0] o12 = 74'h3 ** n;
  wire [70:0] o13 = {1'bx, 73'h2f9532a17c7a14276e} ** 2'd2;
  always @(posedge clk) $display("%b %b %b %b %h", o9, o10, o11, o12, o13);
endmodule
"""
_POW_WIDE_BASE_EXPECTED = ["xxxxxxxxxxxxxxx xxxxxxxxxxxxxxx xxxxxxxxxxxxxxx xxxxxxxxxxxxxxx xxxxxxxxxxxxxxxxxx"]

# `%d` pads a 1-bit signed value to 1 column, like an unsigned one (Icarus)
_DEC_PAD = """module t(input clk);
  reg signed [0:0] s1 = 0; reg [0:0] u1 = 0;
  reg signed [1:0] s2 = 0; reg [1:0] u2 = 0;
  reg signed [2:0] s3 = 0; reg [2:0] u3 = 0;
  reg signed [3:0] s4 = 0; reg [3:0] u4 = 0;
  reg signed [4:0] s5 = 0; reg [4:0] u5 = 0;
  reg signed [5:0] s6 = 0; reg [5:0] u6 = 0;
  reg signed [6:0] s7 = 0; reg [6:0] u7 = 0;
  reg signed [7:0] s8 = 0; reg [7:0] u8 = 0;
  reg signed [8:0] s9 = 0; reg [8:0] u9 = 0;
  reg signed [9:0] s10 = 0; reg [9:0] u10 = 0;
  reg signed [10:0] s11 = 0; reg [10:0] u11 = 0;
  reg signed [15:0] s16 = 0; reg [15:0] u16 = 0;
  reg signed [16:0] s17 = 0; reg [16:0] u17 = 0;
  reg signed [19:0] s20 = 0; reg [19:0] u20 = 0;
  reg signed [30:0] s31 = 0; reg [30:0] u31 = 0;
  reg signed [31:0] s32 = 0; reg [31:0] u32 = 0;
  reg signed [32:0] s33 = 0; reg [32:0] u33 = 0;
  reg signed [63:0] s64 = 0; reg [63:0] u64 = 0;
  reg signed [64:0] s65 = 0; reg [64:0] u65 = 0;
  reg signed [99:0] s100 = 0; reg [99:0] u100 = 0;
  always @(posedge clk) begin $display("[%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d]", s1, s2, s3, s4, s5, s6, s7, s8, s9, s10, s11, s16, s17, s20, s31, s32, s33, s64, s65, s100); $display("[%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d] [%d]", u1, u2, u3, u4, u5, u6, u7, u8, u9, u10, u11, u16, u17, u20, u31, u32, u33, u64, u65, u100); end
endmodule
"""
_DEC_PAD_EXPECTED = [
    "[0] [ 0] [ 0] [ 0] [  0] [  0] [  0] [   0] [   0] [   0] [    0] [     0] [     0] [      0] [          0] [          0] [          0] [                   0] [                    0] [                              0]",
    "[0] [0] [0] [ 0] [ 0] [ 0] [  0] [  0] [  0] [   0] [   0] [    0] [     0] [      0] [         0] [         0] [         0] [                   0] [                   0] [                              0]",
]

# `>>`'s left operand extends with the expression's signedness (the shift
# itself is logical): compiled zero-extended it, so `s8 >> 1` into 16 bits
# gave 007f, not 7fff (narrow and wide paths)
_SHR_SIGNED = """module t(input clk);
  reg signed [7:0] s8 = -8'sd2; reg [7:0] u8 = 8'hfe; reg signed [0:0] a0 = 1'b1; reg [3:0] a4 = 4'b1010;
  integer m = -1; reg [2:0] n = 3'd1;
  reg [15:0] q1, q2, q3, q4, q5, q6, q7, q8;
  always @(posedge clk) begin
    q1 = s8 >> 1;
    q2 = (s8 >> n) | 16'h0;
    q3 = (s8 >> n) | u8;
    q4 = (~s8) >> 1;
    q5 = (a4 & a0) >> n;
    q6 = $signed(s8 >> n);
    q7 = (s8 + 8'sd0) >> n;
    q8 = {s8 >> 1};
    $display("%h %h %h %h %h %h %h %h", q1, q2, q3, q4, q5, q6, q7, q8);
  end
endmodule
"""
_SHR_SIGNED_EXPECTED = ["7fff 007f 00ff 0000 0000 007f 7fff 007f"]
_SHR_SIGNED_WIDE = """module t(input clk);
  reg signed [7:0] s8 = -8'sd2; reg signed [69:0] s70 = -70'sd6; reg [7:0] u8 = 8'hfe; reg [2:0] n = 3'd1;
  reg [99:0] q1, q2, q3, q4, q5;
  always @(posedge clk) begin
    q1 = s8 >> 1;
    q2 = s70 >> n;
    q3 = (~s70) >> 1;
    q4 = (s70 >> n) | u8;
    q5 = (~s8) >> n;
    $display("%h %h %h %h %h", q1, q2, q3, q4, q5);
  end
endmodule
"""
_SHR_SIGNED_WIDE_EXPECTED = [
    "7ffffffffffffffffffffffff 7fffffffffffffffffffffffd 0000000000000000000000002 00000001fffffffffffffffff 0000000000000000000000000"
]

# `7'(a)` of a signed function/task port is signed (reference/vm/vm-fast
# looked the port up at module scope)
_CAST_PORT_SCOPE = """module t(input clk);
  reg signed [0:0] s = 1'b1; integer m = -1; reg [63:0] q1, q2, q3, q4;
  task automatic tk(input signed [0:0] a2, output [63:0] q);
    integer l1;
    begin l1 = a2; q = (1'b0 ? 7'(a2) : ~l1) >> 8; end
  endtask
  function automatic [63:0] fn(input signed [0:0] a2);
    integer l1;
    begin l1 = a2; fn = (1'b0 ? 7'(a2) : ~l1) >> 8; end
  endfunction
  always @(posedge clk) begin
    q1 = (1'b0 ? 7'(s) : ~m) >> 8;
    tk(1'b1, q2);
    q3 = fn(1'b1);
    q4 = (1'b0 ? 7'(s) : ~m);
    $display("%h %h %h %h", q1, q2, q3, q4);
  end
endmodule
"""
_CAST_PORT_SCOPE_EXPECTED = ["0000000000000000 0000000000000000 0000000000000000 0000000000000000"]

# compiled stored `integer`s unmasked at 64 bits: `i = 33'h1_0000_0005`
# kept bit 32, and an x mask leaked above bit 31 into a wider read
_INTEGER_MASKED = """module t(input clk);
  integer i, j, k; reg [63:0] q;
  always @(posedge clk) begin
    i = 33'h1_0000_0005;
    j = 64'hffff_0000_0000_0003;
    k = -7;
    q = i + j;
    $display("%0d %0d %0d %h %h %h", i, j, k, q, {i}, i + 64'd0);
  end
endmodule
"""
_INTEGER_MASKED_EXPECTED = ["5 3 -7 0000000000000008 00000005 0000000000000005"]
_INTEGER_MASK_LEAK = """module t(input clk);
  reg [15:0] a1;
  reg [15:0] a2 = 16'h0;
  integer l1, l3;
  reg [62:0] q1, q3;
  always @(posedge clk) begin
    l1 = $signed(a1 ? a1[9:4] : a1) ? a1[8] : {2{a1}} + (a1[2:0] >> 65);
    l3 = {2{a1}} + (a1[2:0] >> 65);
    q1 = a2 ? a2[0] : l1;
    q3 = a2 ? a2[0] : l3;
    $display("%h %h", q1, q3);
  end
endmodule
"""
_INTEGER_MASK_LEAK_EXPECTED = ["00000000xxxxxxxx 00000000xxxxxxxx"]

# compiled: a continuous assign's x mask wasn't truncated to the wire's
# width, and an assign whose inputs never changed at t=0 never ran (left
# all x) -- every continuous assign runs once at t=0
_CONT_MASK = """module t(input clk);
  reg signed [47:0] i5 = 48'bx0xx0x10xxx0xx1xxx00xx0x010x1xx1x010xxxx010xxx0x;
  reg [7:0] r8 = 8'b0000x1x0;
  wire [0:0] o6 = 8'(i5);
  wire [2:0] o7 = o6;
  wire [0:0] w1 = r8;
  wire [2:0] w3 = w1;
  wire [0:0] w2 = r8 + 8'd0;
  wire [2:0] w4 = w2;
  always @(posedge clk) $display("%b %b %b %b", o7, w3, w4, {o6, w1, w2});
endmodule
"""
_CONT_MASK_EXPECTED = ["00x 000 00x x0x"]

# division/modulus by a known zero is x, and the most negative value / -1
# wraps: compiled divided natively (SIGFPE), vm-fast trapped on LLONG_MIN / -1
_DIV_EDGES = """module t(input clk);
  reg signed [63:0] mn = 64'sh8000_0000_0000_0000, m1 = -64'sd1, z = 0, a = -64'sd7, b = 64'sd2;
  reg signed [7:0] s8 = -8'sd128, n8 = -8'sd1;
  reg signed [63:0] q1, q2, q3, q4, q5, q6;
  reg signed [7:0] q7, q8;
  always @(posedge clk) begin
    q1 = mn / m1; q2 = mn % m1; q3 = a / b; q4 = a % b; q5 = a / z; q6 = a % z;
    q7 = s8 / n8; q8 = s8 % n8;
    $display("%h %h %0d %0d %0d %0d %0d %0d", q1, q2, q3, q4, q5, q6, q7, q8);
  end
endmodule
"""
_DIV_EDGES_EXPECTED = ["8000000000000000 0000000000000000 -3 -1 x x -128 0"]
_DIV_ZERO = """module t(input clk);
  reg [31:0] count, c2, c3; integer modulus; integer z;
  initial begin
    modulus = 32'd2 ** 32'd32;
    count = 32'd7 % modulus;
    z = 0;
    c2 = 32'd7 % z;
    c3 = 32'd7 / z;
  end
  always @(posedge clk) $display("%0d %0d %0d %0d", modulus, count, c2, c3);
endmodule
"""
_DIV_ZERO_EXPECTED = ["0 x x x"]

# `$signed`/`$unsigned` results are only widened to their context, never
# truncated (reference truncated `-$unsigned(33'bx...1)` to 7 bits, dropping
# the x bits before negating)
_CAST_NO_TRUNCATE = """module t(input clk);
  reg [12:0] z = 0;
  wire [6:0] o1 = -$unsigned(33'bx110010x10111000010110x10x1000111 ~^ z);
  wire [6:0] o2 = -$unsigned(33'bx110010x10111000010110x10x1000111);
  wire [6:0] o3 = -(33'bx110010x10111000010110x10x1000111);
  always @(posedge clk) $display("%b %b %b", o1, o2, o3);
endmodule
"""
_CAST_NO_TRUNCATE_EXPECTED = ["xxxxxxx xxxxxxx xxxxxxx"]

# an unsigned `&`/`|` zero-extends a `$signed` operand in its x mask too
# (compiled sign-extended the operand's value there)
_BITWISE_MASK_SIGN = """module t(input clk);
  reg [2:0] a = 3'b110; reg c = 1;
  reg [62:0] q1, q2, q3;
  always @(posedge clk) begin
    q1 = {1'b1, 12'b11x0101x0011} & $signed(a[1:0] ? a : a);
    q2 = {1'b1, 12'b11x0101x0011} & $signed(a);
    q3 = {1'b1, 12'b11x0101x0011} & $signed(c ? a : a);
    $display("%b %b %b", q1[12:0], q2[12:0], q3[12:0]);
  end
endmodule
"""
_BITWISE_MASK_SIGN_EXPECTED = ["0000000000010 0000000000010 0000000000010"]
_BITWISE_MASK_SIGN_WIDE = """module t(input clk);
  reg [2:0] a = 3'b110; reg c = 1; reg signed [2:0] sa = 3'sb110;
  reg [99:0] q1, q2, q3, q4;
  always @(posedge clk) begin
    q1 = {1'b1, 80'bx0x0, 12'b11x0101x0011} & $signed(a);
    q2 = {1'b1, 80'bx0x0, 12'b11x0101x0011} | $signed(c ? a : a);
    q3 = {1'b1, 12'b11x0101x0011} & sa;
    q4 = $signed({1'b1, 12'b11x0101x0011}) & sa;
    $display("%h %h %h %h", q1, q2, q3, q4);
  end
endmodule
"""
_BITWISE_MASK_SIGN_WIDE_EXPECTED = [
    "0000000000000000000000002 01xxxxxxxxxxxxxxxxxxxXXX7 0000000000000000000000002 ffffffffffffffffffffffXX2"
]

# a wide `**` result is the base's width (vm-fast kept the full power, so
# bits above it leaked into a wider context)
_POW_WIDE_TRUNC = """module t(input clk);
  reg [107:0] i5 = {4'h9, 104'h1234_5678_9abc_def0_1122_3344_55}; reg b = 1;
  reg signed [79:0] s = -80'sd3; reg signed [1:0] e2 = 2'sd1;
  wire [126:0] o6 = $unsigned({i5, b} ** 2'd3);
  wire [99:0] o7 = $unsigned(s ** 2'd3);
  wire [99:0] o8 = $unsigned(s ** e2);
  always @(posedge clk) $display("%h %h %h", o6, o7, o8);
endmodule
"""
_POW_WIDE_TRUNC_EXPECTED = ["00001d9421a11ecfbe4ec3c7d2b92413 00000ffffffffffffffffffe5 00000fffffffffffffffffffd"]

_CASES = {
    "ternary_context_sign": (_TERNARY_CONTEXT_SIGN, _TERNARY_CONTEXT_SIGN_EXPECTED, 8),
    "unsized_literal_wide": (_UNSIZED_LITERAL_WIDE, _UNSIZED_LITERAL_WIDE_EXPECTED, 18),
    "nested_cast_sign": (_NESTED_CAST_SIGN, _NESTED_CAST_SIGN_EXPECTED, 8),
    "two_state_memory": (_TWO_STATE_MEMORY, _TWO_STATE_MEMORY_EXPECTED, 8),
    "task_signed_arg": (_TASK_SIGNED_ARG, _TASK_SIGNED_ARG_EXPECTED, 8),
    "task_x_arg": (_TASK_X_ARG, _TASK_X_ARG_EXPECTED, 8),
    "wide_ops": (_WIDE_OPS, _WIDE_OPS_EXPECTED, 8),
    "unsigned_ashr": (_UNSIGNED_ASHR, _UNSIGNED_ASHR_EXPECTED, 8),
    "ashr_x": (_ASHR_X, _ASHR_X_EXPECTED, 8),
    "wide_minus_one": (_WIDE_MINUS_ONE, _WIDE_MINUS_ONE_EXPECTED, 8),
    "two": (_TWO, _TWO_EXPECTED, 8),
    "wide512": (_WIDE512, _WIDE512_EXPECTED, 8),
    "tc": (_TC, _TC_EXPECTED, 8),
    "fint": (_FINT, _FINT_EXPECTED, 8),
    "dirty_loop": (_DIRTY_LOOP, _DIRTY_LOOP_EXPECTED, 8),
    "func_wide_local": (_FUNC_WIDE_LOCAL, _FUNC_WIDE_LOCAL_EXPECTED, 8),
    "mul_width": (_MUL_WIDTH, _MUL_WIDTH_EXPECTED, 8),
    "query_x": (_QUERY_X, _QUERY_X_EXPECTED, 8),
    "pow_wide_base": (_POW_WIDE_BASE, _POW_WIDE_BASE_EXPECTED, 8),
    "dec_pad": (_DEC_PAD, _DEC_PAD_EXPECTED, 8),
    "shr_signed": (_SHR_SIGNED, _SHR_SIGNED_EXPECTED, 8),
    "shr_signed_wide": (_SHR_SIGNED_WIDE, _SHR_SIGNED_WIDE_EXPECTED, 8),
    "cast_port_scope": (_CAST_PORT_SCOPE, _CAST_PORT_SCOPE_EXPECTED, 8),
    "integer_masked": (_INTEGER_MASKED, _INTEGER_MASKED_EXPECTED, 8),
    "integer_mask_leak": (_INTEGER_MASK_LEAK, _INTEGER_MASK_LEAK_EXPECTED, 8),
    "cont_mask": (_CONT_MASK, _CONT_MASK_EXPECTED, 8),
    "div_edges": (_DIV_EDGES, _DIV_EDGES_EXPECTED, 8),
    "div_zero": (_DIV_ZERO, _DIV_ZERO_EXPECTED, 8),
    "cast_no_truncate": (_CAST_NO_TRUNCATE, _CAST_NO_TRUNCATE_EXPECTED, 8),
    "bitwise_mask_sign": (_BITWISE_MASK_SIGN, _BITWISE_MASK_SIGN_EXPECTED, 8),
    "bitwise_mask_sign_wide": (_BITWISE_MASK_SIGN_WIDE, _BITWISE_MASK_SIGN_WIDE_EXPECTED, 8),
    "pow_wide_trunc": (_POW_WIDE_TRUNC, _POW_WIDE_TRUNC_EXPECTED, 8),
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


# %e/%f/%g of a partly-x value: x bits read as 0, as in Icarus (it used to
# print 0 for any x bit).
_REAL_X = """module t;
  reg [6:0] a = 7'bxxx0101; reg [5:0] b = 6'bxx1010; reg [7:0] c = 8'bx0000001; reg [9:0] d = 10'bxx00000011;
  reg signed [7:0] s = 8'sbx1111111; reg [2:0] e = 3'bxxx; reg [4:0] f = 5'bx0001;
  initial #1 $display("[%h][%h][%o][%e][%f][%g][%e][%h][%h][%o][%d]", a, b, b, c, d, c, s, e, f, f, f);
endmodule
"""
_REAL_X_EXPECTED = ["[x5][xa][X2][1.000000e+00][3.000000][1][1.270000e+02][x][x1][X1][ X]"]


@pytest.mark.parametrize("engine", ENGINES)
def test_real_formats_of_partly_x_values(engine):
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_REAL_X), source_file="fx.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=10)
    assert list(sim.display_output) == _REAL_X_EXPECTED


# `**` takes its base's signedness (as Icarus): casts/shifts/comparisons of
# `s ** 2'd1` with a signed `s` treated it as unsigned.
_POW_SIGN = """module t; reg signed [7:0] s = -8'sd2; reg [7:0] u = 8'd2;
  wire [15:0] a = s ** 2'd3; wire [15:0] b = s ** 3'sd3; wire [15:0] c = u ** 3'sd3; wire [15:0] d = 3'(s ** 2'd1);
  initial #1 $display("%h %h %h %h", a, b, c, d);
endmodule
module t2; reg signed [7:0] s = -8'sd2; reg [7:0] u = 8'd2;
  wire [15:0] e = (s ** 2'd1) + u; wire [15:0] f = (s ** 2'd1) >>> 1; wire g = (s ** 2'd1) < 0;
  initial #1 $display("%h %h %b %0d", e, f, g, s ** 2'd1);
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_power_result_takes_base_signedness(engine):
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_POW_SIGN), source_file="pw.v")
    for name, expected in (("t", ["fff8 fff8 0008 fffe"]), ("t2", ["0100 ffff 1 -2"])):
        top = next(m for m in design.modules if m.name == name)
        sim = Simulator(top, engine=engine, design=design)
        sim.run(max_time=10)
        assert list(sim.display_output) == expected
