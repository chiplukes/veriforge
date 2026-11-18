"""A unary `- ~ +` evaluates its operand at max(context, operand width) from the start.

The reference engine evaluated the operand at the (narrower) context width first,
so a nested context-determined operator truncated its own operand: `-(a <<< 7)` into a
1-bit target dropped `a`'s x bit before shifting and read 0, where vm, vm-fast,
compiled and Icarus read x. Found by the fuzzer (hierarchical strategy, seed 5057).
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
module t (input [126:0] a, input [7:0] b, output y1, output y2, output y3, output y4, output [3:0] y5,
          output y6, output [7:0] y7, output [69:0] y8);
  assign y1 = -(a <<< 7);
  assign y2 = -a;
  assign y3 = -(b << 1);
  assign y4 = a + 1'b0;
  assign y5 = -(b <<< 3);
  assign y6 = ~(a <<< 7);
  assign y7 = ~(a >> 120);
  assign y8 = -(a << 60);
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_unary_operand_uses_full_context(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    sim.drive("a", Value(4, width=127, mask=1 << 100))  # an x bit that survives the shifts
    sim.drive("b", Value(4, width=8, mask=0x80))  # an x bit the shifts push out of 8 bits
    sim.settle()
    got = {n: sim.read(n) for n in ("y1", "y2", "y3", "y4", "y5", "y6", "y7", "y8")}
    assert got["y1"].mask == 1  # arithmetic on an operand with x: all x (Icarus: x)
    assert got["y2"].mask == 1
    assert got["y3"] == Value(0)  # b << 1 at 8 bits pushes the x out
    assert got["y4"].mask == 1
    assert got["y5"] == Value(0, width=4)
    assert got["y6"] == Value(1)  # bitwise: only bit 0 (a 0 after the shift) reaches the target
    assert got["y7"] == Value(0xFF, width=8)  # ~(a >> 120): bits 120.. are 0, x at 100 is gone
    assert got["y8"] == Value(-(4 << 60) & ((1 << 70) - 1), width=70)  # the x bit is shifted past 127 bits
