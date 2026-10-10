"""Expression width/signedness semantics across engines, against Icarus
(found 2026-10-09 from a user report).

Reported: compiled computed ``s3 <= 21'((p36 + 36'sd16384) >>> 15)`` with
the add and shift at the 21-bit target width (IEEE: max(21, 36) = 36), so
the high product bits were lost. Found while fixing it, all engines:
``N'(e)`` casts were dropped by the parser; ``<<`` by a literal was
``left + amount`` bits wide; bitwise operators computed at their operands'
natural width instead of the context's, and ignored the signedness
propagated from the context (IEEE 5.4.1/5.5.2); vm compiled a signed
``>>>`` operand without a width; vm didn't extend a user-function result
to its context and compiled didn't sign-extend a signed one.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

# reported: 21'((p36 + c) >>> 15) computed the add/shift at 21 bits on compiled
_CAST = """module t(input clk);
  reg signed [35:0] s2_product;
  reg signed [20:0] s3_rounded;
  reg [20:0] u3, a3, b3;
  reg [7:0] k = 0;
  always @(posedge clk) begin
    k <= k + 1;
    case (k)
      0: s2_product <= 36'sd123456789;
      1: s2_product <= -36'sd987654321;
      2: s2_product <= 36'sh7_ffff_ffff;
      3: s2_product <= 36'sd34359738367;
    endcase
    s3_rounded <= 21'( (s2_product + 36'sd16384) >>> 15 );
    u3 <= 21'(s2_product >> 15);
    a3 <= 21'(s2_product + 36'sd1);
    b3 <= 21'((s2_product * 2) >>> 16);
  end
  always @(negedge clk) if (k >= 2 && k <= 6) $display("%0d %0d %0d %0d", s3_rounded, u3, a3, b3);
endmodule
"""
_CAST_EXPECTED = "3768 3767 1821974 3767"

# same without the cast
_NOCAST = """module t(input clk);
  reg signed [35:0] s2_product;
  reg signed [20:0] s3_rounded;
  reg [20:0] u3, a3, b3;
  reg [7:0] k = 0;
  always @(posedge clk) begin
    k <= k + 1;
    case (k)
      0: s2_product <= 36'sd123456789;
      1: s2_product <= -36'sd987654321;
      2: s2_product <= 36'sh7_ffff_ffff;
      3: s2_product <= 36'sd34359738367;
    endcase
    s3_rounded <= (s2_product + 36'sd16384) >>> 15;
    u3 <= s2_product >> 15;
    a3 <= s2_product + 36'sd1;
    b3 <= (s2_product * 2) >>> 16;
  end
  always @(negedge clk) if (k >= 2 && k <= 6) $display("%0d %0d %0d %0d", s3_rounded, u3, a3, b3);
endmodule
"""
_NOCAST_EXPECTED = "3768 3767 1821974 3767"

# N'(e) size casts (were dropped by the parser)
_WC = """module t(input clk);
  reg [15:0] a = 16'hffff, b = 16'h0003;
  reg signed [7:0] sn = -8'sd3;
  reg [35:0] p = 36'h8_0000_0010;
  reg [31:0] y1, y2, y4, y5, y7;
  reg [7:0] y3;
  reg signed [35:0] y6;
  reg [99:0] w = {4'hc, 96'h5};
  wire [31:0] cw = 16'(a + b);
  wire [31:0] cw2 = 20'(a + b);
  always @(posedge clk) begin
    y1 = 16'(a + b);
    y2 = 20'(a + b) >> 1;
    y3 = 4'(8'hab);
    y4 = 32'(sn);
    y5 = 4'(a) + 4'(b);
    y6 = 21'(sn);
    y7 = 8'(w >> 92);
    $display("%h %h %h %h %h %h %h %h %h %0d %0d", y1, y2, y3, y4, y5, y6, y7, cw, cw2, 4'(sn), 12'(sn));
  end
