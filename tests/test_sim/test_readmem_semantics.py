"""``$readmemh`` semantics (IEEE 1364-2005 17.2.8; expected output from Icarus).

Every engine raised ValueError on an x/z digit or a ``/* */`` comment,
ignored the start/end address arguments, and took ``@address`` lines as
slot numbers (wrong for ``reg m [1:4]``). See sim/readmem.py.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_FILES = {
    "plain.hex": "11 22 33 44\n",
    "at.hex": "@2\n77 88\n",
    "xz.hex": "11 2x // c\n/* block\n 99 */ 3_3 zz\n",
    "ext.hex": "x 1 z1 x_1\n",
    "ord.hex": "@1\nAA\n@0\nBB CC DD EE FF\n",
    "n.hex": "a1 b2 c3\n",
    "w.hex": "123456789abcdef0123456789 x0000000000000000000000001\n",
}

_SOURCE = """module t;
  reg [7:0] up [0:3];
  reg [7:0] dn [3:0];
  reg [7:0] b1 [1:4];
  reg [7:0] b2 [4:1];
  reg [7:0] se [0:7];
  reg [7:0] rv [0:7];
  reg [7:0] at1 [1:4];
  reg [7:0] xz [0:3];
  reg [7:0] e [0:3];
  reg [7:0] o [0:3];
  reg [7:0] s [0:7];
  reg [7:0] f [-2:1];
  reg [99:0] w [0:1];
  initial begin
    $readmemh("D/plain.hex", up);
    $readmemh("D/plain.hex", dn);
    $readmemh("D/plain.hex", b1);
    $readmemh("D/plain.hex", b2);
    $readmemh("D/plain.hex", se, 2, 4);
    $readmemh("D/plain.hex", rv, 5, 2);
    $readmemh("D/at.hex", at1);
    $readmemh("D/xz.hex", xz);
    $readmemh("D/ext.hex", e);
    $readmemh("D/ord.hex", o);
    $readmemh("D/plain.hex", s, 6);
    $readmemh("D/n.hex", f, -1, 1);
    $readmemh("D/w.hex", w);
    $display("up %h %h %h %h", up[0], up[1], up[2], up[3]);
    $display("dn %h %h %h %h", dn[0], dn[1], dn[2], dn[3]);
    $display("b1 %h %h %h %h", b1[1], b1[2], b1[3], b1[4]);
    $display("b2 %h %h %h %h", b2[1], b2[2], b2[3], b2[4]);
    $display("se %h %h %h %h %h %h", se[1], se[2], se[3], se[4], se[5], se[6]);
    $display("rv %h %h %h %h %h %h", rv[1], rv[2], rv[3], rv[4], rv[5], rv[6]);
    $display("at %h %h %h %h", at1[1], at1[2], at1[3], at1[4]);
    $display("xz %h %h %h %h", xz[0], xz[1], xz[2], xz[3]);
    $display("%h %h %h %h | %h %h %h %h | %h %h %h", e[0], e[1], e[2], e[3], o[0], o[1], o[2], o[3], s[5], s[6], s[7]);
    $display("%h %h %h %h | %h %h", f[-2], f[-1], f[0], f[1], w[0], w[1]);
  end
endmodule
"""

_EXPECTED = [
    "up 11 22 33 44",
    "dn 11 22 33 44",  # the lowest address first, whatever the direction
    "b1 11 22 33 44",
    "b2 11 22 33 44",
    "se xx 11 22 33 xx xx",
    "rv xx 44 33 22 11 xx",  # start > end loads downward
    "at xx 77 88 xx",  # @2 is address 2, not slot 2
    "xz 11 2x 33 xx",  # (Icarus: zz -- x and z share one state here)
    "0x 01 x1 x1 | bb cc dd ee | xx 11 22",  # (Icarus: z1)
    "xx a1 b2 c3 | 123456789abcdef0123456789 0000000000000000000000001",
]


@pytest.mark.parametrize("engine", ENGINES)
def test_readmemh(engine, tmp_path):
    for name, text in _FILES.items():
        (tmp_path / name).write_text(text)
    source = _SOURCE.replace('"D/', f'"{tmp_path}/')
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=10)
    assert sim.display_output == _EXPECTED
