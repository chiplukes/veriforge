"""Wide (>64-bit) shapes the compiled engine refused to build, and `$countbits`.

Found by the corpus run (2026-11-17), fixed 2026-11-19:

- compiled: `**` whose exponent reads a wide value (`x ** x[3:0]`) or is
  itself wider than 64 bits stopped the build ("RHS shape not yet
  supported"); the exponent now goes to `wide_pow_w` as words.
- compiled: a string literal into a destination wider than 64 bits
  (picorv32's `reg [127:0] dbg_ascii_state; dbg_ascii_state = "ld_rs1";`)
  stopped the build; the wide emitter now loads its characters as words.
- every engine: `$countbits(e, c, ...)` (IEEE 1800 20.9) was never
  implemented -- reference returned x, compiled 0, vm/vm-fast raised. It is
  now lowered with `$countones` (`sim/sv_functions.py`). Icarus 14 aborts on
  `$countbits`, so the expected counts are computed here.

Expected `**` and string values match Icarus Verilog.
"""

from __future__ import annotations

import random

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_M128 = (1 << 128) - 1


def _sim(src: str, engine: str) -> Simulator:
    design = parse_text(src)
    return Simulator(design.get_module("t"), engine=engine, design=design)


_POW = """\
module t(input [100:0] x, input [7:0] s, output [127:0] y, output [7:0] u, output [127:0] w,
    output reg [127:0] v, output [127:0] g);
  assign y = {120'd0, s} ** x;
  assign u = s ** x;
  assign w = 128'd3 ** (x >> 30);
  always @* v = x ** x[3:0];
  assign g = (x + 1) ** s[2:0] + 1;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_power_with_a_wide_exponent(engine):
    sim = _sim(_POW, engine)
    rng = random.Random(3)
    for _ in range(25):
        x, s = rng.getrandbits(101), rng.getrandbits(8)
        sim.drive("x", x)
        sim.drive("s", s)
        sim.settle()
        assert int(sim.read("y")) == pow(s, x, 1 << 128)
        assert int(sim.read("u")) == pow(s, x, 256)
        assert int(sim.read("w")) == pow(3, x >> 30, 1 << 128)
        assert int(sim.read("v")) == pow(x, x & 15, 1 << 128)
        assert int(sim.read("g")) == (pow(x + 1, s & 7, 1 << 128) + 1) & _M128
    sim.drive("x", Value(0, mask=1 << 80, width=101))  # an x exponent bit above word 0
    sim.settle()
    assert sim.read("y").mask == _M128


_SIGNED_POW = """\
module t(input [100:0] x, input [7:0] b, output [69:0] y);
  assign y = $signed({{62{b[7]}}, b}) ** $signed(x);
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize(
    ("base", "exponent", "expected"),
    [
        (0xFF, (1 << 101) - 3, (1 << 70) - 1),  # (-1) ** -3 = -1
        (0xFF, (1 << 101) - 2, 1),  # (-1) ** -2 = 1
        (1, (1 << 100) | 5, 1),  # 1 ** negative = 1
        (3, 1 << 99, 0),  # positive exponent past 64 bits: 3 ** 2**99 mod 2**70
        (2, 1 << 70, 0),
    ],
)
def test_signed_power_with_a_wide_exponent(engine, base, exponent, expected):
    sim = _sim(_SIGNED_POW, engine)
    sim.drive("x", exponent)
    sim.drive("b", base)
    sim.settle()
    if base == 3:
        expected = pow(3, exponent, 1 << 70)
    assert sim.read("y") == Value(expected, width=70)


_STRING = """\
module t(input [1:0] s, output reg [127:0] y, output [127:0] z, output [99:0] u, output [63:0] n);
  always_comb begin
    y = "";
    if (s == 0) y = "trap";
    if (s == 1) y = "fetch";
    if (s == 2) y = "ld_rs1_and_more";
    if (s == 3) y = {"ab", 6'd0, s, "cdefghijklmn"};
  end
  assign z = s[0] ? "abcdefghijklmnopq" : "x";
  assign u = "abcdefghijklmnopq";
  assign n = "abcdefghij";
endmodule
"""


def _text(value: str, width: int) -> int:
    return int.from_bytes(value.encode(), "big") & ((1 << width) - 1)


@pytest.mark.parametrize("engine", ENGINES)
def test_string_literal_into_a_wide_destination(engine):
    sim = _sim(_STRING, engine)
    expected_y = ["trap", "fetch", "ld_rs1_and_more", "ab\x03cdefghijklmn"]
    for s in range(4):
        sim.drive("s", s)
        sim.settle()
        assert sim.read("y") == Value(_text(expected_y[s], 128), width=128), s
        assert sim.read("z") == Value(_text("abcdefghijklmnopq" if s & 1 else "x", 128), width=128), s
        assert sim.read("u") == Value(_text("abcdefghijklmnopq", 100), width=100)
        assert sim.read("n") == Value(_text("cdefghij", 64), width=64)


_COUNTBITS = """\
module t(input [100:0] x, input [7:0] s, output [7:0] w0, output [7:0] w1, output [7:0] wx, output [7:0] w01,
    output [7:0] wz, output [7:0] n, output [7:0] c1);
  assign w0 = $countbits(x, 1'b0);
  assign w1 = $countbits(x, '1);
  assign wx = $countbits(x, 'x);
  assign w01 = $countbits(x, 1'b0, '1, 1'b1);
  assign wz = $countbits({x, s}, 1'bz, 1'b0);
  assign n = $countbits(s ^ x[7:0], 2'b10);
  assign c1 = $countones(x) - $countbits(x, 1);
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_countbits(engine):
    sim = _sim(_COUNTBITS, engine)
    rng = random.Random(5)
    for i in range(12):
        x = rng.getrandbits(101)
        m = rng.getrandbits(101) & rng.getrandbits(101) if i % 2 else 0
        s = rng.getrandbits(8)
        sim.drive("x", Value(x & ~m, mask=m, width=101))
        sim.drive("s", s)
        sim.settle()
        ones, xs = (x & ~m).bit_count(), m.bit_count()
        zeros = 101 - ones - xs
        assert int(sim.read("w0")) == zeros
        assert int(sim.read("w1")) == ones
        assert int(sim.read("wx")) == xs  # (x and z share one representation)
        assert int(sim.read("w01")) == zeros + ones
        assert int(sim.read("wz")) == xs + zeros + (8 - s.bit_count())
        if not m & 0xFF:
            assert int(sim.read("n")) == 8 - (s ^ x & 0xFF).bit_count()  # (control: the LSB, 0)
        assert int(sim.read("c1")) == 0


def test_countbits_needs_constant_controls():
    with pytest.raises(ValueError, match="countbits"):
        _sim("module t(input [7:0] a, input c, output [3:0] y); assign y = $countbits(a, c); endmodule", "reference")
