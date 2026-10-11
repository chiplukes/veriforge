"""Writes and selects at the edges of their targets (2026-10-10 audit of
compiled's unmasked-storage paths, which turned up bugs in every engine):
out-of-range and x/z indices and addresses, negative (signed) part-select
bases, and values wider than the target. Expected outputs are from Icarus."""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

# every write form (procedural, NBA, typed, memory, continuous, initializer) with values and x above the target width, read back wider: nothing leaks
_WRITES_BY_FORM = """module t(input clk);
  reg [15:0] src = 16'h1234;
  reg [15:0] srcx = 16'bxx1x_10x1_1011_0110;
  reg [15:0] s2 = 16'hfff5;
  reg signed [3:0] sn = -4'sd3;
  reg [63:0] wide = 64'hx0f0_1234_5678_9abc;
  reg [3:0] idx = 4'd2; reg [3:0] xi = 4'bx01x; reg c = 1'bx;
  function automatic [15:0] f16(input [15:0] a); f16 = a ^ 16'hff00; endfunction
  task automatic tk16(input [15:0] a, output [15:0] q); q = a + 16'h0101; endtask
  reg [7:0] b_whole;
  reg [7:0] b_range;
  reg [7:0] b_range2;
  reg [7:0] b_pup;
  reg [7:0] b_pdn;
  reg [7:0] b_bit;
  reg [7:0] b_bitx;
  reg [7:0] b_cat;
  reg [7:0] b_tern;
  reg [7:0] b_sub;
  reg [7:0] b_neg;
  reg [7:0] b_not;
  reg [7:0] b_shl;
  reg [7:0] b_mul;
  reg [7:0] b_sgn;
  reg [7:0] b_func;
  reg [7:0] b_task;
  reg [7:0] n_whole;
  reg [7:0] n_range;
  reg [7:0] n_bit;
  reg [7:0] n_pup;
  reg [7:0] n_cat;
  int t_int;
  integer t_integer;
  byte t_byte;
  shortint t_short;
  bit [5:0] t_bit;
  reg [7:0] mm [0:7];
  reg [7:0] mn [0:7];
  wire [7:0] c_whole;
  assign c_whole = srcx;
  wire [7:0] c_range;
  assign c_range[3:0] = srcx; assign c_range[7:4] = s2;
  wire [7:0] c_bit;
  assign c_bit[0] = srcx; assign c_bit[7:1] = 0;
  wire [7:0] c_cat;
  assign {c_cat[1:0], c_cat[7:2]} = srcx;
  wire [7:0] c_tern;
  assign c_tern = c ? srcx : wide;
  wire [7:0] c_func;
  assign c_func = f16(srcx);
  wire [7:0] c_sub;
  assign c_sub = srcx - wide;
  reg [7:0] i_init = 16'hx5a5;
  wire [7:0] i_wire = srcx + 16'h100;
  always @(posedge clk) begin
    b_whole = src;
    b_range = 0; b_range[3:0] = src;
    b_range2 = 0; b_range2[6:3] = srcx;
    b_pup = 0; b_pup[idx +: 3] = srcx;
    b_pdn = 0; b_pdn[idx+3 -: 3] = srcx;
    b_bit = 0; b_bit[idx] = srcx;
    b_bitx = 0; b_bitx[xi] = 1'b1;
    {b_cat[3:0], b_cat[7:4]} = srcx;
    b_tern = c ? srcx : s2;
    b_sub = srcx - 1;
    b_neg = -srcx;
    b_not = ~srcx;
    b_shl = srcx << 2;
    b_mul = srcx * 3;
    b_sgn = $signed(sn) ;
    b_func = f16(srcx);
    tk16(srcx, b_task);
    n_whole <= srcx;
    n_range <= 0; n_range[5:2] <= srcx;
    n_bit <= 0; n_bit[idx] <= srcx;
    n_pup <= 0; n_pup[idx +: 3] <= srcx;
    {n_cat[3:0], n_cat[7:4]} <= srcx;
    t_int = wide;
    t_integer = wide;
    t_byte = srcx;
    t_short = wide;
    t_bit = srcx;
    mm[0] = srcx;
    mm[1] = 0; mm[1][5:2] = srcx;
    mm[2] = 0; mm[2][idx] = srcx;
    mm[3] = 0; mm[3][idx +: 3] = srcx;
    mm[idx+2] = srcx;
    mn[0] <= srcx;
    mn[1] <= 0; mn[1][5:2] <= srcx;
    mn[2] <= 0; mn[2][idx] <= srcx;
  end
  always @(negedge clk) begin
    $display("b_whole %h %h %h", b_whole + 16'd0, b_whole ^ 16'd0, {8'd0, b_whole} >> 0);
    $display("b_range %h %h %h", b_range + 16'd0, b_range ^ 16'd0, {8'd0, b_range} >> 0);
    $display("b_range2 %h %h %h", b_range2 + 16'd0, b_range2 ^ 16'd0, {8'd0, b_range2} >> 0);
    $display("b_pup %h %h %h", b_pup + 16'd0, b_pup ^ 16'd0, {8'd0, b_pup} >> 0);
    $display("b_pdn %h %h %h", b_pdn + 16'd0, b_pdn ^ 16'd0, {8'd0, b_pdn} >> 0);
    $display("b_bit %h %h %h", b_bit + 16'd0, b_bit ^ 16'd0, {8'd0, b_bit} >> 0);
    $display("b_bitx %h %h %h", b_bitx + 16'd0, b_bitx ^ 16'd0, {8'd0, b_bitx} >> 0);
    $display("b_cat %h %h %h", b_cat + 16'd0, b_cat ^ 16'd0, {8'd0, b_cat} >> 0);
    $display("b_tern %h %h %h", b_tern + 16'd0, b_tern ^ 16'd0, {8'd0, b_tern} >> 0);
    $display("b_sub %h %h %h", b_sub + 16'd0, b_sub ^ 16'd0, {8'd0, b_sub} >> 0);
    $display("b_neg %h %h %h", b_neg + 16'd0, b_neg ^ 16'd0, {8'd0, b_neg} >> 0);
    $display("b_not %h %h %h", b_not + 16'd0, b_not ^ 16'd0, {8'd0, b_not} >> 0);
    $display("b_shl %h %h %h", b_shl + 16'd0, b_shl ^ 16'd0, {8'd0, b_shl} >> 0);
    $display("b_mul %h %h %h", b_mul + 16'd0, b_mul ^ 16'd0, {8'd0, b_mul} >> 0);
    $display("b_sgn %h %h %h", b_sgn + 16'd0, b_sgn ^ 16'd0, {8'd0, b_sgn} >> 0);
    $display("b_func %h %h %h", b_func + 16'd0, b_func ^ 16'd0, {8'd0, b_func} >> 0);
    $display("b_task %h %h %h", b_task + 16'd0, b_task ^ 16'd0, {8'd0, b_task} >> 0);
    $display("n_whole %h %h %h", n_whole + 16'd0, n_whole ^ 16'd0, {8'd0, n_whole} >> 0);
    $display("n_range %h %h %h", n_range + 16'd0, n_range ^ 16'd0, {8'd0, n_range} >> 0);
    $display("n_bit %h %h %h", n_bit + 16'd0, n_bit ^ 16'd0, {8'd0, n_bit} >> 0);
    $display("n_pup %h %h %h", n_pup + 16'd0, n_pup ^ 16'd0, {8'd0, n_pup} >> 0);
    $display("n_cat %h %h %h", n_cat + 16'd0, n_cat ^ 16'd0, {8'd0, n_cat} >> 0);
    $display("t_int %h %h %h", t_int + 16'd0, t_int ^ 16'd0, {8'd0, t_int} >> 0);
    $display("t_integer %h %h %h", t_integer + 16'd0, t_integer ^ 16'd0, {8'd0, t_integer} >> 0);
    $display("t_byte %h %h %h", t_byte + 16'd0, t_byte ^ 16'd0, {8'd0, t_byte} >> 0);
    $display("t_short %h %h %h", t_short + 16'd0, t_short ^ 16'd0, {8'd0, t_short} >> 0);
    $display("t_bit %h %h %h", t_bit + 16'd0, t_bit ^ 16'd0, {8'd0, t_bit} >> 0);
    $display("mm_0 %h %h %h", mm[0] + 16'd0, mm[0] ^ 16'd0, {8'd0, mm[0]} >> 0);
    $display("mm_1 %h %h %h", mm[1] + 16'd0, mm[1] ^ 16'd0, {8'd0, mm[1]} >> 0);
    $display("mm_2 %h %h %h", mm[2] + 16'd0, mm[2] ^ 16'd0, {8'd0, mm[2]} >> 0);
    $display("mm_3 %h %h %h", mm[3] + 16'd0, mm[3] ^ 16'd0, {8'd0, mm[3]} >> 0);
    $display("mm_4 %h %h %h", mm[4] + 16'd0, mm[4] ^ 16'd0, {8'd0, mm[4]} >> 0);
    $display("mn_0 %h %h %h", mn[0] + 16'd0, mn[0] ^ 16'd0, {8'd0, mn[0]} >> 0);
    $display("mn_1 %h %h %h", mn[1] + 16'd0, mn[1] ^ 16'd0, {8'd0, mn[1]} >> 0);
    $display("mn_2 %h %h %h", mn[2] + 16'd0, mn[2] ^ 16'd0, {8'd0, mn[2]} >> 0);
    $display("c_whole %h %h %h", c_whole + 16'd0, c_whole ^ 16'd0, {8'd0, c_whole} >> 0);
    $display("c_range %h %h %h", c_range + 16'd0, c_range ^ 16'd0, {8'd0, c_range} >> 0);
    $display("c_bit %h %h %h", c_bit + 16'd0, c_bit ^ 16'd0, {8'd0, c_bit} >> 0);
    $display("c_cat %h %h %h", c_cat + 16'd0, c_cat ^ 16'd0, {8'd0, c_cat} >> 0);
    $display("c_tern %h %h %h", c_tern + 16'd0, c_tern ^ 16'd0, {8'd0, c_tern} >> 0);
    $display("c_func %h %h %h", c_func + 16'd0, c_func ^ 16'd0, {8'd0, c_func} >> 0);
    $display("c_sub %h %h %h", c_sub + 16'd0, c_sub ^ 16'd0, {8'd0, c_sub} >> 0);
    $display("i_init %h %h %h", i_init + 16'd0, i_init ^ 16'd0, {8'd0, i_init} >> 0);
    $display("i_wire %h %h %h", i_wire + 16'd0, i_wire ^ 16'd0, {8'd0, i_wire} >> 0);
  end
endmodule
"""
_WRITES_BY_FORM_EXPECTED = [
    "b_whole 0034 0034 0034",
    "b_range 0004 0004 0004",
    "b_range2 0030 0030 0030",
    "b_pup 0018 0018 0018",
    "b_pdn 0030 0030 0030",
    "b_bit 0000 0000 0000",
    "b_bitx 0000 0000 0000",
    "b_cat 006b 006b 006b",
    "b_tern xxxx 00XX 00XX",
    "b_sub xxxx 00xx 00xx",
    "b_neg xxxx 00xx 00xx",
    "b_not 0049 0049 0049",
    "b_shl 00d8 00d8 00d8",
    "b_mul xxxx 00xx 00xx",
    "b_sgn 00fd 00fd 00fd",
    "b_func 00b6 00b6 00b6",
    "b_task xxxx 00xx 00xx",
    "n_whole 00b6 00b6 00b6",
    "n_range 0018 0018 0018",
    "n_bit 0000 0000 0000",
    "n_pup 0018 0018 0018",
    "n_cat 006b 006b 006b",
    "t_int 56789abc 56789abc 0056789abc",
    "t_integer 56789abc 56789abc 0056789abc",
    "t_byte 00b6 00b6 00b6",
    "t_short 9abc 9abc 009abc",
    "t_bit 0036 0036 0036",
    "mm_0 00b6 00b6 00b6",
    "mm_1 0018 0018 0018",
    "mm_2 0000 0000 0000",
    "mm_3 0018 0018 0018",
    "mm_4 00b6 00b6 00b6",
    "mn_0 00b6 00b6 00b6",
    "mn_1 0018 0018 0018",
    "mn_2 0000 0000 0000",
    "c_whole 00b6 00b6 00b6",
    "c_range 0056 0056 0056",
    "c_bit 0000 0000 0000",
    "c_cat 00da 00da 00da",
    "c_tern xxxx 00bX 00bX",
    "c_func 00b6 00b6 00b6",
    "c_sub xxxx 00xx 00xx",
    "i_init 00a5 00a5 00a5",
    "i_wire xxxx 00xx 00xx",
]

