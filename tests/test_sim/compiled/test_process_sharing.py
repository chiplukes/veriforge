"""Shared bodies keep parameter specialization and per-instance state."""

import re

import pytest

from veriforge.sim.compiled.codegen import CythonCodegen
from veriforge.sim.elaborate import flatten_module
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from ..engines import ENGINES

SOURCE = """module leaf #(parameter INC=1)(input clk, rst, en, output reg [7:0] q);
  always @(posedge clk) begin
    if (rst) q <= 0;
    else if (en) q <= q + INC;
  end
endmodule
module top(input clk, rst, input [11:0] en, output [95:0] y);
  for (genvar i=0; i<12; i=i+1) begin : g
    leaf #(.INC(i % 3 + 1)) u(.clk(clk), .rst(rst), .en(en[i]), .q(y[i*8 +: 8]));
  end
endmodule"""


def test_repeated_sequential_bodies_are_specialized_once(parser, monkeypatch, tmp_path):
    monkeypatch.setenv("VERIFORGE_COMPILE_SHARE_PROCESSES", "1")
    design = tree_to_design(parser.build_tree(SOURCE))
    flat = flatten_module(design.get_module("top"), design=design)
    source = CythonCodegen().generate(flat)
    groups = re.findall(r"^cdef inline void seq_\d+[^\n]*:\n\s+_vf_shared_fn_(\d+)", source, re.M)
    assert len(groups) == 12
    assert len(set(groups)) == 3
    path = tmp_path / "generated.pyx"
    CythonCodegen().generate_to_file(flat, str(path))
    assert path.read_text() == source


@pytest.mark.parametrize("split", ["0", "1"])
def test_shared_instance_states_match_other_engines(parser, monkeypatch, split):
    monkeypatch.setenv("VERIFORGE_COMPILE_SHARE_PROCESSES", "1")
    monkeypatch.setenv("VERIFORGE_COMPILE_SPLIT", split)
    sims = {}
    source = SOURCE
    if split == "1":
        source = source.replace("[11:0]", "[63:0]").replace("[95:0]", "[511:0]").replace("i<12", "i<64")
    for engine in ENGINES:
        design = tree_to_design(parser.build_tree(source))
        sims[engine] = Simulator(design.get_module("top"), design=design, engine=engine)
    for cycle in range(10):
        states = {}
        for engine, sim in sims.items():
            sim.drive("clk", 0)
            sim.drive("rst", int(cycle == 0))
            sim.drive("en", (0xABD >> (cycle % 4)) & 0xFFF)
            sim.settle()
            sim.drive("clk", 1)
            sim.settle()
            value = sim.read("y")
            states[engine] = (value.val, value.mask, value.width)
        assert len(set(states.values())) == 1, (cycle, states)


def test_wide_processes_use_safe_fallback(parser, monkeypatch):
    monkeypatch.setenv("VERIFORGE_COMPILE_SHARE_PROCESSES", "1")
    source = """module top(input clk, input [127:0] d, output reg [127:0] q);
  always @(posedge clk) q <= d + 128'd1;
endmodule"""
    design = tree_to_design(parser.build_tree(source))
    generated = CythonCodegen().generate(design.modules[0])
    assert "_vf_shared_impl_" not in generated
    sim = Simulator(design.modules[0], engine="compiled")
    sim.drive("clk", 0)
    sim.drive("d", Value((1 << 100) + (1 << 64) - 1, width=128))
    sim.settle()
    sim.drive("clk", 1)
    sim.settle()
    assert int(sim.read("q")) == (1 << 100) + (1 << 64)
