"""A signed `+ - * / %` whose operand is itself context-determined (`x * y + c`,
`(x + y) + c`, `(x << 4) + c`) evaluates that operand at the context width.

Compiled re-sign-extended such an operand from its static self-width (17 bits for
17-bit `x * y`), after it had already been computed at the 35-bit context: the
product's real upper bits were replaced by copies of bit 16. Found by
tools/corpus_check.py in ibex's multiplier (`mac_res_signed = $signed({sign_a,
mult_op_a}) * $signed({sign_b, mult_op_b}) + $signed(accum);`).
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
module t (input signed [16:0] x, input signed [16:0] y, input signed [16:0] w, input [15:0] a, input [15:0] b,
  input sa, input sb, input [33:0] acc,
  output signed [34:0] mac, output signed [34:0] r1, output signed [34:0] r2, output signed [34:0] r3,
  output signed [34:0] r4, output signed [34:0] r5, output signed [79:0] r6, output signed [34:0] d1,
  output signed [34:0] d2, output signed [34:0] d3, output signed [34:0] t1, output signed [34:0] u1,
  output signed [34:0] m1, output reg signed [34:0] p1);
  assign mac = $signed({sa, a}) * $signed({sb, b}) + $signed(acc);
  assign r1 = x * y + 35'sd0;
  assign r2 = (x + y) + 35'sd0;
  assign r3 = (x - y) - w;
  assign r4 = (x << 4) + 35'sd0;
  assign r5 = x * y - w;
  assign r6 = x * y * w + 80'sd0;
  assign d1 = (x * y) / w;
  assign d2 = (x * y) % w;
  assign d3 = (x + y) / 35'sd3;
  assign t1 = (x[0] ? x * y : -(x * y)) + 35'sd1;
  assign u1 = -(x + y) + 35'sd0;
  assign m1 = (x * y) * 35'sd3;
  always @* p1 = x * y + 35'sd0;
endmodule
"""

_NAMES = ("mac", "r1", "r2", "r3", "r4", "r5", "r6", "d1", "d2", "d3", "t1", "u1", "m1", "p1")


def _s(v: int, w: int) -> int:
    return v - (1 << w) if v >> (w - 1) else v


def _expected(x: int, y: int, w: int, a: int, b: int, sa: int, sb: int, acc: int) -> dict[str, int]:
    X, Y, W = _s(x, 17), _s(y, 17), _s(w, 17)

    def tdiv(n: int, d: int) -> int:  # C/Verilog division truncates toward zero
        q = abs(n) // abs(d)
        return q if (n < 0) == (d < 0) else -q

    vals = {
        "mac": _s((sa << 16) | a, 17) * _s((sb << 16) | b, 17) + _s(acc, 34),
        "r1": X * Y,
        "r2": X + Y,
        "r3": X - Y - W,
        "r4": X << 4,
        "r5": X * Y - W,
        "r6": X * Y * W,
        "d1": tdiv(X * Y, W),
        "d2": X * Y - tdiv(X * Y, W) * W,
        "d3": tdiv(X + Y, 3),
        "t1": (X * Y if x & 1 else -(X * Y)) + 1,
        "u1": -(X + Y),
        "m1": X * Y * 3,
        "p1": X * Y,
    }
    return {n: v & ((1 << (80 if n == "r6" else 35)) - 1) for n, v in vals.items()}


_CASES = [
    (0xB9FC, 0xC00E, 0x1FFF0, 0xB9FC, 0xC00E, 0, 0, 0),
    (0x1B9FC, 0x0C00E, 0x00007, 0xB9FC, 0xC00E, 1, 0, 0x2_0000_0001),
    (0x10000, 0x10000, 0x1FFFF, 0xFFFF, 0xFFFF, 1, 1, 0x3_FFFF_FFFF),
    (0x0FFFF, 0x0FFFF, 0x00005, 0x0001, 0x8000, 0, 1, 12345),
]


@pytest.mark.parametrize("engine", ENGINES)
def test_signed_nested_arithmetic_uses_context_width(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    for x, y, w, a, b, sa, sb, acc in _CASES:
        for name, val, width in (
            ("x", x, 17),
            ("y", y, 17),
            ("w", w, 17),
            ("a", a, 16),
            ("b", b, 16),
            ("sa", sa, 1),
            ("sb", sb, 1),
            ("acc", acc, 34),
        ):
            sim.drive(name, Value(val, width=width))
        sim.settle()
        got = {n: sim.read(n).val for n in _NAMES}
        assert got == _expected(x, y, w, a, b, sa, sb, acc), (engine, hex(x), hex(y), hex(w))
