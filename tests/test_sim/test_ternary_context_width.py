"""Compiled-engine expression fixes. A ternary's branches are context-determined (IEEE 1364-2005 5.4.1): they
are evaluated at the wider of the destination and both branches, and only
then cut to the destination. Compiled evaluated them at the destination's
width, so `c ? ~r / 6'd5 : 121'd0` into 16 bits divided a 16-bit `~r`
(found replaying old fuzz mismatches). Expected values from Icarus.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


@pytest.mark.parametrize("engine", ENGINES)
def test_ternary_branches_use_context_width(engine):
    source = """module t;
  reg [14:0] r; reg c, x;
  wire [15:0] b = c ? ~r / 6'd5 : 121'd0;
  wire [15:0] d = c ? ~r >> 100 : 121'd0;
  wire [15:0] h = c ? ~r / 6'd5 : 40'd0;
  wire [15:0] k = c ? ~r >> 30 : 40'd0;
  wire [15:0] m = x ? ~r / 6'd5 : 121'd0;
  wire [99:0] n = c ? ~r / 6'd5 : 121'd0;
  reg [15:0] f, g;
  always @* begin f = c ? ~r / 6'd5 : 121'd0; g = c ? ~r >> 30 : 40'd0; end
  initial begin r = 2454; c = 1; x = 1'bx; #1 $display("%h %h %h %h %h %h %h %h", b, d, h, k, m, n, f, g); end
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=10)
    assert sim.display_output == ["647b ffff 3148 03ff XXXX 666666666666666666666647b 647b 03ff"]


@pytest.mark.parametrize("engine", ENGINES)
def test_ternary_x_merge_of_power_and_shift_branches(engine):
    """With an x condition, a `**`/shift branch is x across the whole context
    width; compiled took its mask at the branch's own (1-bit) width."""
    source = """module t;
  reg [6:0] o7; reg [3:0] s; reg c;
  wire [15:0] o6 = o7[2:0] ? o7[0] ** 2'd3 : ^o7;
  wire [15:0] a = c ? o7[0] << s : 1'b0;
  wire [15:0] d = c ? o7[1:0] ** 2'd2 : 2'd0;
  initial begin s = 4'bxx00; c = 1'bx; #1 $display("%b %b %b", o6, a, d); end
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=10)
    assert sim.display_output == ["xxxxxxxxxxxxxxxx xxxxxxxxxxxxxxxx xxxxxxxxxxxxxxxx"]


@pytest.mark.parametrize("engine", ENGINES)
def test_literal_product_past_32_bits(engine):
    """`7'd73 * 2147483647` didn't compile on compiled: Cython folded the two
    literals as Python ints into a Python object (in a `nogil` block)."""
    source = """module t(input [4:0] i5, output [31:0] a, output [63:0] b, output [63:0] c);
  assign a = ~(7'd73 * 2147483647 << ~&i5);
  assign b = 65536 * 65536 + 3;
  assign c = 4000000000 - 1 * 3;
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.drive("i5", 0b11111)
    sim.settle()
    assert [int(sim.read(n).val) for n in ("a", "b", "c")] == [0x80000048, 0x1_0000_0003, 0xEE6B27FD]


@pytest.mark.parametrize("engine", ENGINES)
def test_wide_shift_left_drops_bits_past_its_width(engine):
    """vm-fast's wide `<<` masked only the top word of the result's width,
    so `{34'd0, k << 64}` (k 120 bits) carried k's shifted-out high bits
    into the concatenation (fuzz seed 26050)."""
    source = """module t;
  reg [51:0] i7;
  wire [153:0] c1 = {{2{i7[23:7]}}, 120'd514807271207555964258565164011671173 << 64};
  wire [153:0] c5 = {34'd0, 120'd514807271207555964258565164011671173 <<< 64};
  initial begin i7 = 0; #1 $display("%h %h", c1, c5); end
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=10)
    assert sim.display_output == ["000000000c4904410a4ce850000000000000000 000000000c4904410a4ce850000000000000000"]
