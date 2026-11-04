"""Indexed continuous writes wake vector readers, including generated fanout."""

from pathlib import Path

import pytest

from veriforge.sim.bench import Testbench
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from .engines import ENGINES


@pytest.mark.parametrize("engine", ENGINES)
def test_reported_generate_partial_assign_fanout(parser, engine):
    source = (Path(__file__).parent / "verilog" / "partial_assign_fanout.sv").read_text()
    design = tree_to_design(parser.build_tree(source))
    bench = Testbench(design.get_module("partial_assign_fanout"), design=design, engine=engine)
    with bench.run():
        for data in (5, 0, 14, 3):
            bench.sim.drive("d", data)
            bench.step(4, domain="clk")
            last = (data + 1) & 15
            for name, expected in {
                "y_last_slice": last,
                "y_whole": (last << 4) | data,
                "y_sub": (last << 4) | data,
                "y_sampled": last,
            }.items():
                value = bench.sim.read(name)
                assert value.is_defined and int(value) == expected, (engine, data, name, value)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [4, 80])
@pytest.mark.parametrize("direction", ["+:", "-:"])
def test_generated_slice_fanout_preserves_masks_and_wide_values(parser, engine, width, direction):
    base = "8 + i*W" if direction == "+:" else "8 + (i+1)*W - 1"
    source = f"""module tap #(parameter W=8)(input [W-1:0] a, output [W-1:0] y);
      assign y = a;
    endmodule
    module top(input clk, input [{width - 1}:0] d,
      output [{3 * width - 1}:0] whole, through_port, output reg [{3 * width - 1}:0] combo,
      output [{width - 1}:0] last, hierarchical);
      localparam W = {width};
      wire [3*W+7:8] vec;
      for (genvar i=0; i<3; i=i+1) begin : g
        reg [W-1:0] q;
        always @(posedge clk) q <= d ^ {width}'d2;
        assign vec[{base} {direction} W] = q;
      end
      assign whole = vec;
      assign last = vec[8+2*W +: W];
      assign hierarchical = g[2].q;
      tap #(.W(3*W)) u(.a(vec), .y(through_port));
      always @* combo = vec;
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.get_module("top"), design=design, engine=engine)
    sim.drive("clk", 0)
    sim.drive("d", 0)
    sim.settle()
    for data, mask in ((0, 0), (1 << (width - 1), 0), (0, 1 << (width - 1)), (0, 0), (5, 1)):
        sim.drive("d", Value(data, mask=mask, width=width))
        sim.drive("clk", 1)
        sim.settle()
        lane = Value(data ^ 2, mask=mask, width=width)
        packed = lane.concat(lane).concat(lane)
        for name in ("whole", "through_port", "combo", "last", "hierarchical"):
            expected = lane if name in {"last", "hierarchical"} else packed
            actual = sim.read(name)
            assert (actual.val, actual.mask, actual.width) == (expected.val, expected.mask, expected.width), (
                engine,
                name,
                actual,
                expected,
            )
        sim.drive("clk", 0)
        sim.settle()


@pytest.mark.parametrize("filler", [0, 20])
@pytest.mark.parametrize("reverse", [False, True])
def test_indexed_target_notifies_dense_and_indexed_readers(parser, filler, reverse):
    statements = ["assign vec[0 +: 8] = data;", "assign y = vec;"]
    if reverse:
        statements.reverse()
    extra = "\n".join(f"wire dummy_{i}; assign dummy_{i} = other;" for i in range(filler))
    source = f"""module top(input [7:0] data, input other, output [7:0] y);
      wire [7:0] vec;
      {" ".join(statements)}
      {extra}
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine="reference")
    for data in (Value(0, width=8), Value(0, mask=0x80, width=8), Value(0, width=8), Value(0xA5, width=8)):
        sim.drive("data", data)
        sim.settle()
        actual = sim.read("y")
        assert (actual.val, actual.mask) == (data.val, data.mask)


@pytest.mark.parametrize("engine", ENGINES)
def test_concatenated_indexed_targets_notify_readers(parser, engine):
    source = """module top(input [7:0] data, output [7:0] y);
      wire [7:0] a, b;
      assign y = {a[0 +: 4], b[7 -: 4]};
      assign {a[0 +: 4], b[7 -: 4]} = data;
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    for data in (Value(0, width=8), Value(0, mask=0x80, width=8), Value(0x65, width=8)):
        sim.drive("data", data)
        sim.settle()
        actual = sim.read("y")
        assert (actual.val, actual.mask) == (data.val, data.mask)