endmodule
"""
_WC_EXPECTED = "00000002 00008001 0b fffffffd 00000012 ffffffffd 000000c0 00000002 00010002 -3 -3"

# user-function results in wider contexts (vm: not extended; compiled: signed not sign-extended)
_UF = """module t(input clk);
  function automatic [3:0] f4(input [15:0] v); f4 = v; endfunction
  function automatic signed [3:0] g4(input signed [7:0] v); g4 = v; endfunction
  reg [15:0] a = 16'hffff, b = 16'h0003; reg signed [7:0] sn = -8'sd3;
  reg [31:0] y5, y6, y8; reg signed [31:0] y7;
  wire [31:0] cw = f4(a) + f4(b);
  always @(posedge clk) begin
    y5 = f4(a) + f4(b);
    y6 = {f4(a), f4(b)};
    y7 = g4(sn);
    y8 = f4(a) << 4;
    $display("%h %h %h %h %h", y5, y6, y7, y8, cw);
  end
endmodule
"""
_UF_EXPECTED = "00000012 000000f3 fffffffd 000000f0 00000012"

# << by a literal counted left+amount bits
_SH = """module t(input clk);
  reg [35:0] a36 = 36'h9_8765_4321; reg [7:0] a8 = 8'hc3;
  reg [20:0] y1; reg [35:0] y2; reg [63:0] y3; reg [3:0] y4; reg [7:0] y5;
  always @(posedge clk) begin
    y1 = (a36 << 2) >> 18;
    y2 = (a36 << 2) >> 18;
    y3 = (a36 << 2) >> 18;
    y4 = (a8 << 2) >> 6;
    y5 = (a8 << 2) >> 6;
    $display("%h %h %h %h %h", y1, y2, y3, y4, y5);
  end
