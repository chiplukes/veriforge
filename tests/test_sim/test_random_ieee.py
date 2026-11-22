"""`$random` gives IEEE 1364-2005 17.9.2's sequence on every engine (fixed
2026-11-22, found by sv-tests' 20.15 run as a corpus).

Each engine read Python's (or C's) own generator, so each gave a different
value, a new one on every run, and `$random(seed)` never updated `seed`.
`sv_functions.lower_random` now hoists each call into `$__vf_random(seed)` /
`$__vf_random_seed(seed)` (`semantics.ieee_random`). The expected values are
Icarus 14's.
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.semantics import ieee_random
from veriforge.sim.testbench import Simulator

from .engines import ENGINES

_SRC = """\
module t(input clk, output logic [31:0] q, output logic [69:0] w);
  integer s, z, u;
  initial begin
    $display("%0d %0d %0d", $random, $random, $random);
    s = 5;
    $display("%0d %0d %0d s=%0d", $random(s), $random(s), $random(s), s);
    $display("%0d u=%0d", $random(u), u);
    z = 7;
  end
  always_ff @(posedge clk) begin q <= $random(z); w <= $random; end
endmodule
"""


def test_ieee_random_matches_icarus():
    seed, values = 0, []
    for _ in range(3):
        value, seed = ieee_random(seed)
        values.append(value)
    assert values == [303379748, -1064739199, -2071669239]
    assert ieee_random(5) == (-2147138048, ieee_random(5)[1])


@pytest.mark.parametrize("engine", ENGINES)
def test_random_sequence_and_seed_updates(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    assert sim.display_output == [
        "303379748 -1064739199 -2071669239",
        "-2147138048 230383387 -547878722 s=1599604512",
        "303379748 u=-1844104698",  # (an x seed reads 0)
    ]
    got = []
    for _ in range(2):
        sim.drive("clk", 0)
        sim.settle()
        sim.drive("clk", 1)
        sim.settle()
        w = sim.read("w")
        got.append((int(sim.read("q")), w.val, w.mask))
    # (A negative value sign-extends into the 70-bit `w`.)
    assert got == [(2147967488, 0x3FFFFFFFFFB1F05663, 0), (1181502348, 0x6B97B0D, 0)]