# out-of-range bit/part selects and memory addresses: reads x, writes ignored (compiled indexed past its C arrays: m[5] of m[0:3] aliased m[1] and other state)
_OUT_OF_RANGE = """module t(input clk);
  reg [7:0] a, d, e, f; reg [7:0] m [0:3]; reg [99:0] w; reg [99:0] wm [0:1];
  reg [7:0] big = 8'd200; reg [3:0] nine = 4'd9; reg [2:0] five = 3'd5; reg [7:0] k = 8'd150;
  wire [7:0] cw; assign cw[0] = 1'b1; assign cw[7:1] = 0;
  always @(posedge clk) begin
    a = 0; a[nine] = 1'b1;
    d = 0; d[nine +: 2] = 2'b11;
    m[1] = 1; m[five] = 8'hff;
    w = 0; w[k] = 1'b1; w[big +: 4] = 4'hf;
    wm[0] = 0; wm[five] = 100'h5;
    e = 0; e[nine -: 3] = 3'b111;
    f <= 0; f[nine] <= 1'b1;
    $display("%h %h %h %h %h %b %b %b %h %h %h", a, d, m[1], w, wm[0], a[nine], m[five], w[k], d[nine +: 2], e, wm[five]);
  end
endmodule
"""
_OUT_OF_RANGE_EXPECTED = [
    "00 00 01 0000000000000000000000000 0000000000000000000000000 x xxxxxxxx x x 80 xxxxxxxxxxxxxxxxxxxxxxxxx",
    "00 00 01 0000000000000000000000000 0000000000000000000000000 x xxxxxxxx x x 80 xxxxxxxxxxxxxxxxxxxxxxxxx",
]

