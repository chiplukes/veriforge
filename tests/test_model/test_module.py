"""Tests for the tree_to_model transformer — Module extraction."""

from veriforge.model.design import Design, Module
from veriforge.model.assignments import ContinuousAssign
from veriforge.model.expressions import BinaryOp, BitSelect, Concatenation, Identifier, Literal, Replication
from veriforge.model.nets import NetKind
from veriforge.model.ports import PortDirection
from veriforge.model.variables import VariableKind
from veriforge.transforms.tree_to_model import tree_to_design


def _parse_module(parser, source: str) -> Module:
    """Helper: parse source and return the first Module."""
    tree = parser.build_tree(source)
    design = tree_to_design(tree, source_file="test.v")
    assert isinstance(design, Design)
    assert len(design.modules) == 1
    return design.modules[0]


class TestModuleBasic:
    """Basic module parsing."""

    def test_empty_module(self, parser):
        m = _parse_module(parser, "module empty; endmodule")
        assert m.name == "empty"
        assert m.ports == []
        assert m.nets == []
        assert m.variables == []
        assert m.parameters == []

    def test_module_name(self, parser):
        m = _parse_module(parser, "module my_module; endmodule")
        assert m.name == "my_module"

    def test_source_location(self, parser):
        m = _parse_module(parser, "module loc_test; endmodule")
        assert m.loc is not None
        assert m.loc.file == "test.v"
        assert m.loc.line > 0

    def test_integer_array_dimensions_preserved(self, parser):
        m = _parse_module(parser, "module dims; integer table[0:10]; endmodule")
        assert len(m.variables) == 1
        var = m.variables[0]
        assert var.name == "table"
        assert var.kind == VariableKind.INTEGER
        assert len(var.dimensions) == 1
        assert isinstance(var.dimensions[0].msb, Literal)
        assert isinstance(var.dimensions[0].lsb, Literal)
        assert int(var.dimensions[0].msb.value) == 0
        assert int(var.dimensions[0].lsb.value) == 10

    def test_typedef_array_dimensions_preserved(self, parser):
        m = _parse_module(
            parser, "module dims; typedef logic [7:0] payload_t; payload_t [1:0][2:0] out_data; endmodule"
        )
        assert len(m.variables) == 1
        var = m.variables[0]
        assert var.name == "out_data"
        assert var.type_name == "payload_t"
        assert len(var.dimensions) == 2
        assert isinstance(var.dimensions[0].msb, Literal)
        assert isinstance(var.dimensions[0].lsb, Literal)
        assert int(var.dimensions[0].msb.value) == 1
        assert int(var.dimensions[0].lsb.value) == 0
        assert isinstance(var.dimensions[1].msb, Literal)
        assert isinstance(var.dimensions[1].lsb, Literal)
        assert int(var.dimensions[1].msb.value) == 2
        assert int(var.dimensions[1].lsb.value) == 0


class TestPorts:
    """Port extraction tests."""

    def test_single_input(self, parser):
        m = _parse_module(parser, "module m(input a); endmodule")
        assert len(m.ports) == 1
        p = m.ports[0]
        assert p.name == "a"
        assert p.direction == PortDirection.INPUT
        assert p.width is None
        assert p.dimensions == []
        assert p.signed is False

    def test_typedef_port_dimensions_preserved(self, parser):
        m = _parse_module(parser, "module m(input payload_t [1:0] data_i); endmodule")
        assert len(m.ports) == 1
        p = m.ports[0]
        assert p.name == "data_i"
        assert p.data_type == "payload_t"
        assert len(p.dimensions) == 1
        assert isinstance(p.dimensions[0].msb, Literal)
        assert isinstance(p.dimensions[0].lsb, Literal)
        assert int(p.dimensions[0].msb.value) == 1
        assert int(p.dimensions[0].lsb.value) == 0

    def test_single_output(self, parser):
        m = _parse_module(parser, "module m(output b); endmodule")
        assert len(m.ports) == 1
        assert m.ports[0].direction == PortDirection.OUTPUT

    def test_single_inout(self, parser):
        m = _parse_module(parser, "module m(inout c); endmodule")
        assert len(m.ports) == 1
        assert m.ports[0].direction == PortDirection.INOUT