endmodule
"""
_SH_EXPECTED = "018765 000018765 0000000000098765 0 00"

# << by a literal inside concatenation / bitwise
_MUL = """module t(input clk);
  reg [7:0] a8 = 8'hc3, b8 = 8'h45; reg [30:0] hi = 31'h7fff_0001; reg [4:0] n = 5'd3;
  reg [7:0] y1, y2; reg [63:0] y3, y4; reg [15:0] y5; reg [7:0] y6;
  always @(posedge clk) begin
    y1 = (a8 * b8) >> 8;
    y2 = (a8 << n) >> 6;
    y3 = hi << 32;
    y4 = {32'd0, hi << 32};
    y5 = (a8 * b8) >> 4;
    y6 = ((a8 << 2) + 8'd0) >> 6;
    $display("%h %h %h %h %h %h", y1, y2, y3, y4, y5, y6);
  end
endmodule
"""
_MUL_EXPECTED = "00 00 7fff000100000000 0000000000000000 0348 00"

# context width above the target for arithmetic/shift
_EDGE = """module t(input clk);
  reg [35:0] a36 = 36'h9_8765_4321; reg [7:0] b8 = 8'hc5; reg [7:0] a8 = 8'hff;
  reg [15:0] a16 = 16'hfedc; reg [11:0] a12 = 12'hfff; reg [3:0] b4 = 4'h7;
  reg signed [35:0] s36 = -36'sd123456789; reg signed [7:0] s8 = -8'sd7; reg c = 1;
  reg [20:0] y1, y2, y6, y7, y8; reg [7:0] y3, y4, y5; reg signed [20:0] y9, y10;
  always @(posedge clk) begin
    y1 = (a36 * b8) >>> 3;
    y2 = (a36 * b8) >> 20;
    y3 = (a8 + 1) >> 1;
    y4 = a16 / b8;
    y5 = a12 % b4;
    y6 = c ? (a36 + 36'd5) >> 16 : 21'd0;
    y7 = (s36 * s8) >>> 12;
    y8 = (a36 << 2) >> 18;
    y9 = (s36 - 36'sd1) / 36'sd1000;
    y10 = -(s36) >>> 14;
    $display("%h %h %h %h %h %h %h %h %h %h", y1, y2, y3, y4, y5, y6, y7, y8, y9, y10);
  end
endmodule
"""
_EDGE_EXPECTED = "1d950c 00530e 80 4b 00 098765 033829 018765 1e1dc0 001d6f"

# bitwise operators are context-determined
_BW = """module t(input clk);
  reg a1 = 1; reg [31:0] b32 = 32'h1234_5678; reg [7:0] a8 = 8'hc3; reg signed [3:0] s4 = -4'sd2; reg [7:0] u8 = 8'h0f;
  reg signed [7:0] t8 = -8'sd16;
  reg [63:0] y1, y2, y3; reg [15:0] y4, y5, y6, y7; reg [7:0] y8; reg [31:0] y9;
  always @(posedge clk) begin
    y1 = (a1 << 32) | b32;
    y2 = {a1 << 32} | b32;
    y3 = ((a1 << 40) & 64'hffff_ffff_ffff_ffff) ^ (b32 << 8);
    y4 = (a8 << 4) | 16'h0001;
    y5 = s4 & u8;
    y6 = s4 | t8;
    y7 = ~(a8 << 4) ^ 16'h0;
    y8 = ((a8 << 2) | 8'h00) >> 6;
    y9 = (a8 << 28) >> 24;
    $display("%h %h %h %h %h %h %h %h %h", y1, y2, y3, y4, y5, y6, y7, y8, y9);
  end
endmodule
"""
_BW_EXPECTED = "0000000112345678 0000000012345678 0000011234567800 0c31 000e fffe f3cf 00 00000030"

# carry of a nested + and unsigned propagation through bitwise
_BP = """module t(input clk);
  reg [7:0] a8 = 8'h80, b8 = 8'h81; reg signed [3:0] sa = -4'sd2; reg signed [7:0] sb = 8'sd1; reg [15:0] u16 = 16'hffff;
  reg [15:0] y1, y2, y3, y4; reg [11:0] y5;
  always @(posedge clk) begin
    y1 = (a8 + b8) | 16'h0;
    y2 = (a8 + b8) ^ (a8 - b8);
    y3 = (sa ^ sb) & u16;
    y4 = ((sa ^ sb) & u16) | (a8 * b8);
    y5 = ~(a8 + b8) & 12'hfff;
    $display("%h %h %h %h %h", y1, y2, y3, y4, y5);
  end
endmodule
"""
_BP_EXPECTED = "0101 fefe 000f 408f efe"

# signed/unsigned mixes in bitwise operators
_BS = """module t(input clk);
  reg signed [3:0] s4 = -4'sd2; reg [7:0] u8 = 8'h0f; reg signed [7:0] t8 = -8'sd16; reg [3:0] u4 = 4'h9;
  reg [15:0] y1, y2, y3, y4, y5; reg [63:0] y6;
  always @(posedge clk) begin
    y1 = s4 | u8;
    y2 = s4 | t8;
    y3 = ~s4 & u8;
    y4 = (s4 ^ u4) | 16'h0;
    y5 = s4 ^ 8'sd0;
    y6 = (u8 << 60) | (s4 << 1);
    $display("%h %h %h %h %h %h", y1, y2, y3, y4, y5, y6);
  end
endmodule
"""
_BS_EXPECTED = "000f fffe 0001 0007 fffe f00000000000001c"

# unsigned propagation into a nested -
_PM = """module t(input clk);
  reg [63:0] a3 = 64'h0000_2000_0000_0000; reg signed [31:0] a4 = 32'sh0080_0000; reg signed a0 = 1'b1;
  reg [7:0] y1, y2, y3; reg [15:0] y4, y5;
  reg signed [3:0] sa = -4'sd2; reg signed [7:0] sb = 8'sd1; reg [15:0] u16 = 16'hffff;
  always @(posedge clk) begin
    y1 = (|a3[45]) % (($signed(a4[23]) - a0) | 1);
    y2 = (($signed(a4[23]) - a0) | 1);
    y3 = 8'd7 % (($signed(a4[23]) - a0) | 1);
    y4 = (sa - sb) | u16[3:0];
    y5 = (sa - sb) & u16;
    $display("%h %h %h %h %h", y1, y2, y3, y4, y5);
  end
endmodule
"""
_PM_EXPECTED = "00 01 00 000f 000d"

# unsigned propagation through % and +
_PM2 = """module t(input clk);
  reg [63:0] a3 = 64'h0000_2000_0000_0000; reg signed [31:0] a4 = 32'sh0; reg signed a0 = 1'b1;
  reg [31:0] y1, y2, y3, y4;
  always @(posedge clk) begin
    y1 = (|a3[45]) % (($signed(a4[23]) - a0) | 1);
    y2 = (|a3[45]) + (($signed(a4[23]) - a0) | 1);
    y3 = 32'd5 + (($signed(a4[23]) - a0) | 1);
    y4 = (($signed(a4[23]) - a0) | 1);
    $display("%h %h %h %h", y1, y2, y3, y4);
  end
endmodule
"""
_PM2_EXPECTED = "00000001 00000000 00000004 00000001"

# the same rules wider than 64 bits
_BWIDE = """module t(input clk);
  reg [7:0] a8 = 8'h80, b8 = 8'h81; reg signed [3:0] sa = -4'sd2; reg signed [7:0] sb = 8'sd1;
  reg [99:0] u100 = {100{1'b1}}; reg a1 = 1; reg [63:0] b64 = 64'h1234_5678_9abc_def0; reg [69:0] w70 = {6'h2a, 64'h1};
  reg [99:0] y1, y2, y3, y4, y5; reg [69:0] y6; reg [20:0] y7;
  always @(posedge clk) begin
    y1 = (a8 + b8) ^ (a8 - b8);
    y2 = (sa ^ sb) & u100;
    y3 = (a1 << 80) | b64;
    y4 = (sa - sb) | u100[3:0];
    y5 = ~(a8 + b8) & u100;
    y6 = (w70 << 3) >> 60;
    y7 = 21'((w70 + 70'd5) >> 50);
    $display("%h %h %h %h %h %h %h", y1, y2, y3, y4, y5, y6, y7);
  end
endmodule
"""
_BWIDE_EXPECTED = "ffffffffffffffffffffffefe 000000000000000000000000f 000010000123456789abcdef0 000000000000000000000000f ffffffffffffffffffffffefe 000000000000000100 0a8000"


_CASES = {
    "cast": (_CAST, _CAST_EXPECTED),
    "nocast": (_NOCAST, _NOCAST_EXPECTED),
    "wc": (_WC, _WC_EXPECTED),
    "uf": (_UF, _UF_EXPECTED),
    "sh": (_SH, _SH_EXPECTED),
    "mul": (_MUL, _MUL_EXPECTED),
    "edge": (_EDGE, _EDGE_EXPECTED),
    "bw": (_BW, _BW_EXPECTED),
    "bp": (_BP, _BP_EXPECTED),
    "bs": (_BS, _BS_EXPECTED),
    "pm": (_PM, _PM_EXPECTED),
    "pm2": (_PM2, _PM2_EXPECTED),
    "bwide": (_BWIDE, _BWIDE_EXPECTED),
}


@pytest.mark.parametrize("case", sorted(_CASES))
@pytest.mark.parametrize("engine", ENGINES)
def test_expression_semantics(engine, case):
    src, expected = _CASES[case]
    design = tree_to_design(verilog_parser(start="source_text").build_tree(src), source_file=f"{case}.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=40)  # cast/nocast start printing on the 3rd edge
    assert sim.display_output[0] == expected
