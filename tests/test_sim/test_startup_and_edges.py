"""Start-up activation and edge detection (2026-10-16; expected values from
Icarus).

* An `always_comb`/`always_latch` block runs once at time 0; an `always @*`
  waits for its first change (it used to run at start-up on every engine).
  A declaration initializer or a continuous assign's value before time 0
  isn't a change; a drive or an initial block's write is.
* `always_latch` never ran on any engine (no inferred sensitivity), and was
  emitted as a bare `always`.
* An initial block's t=0 change is an edge (`initial w = 0;` is a negedge
  from x): vm, vm-fast and compiled missed it.
* An edge on an expression (`posedge w[0]`, `negedge (a & b)`) was ignored
  on every engine, and `negedge (a & b)` parsed as a call of a function
  named `negedge`. An edge on a signal wider than 64 bits (on its bit 0)
  raised on compiled and never fired on vm-fast.
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _design(source: str):
    return tree_to_design(verilog_parser(start="source_text").build_tree(source))


def _display(source: str, engine: str, max_time: int = 20) -> list[str]:
    design = _design(source)
    sim = Simulator(design.modules[-1], engine=engine, design=design)
    sim.run(max_time=max_time)
    return sim.display_output


@pytest.mark.parametrize("engine", ENGINES)
def test_always_latch_and_startup(engine):
    source = """module t;
  reg en, d, l, c; reg [3:0] k;
  always_latch if (en) l = d;
  always_comb c = 1'b1;
  always @* k = 4'd5;
  initial begin en = 0; d = 1; #1 en = 1; #1 $display("%b %b %b %h", l, c, en, k); d = 0; #1 $display("%b", l); en = 0; d = 1; #1 $display("%b", l); end
endmodule
"""
    assert _display(source, engine) == ["1 1 1 x", "0", "0"]


@pytest.mark.parametrize("engine", ENGINES)
def test_what_counts_as_a_t0_change(engine):
    source = """module t;
  reg a = 1; wire b = a; reg [3:0] y1, y2, y4, y5; reg e;
  always @* y1 = {3'b0, a};
  always @* y2 = {3'b0, b};
  always_comb y4 = {3'b0, a};
  always @* y5 = {3'b0, e};
  initial begin e = 1; #1 $display("%h %h %h %h", y1, y2, y4, y5); end
endmodule
"""
    assert _display(source, engine) == ["x x 1 1"]


@pytest.mark.parametrize("engine", ENGINES)
def test_initial_block_t0_edge(engine):
    source = """module t;
  reg w; reg [7:0] n;
  always @(negedge w) begin n = n + 1; $display("neg at %0t w=%b", $time, w); end
  initial begin
    n = 0; w = 0;
    #1 w = 1; #1 w = 0; #1 w = 1; #1 w = 1'bx; #1 w = 0; #1 $display("n=%0d", n);
  end
endmodule
"""
    assert _display(source, engine) == ["neg at 0 w=0", "neg at 2 w=0", "neg at 4 w=x", "neg at 5 w=0", "n=4"]


@pytest.mark.parametrize("engine", ENGINES)
def test_edges_on_expressions_and_wide_signals(engine):
    source = """module t;
  reg [99:0] w; reg [7:0] cnt_p, cnt_n, cnt_d, cnt_e, cnt_x;
  wire [99:0] wd = {w[98:0], 1'b0} | (w >> 1);
  reg [69:0] e; reg a, b;
  always @(posedge w) cnt_p <= cnt_p + 1;
  always @(negedge w) cnt_n <= cnt_n + 1;
  always @(posedge wd) cnt_d <= cnt_d + 1;
  always @(posedge e) cnt_e <= cnt_e + 1;
  always @(posedge w[0]) e <= {e[68:0], ~e[0]};
  always @(negedge (a & b)) cnt_x <= cnt_x + 1;
  initial begin
    cnt_p = 0; cnt_n = 0; cnt_d = 0; cnt_e = 0; cnt_x = 0; e = 0; w = 0; a = 1; b = 1;
    #1 w = 100'h1; b = 0;
    #1 w = {1'b1, 99'h0}; b = 1;
    #1 w = {1'b0, 98'h0, 1'b0}; a = 0;
    #1 w = 100'h3;
    #1 w = 100'bx;
    #1 w = 100'h2;
    #1 w = 100'h5;
    #1 $display("%0d %0d %0d %0d %0d %h", cnt_p, cnt_n, cnt_d, cnt_e, cnt_x, e);
  end
endmodule
"""
    assert _display(source, engine) == ["3 4 2 2 2 000000000000000005"]


def test_edge_expression_parse_and_keyword_round_trip():
    source = """module t(input a, b, clk); reg x, y, q, l, m;
always_comb x = a; always @* y = b; always_ff @(posedge clk) q <= a; always_latch if (a) l = b;
always @(negedge (a & b)) m <= a;
endmodule
"""
    module = _design(source).modules[0]
    edge = module.always_blocks[-1].sensitivity_list[0]
    assert edge.edge == "negedge" and type(edge.signal).__name__ == "BinaryOp"
    out = emit_module(module)
    for text in ("always_comb x = a;", "always @(*) y = b;", "always_ff @(posedge clk)", "always_latch if (a)"):
        assert text in out, (text, out)
    assert emit_module(_design(out).modules[0]) == out


_PREFIX_DESIGN = """module c(input [64:0] i3, output logic signed [110:0] o12, output reg [31:0] o13);
  reg [1:0] aa = 2'b10;
  wire signed [108:0] w6 = aa;
  wire signed [6:0] w7;
  logic signed [127:0] w8;
  logic [14:0] r9, r10;
  assign w7 = -{o13, o13[9:5]};
  assign w8 = {-r10[0], r9};
  always @(*) r9 = 15'h7123 | i3 & 1'b0;
  always @(*) o12 = 3'(~(w6[25:12] ** 2'd1));
endmodule
module t(input shortint i4, input signed [15:0] i5, output signed [92:0] o9);
  logic signed [110:0] w6;
  wire [31:0] w7;
  assign o9 = {w6, w7};
  c u8(.i3(65'({i5, i4})), .o12(w6), .o13(w7));
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_instance_prefix_is_not_a_signal(engine):
    # Reference put a flattened child's instance prefix (`u8` of `u8.w6`)
    # in its `always @*` sensitivity, and a continuous assign writing any
    # `u8.*` signal dirtied it: the `o12` block ran, though w6 never changes
    # after time 0 (fuzz 40030). Icarus wakes it too, via its own t=0 net
    # propagation (known_issues.md, "Start-up").
    design = _design(_PREFIX_DESIGN)
    sim = Simulator(design.modules[-1], engine=engine, design=design)
    if engine == "reference":
        assert all("u8" not in proc.sensitivity for proc in sim._sched._combo_procs)
    sim.drive("i4", 5)
    sim.drive("i5", 5)
    sim.settle()
    assert sim.read("o9").mask >> 32 == (1 << 61) - 1  # (o12 never ran)
