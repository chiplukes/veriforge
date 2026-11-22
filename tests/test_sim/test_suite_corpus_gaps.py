"""Gaps found by running sv-tests and ivtest as corpora (fixed 2026-11-22), on
every engine.

Parser: end labels (`end : blk`, `endmodule : t`), `timeunit`/`timeprecision`,
`unsigned` on `bit`/`reg`/`logic` declarations and ports, `input wire logic`
ports, task ports with no direction (`task t(integer a, b)`), and empty task
and function bodies. Allowing an empty function body made `return x;` also
parse as a declaration of `x` with type `return`, so a block declaration's
type name now excludes the keywords.

Every engine, silently: a function with a typedef return type returned 1 bit,
and a function's `output` arguments were never written back.
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_PARSER_FORMS = """\
timeunit 1ns;
timeprecision 1ps;
package p;
  timeunit 1ns / 1ps;
  localparam int K = 3;
endpackage : p
module t(input wire logic unsigned [3:0] a, output wire logic [7:0] y, output logic [31:0] s, output logic [7:0] u);
  timeunit 1ns;
  bit unsigned [7:0] b;
  reg unsigned [7:0] r;
  task automatic add(integer x, y, output integer sum);
    sum = x + y;
  endtask : add
  task automatic nothing(input int i);
  endtask
  function automatic void none();
  endfunction
  function automatic int f(int v);
    return v + 1;
  endfunction : f
  always_comb begin : blk
    integer t0;
    add(5, 7, t0);
    nothing(1);
    none();
    s = t0;
    b = 8'hf0;
    r = 8'h0f;
    u = (b | r) - f(p::K);
  end : blk
  for (genvar g = 0; g < 1; g++) begin : gen
    assign y = a;
  end : gen
endmodule : t
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_parser_forms(engine):
    design = parse_text(_PARSER_FORMS)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.drive("a", 0xA)
    sim.settle()
    assert (int(sim.read("y")), int(sim.read("s")), int(sim.read("u"))) == (0xA, 12, 0xFF - 4)


def test_parser_forms_round_trip():
    design = parse_text(_PARSER_FORMS)
    assert [(p.name, p.net_type) for p in design.get_module("t").ports][:2] == [("a", "wire"), ("y", "wire")]
    again = parse_text(emit_design(design))
    sim = Simulator(again.get_module("t"), engine="reference", design=again)
    sim.drive("a", 0xA)
    sim.settle()
    assert (int(sim.read("y")), int(sim.read("s")), int(sim.read("u"))) == (0xA, 12, 0xFF - 4)


def test_return_is_not_a_declaration():
    module = parse_text("module t; function automatic int f(input int x); return x; endfunction endmodule").get_module(
        "t"
    )
    (func,) = module.functions
    assert func.locals == []
    assert func.body is not None and type(func.body).__name__ == "BlockingAssign"


_TYPEDEF_RETURNS = """\
package q;
  typedef logic [69:0] wide_t;
endpackage
module t(output logic [7:0] b, output logic [69:0] w);
  typedef logic [7:0] b_t;
  typedef struct packed { logic [3:0] hi; logic [3:0] lo; } pair_t;
  function automatic b_t ident(input b_t x); return x; endfunction
  function automatic q::wide_t widen(input logic [7:0] x); return {x, 62'h1}; endfunction
  function automatic pair_t swap(input pair_t x); return {x.lo, x.hi}; endfunction
  assign b = ident(8'h5a) ^ swap(8'h12);
  assign w = widen(8'hc3);
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_typedef_return_types(engine):
    design = parse_text(_TYPEDEF_RETURNS)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.settle()
    assert int(sim.read("b")) == 0x5A ^ 0x21
    assert int(sim.read("w")) == (0xC3 << 62) | 1


_OUTPUTS = """\
module t(input clk, output logic [31:0] z, output logic [31:0] y, output logic [69:0] w, output logic [7:0] n);
  function automatic void two(output int a, int b); a = 3; b = 4; endfunction
  function automatic int three(input int i, output int a); a = i + 9; return i * 2; endfunction
  function automatic logic [69:0] wide(output logic [69:0] o); o = 70'h3f_0000_0000_0000_0002; return 70'h1; endfunction
  always_comb begin
    int p, q, r, s;
    logic [69:0] u, v;
    two(p, q);
    r = three(5, s) + 1;
    if (three(1, s) == 2) y = s; else y = 0;
    v = wide(u);
    z = p * 10 + q + r * 100;
    w = u | v;
  end
  always_ff @(posedge clk) begin int k; n <= three(2, k); end
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_function_outputs_are_written_back(engine):
    """`b` of `two(output int a, int b)` is an output too (a port with no
    direction takes the previous one's)."""
    design = parse_text(_OUTPUTS)
    (two,) = [f for f in design.get_module("t").functions if f.name == "two"]
    assert [p.direction.value for p in two.ports] == ["output", "output"]
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    for level in (0, 1, 0):
        sim.drive("clk", level)
        sim.settle()
    assert int(sim.read("z")) == 3 * 10 + 4 + (5 * 2 + 1) * 100
    assert int(sim.read("y")) == 1 + 9
    w = sim.read("w")
    assert (w.val, w.mask) == ((0x3F << 64) | 3, 0)
    assert int(sim.read("n")) == 4


_LOOPS = """\
module t(output logic [7:0] y, output logic [31:0] k);
  integer i, j;
  logic [7:0] x;
  always_comb begin
    x = 0;
    for (int n = 0; n < 4; ++n) x[n] = 1;
    for (int m = 7; m > 5; --m) x[m] = 1;
  end
  initial begin
    for (i = 0; i < 9; i++) ;
    j = 0;
    while (j < 5) j++;
    repeat (2) ;
    k = i * 10 + j;
  end
  assign y = x;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_loop_prefix_steps_and_null_bodies(engine):
    """`for (...; ++n)` and a null loop body (`for (...) ;`) did not parse."""
    design = parse_text(_LOOPS)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    sim.settle()
    assert (int(sim.read("y")), int(sim.read("k"))) == (0b1100_1111, 95)