class TestExpressions:
    """Expression extraction tests."""

    def test_nested_index_expression_preserved(self, parser):
        m = _parse_module(
            parser,
            "module m; logic [1:0][2:0] inp_valid; logic [2:0] arb_req_i; assign arb_req_i[0] = inp_valid[0][1]; endmodule",
        )
        assert len(m.continuous_assigns) == 1
        assign = m.continuous_assigns[0]
        assert isinstance(assign, ContinuousAssign)
        assert isinstance(assign.rhs, BitSelect)
        assert isinstance(assign.rhs.target, BitSelect)
        assert isinstance(assign.rhs.target.target, Identifier)
        assert assign.rhs.target.target.name == "inp_valid"
        assert isinstance(assign.rhs.target.index, Literal)
        assert int(assign.rhs.target.index.value) == 0
        assert isinstance(assign.rhs.index, Literal)
        assert int(assign.rhs.index.value) == 1

    def test_output_reg(self, parser):
        m = _parse_module(parser, "module m(output reg q); endmodule")
        p = m.ports[0]
        assert p.direction == PortDirection.OUTPUT
        assert p.data_type == "reg"

    def test_port_with_range(self, parser):
        m = _parse_module(parser, "module m(input [7:0] data); endmodule")
        p = m.ports[0]
        assert p.width is not None
        assert isinstance(p.width.msb, Literal)
        assert p.width.msb.value == 7
        assert isinstance(p.width.lsb, Literal)
        assert p.width.lsb.value == 0

    def test_port_signed(self, parser):
        m = _parse_module(parser, "module m(input signed [3:0] s); endmodule")
        p = m.ports[0]
        assert p.signed is True
        assert p.width is not None

    def test_multiple_ports(self, parser):
        m = _parse_module(parser, "module m(input a, output b, inout c); endmodule")
        assert len(m.ports) == 3
        assert m.ports[0].name == "a"
        assert m.ports[1].name == "b"
        assert m.ports[2].name == "c"
        assert m.input_ports() == [m.ports[0]]
        assert m.output_ports() == [m.ports[1]]
        assert m.inout_ports() == [m.ports[2]]

    def test_output_reg_with_range(self, parser):
        m = _parse_module(parser, "module m(output reg [15:0] q); endmodule")
        p = m.ports[0]
        assert p.direction == PortDirection.OUTPUT
        assert p.data_type == "reg"
        assert p.width is not None
        assert p.width.msb.value == 15

    def test_port_parent(self, parser):
        m = _parse_module(parser, "module m(input a); endmodule")
        assert m.ports[0].parent is m


class TestParameters:
    """Parameter extraction tests."""

    def test_single_parameter(self, parser):
        m = _parse_module(parser, "module m #(parameter W = 8) (); endmodule")
        assert len(m.parameters) == 1
        p = m.parameters[0]
        assert p.name == "W"
        assert p.is_local is False
        assert isinstance(p.default_value, Literal)
        assert p.default_value.value == 8

    def test_multiple_parameters(self, parser):
        m = _parse_module(parser, "module m #(parameter A = 1, parameter B = 2) (); endmodule")
        assert len(m.parameters) == 2
        assert m.parameters[0].name == "A"
        assert m.parameters[1].name == "B"

    def test_localparam(self, parser):
        m = _parse_module(parser, "module m; localparam LP = 42; endmodule")
        assert len(m.parameters) == 1
        lp = m.parameters[0]
        assert lp.name == "LP"
        assert lp.is_local is True
        assert isinstance(lp.default_value, Literal)
        assert lp.default_value.value == 42

    def test_get_parameter(self, parser):
        m = _parse_module(parser, "module m #(parameter W = 8) (); endmodule")
        assert m.get_parameter("W") is not None
        assert m.get_parameter("X") is None

    def test_parameter_parent(self, parser):
        m = _parse_module(parser, "module m #(parameter W = 8) (); endmodule")
        assert m.parameters[0].parent is m

    def test_parameter_expression(self, parser):
        """Parameter default with a binary expression."""
        m = _parse_module(parser, "module m #(parameter W = 8) (output reg [W-1:0] q); endmodule")
        p = m.ports[0]
        assert p.width is not None
        assert isinstance(p.width.msb, BinaryOp)
        assert p.width.msb.op == "-"

    def test_typed_parameter_replication_default(self, parser):
        m = _parse_module(
            parser,
            """module m #(
    parameter int N = 4,
    parameter logic [N-1:0] MASK = {N{1'b0}},
    parameter logic [N*8-1:0] DATA = {N{8'h00}}
) ();
endmodule""",
        )
        mask = m.get_parameter("MASK")
        data = m.get_parameter("DATA")
        assert mask is not None
        assert data is not None
        assert mask.width is not None
        assert data.width is not None
        assert isinstance(mask.width.msb, BinaryOp)
        assert mask.width.msb.op == "-"
        assert isinstance(data.width.msb, BinaryOp)
        assert data.width.msb.op == "-"
        assert isinstance(mask.default_value, Replication)
        assert isinstance(mask.default_value.count, Identifier)
        assert mask.default_value.count.name == "N"
        assert isinstance(mask.default_value.value, Concatenation)
        assert len(mask.default_value.value.parts) == 1
        assert isinstance(mask.default_value.value.parts[0], Literal)
        assert mask.default_value.value.parts[0].width == 1
        assert mask.default_value.value.parts[0].value == 0
        assert isinstance(data.default_value, Replication)
        assert isinstance(data.default_value.count, Identifier)
        assert data.default_value.count.name == "N"
        assert isinstance(data.default_value.value, Concatenation)
        assert len(data.default_value.value.parts) == 1
        assert isinstance(data.default_value.value.parts[0], Literal)
        assert data.default_value.value.parts[0].width == 8
        assert data.default_value.value.parts[0].value == 0


