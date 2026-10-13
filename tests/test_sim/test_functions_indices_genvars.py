"""Function declarations, wide indices and genvar values (expected values from
Icarus, or from Verilator where Icarus deviates -- see known_issues.md).

* A function's scalar `input j;` (no range) parsed as a local variable of
  "type" `input`, so the argument was never bound: `m[j]` read x.
* A function with no return range (`function f;`) returned 32 bits on every
  engine instead of 1.
* An index wider than 32 bits is truncated to its low 32 (Icarus, Verilator);
  every engine read it as out of range.
* A genvar is a signed 32-bit value: `i2 & g` of a signed `i2` sign-extends,
  and in an unsigned context its 32-bit pattern zero-extends.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _display(source: str, engine: str) -> list[str]:
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=10)
    return sim.display_output


def test_scalar_function_inputs_are_ports():
    source = """module t;
  function g; input p, q; g = p & ~q; endfunction
  task tk; input x; output y; inout z; begin y = ~x; z = z ^ x; end endtask
endmodule
"""
    module = tree_to_design(verilog_parser(start="source_text").build_tree(source)).modules[0]
    assert [p.name for p in module.functions[0].ports] == ["p", "q"]
    assert [p.name for p in module.tasks[0].ports] == ["x", "y", "z"]


@pytest.mark.parametrize("engine", ENGINES)
def test_functions(engine):
    source = """module t;
  reg [7:0] m [0:1];
  reg a, b, c;
  function [7:0] f; input j; f = m[j]; endfunction
  function g; input p, q; g = p & ~q; endfunction
  function signed h; input p; h = p; endfunction
  function time tm; input p; tm = 64'h1_0000_0000; endfunction
  task tk; input x; output y; inout z; begin y = ~x; z = z ^ x; end endtask
  wire [7:0] w1 = g(1'b1, 1'b0) + 8'd1;
  wire [7:0] w2 = h(1'b1);
  initial begin
    m[0] = 8'h11; m[1] = 8'h5a; a = 1; c = 0;
    tk(a, b, c);
    #1 $display("%h %h %b %b %h %h %0d %b %b", f(1'b1), f(1'b0), g(1'b1, 1'b0), h(1'b1), w1, w2, tm(1'b0), b, c);
  end
endmodule
"""
    assert _display(source, engine) == ["5a 11 1 1 02 ff 4294967296 0 1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_wide_indices_use_low_32_bits(engine):
    source = """module t;
  reg [7:0] m [0:7];
  reg [7:0] n [3:5];
  reg [7:0] v;
  reg [4:0] h; reg b;
  reg [39:0] i40; reg [63:0] i64; reg signed [39:0] s40;
  integer k;
  initial begin
    for (k = 0; k < 8; k = k + 1) m[k] = k + 10;
    n[3] = 30; n[4] = 40; n[5] = 50; v = 8'b1010_0101;
    h = 5'd3; b = 1; i40 = 40'h100000001; i64 = 64'hffffffff00000002; s40 = -40'sd1;
    #1 $display("%h %h %h %b %b %h %h", m[i40], m[i64], m[s40], v[i40], v[i64], m[{h, 31'd0, b} + 4], n[{h, 31'd0, b} + 3]);
    m[{h, 31'd0, b} + 4] = 8'hee; m[i64] <= 8'hdd; n[{h, 31'd0, b} + 3] = 8'hcc;
    #1 $display("%h %h %h", m[5], m[2], n[4]);
  end
endmodule
"""
    assert _display(source, engine) == ["0b 0c xx 0 1 0f 28", "ee dd cc"]


@pytest.mark.parametrize("engine", ENGINES)
def test_genvar_values(engine):
    source = """module t;
  reg signed [3:0] i2;
  reg [3:0] u;
  reg [5:0] i6;
  wire [7:0] nmem [-1:1];
  wire [7:0] umem [-1:1];
  wire [69:0] n [0:3];
  for (genvar g = -1; g <= 1; g = g + 1) begin : gnm
    assign nmem[g] = i2 & g;
    assign umem[g] = u + g;
  end
  for (genvar g = -2; g <= -1; g = g + 1) begin : gg
    assign n[g + 3] = (4294967295 || i6) ^ g;
  end
  initial begin
    i2 = 4'b1011; u = 4'd3; i6 = 0;
    #1 $display("%h %h %h | %h %h %h | %h %h", nmem[-1], nmem[0], nmem[1], umem[-1], umem[0], umem[1], n[1], n[2]);
  end
endmodule
"""
    # (n[1]/n[2]: Verilator's values; Icarus sign-extends the genvar there.)
    assert _display(source, engine) == ["fb 00 01 | 02 03 04 | 0000000000ffffffff 0000000000fffffffe"]
