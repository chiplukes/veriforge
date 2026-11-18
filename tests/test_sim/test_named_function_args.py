"""Named-argument function calls (`f(.rs(a), .b(1'b1))`), IEEE 1800-2017 13.5.4.

The parser dropped named arguments, so such a call had no arguments at all: the
reference and vm engines computed garbage silently and compiled failed to build
("Call with wrong number of arguments"). Found by tools/corpus_check.py in ibex's
compressed decoder (`instr_o = cm_mva01s(.rs(instr_i[9:7]), .a01(1'b0));`).
`FunctionCall.arg_names` keeps them; `lower_named_arguments` makes them positional
for every engine; the emitter writes them back as named.
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
module sub(input [2:0] a, output logic [3:0] q);
  function automatic logic [3:0] g(input logic [2:0] x, input logic hi);
    return {hi, x};
  endfunction
  always_comb q = g(.hi(1'b1), .x(a));
endmodule
module t(input [2:0] a, input [69:0] w, output logic [3:0] y, output logic [3:0] z, output [3:0] c,
         output logic [4:0] n, output logic [69:0] wy, output [3:0] s);
  function automatic logic [3:0] f(input logic [2:0] rs, input logic b);
    return {b, rs};
  endfunction
  function automatic logic [4:0] outer(input logic [2:0] rs, input logic a01);
    logic [3:0] inner;
    inner = f(.b(a01), .rs(rs));
    return {inner, ~a01};
  endfunction
  function automatic logic [69:0] wf(input logic [69:0] v, input logic [69:0] m);
    return v ^ m;
  endfunction
  always_comb y = f(.rs(a), .b(1'b1));
  always_comb z = f(.b(1'b0), .rs(a));
  assign c = f(.rs(~a), .b(a[0]));
  always_comb n = outer(.a01(1'b1), .rs(a));
  always_comb wy = wf(.m({70{1'b1}}), .v(w));
  sub u (.a(a), .q(s));
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_named_function_arguments(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    sim.drive("a", Value(0b101, width=3))
    sim.drive("w", Value((1 << 69) | 0x1234, width=70))
    sim.settle()
    assert sim.read("y") == Value(0b1101, width=4)
    assert sim.read("z") == Value(0b0101, width=4)
    assert sim.read("c") == Value(0b1010, width=4)
    assert sim.read("n") == Value(0b11010, width=5)
    assert sim.read("wy") == Value(((1 << 70) - 1) ^ ((1 << 69) | 0x1234), width=70)
    assert sim.read("s") == Value(0b1101, width=4)


def test_named_arguments_round_trip_and_errors():
    text = emit_design(parse_text(_SRC))
    assert "f(.rs(a), .b(1'b1))" in text and "f(.b(1'b0), .rs(a))" in text
    assert emit_design(parse_text(text)) == text
    bad = "module t(output logic [3:0] y);\n  function automatic logic [3:0] f(input logic [2:0] rs);\n    return {1'b0, rs};\n  endfunction\n  always_comb y = f(.nope(3'd1));\nendmodule\n"
    design = parse_text(bad)
    with pytest.raises(ValueError, match="no port nope"):
        Simulator(design.get_module("t"), engine="reference", design=design)
