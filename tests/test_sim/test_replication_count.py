"""A replication whose count is a parameter (or parameter arithmetic), including a zero
count, which drops out of the concatenation (IEEE 1800-2017 11.4.12.1).

The reference engine sized a non-literal-count replication as its inner width, so
`{a, {B{1'b0}}}` with B = 0 claimed a bit and the evaluator padded the empty result to
a 0: serv_csr's `{mcause31, {B{1'b0}}}` read 0 where vm, compiled and Icarus read
mcause31. Found by tools/corpus_check.py.
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
module t #(parameter B = 0, parameter C = 3) (input a, input [3:0] b, input [39:0] w,
    output y1, output [4:0] y2, output [1:0] y3, output [7:0] y4, output [9:0] y5,
    output [119:0] y6, output [40:0] y7);
  assign y1 = {a, {B{1'b0}}};
  assign y2 = {b, {B{1'b1}}, a};
  assign y3 = {{B{b}}, a, a};
  assign y4 = {C{a}} + 8'd1;
  assign y5 = {{(C-1){b}}, a, a};
  assign y6 = {C{w}};
  assign y7 = {w, {B{w}}, a};
endmodule
"""

_W = 0xA5_1234_5678


def _run(engine: str, a: Value) -> dict[str, Value]:
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    sim.drive("a", a)
    sim.drive("b", Value(0b1010, width=4))
    sim.drive("w", Value(_W, width=40))
    sim.settle()
    return {n: sim.read(n) for n in ("y1", "y2", "y3", "y4", "y5", "y6", "y7")}


@pytest.mark.parametrize("engine", ENGINES)
def test_parameter_and_zero_replication_counts(engine):
    got = _run(engine, Value(1))
    assert {n: v.val for n, v in got.items()} == {
        "y1": 1,
        "y2": 0b10101,
        "y3": 0b11,
        "y4": 0b1000,
        "y5": 0b1010_1010_11,
        "y6": (_W << 80) | (_W << 40) | _W,
        "y7": (_W << 1) | 1,
    }
    assert not any(v.mask for v in got.values())


@pytest.mark.parametrize("engine", ENGINES)
def test_zero_replication_keeps_x_of_the_other_parts(engine):
    got = _run(engine, Value(0, mask=1))
    assert got["y1"].mask == 1
    assert got["y2"].mask == 1 and got["y2"].val == 0b10100
    assert got["y7"].mask == 1
