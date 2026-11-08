"""Operator assignments (`+=`, `|=`, `<<=` ...) and `x++`/`++x` statements.

Statement-level operator assignments dropped their operator while the model
was built, so `cnt += v[i]` ran as `cnt = v[i]` in every engine: a popcount
loop returned its last bit (2026-11-07, found from an axis_pix_correction2
popcount that misbehaved). Only `for` steps applied the operator. `x++;`
and `++x;` statements did not parse at all.
"""

from __future__ import annotations

import warnings

import pytest

from veriforge.model.expressions import BinaryOp
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from .engines import ENGINES

_BODY = """
  always_comb begin
    c_add = 0; for (int i = 0; i < 16; i++) c_add += v[i];
    c_inc = 0; for (int i = 0; i < 16; i++) if (v[i]) c_inc++;
    c_pre = 0; for (int i = 0; i < 16; i++) if (!v[i]) ++c_pre;
    r_sub = 8'd100; r_sub -= v[7:0];
    r_mul = 8'd7; r_mul *= 8'd3;
    r_div = 8'd200; r_div /= 8'd7;
    r_mod = 8'd200; r_mod %= 8'd7;
    r_and = 8'hF0; r_and &= v[7:0];
    r_or = 8'h0F; r_or |= v[15:8];
    r_xor = 8'hAA; r_xor ^= v[7:0];
    r_shl = 8'd3; r_shl <<= 2;
    r_shr = 8'h80; r_shr >>= 3;
    r_ashr = -8'sd64; r_ashr >>>= 2;
    r_dec = 8'd0; r_dec--; --r_dec;
    wd = {36'd0, v, v, v, v}; wd += 100'd1; wd <<= 4;
  end
"""
_SRC = f"""module t(input logic [15:0] v, output logic [4:0] c_add, output logic [4:0] c_inc,
         output logic [4:0] c_pre, output logic [7:0] r_sub, output logic [7:0] r_mul,
         output logic [7:0] r_div, output logic [7:0] r_mod, output logic [7:0] r_and,
         output logic [7:0] r_or, output logic [7:0] r_xor, output logic [7:0] r_shl,
         output logic [7:0] r_shr, output logic signed [7:0] r_ashr, output logic [7:0] r_dec,
         output logic [99:0] wd);
{_BODY}
endmodule
"""


def _expected(v: int) -> dict[str, int]:
    ones = bin(v).count("1")
    lo, hi = v & 0xFF, v >> 8
    quad = (v << 48) | (v << 32) | (v << 16) | v
    return {
        "c_add": ones,
        "c_inc": ones,
        "c_pre": 16 - ones,
        "r_sub": (100 - lo) & 0xFF,
        "r_mul": 21,
        "r_div": 200 // 7,
        "r_mod": 200 % 7,
        "r_and": 0xF0 & lo,
        "r_or": 0x0F | hi,
        "r_xor": 0xAA ^ lo,
        "r_shl": 12,
        "r_shr": 0x10,
        "r_ashr": (-16) & 0xFF,
        "r_dec": 254,
        "wd": ((quad + 1) << 4) & ((1 << 100) - 1),
    }


@pytest.mark.parametrize("engine", ENGINES)
def test_operator_assignments(parser, engine):
    sim = Simulator(tree_to_design(parser.build_tree(_SRC)).modules[0], engine=engine)
    for v in (0xFFFF, 0x00F3, 0x8001, 0):
        sim.drive("v", Value(v, width=16))
        sim.settle()
        for name, want in _expected(v).items():
            got = sim.read(name)
            assert got.mask == 0 and int(got) == want, f"{engine} v={v:#06x} {name}: {got!r} != {want}"


def test_operator_assignment_model(parser):
    module = tree_to_design(
        parser.build_tree(
            "module t(input [3:0] a, output reg [3:0] q); always @* begin q = a; q <<= 1; q++; end endmodule"
        )
    ).modules[0]
    _first, shift, increment = module.always_blocks[0].body.statements
    assert isinstance(shift.rhs, BinaryOp) and shift.rhs.op == "<<"
    assert isinstance(increment.rhs, BinaryOp) and increment.rhs.op == "+"


def test_intra_assignment_timing_warns(parser):
    """`b = #5 a` and `q <= #1 d` have no model representation: the
    assignment happens immediately. Say so instead of dropping it silently."""
    src = (
        "module t(input c, input d, output reg q, output reg b);\n"
        "  always @(posedge c) q <= #1 d;\n"
        "  initial b = #5 d;\n"
        "  always @(posedge c) b <= d;\n"
        "endmodule\n"
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        tree_to_design(parser.build_tree(src))
    messages = [str(w.message) for w in caught if "intra-assignment" in str(w.message)]
    assert len(messages) == 2
    assert "'#1'" in messages[0] and "'#5'" in messages[1]
