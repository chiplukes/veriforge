"""Assignments between sub-arrays of multi-dimensional unpacked arrays
(2026-10-16; expected values from Verilator 5.046, as Icarus doesn't support
array slices).

`m[0] = m[1]` on `reg m [0:1][0:2]` used to raise NotImplementedError. It is
now expanded into one assignment per element (`sim/ascending.py`,
`_expand_sub_array_assigns`). This covers blocking, nonblocking and
continuous assignments, and a whole one-dimensional array of the same shape
on either side. Elements pair up left to right, so `[1:0]` against `[0:1]`
reverses them.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _display(source: str, engine: str) -> list[str]:
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[-1], engine=engine, design=design)
    sim.run(max_time=20)
    return sim.display_output


@pytest.mark.parametrize("engine", ENGINES)
def test_sub_array_assignments(engine):
    source = """module t;
  reg [7:0] m [0:1][0:2];
  reg [7:0] n [1:0][2:0];
  reg [7:0] f [0:2];
  reg [7:0] g [2:0];
  reg [7:0] z [0:2][0:1][0:1];
  reg [7:0] c [0:1][0:2];
  wire [7:0] w [0:1][0:2];
  integer i, j, k;
  assign w[1] = m[0];
  assign w[0] = g;
  always @(posedge c[0][0][0]) c[1] <= m[1];
  initial begin
    for (i = 0; i < 2; i = i + 1) for (j = 0; j < 3; j = j + 1) begin m[i][j] = 8'h10 * i + j; n[i][j] = 8'h40 + 8'h10 * i + j; end
    for (i = 0; i < 3; i = i + 1) begin f[i] = 8'h80 + i; g[i] = 8'h90 + i; end
    for (i = 0; i < 3; i = i + 1) for (j = 0; j < 2; j = j + 1) for (k = 0; k < 2; k = k + 1) z[i][j][k] = 8'h20 * i + 8'h4 * j + k;
    c[0][0] = 0;
    #1 m[1] = m[0];
    $display("A %h %h %h", m[1][0], m[1][1], m[1][2]);
    m[0] = n[1];
    $display("B %h %h %h", m[0][0], m[0][1], m[0][2]);
    f = m[1]; $display("C %h %h %h", f[0], f[1], f[2]);
    m[1] = g; $display("D %h %h %h", m[1][0], m[1][1], m[1][2]);
    i = 1; m[i] <= n[i-1]; #1 $display("E %h %h %h", m[1][0], m[1][1], m[1][2]);
    z[2] = z[0]; z[1][1] = z[2][0];
    $display("F %h %h %h %h %h %h", z[2][0][0], z[2][0][1], z[2][1][0], z[2][1][1], z[1][1][0], z[1][1][1]);
    $display("G %h %h %h %h %h %h", w[1][0], w[1][1], w[1][2], w[0][0], w[0][1], w[0][2]);
    c[0][0] = 1; #1 $display("H %h %h %h", c[1][0], c[1][1], c[1][2]);
  end
endmodule
"""
    assert _display(source, engine) == [
        "A 00 01 02",
        "B 52 51 50",
        "C 00 01 02",
        "D 92 91 90",
        "E 42 41 40",
        "F 00 01 04 05 00 01",
        "G 52 51 50 92 91 90",
        "H 42 41 40",
    ]


@pytest.mark.parametrize("engine", ENGINES)
def test_wide_and_combinational_sub_arrays(engine):
    # >64-bit elements, an out-of-range row index (writes nothing), and an
    # `always @*` reading a sub-array (sensitive to each element).
    source = """module t;
  reg [99:0] q [0:1][0:1];
  reg [7:0] m [0:1][0:1];
  integer i;
  always @* m[1] = m[0];
  initial begin
    q[0][0] = {4'ha, 96'h1}; q[0][1] = {4'h5, 96'h2}; q[1][0] = 0; q[1][1] = 0;
    m[0][0] = 8'h11; m[0][1] = 8'h22;
    #1 q[1] = q[0];
    $display("%h %h", q[1][0], q[1][1]);
    i = 7; q[i] = q[1]; i = 0;
    $display("%h %h %h %h", q[0][0], q[0][1], m[1][0], m[1][1]);
    m[0][1] = 8'h33; #1 $display("%h %h", m[1][0], m[1][1]);
  end
endmodule
"""
    assert _display(source, engine) == [
        "a000000000000000000000001 5000000000000000000000002",
        "a000000000000000000000001 5000000000000000000000002 11 22",
        "11 33",
    ]


def test_differently_shaped_sub_arrays_raise():
    source = """module t;
  reg [7:0] m [0:1][0:2]; reg [7:0] f [0:1];
  initial m[0] = f;
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    with pytest.raises(ValueError, match="differently shaped"):
        Simulator(design.modules[0], engine="reference", design=design)
