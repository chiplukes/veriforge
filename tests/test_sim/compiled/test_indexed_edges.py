"""Sequential subscriber bitmaps preserve wide state and mixed edge triggers."""

import pytest

from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from ..engines import ENGINES


@pytest.mark.parametrize("mode", ["scan", "queue"])
def test_subscribers_across_bitmap_words_match_all_engines(parser, monkeypatch, mode):
    monkeypatch.setenv("VERIFORGE_DELTA_ENGINE", mode)
    source = """module top(input clk_a, clk_b, rst_n, output [129:0] y);
  for (genvar i=0; i<130; i=i+1) begin : g
    reg q;
    if (i % 2 == 0) begin : a
      always @(posedge clk_a or negedge rst_n)
        if (!rst_n) q <= 0; else q <= ~q;
    end else begin : b
      always @(negedge clk_b or negedge rst_n)
        if (!rst_n) q <= 0; else q <= ~q;
    end
    assign y[i] = q;
  end
endmodule"""
    sims = {}
    for engine in ENGINES:
        design = tree_to_design(parser.build_tree(source))
        sims[engine] = Simulator(design.modules[0], engine=engine)
    steps = [
        (0, 0, 1),
        (0, 0, 0),
        (0, 0, 1),
        (1, 1, 1),
        (0, 0, 1),
        (1, 1, 1),
        (Value(0, mask=1, width=1), 0, 1),
        (1, Value(0, mask=1, width=1), 1),
        (0, 0, 0),
    ]
    for step, (clk_a, clk_b, rst_n) in enumerate(steps):
        states = {}
        for engine, sim in sims.items():
            for name, value in (("clk_a", clk_a), ("clk_b", clk_b), ("rst_n", rst_n)):
                sim.drive(name, value)
            sim.settle()
            value = sim.read("y")
            states[engine] = (value.val, value.mask, value.width)
        assert len(set(states.values())) == 1, (step, states)
        if step in (1, 2, 8):
            assert states["reference"] == (0, 0, 130)
