"""A 64-bit-wide memory in a design with an `initial` block.

vm-fast crashed at start-up ("can't convert negative value to unsigned
PY_LONG_LONG"): running an initial block syncs memory words to Python lists
and back, the lists hold them as signed `long long`, and an all-x (or
all-ones) 64-bit word came back negative. Found by the fuzzer once its
harness started running initial blocks; reduced by veriforge.reduce to
`real rm [0:3]; initial begin end`.
"""

from __future__ import annotations

import pytest

from veriforge.reduce import parse_text
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value

from .engines import ENGINES

_SRC = """\
module t(input clk, input [1:0] a, input [63:0] d, output [63:0] y, output [63:0] z, output real r);
  reg [63:0] m [0:3];
  real rm [0:3];
  integer k;
  initial begin
    m[0] = 64'hffff_ffff_ffff_ffff;
    m[1] = 64'h8000_0000_0000_0001;
    rm[0] = -1.5;
    k = 1;
  end
  always @(posedge clk) m[a] <= d;
  assign y = m[a];
  assign z = m[0] ^ m[1];
  assign r = rm[0];
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_memory64_with_initial_block(engine):
    design = parse_text(_SRC)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    for name, width in (("clk", 1), ("a", 2), ("d", 64)):
        sim.drive(name, Value(0, width=width))
    sim.run(max_time=0)
    sim.settle()
    assert sim.read("y") == Value(0xFFFF_FFFF_FFFF_FFFF, width=64)
    assert sim.read("z") == Value(0x7FFF_FFFF_FFFF_FFFE, width=64)
    assert sim.read_real("r") == -1.5
    sim.drive("a", Value(2, width=2))
    sim.settle()
    assert sim.read("y").mask == (1 << 64) - 1  # never written: x
    sim.drive("d", Value(0x8000_0000_0000_0000, width=64))
    sim.drive("clk", Value(1))
    sim.settle()
    assert sim.read("y") == Value(0x8000_0000_0000_0000, width=64)


@pytest.mark.parametrize("engine", ENGINES)
def test_drive_2d_packed_port_with_64bit_elements(engine):
    # A 2-D packed input port is a memory; driving it with a 64-bit element
    # whose top bit is set raised OverflowError on vm-fast (`write_mem` took a
    # signed `long long`): ibex_if_stage, found by tools/corpus_check.py.
    src = """module t(input logic [3:0][63:0] p, input [1:0] i, output [63:0] y, output [63:0] y3);
  assign y = p[i];
  assign y3 = p[3];
endmodule
"""
    design = parse_text(src)
    sim = Simulator(design.get_module("t"), engine=engine, design=design)
    sim.run(max_time=0)
    sim.drive("p", Value(((1 << 256) - 1) ^ (0x5 << 192), width=256))
    sim.drive("i", Value(3, width=2))
    sim.settle()
    assert sim.read("y") == Value(0xFFFF_FFFF_FFFF_FFFA, width=64)
    sim.drive("i", Value(0, width=2))
    sim.settle()
    assert sim.read("y") == Value((1 << 64) - 1, width=64)
    assert sim.read("y3") == Value(0xFFFF_FFFF_FFFF_FFFA, width=64)
