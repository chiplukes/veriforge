"""Signed memory elements and integer-atom-typed module ports.

* A word select of a signed memory (`reg signed [7:0] m [0:3]`, `byte m [..]`)
  is signed (IEEE 5.5.1: only bit/part selects are unsigned). Every engine read
  it as unsigned: `$display("%0d", m[0])`, comparisons with 0 and widening into
  a wider target all lost the sign.
* A module port of an integer atom type (`input byte a`, `output integer y`,
  `input time t`) simulated as 0 on every engine, and `byte unsigned` /
  `shortint unsigned` didn't parse on a port.
* A 2-state port (`input int a`, `output byte q`) kept x: a child's input
  port read the x/z driven into it, a 2-state output started at x, and a
  top-level drive of x stayed x (Icarus: 0).

Expected values are from Icarus, except bit/part selects of a signed memory
word: Icarus treats those as signed too, so `wm[0][99:96]` is Verilator's
(IEEE) 15, not Icarus's -1.
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _design(source: str):
    return tree_to_design(verilog_parser(start="source_text").build_tree(source))


def _display(source: str, engine: str) -> list[str]:
    design = _design(source)
    sim = Simulator(design.modules[-1], engine=engine, design=design)
    sim.run(max_time=20)
    return sim.display_output


@pytest.mark.parametrize("engine", ENGINES)
def test_signed_memory_words(engine):
    source = """module t;
  reg signed [7:0] rm [0:3];
  reg signed [99:0] wm [0:1];
  byte bm [1:2];
  reg [7:0] um [0:1];
  reg signed [15:0] acc; reg [15:0] q; reg lt;
  wire [119:0] ww = wm[1];
  wire c1 = rm[0] < 0;
  wire c2 = um[0] < 0;
  wire signed [15:0] s1 = rm[0] + rm[1];
  wire [15:0] s2 = rm[0][7] ? 16'hffff : 16'h0;
  wire [15:0] s3 = rm[0][3:0];
  function signed [15:0] f(input i); f = rm[i] * 2; endfunction
  reg ck;
  always @(posedge ck) begin acc <= rm[1] - 8'sd3; q <= bm[2]; lt <= bm[1] > rm[0]; end
  initial begin
    rm[0] = -5; rm[1] = 4; wm[1] = -100'sd7; bm[1] = -1; bm[2] = -128; um[0] = 8'hff; ck = 0;
    #1 $display("%h %b %b %0d %h %h %0d", ww, c1, c2, s1, s2, s3, f(1'b0));
    ck = 1;
    #1 $display("%0d %h %b", acc, q, lt);
  end
endmodule
"""
    assert _display(source, engine) == [
        "fffffffffffffffffffffffffffff9 1 0 -1 ffff 000b -10",
        "1 ff80 1",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_signed_memory_words_procedural(engine):
    source = """module t;
  reg signed [99:0] wm [0:1];
  reg signed [7:0] rm [0:1];
  byte bm [1:2];
  shortint sm [0:1];
  reg [119:0] a, b; reg [15:0] c, d; reg [70:0] e;
  initial begin
    wm[0] = -100'sd3; rm[0] = -2; bm[1] = -3; sm[0] = -4;
    a = wm[0]; b <= wm[0]; c = rm[0]; d <= bm[1]; e = sm[0] + rm[0];
    #1 $display("%h %h %h %h %h %0d %0d", a, b, c, d, e, bm[1], wm[0][99:96]);
  end
endmodule
"""
    assert _display(source, engine) == [
        "fffffffffffffffffffffffffffffd fffffffffffffffffffffffffffffd fffe fffd 7ffffffffffffffffa -3 15"
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_atom_typed_ports(engine):
    source = """module c(input byte a, input int b, output integer y, output [15:0] z, output [39:0] w);
  assign y = a;
  assign z = a;
  assign w = b;
endmodule
module t;
  byte x; int q; integer y; wire [15:0] z; wire [39:0] w;
  c u(.a(x), .b(q), .y(y), .z(z), .w(w));
  initial begin x = 8'hf0; q = -2; #1 $display("%0d %h %h", y, z, w); end
