"""Unpacked-array slices and assignment patterns on every engine (fixed
2026-11-22, found by sv-tests' 7.4.3 run as a corpus).

Every engine read `b[5:3] = a[2:0]` as a bit range (reference and compiled
copied one element, vm none), filled a descending array (`[2:0]`) from the
wrong end, set only element 0 of a 1-bit array, and (reference, compiled) of a
`default:` pattern; a declaration's initializer (`logic [7:0] m [3] = '{...}`)
did not parse. `sv_functions.lower_array_slices` expands each into element
assignments, left index to left index. The expected values are Icarus 14's
where it supports the form.
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_SRC = """\
module top(input clk, output [7:0] b8, output [23:0] d24, output [2:0] bits, output [23:0] f24, output [23:0] k24,
    output [7:0] c8, output [69:0] wv, output [23:0] i24, output [23:0] j24);
  bit arr_a [7:0];
  bit arr_b [7:0];
  logic [7:0] d [2:0];
  bit b [0:2];
  logic [7:0] f [0:2];
  logic [7:0] k [0:2];
  logic [69:0] wa [0:3];
  logic [69:0] wb [0:3];
  logic [7:0] ca [3];
  logic [7:0] cb [0:2];
  logic [7:0] i [2:0] = '{8'h11, 8'h22, 8'h33};
  logic [7:0] j [0:2] = '{default: 8'h5a};
  initial begin
    arr_a = '{1, 1, 1, 1, 1, 1, 1, 1};
    arr_b = '{0, 0, 0, 0, 0, 0, 0, 0};
    arr_b[5:3] = arr_a[2:0];
    d = '{8'h11, 8'h22, 8'h33};
    b = '{1, 1, 1};
    f = '{default: 8'h5a};
    k = '{1: 8'h77, default: 8'h01};
    wa[0] = 70'h3f_0000_0000_0000_0001;
    wa[1] = 70'h2;
    wb = '{default: '0};
    wb[2:3] = wa[0:1];
    ca = '{8'h11, 8'h22, 8'h33};
  end
  assign cb[0:1] = ca[1:2];
  assign cb[2] = 8'h44;
  always_ff @(posedge clk) j[2] <= j[2] + 1;
  assign b8 = {arr_b[7], arr_b[6], arr_b[5], arr_b[4], arr_b[3], arr_b[2], arr_b[1], arr_b[0]};
  assign d24 = {d[2], d[1], d[0]};
  assign bits = {b[0], b[1], b[2]};
  assign f24 = {f[0], f[1], f[2]};
  assign k24 = {k[0], k[1], k[2]};
  assign c8 = cb[0] ^ cb[1];
  assign wv = wb[2] | wb[3];
  assign i24 = {i[2], i[1], i[0]};
  assign j24 = {j[0], j[1], j[2]};
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_array_slices_and_patterns(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("top"), engine=engine, design=design)
    sim.run(max_time=0)
    sim.drive("clk", 0)
    sim.settle()
    sim.drive("clk", 1)
    sim.settle()
    got = {n: int(sim.read(n)) for n in ("b8", "d24", "bits", "f24", "k24", "c8", "i24", "j24")}
    assert got == {
        "b8": 0b0011_1000,
        "d24": 0x112233,  # (d[2] = 8'h11: the left index takes the first item)
        "bits": 0b111,
        "f24": 0x5A5A5A,
        "k24": 0x017701,
        "c8": 0x22 ^ 0x33,
        "i24": 0x112233,
        "j24": 0x5A5A5B,
    }
    wv = sim.read("wv")
    assert (wv.val, wv.mask) == ((0x3F << 64) | 3, 0)
