"""An indexed part-select (`v[base +: w]`, `v[base -: w]`) whose base has an x/z bit
reads x, and the bits of a select outside the source read x (IEEE 1364-2005 5.2.1).

Compiled read data instead, as if an x base were 0: a continuous assign from a
wide signal (`wire m = big[sel*W +: W]`) used the base's raw value, and the
whole-signal slice helpers read source words outside the signal as a defined 0
(also behind `always @*`). Found by tools/corpus_check.py in verilog-axi's
crossbar (`s_axi_araddr_mux = int_s_axi_araddr[a_grant_encoded*ADDR_WIDTH +: ADDR_WIDTH]`
with the grant still x).
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
module t #(parameter W = 32) (input clk, input [1:0] sel, input [6:0] b, input [4*W-1:0] big,
    input [255:0] wide, input [7:0] d,
    output [W-1:0] y1, output [W-1:0] y2, output [7:0] y3, output [7:0] y4, output [69:0] y5,
    output [W-1:0] y6, output reg [7:0] p1, output [15:0] o1, output [79:0] o2, output reg [15:0] o3,
    output bsel, output reg [31:0] w1);
  assign y1 = big[sel*W +: W];
  assign y2 = big[sel +: W];
  assign y3 = big[sel*8 -: 8];
  assign y4 = wide[sel*8 +: 8];
  assign y5 = wide[sel*70 +: 70];
  wire [1:0] g = sel;
  assign y6 = big[g*W +: W];
  always @* p1 = big[sel*8 +: 8];
  assign o1 = big[b +: 16];
  assign o2 = big[b +: 80];
  always @* o3 = big[b -: 16];
  assign bsel = big[sel];
  initial w1 = 32'h12345678;
  always @(posedge clk) w1[sel*8 +: 8] <= d;
endmodule
"""

_BIG = 0x4444_3333_2222_1111_0000_FFFF_EEEE_DDDD
_WIDE = (1 << 256) - 12345


def _run(engine: str, sel: Value, b: int) -> dict[str, Value]:
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.drive("clk", Value(0))
    sim.drive("sel", sel)
    sim.drive("b", Value(b, width=7))
    sim.drive("big", Value(_BIG, width=128))
    sim.drive("wide", Value(_WIDE, width=256))
    sim.drive("d", Value(0xAB, width=8))
    sim.run(max_time=0)
    sim.settle()
    sim.drive("clk", Value(1))
    sim.settle()
    names = ("y1", "y2", "y3", "y4", "y5", "y6", "p1", "o1", "o2", "o3", "bsel", "w1")
    return {n: sim.read(n) for n in names}


@pytest.mark.parametrize("mask", [0b11, 0b01, 0b10])
@pytest.mark.parametrize("engine", ENGINES)
def test_x_base_reads_x_and_writes_nothing(engine, mask):
    got = _run(engine, Value(0, width=2, mask=mask), 4)
    for name in ("y1", "y2", "y3", "y4", "y5", "y6", "p1", "bsel"):
        v = got[name]
        assert v.mask == (1 << v.width) - 1, f"{engine} {name} = {v}"
    assert got["w1"] == Value(0x12345678, width=32)  # an x-base write changes nothing


@pytest.mark.parametrize("engine", ENGINES)
def test_defined_and_partly_out_of_range_selects(engine):
    got = _run(engine, Value(1, width=2), 120)
    assert got["y1"] == Value((_BIG >> 32) & 0xFFFF_FFFF, width=32)
    assert got["y2"] == Value((_BIG >> 1) & 0xFFFF_FFFF, width=32)
    assert got["y4"] == Value((_WIDE >> 8) & 0xFF, width=8)
    assert got["y5"] == Value((_WIDE >> 70) & ((1 << 70) - 1), width=70)
    # big[120 +: 16]: bits 128..135 are outside big -> x.
    assert got["o1"].mask == 0xFF00 and (got["o1"].val & 0xFF) == (_BIG >> 120) & 0xFF
    assert got["o2"].mask == ((1 << 80) - 1) & ~0xFF
    assert got["o3"] == Value((_BIG >> 105) & 0xFFFF, width=16)  # big[120 -: 16] = [120:105]
    assert got["w1"] == Value(0x1234AB78, width=32)
