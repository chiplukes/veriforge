"""Integer atom types with an explicit signing (`byte unsigned`,
`int signed`, `integer unsigned`, ...) as variables, function/task ports and
function returns. The grammar only accepted the non-default keyword on some
types, `byte`/`int`/`shortint` ports were 1-bit (all engines),
`byte`/`shortint`/`longint` returns were 32 bits wide, and tasks took a
single statement. Expected outputs are from Icarus, except `_SIGNING`:
Icarus prints an `integer unsigned` with %d as signed (but compares it as
unsigned), so that one is from Verilator, which follows IEEE."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_SIGNING = """module t(input clk);
  integer unsigned iu = 32'hffff_ffff;
  integer signed is = 32'hffff_ffff;
  byte unsigned b = 8'hf0;
  int signed i = -3;
  function byte unsigned fb(input byte unsigned x); fb = x + 1; endfunction
  function int unsigned fi(input int unsigned x); fi = x >> 1; endfunction
  function integer unsigned fu(input integer unsigned x); fu = x; endfunction
  always @(posedge clk) begin
    $display("%0d %0d %0d %0d", iu, is, b, i);
    $display("%0d %0d %0d %0d %0d", iu > 0, fb(8'hff), fi(-1), fu(-1), fb(8'hff) > -1);

  end
endmodule
"""
_SIGNING_EXPECTED = ["4294967295 -1 240 -3", "1 0 2147483647 4294967295 0"]

_PORTS = """module t(input clk);
  function integer f1(input byte x); f1 = x; endfunction
  function integer f2(input int x); f2 = x; endfunction
  function integer f3(input shortint x); f3 = x; endfunction
  task automatic tk(input byte x, output integer y); y = x; endtask
  integer r;
  always @(posedge clk) begin
    tk(8'hf0, r);
    $display("%0d %0d %0d %0d", f1(8'hf0), f2(32'hffff_fff0), f3(16'hfff0), r);
  end
endmodule
"""
_PORTS_EXPECTED = ["-16 -16 -16 -16"]

_RETURNS = """module t(input clk);
  function int fi(input int x); fi = x - 1; endfunction
  function shortint fs(input shortint x); fs = x * 2; endfunction
  function longint fl(input longint x); fl = x >>> 1; endfunction
  function byte fb(input byte x); fb = x + 8'd1; endfunction
  function longint unsigned flu(input longint unsigned x); flu = x >> 1; endfunction
  function automatic shortint unsigned fsu(input byte unsigned a, input shortint unsigned b); fsu = a + b; endfunction
  task automatic tk(input int unsigned a, output shortint b, output byte unsigned c); b = a; c = a; endtask
  shortint b; byte unsigned cc;
  always @(posedge clk) begin
    tk(-5, b, cc);
    $display("%0d %0d %0d %0d %0d %0d", fi(0), fs(16'h7fff), fl(-64'd8), fb(8'h7f), flu(-1), fsu(8'hff, 16'hffff));
    $display("%0d %0d %0d %0d %0d %0d", fi(0) < 0, fs(-3) < 0, fb(-1) < 0, flu(-1) > 0, b, cc);
    $display("%h %h", {fi(0), fb(8'h80)}, {fs(1), fsu(1, 2)});
  end
endmodule
"""
_RETURNS_EXPECTED = ["-1 -2 -4 -128 9223372036854775807 254", "1 1 0 1 -5 251", "ffffffff81 00020003"]

# a task output bound to a caller signal named like one of its ports (reference)
_SHADOW = """module t(input clk);
  task automatic tk(input integer a, output shortint b, output byte unsigned c); b = a; c = a; endtask
  task automatic tk2(input integer a, output shortint q); q = a; endtask
  task automatic tk3(input integer a, output integer b); b = a; endtask
  shortint b; byte unsigned cc; shortint b2; integer b3;
  always @(posedge clk) begin
    tk(-5, b, cc); tk2(-5, b2); tk3(-5, b3);
    $display("%0d %0d %0d %0d", b, cc, b2, b3);
  end
endmodule
"""
_SHADOW_EXPECTED = ["-5 251 -5 -5"]

_CASES = {
    "signing": (_SIGNING, _SIGNING_EXPECTED),
    "ports": (_PORTS, _PORTS_EXPECTED),
    "returns": (_RETURNS, _RETURNS_EXPECTED),
    "shadow": (_SHADOW, _SHADOW_EXPECTED),
}


def _design(src: str, name: str):
    return tree_to_design(verilog_parser(start="source_text").build_tree(src), source_file=name)


@pytest.mark.parametrize("case", sorted(_CASES))
@pytest.mark.parametrize("engine", ENGINES)
def test_integer_atom_types(engine, case):
    src, expected = _CASES[case]
    design = _design(src, f"atom_{case}.v")
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.fork(Clock(sim.signal("clk"), period=10))
    sim.run(max_time=8)
    assert list(sim.display_output) == expected


@pytest.mark.parametrize("case", sorted(_CASES))
def test_integer_atom_types_round_trip(case):
    """The emitter writes these types back as declared (`input byte
    unsigned x`, never `input byte signed [7:0] x`), and multi-statement
    task bodies get a begin/end."""
    out = emit_module(_design(_CASES[case][0], "a.v").modules[0])
    assert emit_module(_design(out, "b.v").modules[0]) == out