# an x/z index selects nothing: reads x, writes ignored (compiled read x bits as 0)
_X_INDEX = """module t(input clk);
  reg [7:0] a, b, d, e; reg [7:0] m [0:3]; reg [3:0] xi = 4'bx01x; reg [3:0] zi = 4'bxxxx; reg [1:0] mx = 2'bx1;
  always @(posedge clk) begin
    a = 0; a[xi] = 1'b1;
    b = 0; b[zi] = 1'b1;
    d = 0; d[xi +: 2] = 2'b11;
    m[1] = 0; m[3] = 0; m[mx] = 8'hff;
    e = 0; e <= 8'h0; e[xi] <= 1'b1;
    $display("%h %h %h %h %h %h", a, b, d, m[1], m[3], a[xi]);
  end
endmodule
"""
_X_INDEX_EXPECTED = ["00 00 00 00 00 x", "00 00 00 00 00 x"]

# part selects partly/fully out of range, negative signed base, x base (x-filled, w bits wide)
_PART_SELECT_READS = """module t(input clk);
  reg [7:0] d = 8'hb5; reg [3:0] xb = 4'bx010; reg [99:0] w = {4'ha, 96'h1234_5678_9abc_def0_1357_9bdf};
  reg [7:0] m [0:1]; reg [3:0] six = 4'd6, nine = 4'd9; reg [7:0] k = 8'd98;
  reg signed [4:0] neg = -5'sd2;
  initial m[0] = 8'h3c;
  always @(posedge clk) begin
    $display("%b %b %b %b", d[six +: 4], d[nine +: 2], d[nine -: 4], d[neg +: 4]);
    $display("%b %b %b %b", w[k +: 4], m[0][six +: 4], d[xb +: 3], w[k -: 70] + 72'd0);
    $display("%h %h %h", {d[six +: 4], 4'h0} + 16'd0, d[nine +: 2] + 8'd0, d[xb +: 3] + 8'd0);
  end
endmodule
"""
_PART_SELECT_READS_EXPECTED = [
    "xx10 xx xx10 01xx",
    "xx10 xx00 xxx 000100001001000110100010101100111100010011010101111001101111011110000000",
    "xxxx xx xx",
    "xx10 xx xx10 01xx",
    "xx10 xx00 xxx 000100001001000110100010101100111100010011010101111001101111011110000000",
    "xxxx xx xx",
]

