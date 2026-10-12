"""Constant folding of `/`, `%`, `**` and `<<` in parameters and generate
loops (values confirmed with Icarus).

* `/` and `%` used Python's flooring `//`/`%`: `-7 / 2` folded to -4 and
  `-7 % 2` to 1 (Verilog truncates toward zero: -3 and -1).
* `**` with a negative exponent gave a Python float in elaboration's folder.
* `3 ** 2**33` / `1 << 2**33` built the full number and hung.
"""

from __future__ import annotations

import pytest

from veriforge.semantics import const_div, const_mod, const_pow, const_shl
from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_SOURCE = """module t(output signed [15:0] p, output signed [15:0] q, output signed [15:0] s,
         output [15:0] r, output [15:0] h, output [15:0] k, output [7:0] g);
  localparam integer P = -7 / 2;
  localparam integer Q = -7 % 2;
  localparam integer S = 7 / -2;
  localparam integer R = (-1) ** -3;
  localparam [15:0] H = 3 ** 40'd8589934592;
  localparam [15:0] K = 1 << 40'd8589934592;
  genvar i;
  generate for (i = -7; i < -7 / 2 + 1; i = i + 3) begin : gb
    assign g[i + 7] = 1'b1;
  end endgenerate
  assign g[7:4] = 4'b0;
  assign g[2:1] = 2'b0;
  assign p = P; assign q = Q; assign s = S; assign r = R; assign h = H; assign k = K;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_constant_folding(engine):
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_SOURCE))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.settle()
    got = {n: sim.read(n) for n in ("p", "q", "s", "r", "h", "k", "g")}
    assert [got[n].as_signed() for n in ("p", "q", "s", "r")] == [-3, -1, -3, -1]
    assert int(got["h"]) == 1
    assert int(got["k"]) == 0
    # i = -7, -4: bits 0 and 3 (the loop stops at -1, as -7 / 2 + 1 == -2)
    assert str(got["g"]) == "8'b00001001"


def test_helpers():
    assert [const_div(-7, 2), const_div(7, -2), const_div(-7, -2), const_div(7, 0)] == [-3, -3, 3, None]
    assert [const_mod(-7, 2), const_mod(7, -2), const_mod(7, 0)] == [-1, 1, None]
    assert [const_pow(2, -1), const_pow(1, -5), const_pow(-1, -3), const_pow(-1, -4), const_pow(0, -1)] == [
        0,
        1,
        -1,
        1,
        None,
    ]
    assert const_pow(3, 2**33) % (1 << 16) == pow(3, 2**33, 1 << 16)
    assert const_pow(-3, 5) == -243
    assert const_shl(1, 2**33) == 0
    assert const_shl(1, -1) is None
