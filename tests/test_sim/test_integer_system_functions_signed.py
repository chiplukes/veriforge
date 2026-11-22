"""`$clog2`, `$bits`, `$size`, `$countones`, ... return a signed 32-bit type
(IEEE 1800 20.6-20.9), and a select with a negative signed bound selects
partly out of range (fixed 2026-11-21).

Every engine read these functions as unsigned. `serv_rf_ram` checked as a top
with its default `width = 0` selects `i_raddr[$clog2(depth)-1:5-$clog2(width)]`
with `depth = 0`: reference and vm took the `-1` bound as about 2**32 and
allocated until the OOM killer took WSL down. vm also compiled a range
select's bounds unsigned, and reference and vm dropped a partly out-of-range
range write instead of writing its in-range bits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from veriforge import parse_file
from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import MAX_SELECT_BITS, Value

from .engines import ENGINES

_ROOT = Path(__file__).resolve().parents[2]

_SIGNED = """\
module t #(parameter P = 0) (input [7:0] a, output [7:0] y);
  logic [3:0] m [4];
  assign y[0] = ($clog2(P) - 1) < 0;
  assign y[1] = ($bits(a) - 9) < 0;
  assign y[2] = ($countones(a) - 1) < 0;
  assign y[3] = ($size(m) - 5) < 0;
  assign y[4] = ($high(a) - 8) < 0;
  assign y[5] = ($countbits(a, 1'b1) - 1) < 0;
  assign y[6] = ($clog2(a) - 1) < 0;
  assign y[7] = ($left(m) - 1) < 0;
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_integer_system_functions_are_signed(engine):
    design = parse_text(_SIGNED)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.drive("a", 0)
    sim.settle()
    assert str(sim.read("y")) == "8'b11111111"


_BOUNDS = """\
module t #(parameter P = 1) (input clk, input [7:0] a, input [99:0] b, output [2:0] y, output [2:0] z,
    output logic [7:0] r, output logic [99:0] q, output logic [7:0] n, output [7:0] mo, output [99:0] wo);
  logic [7:0] m [2];
  logic [99:0] w [2];
  assign y = a[$clog2(P)+1 : $clog2(P)-1];
  assign z = b[$clog2(P)+1 : $clog2(P)-1];
  always_comb begin
    r = 8'hff;
    r[$clog2(P)+1 : $clog2(P)-1] = 3'b010;
    q = '1;
    q[$clog2(P)+1 : $clog2(P)-1] = 3'b010;
  end
  always_ff @(posedge clk) begin
    n <= 8'hff;
    n[$clog2(P)+1 : $clog2(P)-1] <= 3'b010;
    m[1] <= 8'hff;
    m[1][$clog2(P)+1 : $clog2(P)-1] <= 3'b010;
    w[1] <= '1;
    w[1][$clog2(P)+1 : $clog2(P)-1] <= 3'b010;
  end
  assign mo = m[1];
  assign wo = w[1];
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_negative_signed_range_bounds(engine):
    """`[1:-1]`: bit -1 reads x, and a write sets bits 1 and 0 only."""
    design = parse_text(_BOUNDS)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.drive("a", 0b101)
    sim.drive("b", (1 << 99) | 0b101)
    for level in (0, 1, 0):
        sim.drive("clk", level)
        sim.settle()
    assert str(sim.read("y")) == "3'b01x"
    assert str(sim.read("z")) == "3'b01x"
    all_but_bit1 = ((1 << 100) - 1) & ~0b10
    for name, want in (("r", 0xFD), ("n", 0xFD), ("mo", 0xFD), ("q", all_but_bit1), ("wo", all_but_bit1)):
        got = sim.read(name)
        assert (name, got.val, got.mask) == (name, want, 0)


@pytest.mark.parametrize("engine", ENGINES)
def test_serv_rf_ram_with_invalid_default_width_settles(engine):
    design = parse_file(_ROOT / "examples/serv/rtl/serv_rf_ram.v", preprocess=True)
    sim = Simulator(design.get_module("serv_rf_ram"), engine=engine, design=design)
    for level in (0, 1, 0, 1):
        sim.drive("i_clk", level)
        sim.settle()
    assert sim.read("o_rdata").mask == 0b11


def test_a_huge_select_raises_instead_of_allocating():
    with pytest.raises(ValueError, match="bits wide"):
        Value(0, width=8)[MAX_SELECT_BITS + 7 : 0]


@pytest.mark.parametrize("engine", ENGINES)
def test_display_of_integer_system_functions_matches_icarus(engine):
    """Icarus 14 prints `[          8] [          3] [         -1]` (signed, 11 columns)."""
    src = """\
module t;
  logic [7:0] a;
  initial $display("[%d] [%d] [%d]", $bits(a), $clog2(8), $clog2(0) - 1);
endmodule
"""
    design = parse_text(src)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=10)
    assert sim.display_output == ["[          8] [          3] [         -1]"]