# part-select writes partly out of range, negative base, x base, memory elements, NBAs
_PART_SELECT_WRITES = """module t(input clk);
  reg [7:0] a, b, c, e, f, g; reg [99:0] w, w2; reg [7:0] m [0:1]; reg [99:0] wm [0:1];
  reg [3:0] six = 4'd6, nine = 4'd9, xb = 4'bx010; reg signed [4:0] neg = -5'sd2; reg [7:0] k = 8'd98;
  always @(posedge clk) begin
    a = 0; a[six +: 4] = 4'hf;
    b = 0; b[nine -: 4] = 4'hf;
    c = 0; c[neg +: 4] = 4'hf;
    e = 0; e[xb +: 2] = 2'b11;
    w = 0; w[k +: 8] = 8'hff;
    m[0] = 0; m[0][six +: 4] = 4'hf; m[1] = 0; m[1][xb +: 2] = 2'b11;
    wm[0] = 0; wm[0][k +: 8] = 8'hff;
    f <= 0; f[six +: 4] <= 4'hf;
    g <= 0; g[xb] <= 1'b1;
    w2 <= 0; w2[k -: 4] <= 4'hf;
    $display("%h %h %h %h %h %h %h %h", a, b, c, e, w, m[0], m[1], wm[0]);
  end
  always @(negedge clk) $display("%h %h %h", f, g, w2);
endmodule
"""
_PART_SELECT_WRITES_EXPECTED = [
    "c0 c0 03 00 c000000000000000000000000 c0 00 c000000000000000000000000",
    "c0 00 7800000000000000000000000",
    "c0 c0 03 00 c000000000000000000000000 c0 00 c000000000000000000000000",
]