class TestNets:
    """Net extraction tests."""

    def test_wire(self, parser):
        m = _parse_module(parser, "module m; wire w; endmodule")
        assert len(m.nets) == 1
        n = m.nets[0]
        assert n.name == "w"
        assert n.kind == NetKind.WIRE

    def test_tri(self, parser):
        m = _parse_module(parser, "module m; tri t; endmodule")
        assert len(m.nets) == 1
        assert m.nets[0].kind == NetKind.TRI

    def test_wire_with_range(self, parser):
        m = _parse_module(parser, "module m; wire [7:0] bus; endmodule")
        n = m.nets[0]
        assert n.width is not None
        assert n.width.msb.value == 7
        assert n.width.lsb.value == 0

    def test_wire_signed(self, parser):
        m = _parse_module(parser, "module m; wire signed [15:0] sw; endmodule")
        n = m.nets[0]
        assert n.signed is True
        assert n.width is not None

    def test_get_net(self, parser):
        m = _parse_module(parser, "module m; wire w; endmodule")
        assert m.get_net("w") is not None
        assert m.get_net("x") is None

    def test_net_parent(self, parser):
        m = _parse_module(parser, "module m; wire w; endmodule")
        assert m.nets[0].parent is m

    def test_logic_2d_packed_array(self, parser):
        """2D packed array: outer range becomes array dimension, inner range is element width."""
        m = _parse_module(parser, "module m; logic [2:0] [31:0] rdata; endmodule")
        n = m.nets[0]
        assert n.name == "rdata"
        assert n.kind == NetKind.LOGIC
        # Inner range is the element width
        assert n.width is not None
        assert n.width.msb.value == 31
        assert n.width.lsb.value == 0
        # Outer range becomes an array dimension
        assert len(n.dimensions) == 1
        assert n.dimensions[0].msb.value == 2
        assert n.dimensions[0].lsb.value == 0

    def test_logic_2d_packed_multiple_signals(self, parser):
        """2D packed array with multiple signal names."""
        m = _parse_module(parser, "module m; logic [1:0] [7:0] a, b; endmodule")
        assert len(m.nets) == 2
        for n in m.nets:
            assert n.width.msb.value == 7
            assert n.width.lsb.value == 0
            assert len(n.dimensions) == 1
            assert n.dimensions[0].msb.value == 1
            assert n.dimensions[0].lsb.value == 0

    def test_logic_1d_unchanged(self, parser):
        """Single-range logic declaration still works as before."""
        m = _parse_module(parser, "module m; logic [15:0] data; endmodule")
        n = m.nets[0]
        assert n.width.msb.value == 15
        assert n.width.lsb.value == 0
        assert len(n.dimensions) == 0


