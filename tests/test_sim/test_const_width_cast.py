"""A size cast in a parameter value (`localparam M = 32'(!R) << 8`).

Two bugs, both on every engine: the parser built the cast's operand with the
constant-expression builder, which read `!R` as `Identifier("!")`, and the
constant evaluator had no `WidthCast` case, so the parameter didn't resolve.
Found re-emitting ibex_cs_registers (`(32'(!RV32E) << 8)` in its MISA value).
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_design
from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_SRC = """\
module t #(parameter bit RV32E = 0, parameter int N = 3) (output [31:0] y, output [31:0] y2, output [7:0] y3,
    output [69:0] y4);
  localparam logic [31:0] M = (32'(!RV32E) << 8) | (32'(RV32E) << 4) | 32'(~3'd5);
  localparam logic [7:0] K = 8'(N * 100);
  localparam logic [69:0] B = 70'(-1);
  assign y = M;
  assign y2 = 32'(!RV32E);
  assign y3 = K;
  assign y4 = B;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_size_cast_in_parameter(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    sim.settle()
    assert sim.read("y").val == 0xFFFF_FFFA  # 0x100 | 0 | 32'(~3'd5)
    assert sim.read("y2").val == 1
    assert sim.read("y3").val == 300 & 0xFF
    assert sim.read("y4").val == (1 << 70) - 1


def test_size_cast_operand_round_trips():
    text = emit_design(parse_text(_SRC))
    assert "32'(!RV32E)" in text and "32'(!)" not in text
    assert emit_design(parse_text(text)) == text
