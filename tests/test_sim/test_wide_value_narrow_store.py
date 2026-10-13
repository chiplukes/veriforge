"""Narrow stores of wide (>64-bit) values, and x/z bit writes.

* vm-fast kept a wide stack value's low word only in its wide-word arrays
  after a wide RESIZE/TERNARY, and the memory and bit store opcodes read the
  stale narrow slot: `m[2] <= c ? {3'b101, 63'h...} : 66'd0` stored 0.
* `r[i] = 1'bx` (a blocking bit write) stored 0 on reference, vm and vm-fast
  -- `Value.set_bit` took no mask -- and on every engine in a block run by
  the reference executor (an initial block with delays). NBA and range writes
  were right. Expected values from Icarus.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_WIDE = "c ? {3'b101, 63'h300559734b3f2b79} : 66'd0"


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize(
    ("stmt", "expected"),
    [
        (f"m[2] <= {_WIDE};", "8'b01111001"),
        (f"m[2] = {_WIDE};", "8'b01111001"),
        (f"m[2][3:0] <= {_WIDE};", "8'bxxxx1001"),
        (f"m[2][3:0] = {_WIDE};", "8'bxxxx1001"),
        (f"m[2][1] <= {_WIDE};", "8'bxxxxxx1x"),
        (f"r[0] <= {_WIDE};", "8'bxxxxxxx1"),
        (f"r[0] = {_WIDE};", "8'bxxxxxxx1"),
    ],
)
def test_wide_value_into_narrow_store(engine, stmt, expected):
    source = f"""module t(input clk, input c, output [7:0] o, output [7:0] q);
  reg [7:0] m [0:3];
  reg [7:0] r;
  assign o = m[2];
  assign q = r;
  always @(posedge clk) begin {stmt} end
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.drive("c", 1)
    sim.drive("clk", 0)
    sim.settle()
    sim.drive("clk", 1)
    sim.settle()
    assert str(sim.read("q" if stmt.startswith("r") else "o")) == expected


_XBIT = """module t;
  reg [7:0] a, b, c, d, e, f;
  reg [99:0] w;
  reg [7:0] m [0:1];
  reg [2:0] i;
  reg x1;
  initial begin
    i = 2; x1 = 1'bx;
    a = 8'h0f; a[i] = x1;
    b = 8'h0f; b[2] = x1;
    c = 8'h0f; c[i] <= x1;
    d = 8'h0f; d[3:2] = {x1, 1'b0};
    e = 8'h0f; e[i +: 2] = {x1, x1};
    w = 0; w[i + 70] = x1;
    m[1] = 8'h0f; m[1][i] = x1;
    f = 8'h0f; f[i] <= c[i] ? 1'b1 : x1;
    #1 $display("%b %b %b %b %b %h %b %b", a, b, c, d, e, w[73:70], m[1], f);
  end
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_x_bit_writes(engine):
    design = tree_to_design(verilog_parser(start="source_text").build_tree(_XBIT))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.run(max_time=5)
    assert sim.display_output == ["00001x11 00001x11 00001x11 0000x011 0000xx11 X 00001x11 00001111"]


@pytest.mark.parametrize("engine", ENGINES)
def test_x_bit_writes_in_clocked_block(engine):
    source = """module t(input clk, input c, input [2:0] i, output [7:0] q, output [99:0] w, output [7:0] q3);
  reg [7:0] r; reg [99:0] rw; reg [7:0] m [0:1];
  assign q = r; assign w = rw; assign q3 = m[1];
  always @(posedge clk) begin
    r = 8'h0f; r[i] = c ? 1'bx : 1'b1;
    rw = 100'h0; rw[i + 70] = c ? 1'bx : 1'b1;
    m[1] = 8'h0f; m[1][i] = c ? 1'bx : 1'b1;
  end
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    sim = Simulator(design.modules[0], engine=engine, design=design)
    sim.drive("c", 1)
    sim.drive("i", 2)
    sim.drive("clk", 0)
    sim.settle()
    sim.drive("clk", 1)
    sim.settle()
    assert str(sim.read("q")) == "8'b00001x11"
    assert sim.read("w").mask == 1 << 72
    assert str(sim.read("q3")) == "8'b00001x11"


@pytest.mark.parametrize("engine", ENGINES)
def test_bit_assign_x_to_zero_propagates(engine):
    # Once x bit writes were kept, `assign Z[3] = A[3]` going x -> 0 wasn't
    # seen as a change on reference (the bit read for change detection had
    # no mask), so `z = dut.Z` downstream stayed x.
    from veriforge.analysis.resolver import link_instances, resolve_port_connections  # noqa: PLC0415

    source = """module g(input logic [3:0] A, output logic [3:0] Z);
  assign Z[3] = A[3];
  assign Z[2] = A[3] ^ A[2];
  assign Z[1] = A[2] ^ A[1];
  assign Z[0] = A[1] ^ A[0];
endmodule
module tb; logic [3:0] a; logic [3:0] z;
  g dut(.A(a), .Z(z));
  initial begin a = 4'b0000; #1 $display("%b", z); a = 4'b0011; #1 $display("%b", z); end
endmodule
"""
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    link_instances(design)
    resolve_port_connections(design)
    top = next(m for m in design.modules if m.name == "tb")
    sim = Simulator(top, engine=engine, design=design)
    sim.run(max_time=5)
    assert sim.display_output == ["0000", "0010"]