class TestVariables:
    """Variable extraction tests."""

    def test_reg(self, parser):
        m = _parse_module(parser, "module m; reg r; endmodule")
        assert len(m.variables) == 1
        v = m.variables[0]
        assert v.name == "r"
        assert v.kind == VariableKind.REG

    def test_reg_with_range(self, parser):
        m = _parse_module(parser, "module m; reg [7:0] r; endmodule")
        v = m.variables[0]
        assert v.width is not None
        assert v.width.msb.value == 7

    def test_reg_signed(self, parser):
        m = _parse_module(parser, "module m; reg signed [31:0] sr; endmodule")
        v = m.variables[0]
        assert v.signed is True

    def test_integer(self, parser):
        m = _parse_module(parser, "module m; integer i; endmodule")
        assert len(m.variables) == 1
        assert m.variables[0].kind == VariableKind.INTEGER

    def test_real(self, parser):
        m = _parse_module(parser, "module m; real r; endmodule")
        assert len(m.variables) == 1
        assert m.variables[0].kind == VariableKind.REAL

    def test_get_variable(self, parser):
        m = _parse_module(parser, "module m; reg r; endmodule")
        assert m.get_variable("r") is not None
        assert m.get_variable("x") is None

    def test_all_signals(self, parser):
        m = _parse_module(parser, "module m; wire w; reg r; endmodule")
        signals = m.all_signals()
        assert len(signals) == 2
        assert signals[0].name == "w"
        assert signals[1].name == "r"


class TestDesign:
    """Design-level tests."""

    def test_source_file(self, parser):
        tree = parser.build_tree("module m; endmodule")
        design = tree_to_design(tree, source_file="test.v")
        assert design.source_files == ["test.v"]

    def test_get_module(self, parser):
        tree = parser.build_tree("module m; endmodule")
        design = tree_to_design(tree)
        assert design.get_module("m") is not None
        assert design.get_module("x") is None

    def test_to_json(self, parser):
        tree = parser.build_tree("module m(input a); wire w; endmodule")
        design = tree_to_design(tree)
        json_str = design.to_json()
        assert '"name": "m"' in json_str
        assert '"name": "a"' in json_str
        assert '"name": "w"' in json_str

    def test_walk(self, parser):
        """Test the walk() traversal finds all nodes."""
        tree = parser.build_tree("module m(input a, output b); wire w; reg r; endmodule")
        design = tree_to_design(tree)
        all_nodes = list(design.walk())
        # Design + Module + 2 ports + 1 net + 1 var = 6
        assert len(all_nodes) >= 6


class TestExpressions:
    """Expression extraction from ranges and parameter defaults."""

    def test_literal_value(self, parser):
        m = _parse_module(parser, "module m(input [7:0] d); endmodule")
        msb = m.ports[0].width.msb
        assert isinstance(msb, Literal)
        assert msb.value == 7
        assert msb.original_text == "7"

    def test_identifier_in_range(self, parser):
        m = _parse_module(parser, "module m #(parameter W = 8) (input [W-1:0] d); endmodule")
        msb = m.ports[0].width.msb
        assert isinstance(msb, BinaryOp)
        assert isinstance(msb.left, Identifier)
        assert msb.left.name == "W"
        assert msb.op == "-"
        assert isinstance(msb.right, Literal)
        assert msb.right.value == 1


class TestComplexModule:
    """Integration test with a realistic module."""

    def test_full_module(self, parser):
        source = """module alu #(parameter WIDTH = 32, parameter OP_BITS = 4) (
    input clk,
    input rst,
    input signed [WIDTH-1:0] a,
    input signed [WIDTH-1:0] b,
    input [OP_BITS-1:0] op,
    output reg signed [WIDTH-1:0] result,
    output reg zero_flag
);

wire [WIDTH-1:0] sum;
wire signed [WIDTH-1:0] diff;
reg [31:0] temp;
integer count;
localparam ADD = 0;
localparam SUB = 1;

endmodule"""
        m = _parse_module(parser, source)

        assert m.name == "alu"
        # 2 module params + 2 localparams
        assert len(m.parameters) == 4
        assert m.parameters[0].name == "WIDTH"
        assert m.parameters[1].name == "OP_BITS"
        assert m.parameters[2].name == "ADD"
        assert m.parameters[2].is_local is True
        assert m.parameters[3].name == "SUB"

        # 7 ports
        assert len(m.ports) == 7
        assert len(m.input_ports()) == 5
        assert len(m.output_ports()) == 2

        # signed ports
        assert m.get_port("a").signed is True
        assert m.get_port("b").signed is True
        assert m.get_port("result").signed is True
        assert m.get_port("result").data_type == "reg"

        # 2 nets
        assert len(m.nets) == 2
        assert m.get_net("sum") is not None
        assert m.get_net("diff").signed is True

        # 2 variables
        assert len(m.variables) == 2
        assert m.get_variable("temp").kind == VariableKind.REG
        assert m.get_variable("count").kind == VariableKind.INTEGER
