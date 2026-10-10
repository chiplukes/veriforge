"""Functions, tasks and ``integer`` across engines, against Icarus (found
2026-10-09 while testing $display of function/task locals).

Before: ``integer``/``int``/``byte``/... were unsigned on every engine
(the parser never marked them signed: ``integer i = -5; i < 0`` was false);
the parser dropped task-local declarations; function/task ports and locals
read as unsigned inside the body; reference ran a function body on a copy
of the signals (module-variable writes were lost) and bound task inputs
without the port width; vm made a function-local ``integer`` 1 bit wide;
compiled called a user function up to three times per use (value + mask)
and its clocked processes read a function's module-signal writes from the
pre-edge snapshot.
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

# module-level integer/int signedness
_INT = """module t(input clk);
  integer i = -5; int j = -3; reg [7:0] u = 8'd10; reg signed [7:0] s = -8'sd4;
  reg [31:0] y1, y2, y3, y4; reg c1, c2;
  always @(posedge clk) begin
    c1 = i < 0; c2 = j < u;
    y1 = i >>> 1; y2 = (i + s) >>> 1; y3 = i / 2; y4 = j * s;
    $display("%b %b %h %h %h %h", c1, c2, y1, y2, y3, y4);
  end
endmodule
"""
_INT_EXPECTED = ["1 0 fffffffd fffffffb fffffffe 0000000c", "1 0 fffffffd fffffffb fffffffe 0000000c"]

# signed function ports/locals
_FL = """module t(input clk);
  function automatic signed [15:0] g(input signed [7:0] a);
    integer n; reg signed [7:0] s;
    begin n = a; s = a; g = (n * 3) + (s >>> 1); end
  endfunction
  reg signed [15:0] y;
  always @(posedge clk) begin y = g(-8'sd7); $display("%d", y); end
endmodule
"""
_FL_EXPECTED = ["   -25", "   -25"]

# task ports/locals, impure function side effects, single evaluation
_TK = """module t(input clk);
  reg signed [15:0] acc;
  integer calls = 0;
  task automatic add(input signed [7:0] a, input signed [3:0] b);
    integer t2; reg signed [7:0] s;
    begin t2 = a; s = b; acc = t2 * 2 + (s >>> 1); end
  endtask
  function automatic [7:0] g(input [7:0] v);
    begin calls = calls + 1; $display("g(%0d)", v); g = v + 1; end
  endfunction
  reg [7:0] y;
  always @(posedge clk) begin
    add(-8'sd9, -4'sd3);
    y = g(8'd4) + 8'd1;
    $display("%d %0d %0d", acc, y, calls);
  end
endmodule
"""
_TK_EXPECTED = ["g(4)", "   -20 6 1"]

# nested/conditional user-function calls
_FN2 = """module t(input clk);
  function automatic [7:0] inc(input [7:0] v); inc = v + 1; endfunction
  function automatic [7:0] twice(input [7:0] v); twice = inc(inc(v)); endfunction
  function automatic signed [7:0] neg(input signed [7:0] v); neg = -v; endfunction
  reg [7:0] a = 8'd5, y1, y2, y3; reg signed [15:0] y4; reg [3:0] k = 0;
  wire [7:0] w = twice(a) + inc(a);
  always @(posedge clk) begin
    k <= k + 1;
    if (inc(a) > 8'd5) y1 = twice(a); else y1 = 0;
    case (k)
      0: y2 = inc(inc(a));
      default: y2 = neg(a);
    endcase
    y3 = (a > 3) ? inc(a) : twice(a);
    y4 = neg(a) * 3;
    $display("%0d %0d %0d %0d %0d", y1, y2, y3, y4, w);
  end
endmodule
"""
_FN2_EXPECTED = ["7 7 6 -15 13", "7 251 6 -15 13"]

_CASES = {
    "int": (_INT, _INT_EXPECTED),
    "fl": (_FL, _FL_EXPECTED),
    "tk": (_TK, _TK_EXPECTED),
    "fn2": (_FN2, _FN2_EXPECTED),
}


@pytest.mark.parametrize("case", sorted(_CASES))
@pytest.mark.parametrize("engine", ENGINES)
def test_functions_tasks_integers(engine, case):
    src, expected = _CASES[case]
    design = tree_to_design(verilog_parser(start="source_text").build_tree(src), source_file=f"{case}.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=18)
    assert list(sim.display_output)[: len(expected)] == expected


def test_task_and_function_locals_round_trip():
    """Task locals are parsed (they were dropped) and both functions' and
    tasks' locals are emitted (the emitter left them out)."""
    src = """module t;
  function automatic [7:0] f(input [7:0] a); reg [7:0] loc; begin loc = a; f = loc; end endfunction
  task automatic tk(input signed [3:0] b); reg signed [3:0] l2; integer n; begin l2 = b; n = 1; end endtask
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(src))
    module = design.modules[0]
    assert [(v.name, v.signed) for v in module.tasks[0].locals] == [("l2", True), ("n", True)]
    text = emit_module(module)
    assert "reg [7:0] loc;" in text
    assert "reg signed [3:0] l2;" in text
    again = tree_to_design(verilog_parser(start="source_text").build_tree(text)).modules[0]
    assert [v.name for v in again.tasks[0].locals] == ["l2", "n"]
    assert [v.name for v in again.functions[0].locals] == ["loc"]
