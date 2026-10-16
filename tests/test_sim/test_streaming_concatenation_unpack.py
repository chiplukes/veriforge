"""Streaming concatenation as an assignment target -- unpacking (IEEE
1800-2017 11.4.14.3): `{<<8{a, b}} = s;`, `{>>{a, b}} = s;`. It used to be a
parse error. Lowered to an assignment to `{a, b}`
(`sim/sv_functions.py:lower_streaming_targets`): `<<n` assigns *s* cut
into n-bit slices from its LSB end, in reverse order (the inverse of the
`{<<n{...}}` expression, which slices from the MSB end).

Expected values from Verilator (Icarus rejects the construct), except a
source wider than the targets: IEEE consumes it from its left end
(`{<<8{a, b}} = 24'hABCDEF` gives a = cd, b = ab), Verilator streams all of
it first (ef, cd) -- known_issues.md.
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


def _display(source: str, engine: str) -> list[str]:
    design = _design(source)
    sim = Simulator(design.modules[-1], engine=engine, design=design)
    sim.run(max_time=10)
    return sim.display_output


@pytest.mark.parametrize("engine", ENGINES)
def test_unpacking(engine):
    source = """module t;
  logic [7:0] a, b; logic [3:0] c; logic [2:0] d, e; logic [23:0] s24;
  logic [7:0] x, y; logic [15:0] src = 16'hC3A5;
  assign {<<4{x, y}} = src;
  logic [99:0] wa; logic [27:0] wb;
  initial begin
    {<<8{a, b}} = 16'hA1B2;               $display("1 %h %h", a, b);
    {<<4{a, c}} = 12'hABC;                $display("2 %h %h", a, c);
    {<<3{a, d}} = 11'b101_0110_1001;      $display("3 %b %b", a, d);
    {>>{a, c}} = 12'h5A7;                 $display("4 %h %h", a, c);
    {<<{a}} = 8'b1100_1010;               $display("5 %b", a);
    s24 = 24'hABCDEF; {>>{a, b}} = s24;   $display("6 %h %h", a, b);
    {<<3{d, e}} = 6'b110_011;             $display("8 %b %b", d, e);
    {<<5{a, b}} = 16'hF0A5;               $display("9 %b %b", a, b);
    {<<8{wa, wb}} = {64'h0123456789abcdef, 64'hfedcba9876543210};  $display("10 %h %h", wa, wb);
    {<<8{a, b}} <= 16'h1234; #1           $display("11 %h %h %h %h", a, b, x, y);
  end
endmodule
"""
    assert _display(source, engine) == [
        "1 b2 a1",
        "2 cb a",
        "3 00110110 110",
        "4 5a 7",
        "5 01010011",
        "6 ab cd",
        "8 011 110",
        "9 00101001 01111001",
        "10 1032547698badcfeefcdab896 7452301",
        "11 34 12 5a 3c",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_wide_source_memory_targets_and_tasks(engine):
    source = """module t;
  logic [7:0] a, b; logic [7:0] m [0:3]; logic [15:0] v; logic [23:0] s24;
  task automatic tk(input [15:0] x, output [7:0] p, output [7:0] q); {<<4{p, q}} = x; endtask
  initial begin
    s24 = 24'hABCDEF; {<<8{a, b}} = s24;  $display("7 %h %h", a, b);
    {<<8{m[1], v[15:8], m[2]}} = 24'h112233; $display("12 %h %h %h", m[1], v[15:8], m[2]);
    tk(16'hBEEF, a, b); $display("13 %h %h", a, b);
  end
endmodule
"""
    assert _display(source, engine) == ["7 cd ab", "12 33 22 11", "13 fe eb"]


@pytest.mark.parametrize("engine", ENGINES)
def test_in_a_child_module(engine):
    source = """module c(input [15:0] s, output logic [7:0] x, output logic [7:0] y);
  always_comb {<<4{x, y}} = s;
endmodule
module t;
  logic [15:0] s; wire [7:0] x, y;
  c u(.s(s), .x(x), .y(y));
  initial begin s = 16'hC3A5; #1 $display("%h %h", x, y); end
endmodule
"""
    assert _display(source, engine) == ["5a 3c"]


def test_narrower_source_is_an_error():
    with pytest.raises(ValueError, match="needs 16 bits but the source has only 8"):
        _display("module t; logic [7:0] a, b; initial {<<8{a, b}} = 8'h12; endmodule\n", "reference")


def test_round_trip():
    source = """module t(input [15:0] s, output [7:0] x, output [7:0] y);
  reg [7:0] a, b;
  assign {<<8{x, y}} = s;
  always @* begin {<<4{a, b}} = s; {>>{a, b}} = s; end
endmodule
"""
    out = emit_module(_design(source).modules[0])
    assert "assign {<<8{x, y}} = s;" in out
    assert "{<<4{a, b}} = s;" in out and "{>>{a, b}} = s;" in out
    assert emit_module(_design(out).modules[0]) == out