endmodule
"""
    assert _display(source, engine) == ["-16 fff0 fffffffffe"]


@pytest.mark.parametrize("engine", ENGINES)
def test_unsigned_atom_ports_and_2state_memory(engine):
    source = """module c(input byte unsigned a, output shortint unsigned y);
  assign y = a;
endmodule
module t;
  byte unsigned x; shortint unsigned y;
  function byte unsigned f(input byte unsigned p); byte unsigned q; begin q = p; f = q; end endfunction
  task automatic tk(input shortint unsigned p, output longint unsigned r); r = p; endtask
  longint unsigned r;
  byte bm [0:1];
  c u(.a(x), .y(y));
  always @(x) bm[1] = x;
  initial begin x = 8'hf0; #1 tk(16'hfff0, r); $display("%0d %0d %0d %0d %0d", y, f(8'hff), r, bm[0], bm[1]); end
endmodule
"""
    assert _display(source, engine) == ["240 255 65520 0 -16"]


@pytest.mark.parametrize(
    ("decl", "data_type", "signed", "width"),
    [
        ("input byte a", "byte", True, 8),
        ("input byte unsigned a", "byte", False, 8),
        ("input shortint unsigned a", "shortint", False, 16),
        ("output integer a", "integer", True, 32),
        ("output longint a", "longint", True, 64),
        ("input time a", "time", False, 64),
        ("input time signed a", "time", True, 64),
        ("input wire int a", "int", True, 32),
    ],
)
def test_atom_port_model_and_roundtrip(decl, data_type, signed, width):
    source = f"module m({decl}); endmodule\n"
    port = _design(source).modules[0].ports[0]
    assert (port.data_type, port.signed, port.width.msb.value + 1) == (data_type, signed, width)
    out = emit_module(_design(source).modules[0])
    again = _design(out).modules[0].ports[0]
    assert (again.data_type, again.signed, again.width.msb.value + 1) == (data_type, signed, width)
    assert emit_module(_design(out).modules[0]) == out


@pytest.mark.parametrize("engine", ENGINES)
def test_signed_memory_word_x_sign_bit(engine):
    source = """module t;
  reg signed [2:0] m [1:4];
  wire [15:0] a = m[3];
  wire [15:0] b = m[1];
  reg [15:0] d;
  initial begin
    m[1] = 3'b1x0; m[3] = 3'bx01;
    d = m[3];
    #1 $display("%b %b %b", a, b, d);
  end
endmodule
"""
    assert _display(source, engine) == ["xxxxxxxxxxxxxx01 11111111111111x0 xxxxxxxxxxxxxx01"]


@pytest.mark.parametrize("engine", ENGINES)
def test_two_state_child_ports(engine):
    source = """module c(input byte a, input int unsigned b, input integer k, output int y, output shortint z, output byte q);
  assign y = a + b;
  always @(*) q = k[7:0];
endmodule
module t;
  reg [7:0] x; reg [31:0] u; reg [31:0] kk;
  int y; shortint z; byte q;
  c u0(.a(x), .b(u), .k(kk), .y(y), .z(z), .q(q));
  initial begin x = 8'bx000_0001; u = 32'hz; kk = 'x; #1 $display("%h %h %h %h", y, z, q, u0.a); end
endmodule
"""
    assert _display(source, engine) == ["00000001 0000 00 01"]


@pytest.mark.parametrize("engine", ENGINES)
def test_two_state_top_ports(engine):
    source = """module t(input byte a, input int unsigned b, input logic [7:0] l,
         output int y, output shortint z, output byte q, output longint w);
  assign y = a + b;
  always @(*) q = l;
endmodule
"""
    design = _design(source)
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.drive("a", Value(1, width=8, mask=0x80))
    sim.signal("b").value = Value(0, width=32, mask=0xFFFF_FFFF)
    sim.drive("l", Value(0, width=8, mask=0xFF))
    sim.run(max_time=1)
    got = [sim.read(n) for n in ("a", "b", "y", "z", "q", "w")]
    assert got == [
        Value(1, width=8),
        Value(0, width=32),
        Value(1, width=32),
        Value(0, width=16),
        Value(0, width=8),
        Value(0, width=64),
    ]
