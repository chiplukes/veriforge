"""Constant folding must retain leading zero bits in a parameter's type."""

import pytest

from veriforge.sim.elaborate import _build_param_env
from veriforge.sim.testbench import Simulator
from veriforge.sim.value import Value
from veriforge.transforms.tree_to_model import tree_to_design

from .engines import ENGINES


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("width", [16, 96, 128])
@pytest.mark.parametrize("expression", ["f(0)", "0 ? 0 : f(0)", "{W{1'b0}}", "(f(0) | 0)", "ALIAS"])
@pytest.mark.parametrize("nested", [False, True])
def test_parameter_expression_retains_width(parser, engine, width, expression, nested):
    source = f"""module leaf #(parameter W={width})(output [W-1:0] y, output [31:0] bits);
      function [W-1:0] f(input dummy); f = 1; endfunction
      localparam P = {expression};
      localparam ALIAS = OTHER;
      localparam OTHER = f(0);
      assign y = P[W-1:0];
      assign bits = $bits(P);
    endmodule"""
    # A ternary or bitwise op with an unsized integer has at least 32 bits.
    expected_width = max(width, 32) if expression in ("0 ? 0 : f(0)", "(f(0) | 0)") else width
    if nested:
        source += f"""\nmodule middle(output [{width - 1}:0] y, output [31:0] bits);
          leaf #(.W({width})) u(.y(y), .bits(bits));
        endmodule
        module top(output [{width - 1}:0] y, output [31:0] bits);
          localparam W=7;
          middle u(.y(y), .bits(bits));
        endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.get_module("top" if nested else "leaf"), design=design, engine=engine)
    sim.settle()
    name = "u.u.P" if nested else "P"
    expected = 0 if expression == "{W{1'b0}}" else 1
    assert sim.read(name) == Value(expected, width=expected_width)
    assert sim.read("y") == Value(expected, width=width)
    assert sim.read("bits") == Value(expected_width, width=32)


@pytest.mark.parametrize("engine", ENGINES)
def test_crossbar_base_address_parameter(parser, engine):
    source = """module top(input [1:0] region, output [31:0] base);
      function [127:0] calc(input dummy);
        calc = 128'h03000000020000000100000000000000;
      endfunction
      localparam BASES = 0 ? 0 : calc(0);
      assign base = BASES[region*32 +: 32];
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    for region in range(4):
        sim.drive("region", region)
        sim.settle()
        assert sim.read("base") == Value(region << 24, width=32)
    assert sim.read("BASES").width == 128


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize("explicit", [False, True])
def test_computed_parameter_override_uses_binding_width(parser, engine, explicit):
    declaration = "[159:0]" if explicit else ""
    source = f"""module leaf #(parameter {declaration} P=0)(output [127:0] y, output [31:0] bits);
      assign y = P[127:0]; assign bits = $bits(P);
    endmodule
    module middle #(parameter [127:0] DATA=128'h1234)(output [127:0] y, output [31:0] bits);
      leaf #(.P(DATA | 0)) u(.y(y),.bits(bits));
    endmodule
    module top(output [127:0] y, output [31:0] bits);
      middle u(.y(y),.bits(bits));
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.get_module("top"), design=design, engine=engine)
    sim.settle()
    assert sim.read("y") == Value(0x1234, width=128)
    assert sim.read("bits") == Value(160 if explicit else 128, width=32)


def test_parameter_bits_forward_reference(parser):
    design = tree_to_design(
        parser.build_tree("""module top;
      localparam BITS = $bits(P);
      localparam P = ALIAS;
      localparam [95:0] ALIAS=0;
    endmodule""")
    )
    env = _build_param_env(design.modules[0])
    assert env["BITS"] == env["$bits:P"] == 96


@pytest.mark.parametrize("engine", ENGINES)
def test_explicit_parameter_width_uses_instance_scope(parser, engine):
    source = """module leaf #(parameter W=96, parameter [W-1:0] P=0)
      (output [95:0] y, output [31:0] bits);
      assign y = P[95:0]; assign bits = $bits(P);
    endmodule
    module middle(output [95:0] y, output [31:0] bits);
      leaf u(.y(y), .bits(bits));
    endmodule
    module top(output [95:0] y, output [31:0] bits);
      localparam W=7;
      middle u(.y(y), .bits(bits));
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.get_module("top"), design=design, engine=engine)
    sim.settle()
    assert sim.read("u.u.P") == Value(0, width=96)
    assert sim.read("y") == Value(0, width=96)
    assert sim.read("bits") == Value(96, width=32)


@pytest.mark.parametrize("engine", ENGINES)
def test_width_waits_for_unselected_branch_bound(parser, engine):
    source = """module top(output [95:0] y, output [31:0] bits);
      localparam P = 1 ? 0 : {W{1'b0}};
      localparam W = 96;
      assign y=P[95:0]; assign bits=$bits(P);
    endmodule"""
    design = tree_to_design(parser.build_tree(source))
    sim = Simulator(design.modules[0], engine=engine)
    sim.settle()
    assert sim.read("P") == Value(0, width=96)
    assert sim.read("y") == Value(0, width=96)
    assert sim.read("bits") == Value(96, width=32)
