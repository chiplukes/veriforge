"""An `always @*` block and its own writes (IEEE 1364-2005 9.7.5).

The block is sensitive to every signal it reads -- including one it also
writes: another process changing that wakes it -- but its own change to one
doesn't re-trigger it (it isn't waiting at the event control while it runs).
The engines disagreed: reference/vm/compiled left written signals out of
the sensitivity entirely (another process's change was missed), vm/vm-fast
re-ran every combinational block to a fixpoint at start-up, and a first
`settle()` ran a block once for the bootstrap and again for a drive it
read. Expected values from Icarus.
"""

from __future__ import annotations

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES


def _sim(source: str, engine: str) -> Simulator:
    design = tree_to_design(verilog_parser(start="source_text").build_tree(source))
    return Simulator(design.modules[-1], engine=engine, design=design)


@pytest.mark.parametrize("engine", ENGINES)
def test_own_write_vs_another_process(engine):
    source = """module t;
  reg [7:0] a, s, y, b, s2, y2;
  always @* begin y = s + a; s = a; end
  always @* begin y2 = s2 + b; s2 = b; end
  initial begin
    a = 1; b = 5;
    #1 $display("%0d %0d %0d %0d", y, s, y2, s2);
    a = 2;
    #1 $display("%0d %0d", y, s);
    s = 9;
    #1 $display("%0d %0d", y, s);
    a = 3;
    #1 $display("%0d %0d", y, s);
  end
endmodule
"""
    sim = _sim(source, engine)
    sim.run(max_time=10)
    # (Line 3: the initial block's `s = 9` wakes the block -- it reads s.)
    assert sim.display_output == ["x 1 x 5", "3 2", "11 2", "5 3"]


@pytest.mark.parametrize("engine", ENGINES)
def test_one_activation_per_change_on_settle(engine):
    """Fuzz seed 11136's shape: the first evaluation sees `n8` as x and
    skips the `if`; `n8 = 0` mustn't re-run the block (it used to, through
    the bootstrap-then-drive double run on reference/vm)."""
    source = """module t #(parameter [23:0] p2 = 24'd6669759) (input [7:0] i3, output reg [104:0] w6);
  reg [7:0] n8;
  always @(*) begin if (n8[3] ? w6 : {i3, p2}) w6 = 1; n8 = 0; end
endmodule
"""
    sim = _sim(source, engine)
    sim.drive("i3", Value(0, width=8))
    sim.settle()
    assert sim.read("w6").mask == (1 << 105) - 1
    assert sim.read("n8") == Value(0, width=8)
    sim.drive("i3", Value(1, width=8))
    sim.settle()
    assert sim.read("w6") == Value(1, width=105)


@pytest.mark.parametrize("engine", ENGINES)
def test_temporaries_and_chains_still_settle(engine):
    """A temporary (written then read) and a block reading another block's
    output (declared after it) settle on the first settle()."""
    source = """module t(input [7:0] a, input [7:0] b, output reg [7:0] y, output reg [7:0] z);
  reg [7:0] tmp; integer i; reg [7:0] acc;
  always @* begin z = y + 1; end
  always @* begin tmp = a & b; acc = 0; for (i = 0; i < 4; i = i + 1) acc = acc + tmp; y = acc; end
endmodule
"""
    sim = _sim(source, engine)
    sim.drive("a", Value(0x0F, width=8))
    sim.drive("b", Value(0x3C, width=8))
    sim.settle()
    assert (sim.read("y"), sim.read("z")) == (Value(0x30, width=8), Value(0x31, width=8))
    sim.drive("b", Value(0xFF, width=8))
    sim.settle()
    assert (sim.read("y"), sim.read("z")) == (Value(0x3C, width=8), Value(0x3D, width=8))
