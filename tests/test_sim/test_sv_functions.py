"""SystemVerilog bit-vector / range query functions, case equality with x
operands, and wide declaration initializers across engines (found
2026-10-08).

Before: no engine implemented ``$countones``/``$onehot``/``$onehot0``/
``$isunknown``/``$size``/``$high``/``$low``/``$left``/``$right`` (reference
returned x, compiled silently 0); compiled's narrow ``===``/``!==`` gave x
for x operands; and vm/vm-fast/compiled dropped a declaration initializer
that wasn't a literal or simple arithmetic (``reg [99:0] w = {...}``),
leaving the signal x. Expected outputs are Icarus Verilog's.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _sim(src: str, engine: str) -> Simulator:
    design = tree_to_design(verilog_parser(start="source_text").build_tree(src), source_file="sv.v")
    return Simulator(design.modules[0], engine=engine, design=design)


_INITIAL = """module t;
  reg [7:0] a = 8'b1011_0010;
  reg [7:0] o = 8'b0001_0000;
  reg [7:0] z = 8'b0;
  reg [7:0] xx = 8'b10x0_0001;
  reg [99:0] w = {4'b1010, 96'h1};
  reg [15:4] r;
  reg [7:0] m [2:9];
  initial begin
    #1;
    $display("%0d %0d %0d %0d", $countones(a), $countones(z), $countones(w), $countones(xx));
    $display("%0d %0d %0d %0d", $onehot(a), $onehot(o), $onehot(z), $onehot(xx));
    $display("%0d %0d %0d", $onehot0(a), $onehot0(o), $onehot0(z));
    $display("%0d %0d %0d", $isunknown(a), $isunknown(xx), $isunknown(8'bz));
    $display("%0d %0d %0d %0d", $size(a), $size(w), $size(r), $size(m));
    $display("%0d %0d %0d %0d", $high(r), $low(r), $high(m), $low(m));
    $display("%0d %0d", $left(r), $right(r));
  end
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_sv_functions_initial(engine):
    sim = _sim(_INITIAL, engine)
    sim.run(max_time=10)
    assert list(sim.display_output) == [
        "4 0 3 2",
        "0 1 0 0",
        "0 1 1",
        "0 1 1",
        "8 100 12 8",
        "15 4 9 2",
        "15 4",
    ]


# Continuous assigns + clocked logic, so compiled runs them natively rather
# than through the reference-executor fallback for timed initial blocks.
_NATIVE = """module t(input clk);
  reg [7:0] a;
  reg [99:0] w;
  reg [99:0] wx;
  reg [7:0] m [2:9];
  wire [31:0] c_a = $countones(a);
  wire [31:0] c_w = $countones(w);
  wire [31:0] c_m = $countones(m[3]);
  wire [31:0] c_e = $countones(a ^ 8'h0f);
  wire oh = $onehot(a), oh0 = $onehot0(a), unk = $isunknown(a), unkw = $isunknown(wx);
  wire ceq = (a === 8'b1x00_0001), cne = (a !== 8'b1x00_0001);
  wire weq = (wx === {4'b1x10, 96'h1}), wne = (wx !== {4'b1x10, 96'h1});
  wire [31:0] sz = $size(m) + $high(w) + $left(m);
  reg [3:0] step = 0;
  always @(posedge clk) begin
    step <= step + 1;
    case (step)
      0: begin a <= 8'b1011_0010; w <= {4'b1010, 96'h1}; wx <= {4'b1010, 96'h1}; m[3] <= 8'hff; end
      1: begin a <= 8'b0001_0000; wx <= {4'b1x10, 96'h1}; m[3] <= 8'h01; end
      2: begin a <= 8'b1x00_0001; w <= 100'h0; wx <= {4'b1x10, 96'h3}; end
      3: begin a <= 8'b0; end
    endcase
  end
  always @(negedge clk) if (step >= 1 && step <= 4)
    $display("%0d %0d %0d %0d %b%b%b%b %b%b %b%b %0d", c_a, c_w, c_m, c_e, oh, oh0, unk, unkw, ceq, cne, weq, wne, sz);
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_sv_functions_native(engine):
    sim = _sim(_NATIVE, engine)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=60)
    assert list(sim.display_output) == [
        "4 3 8 6 0000 01 01 109",
        "1 3 1 5 1101 01 10 109",
        "2 0 1 4 0011 10 01 109",
        "0 0 1 4 0101 01 01 109",
    ]


_WIDE_INIT = """module t;
  localparam P = 3;
  reg [99:0] w = {4'b1010, 96'h1};
  reg [99:0] wp = {P{32'h1}} << 4;
  reg [7:0] n = {4'ha, 4'h5};
  reg [7:0] nx = {4'hx, 4'h5};
  initial #1 $display("%h %h %h %h", w, wp, n, nx);
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_concat_declaration_initializers(engine):
    sim = _sim(_WIDE_INIT, engine)
    sim.run(max_time=10)
    assert list(sim.display_output) == [
        "a000000000000000000000001 0000000100000001000000010 a5 x5",
    ]
