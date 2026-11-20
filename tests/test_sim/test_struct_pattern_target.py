"""Assignment patterns assigned to a packed-struct target, on every engine
(``lower_struct_patterns``, the parser's ``default:`` key and typed package
parameters; fixed 2026-11-20).

- ``'{b: 1, default: 0}``: after a named field the parser kept ``default`` as
  a field name, so no struct matched and every engine raised; then compiled
  packed only the default.
- The struct was picked by the pattern's field names alone: two typedefs with
  the same names packed a 10-bit target with the 76-bit layout.
- A cast inside a pattern (``'{a: 4'(k)}``) reached every engine unlowered
  (the lowering passes did not walk the pattern's ``(field, value)`` pairs).
- A positional pattern's items were packed at their own widths, not the
  fields'.
- ``parameter m_t On`` (a bare typedef name) lost its type, so ``$bits(On)``
  was 32 (the ibex package's ``ibex_mubi_t`` and ``pmp_cfg_t`` parameters).

Expected values are computed here, with fields wider than 64 bits.
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_B = 0x3F_FFFF_FFFF_FFFF_FFF0


def _sim(src: str, engine: str) -> Simulator:
    design = parse_text(src)
    return Simulator(design.get_module("t"), engine=engine, design=design)


_MIXED = """\
module t(input [1:0] k, output [75:0] o1, o2, o5, o6, output [9:0] o3, o4, o7, output [21:0] o8, output [19:0] o9);
  typedef struct packed { logic [3:0] a; logic [69:0] b; logic [1:0] c; } s_t;
  typedef struct packed { logic [3:0] a; logic [3:0] b; logic [1:0] c; } n_t;
  typedef struct packed { n_t a; logic [9:0] b; logic [1:0] c; } o_t;
  s_t s1, s2, s5, s6; n_t n3, n4, n7; o_t p8; n_t [1:0] m;
  n_t init;
  assign init = '{b: 4'd9, default: '0};
  assign s1 = '{b: 70'h3f_ffff_ffff_ffff_fff0, default: '1};
  always_comb begin
    s2 = '{default: 2'd2, c: k};
    n3 = '{a: {2'b0, k}, default: '0};
    n4 = '{default: '1, b: 4'd5};
    s5 = '{a: 4'(k + 2'd3), b: 70'(k), c: 2'(5)};
    s6 = '{1, 2, k};
    n7 = '{c: 2'd1, a: 4'hf, b: 4'(-1)};
    p8 = '{a: '{b: 4'(k), default: '0}, default: 2'd2};
    m[k[0]] = '{b: 4'd3, default: '1};
    m[~k[0]] = '{4'd1, 4'd2, 2'd3};
  end
  assign o1 = s1; assign o2 = s2; assign o3 = n3; assign o4 = n4; assign o5 = s5; assign o6 = s6;
  assign o7 = n7 ^ init; assign o8 = p8; assign o9 = m;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("k", [0, 1, 2, 3])
def test_patterns_take_the_target_struct(engine, k):
    sim = _sim(_MIXED, engine)
    sim.drive("k", k)
    sim.settle()

    def s_t(a, b, c):
        return (a << 72) | (b << 2) | c

    def n_t(a, b, c):
        return (a << 6) | (b << 2) | c

    nest = n_t(2, k, 2)  # (`default: 2'd2` reaches each member of the nested n_t)
    elements = {k & 1: n_t(15, 3, 3), 1 - (k & 1): n_t(1, 2, 3)}
    want = {
        "o1": s_t(15, _B, 3),
        "o2": s_t(2, 2, k),
        "o3": n_t(k, 0, 0),
        "o4": n_t(15, 5, 3),
        "o5": s_t(k + 3, k, 1),  # (the cast evaluates `k + 2'd3` at 4 bits: no wrap)
        "o6": s_t(1, 2, k),
        "o7": n_t(15, 15, 1) ^ n_t(0, 9, 0),
        "o8": (n_t(0, k, 0) << 12) | (2 << 2) | 2,
        "o9": (elements[1] << 10) | elements[0],
    }
    for name, value in want.items():
        assert sim.read(name) == value, name


_PKG = """\
package p;
  typedef struct packed { logic a; logic [1:0] b; } s_t;
  typedef logic [3:0] m_t;
  typedef struct packed { logic [69:0] w; m_t m; } w_t;
  parameter m_t On = 4'b0101;
  localparam s_t One = '{a: 1'b1, b: 2'd2};
  localparam s_t Two = '{b: 2'd1, default: 1'b0};
  localparam m_t Ones = '1;
  localparam w_t Wide = '{w: 70'h3f_ffff_ffff_ffff_fff0, default: '1};
endpackage
module t(output [3:0] o, output [5:0] w, output [3:0] q, output [7:0] bits, output [73:0] wide, output [7:0] wbits);
  import p::*;
  assign o = p::On;
  assign w = {Two, One};
  assign q = Ones;
  assign bits = {4'($bits(One)), 4'($bits(Ones))};
  assign wide = Wide;
  assign wbits = 8'($bits(Wide));
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_typed_package_parameters(engine):
    sim = _sim(_PKG, engine)
    sim.settle()
    assert sim.read("o") == 0b0101
    assert sim.read("w") == (0b001 << 3) | 0b110
    assert sim.read("q") == 0b1111
    assert sim.read("bits") == (3 << 4) | 4
    assert sim.read("wide") == (_B << 4) | 15
    assert sim.read("wbits") == 74


def test_parser_keeps_default_and_typedef_parameter_types():
    design = parse_text(_PKG)
    package = next(pk for pk in design.packages if pk.name == "p")
    params = {prm.name: prm for prm in package.parameters}
    assert params["On"].param_type == "m_t"
    two = params["Two"].default_value
    assert [name for name, _ in two.named_pairs] == ["b"]
    assert two.default_value is not None