# the same on wide signals and wide memory elements (compiled crashed on an x base)
_PART_SELECT_WIDE = """module t(input clk);
  reg [99:0] w, w2, w3; reg [7:0] m [0:1]; reg [99:0] wm [0:1];
  reg signed [4:0] neg = -5'sd2; reg [3:0] xb = 4'bx010; reg [7:0] k = 8'd96;
  always @(posedge clk) begin
    w = 0; w[neg +: 8] = 8'hff;
    w2 = 0; w2[xb +: 8] = 8'hff;
    m[0] = 0; m[0][neg +: 4] = 4'hf;
    wm[0] = 0; wm[0][neg +: 8] = 8'hff; wm[1] = 0; wm[1][k +: 8] = 8'hff;
    w3 <= 0; w3[neg +: 8] <= 8'hff;
    $display("%h %h %h %h %h", w, w2, m[0], wm[0], wm[1]);
    $display("%h %h %h", w[neg +: 8], m[0][neg +: 4], wm[0][neg +: 8]);
  end
  always @(negedge clk) $display("%h", w3);
endmodule
"""
_PART_SELECT_WIDE_EXPECTED = [
    "000000000000000000000003f 0000000000000000000000000 03 000000000000000000000003f f000000000000000000000000",
    "fX X fX",
    "000000000000000000000003f",
    "000000000000000000000003f 0000000000000000000000000 03 000000000000000000000003f f000000000000000000000000",
    "fX X fX",
]

