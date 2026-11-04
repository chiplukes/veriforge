"""Constant expressions in generated scope names resolve before simulation."""

import pytest

from veriforge.model.expressions import Identifier
from veriforge.sim.elaborate import elaborate_generates
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from .engines import ENGINES


def _source(width, index):
    return f"""module channel #(parameter W={width}, parameter SP_K=3)
      (input clk, input [W-1:0] d, output [W-1:0] y);
      for (genvar i=0; i<SP_K; i=i+1) begin : g_sp
        reg [W-1:0] p0;
        always @(posedge clk) p0 <= d ^ i;
      end
      assign y = g_sp[{index}].p0;
    endmodule"""


def _exercise(sim, width, lane):
    sim.drive("clk", 0)
    sim.drive("d", 0)
    sim.settle()
    for data, mask in ((5, 0), (1 << (width - 1), 0), (0, 1 << (width - 1)), (9, 0)):
        sim.drive("d", Value(data, mask=mask, width=width))
        sim.drive("clk", 1)
        sim.settle()
        assert sim.read("y") == Value(data ^ lane, mask=mask, width=width)
        sim.drive("clk", 0)
        sim.settle()


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [8, 96])
@pytest.mark.parametrize("index", ["2", "SP_K-1", "1+1", "(SP_K-1)*2-2", "$clog2(SP_K+1)", "SP_K[1:0]-1"])
def test_generated_register_constant_index(parser, engine, width, index):
    design = tree_to_design(parser.build_tree(_source(width, index)))
    sim = Simulator(design.modules[0], design=design, engine=engine)
    _exercise(sim, width, 2)


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("index", ["2", "SP_K-1"])
def test_generated_register_inside_parameterized_instance(parser, engine, index):
    source = (
        _source(8, index)
        + """\nmodule top(input clk, input [95:0] d, output [95:0] y);
      channel #(.W(96), .SP_K(5)) u(.clk(clk), .d(d), .y(y));
    endmodule"""
    )
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.get_module("top"), design=design, engine=engine)
    _exercise(sim, 96, 2 if index == "2" else 4)


@pytest.mark.parametrize("engine", ENGINES)
def test_nested_generated_register_indices(parser, engine):
    source = """module top(input clk, input [95:0] d, output [95:0] y);
      localparam SP_K=4;
      for (genvar row=0; row<2; row=row+1) begin : rows
        for (genvar col=0; col<2; col=col+1) begin : cols
          reg [95:0] p0;
          always @(posedge clk) p0 <= d ^ (2*row+col);
        end
      end
      assign y = rows[(SP_K-1)/2].cols[(SP_K-1)%2].p0;
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    _exercise(sim, 96, 3)


@pytest.mark.parametrize("engine", ENGINES)
def test_generated_register_genvar_expression(parser, engine):
    source = """module top(input [95:0] d, output [95:0] y);
      localparam SP_K=3;
      for (genvar i=0; i<SP_K; i=i+1) begin : g_sp
        wire [95:0] p0;
        if (i==0) begin : first
          assign p0 = d;
        end else begin : next
          assign p0 = g_sp[i-1].p0 + 96'd1;
        end
      end
      assign y = g_sp[SP_K-1].p0;
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    for data in (5, 1 << 90, 9):
        sim.drive("d", Value(data, width=96))
        sim.settle()
        assert sim.read("y") == Value(data + 2, width=96)


def test_hierarchy_index_elaboration_keeps_input_model(parser):
    design = tree_to_design(parser.build_tree(_source(8, "SP_K-1")))
    module = design.modules[0]
    original = module.continuous_assigns[-1].rhs
    assert isinstance(original, Identifier)
    original_path = list(original.hierarchy)
    for count in (2, 4):
        result = elaborate_generates(module, {"SP_K": count})
        resolved = result.continuous_assigns[-1].rhs
        assert resolved.hierarchy == [f"g_sp[{count - 1}]"]
        assert original.hierarchy == original_path


@pytest.mark.parametrize("engine", ENGINES)
def test_generated_struct_field_inside_instance(parser, engine):
    source = """module channel(input clk, input [7:0] d, output [7:0] y);
      localparam SP_K=3;
      typedef struct packed {logic [7:0] data; logic valid;} item_t;
      for (genvar i=0; i<SP_K; i=i+1) begin : g_sp
        item_t p0;
        always @(posedge clk) p0.data <= d ^ i;
      end
      assign y = g_sp[SP_K-1].p0.data;
    endmodule
    module top(input clk, input [7:0] d, output [7:0] y);
      channel u(.clk(clk), .d(d), .y(y));
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.get_module("top"), design=design, engine=engine)
    _exercise(sim, 8, 2)
