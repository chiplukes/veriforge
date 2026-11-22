"""Forms that did not parse (fixed 2026-11-21), on every engine and through the
emitter.

- an element of a package parameter array, `p::A[1]` and `p::A[i]`
- an index-keyed assignment pattern, `'{1: 8'd7, default: 8'd9}`
- a user-typed declaration with an initializer, `n_t s = '{a: 4'd3, b: 4'd5};`
  (with it, `assign y = q;` must not parse as a declaration of type `assign`)
- a `localparam` in a parameter port list, `#(parameter W = 4, localparam D = 2**W)`
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_SRC = """\
package p;
  localparam logic [7:0] A [3] = '{8'd1, 8'd2, 8'd3};
  localparam logic [69:0] B [2] = '{70'h3f_0000_0000_0000_0001, 70'h1};
endpackage
module t #(parameter int W = 4, localparam int D = 2**W, parameter int E = 1) (
    input [1:0] i, output [7:0] a1, output [7:0] ai, output [3:0] ab, output [69:0] bw, output [31:0] c1,
    output [7:0] k0, output [7:0] k1, output [7:0] k2, output [69:0] kw,
    output [7:0] v, output [7:0] q2, output [D-1:0] d, output [31:0] e);
  localparam int C1 = p::A[2] + 1;
  localparam logic [7:0] K [3] = '{1: 8'd7, default: 8'd9};
  localparam logic [69:0] KW [2] = '{0: 70'h20_0000_0000_0000_0005, default: '0};
  typedef struct packed { logic [3:0] a; logic [3:0] b; } n_t;
  n_t s = '{a: 4'd3, b: 4'd5};
  logic [7:0] q = 8'h2a;
  assign a1 = p::A[1];
  assign ai = p::A[i];
  assign ab = p::A[2][3:0];
  assign bw = p::B[0];
  assign c1 = C1;
  assign k0 = K[0];
  assign k1 = K[1];
  assign k2 = K[2];
  assign kw = KW[0];
  assign v = s;
  assign q2 = q;
  assign d = D;
  assign e = E;
endmodule
"""

_EXPECTED = {
    "a1": 2,
    "ai": 3,  # (i = 2)
    "ab": 3,
    "bw": (0x3F << 64) | 1,
    "c1": 4,
    "k0": 9,
    "k1": 7,
    "k2": 9,
    "kw": (0x20 << 64) | 5,
    "v": 0x35,
    "q2": 0x2A,
    "d": 16,
    "e": 1,
}


def _run(design, engine):
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.drive("i", 2)
    sim.settle()
    return {name: sim.read(name) for name in _EXPECTED}


@pytest.mark.parametrize("engine", ENGINES)
def test_new_forms_on_every_engine(engine):
    got = _run(parse_text(_SRC), engine)
    assert {k: (v.val, v.mask) for k, v in got.items()} == {k: (v, 0) for k, v in _EXPECTED.items()}
    assert got["d"].width == 16  # (the port width uses the header localparam)


def test_emitter_round_trips_the_new_forms():
    out = emit_design(parse_text(_SRC))
    assert "p::A[i]" in out
    assert "'{1: 8'd7, default: 8'd9}" in out
    assert "n_t s = '{a: 4'd3, b: 4'd5};" in out
    header = out[out.index("module t") : out.index(");")]
    assert "localparam int D = 2 ** W" in header  # (kept in the header: a port width uses it)
    got = _run(parse_text(out), "reference")
    assert {k: v.val for k, v in got.items()} == _EXPECTED


def test_assign_is_not_a_type_name():
    """With an initializer allowed on a user-typed declaration, `assign y = q;`
    also matched `<type> <name> = <expr>;` and parsed as a variable `y` of type
    `assign`, so the continuous assign was lost."""
    module = parse_text("module t(output [7:0] y);\n  logic [7:0] q = 8'h35;\n  assign y = q;\nendmodule\n").get_module(
        "t"
    )
    assert [v.name for v in module.variables] == ["q"]
    assert len(module.continuous_assigns) == 1


def test_header_localparam_cannot_be_overridden():
    src = """\
module c #(parameter int W = 4, localparam int D = 2**W, parameter int E = 1) (output [31:0] e);
  assign e = D + E;
endmodule
module t(output [31:0] a, output [31:0] b);
  c #(3, 5) u0 (.e(a));
  c #(.W(3), .D(99)) u1 (.e(b));
endmodule
"""
    design = parse_text(src)
    with pytest.raises(ValueError, match="overrides 'D'"):
        Simulator(design.get_module("t"), engine="reference", design=design)
    design = parse_text(src.replace("  c #(.W(3), .D(99)) u1 (.e(b));\n", "  assign b = 0;\n"))
    sim = Simulator(design.get_module("t"), engine="reference", design=design)
    sim.settle()
    assert int(sim.read("a")) == 8 + 5  # (a positional override skips the localparam)