# ascending packed ranges (`reg [0:7] a`): bit/range/part selects, reads and writes, memory elements -- every engine mapped indices as for a descending range (a[0] read x)
_ASCENDING = """module t(input clk);
  reg [0:7] a = 8'b1100_1010;
  reg [7:15] b = 9'b1_1010_0101;
  reg [3:10] e;
  reg [0:7] m [0:1];
  reg [0:7] w1, w2, w3, w4, w5;
  reg [3:0] i1 = 4'd1, i9 = 4'd9;
  initial begin e = 8'h96; m[0] = 8'b0110_0011; end
  always @(posedge clk) begin
    $display("%b %b %b %b %b", a[0], a[7], a[i1], b[7], b[i9]);
    $display("%b %b %b %b", a[0:3], a[2:5], b[9:12], e[4:7]);
    $display("%b %b %b %b", a[2 +: 3], a[i1 +: 4], b[8 +: 4], a[5 -: 3]);
    $display("%b %b %b", m[0][1], m[0][0:3], m[0][i1 +: 2]);
    w1 = 0; w1[0] = 1'b1;
    w2 = 0; w2[0:2] = 3'b101;
    w3 = 0; w3[i1 +: 3] = 3'b111;
    w4 = 0; w4[6 -: 2] = 2'b11;
    w5 = 0; w5[i1] = 1'b1;
    m[1] = 0; m[1][2:3] = 2'b11;
    $display("%b %b %b %b %b %b", w1, w2, w3, w4, w5, m[1]);
  end
endmodule
"""
_ASCENDING_EXPECTED = [
    "1 0 1 1 0",
    "1100 0010 0100 0010",
    "001 1001 1010 010",
    "1 0110 11",
    "10000000 10100000 01110000 00000110 01000000 00110000",
    "1 0 1 1 0",
    "1100 0010 0100 0010",
    "001 1001 1010 010",
    "1 0110 11",
    "10000000 10100000 01110000 00000110 01000000 00110000",
]

# ascending ports across hierarchy, function/task ports, continuous assigns, $left/$right, NBAs, an x index
_ASCENDING_SCOPES = """module t(input clk);
  reg [0:7] a = 8'b1100_1010;
  reg [2:9] q;
  wire [0:3] w = a[2:5];
  wire [0:7] cw;
  assign cw[0:3] = 4'b1001; assign cw[4] = a[0]; assign cw[5:7] = a[5:7];
  wire [0:3] co;
  c u(.x(a[0:3]), .y(co));
  function automatic [0:3] f(input [0:7] v); f = v[1:4]; endfunction
  task automatic tk(input [0:3] v, output [0:7] o); begin o = 0; o[1:4] = v; end endtask
  reg [0:7] tr; reg [3:0] xi = 4'bx1x0;
  always @(posedge clk) begin
    q <= 0; q[2] <= 1'b1; q[5 +: 2] <= 2'b11;
    tk(4'b1011, tr);
    $display("%b %b %b %b %b", w, cw, co, f(a), tr);
    $display("%0d %0d %0d %0d %b", $left(a), $right(a), $high(q), $low(q), a[xi]);
  end
  always @(negedge clk) $display("%b", q);
endmodule
module c(input [0:3] x, output [0:3] y);
  assign y = {x[3], x[0:2]};
endmodule
"""
_ASCENDING_SCOPES_EXPECTED = [
    "0010 10011010 0110 1001 01011000",
    "0 7 9 2 x",
    "10011000",
    "0010 10011010 0110 1001 01011000",
    "0 7 9 2 x",
]

_CASES = {
    "ascending": (_ASCENDING, _ASCENDING_EXPECTED),
    "ascending_scopes": (_ASCENDING_SCOPES, _ASCENDING_SCOPES_EXPECTED),
    "writes_by_form": (_WRITES_BY_FORM, _WRITES_BY_FORM_EXPECTED),
    "out_of_range": (_OUT_OF_RANGE, _OUT_OF_RANGE_EXPECTED),
    "x_index": (_X_INDEX, _X_INDEX_EXPECTED),
    "part_select_reads": (_PART_SELECT_READS, _PART_SELECT_READS_EXPECTED),
    "part_select_writes": (_PART_SELECT_WRITES, _PART_SELECT_WRITES_EXPECTED),
    "part_select_wide": (_PART_SELECT_WIDE, _PART_SELECT_WIDE_EXPECTED),
}


@pytest.mark.parametrize("case", sorted(_CASES))
@pytest.mark.parametrize("engine", ENGINES)
def test_select_edges(engine, case):
    src, expected = _CASES[case]
    design = tree_to_design(verilog_parser(start="source_text").build_tree(src), source_file=f"edge_{case}.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=12)
    assert list(sim.display_output) == expected
