"""An assignment to a concatenation evaluates its RHS at the concatenation's total width
(IEEE 1800-2017 11.6.1): `{c, s} = a + b` keeps the carry, a signed RHS sign-extends into
the high part, and `(a + b) >> 1` shifts the carry down.

Compiled evaluated the RHS at its own width, so the carry was always 0, even for
defined values. Found by `veriforge.sim.diverge` on the servile SoC
(`examples/serv/rtl/serv_ctrl.v:70`, `assign {pc_plus_4_cy,pc_plus_4} = pc+plus_4+...`).
"""

from __future__ import annotations

import random

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
module t(input clk, input [7:0] a, input [7:0] b, input c, input [69:0] wa, input [69:0] wb,
         input signed [3:0] s, input [59:0] na,
         output n_c, output [7:0] n_s,           // continuous, narrow
         output reg p_c, output reg [7:0] p_s,   // procedural blocking
         output reg q_c, output reg [7:0] q_s,   // nonblocking
         output t_c, output t_s,                  // 1-bit parts, three operands (serv_ctrl)
         output [1:0] m_h, output [7:0] m_l,     // product
         output [3:0] h_h, output [3:0] h_l,     // shift of the carry: (a + b) >> 1
         output [3:0] x_h, output [3:0] x_l,     // signed identifier sign-extends
         output reg [3:0] y_h, output reg [3:0] y_l,
         output [3:0] z_h, output [3:0] z_l,     // signed expression
         output [3:0] e_h, output [59:0] e_l,    // 64-bit total
         output w_c, output [69:0] w_s,          // > 64 bits, continuous
         output reg v_c, output reg [69:0] v_s,  // > 64 bits, procedural
         output [3:0] g_h, output [67:0] g_l,    // signed into > 64 bits, continuous
         output reg [3:0] f_h, output reg [67:0] f_l);
  assign {n_c, n_s} = a + b;
  always @* {p_c, p_s} = a + b;
  always @(posedge clk) {q_c, q_s} <= a + b;
  assign {t_c, t_s} = a[0] + b[0] + c;
  assign {m_h, m_l} = a * b;
  assign {h_h, h_l} = (a[3:0] + b[3:0]) >> 1;
  assign {x_h, x_l} = s;
  always @* {y_h, y_l} = s;
  assign {z_h, z_l} = s + s;
  assign {e_h, e_l} = na + na;
  assign {w_c, w_s} = wa + wb;
  always @* {v_c, v_s} = wa + wb;
  assign {g_h, g_l} = s;
  always @* {f_h, f_l} = s;
endmodule
"""

_WIDTHS = {"clk": 1, "a": 8, "b": 8, "c": 1, "wa": 70, "wb": 70, "s": 4, "na": 60}


def _run(engine: str, inputs: dict[str, Value]) -> dict[str, Value]:
    design = parse_text(_SRC)
    module = design.get_module("t")
    assert module is not None
    sim = Simulator(module, engine=engine, design=design)
    for name, width in _WIDTHS.items():
        sim.drive(name, Value(0, width=width))
    sim.run(max_time=0)
    for name, value in inputs.items():
        sim.drive(name, value)
    sim.settle()
    sim.drive("clk", Value(1))
    sim.settle()
    return {p.name: sim.read(p.name) for p in module.output_ports()}


def _defined(**vals: int) -> dict[str, Value]:
    return {n: Value(v, width=_WIDTHS[n]) for n, v in vals.items()}


_CASE = _defined(a=200, b=100, c=1, wa=(1 << 70) - 1, wb=1, s=0b1010, na=(1 << 60) - 1)

# a=200, b=100: a + b = 300 = 9'h12c. a[0] + b[0] + c = 0 + 0 + 1. a * b = 20000, low 10 bits
# 10'h220. (a[3:0] + b[3:0]) >> 1 = (8 + 4) >> 1. s = -6: 8'hfa; s + s = -12 = 8'hf4.
# na + na = 2**61 - 2 at 64 bits. wa + wb = 2**70 at 71 bits. s at 72 bits: low 68 = 2**68 - 6.
_EXPECTED = {
    "n_c": 1, "n_s": 0x2C, "p_c": 1, "p_s": 0x2C, "q_c": 1, "q_s": 0x2C,
    "t_c": 0, "t_s": 1,
    "m_h": 0b10, "m_l": 0x20,
    "h_h": 0, "h_l": 6,
    "x_h": 0xF, "x_l": 0xA, "y_h": 0xF, "y_l": 0xA,
    "z_h": 0xF, "z_l": 0x4,
    "e_h": 1, "e_l": (1 << 60) - 2,
    "w_c": 1, "w_s": 0, "v_c": 1, "v_s": 0,
    "g_h": 0xF, "g_l": (1 << 68) - 6, "f_h": 0xF, "f_l": (1 << 68) - 6,
}  # fmt: skip


@pytest.mark.parametrize("engine", ENGINES)
def test_concat_target_takes_its_width_as_context(engine):
    got = _run(engine, _CASE)
    wrong = {n: (hex(got[n].val), hex(v)) for n, v in _EXPECTED.items() if got[n].mask or got[n].val != v}
    assert not wrong, f"{engine}: (got, expected) {wrong}"


@pytest.mark.parametrize("engine", ENGINES)
def test_concat_target_carry_with_x(engine):
    x = {**_CASE, "a": Value(0, width=8, mask=1), "wa": Value(0, width=70, mask=1)}
    got = _run(engine, x)
    for name in ("n_c", "n_s", "p_c", "t_c", "t_s", "w_c", "w_s", "v_c"):
        assert got[name].mask, f"{engine}: {name} = {got[name]} (x operand)"


@pytest.mark.parametrize("engine", ENGINES)
def test_engines_agree_on_random_values(engine):
    rng = random.Random(7)
    for _ in range(4):
        case = _defined(**{n: rng.getrandbits(w) for n, w in _WIDTHS.items() if n != "clk"})
        assert _run(engine, case) == _run("reference", case)
