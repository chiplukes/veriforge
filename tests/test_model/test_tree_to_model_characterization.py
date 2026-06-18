"""Characterization tests for tree_to_model extraction boundaries.

These tests intentionally exercise `tree_to_design()` only. They should stay
stable while `tree_to_model.py` is split into smaller implementation modules.
"""

from veriforge.model.behavioral import SensitivityType
from veriforge.model.expressions import BinaryOp, FunctionCall, Identifier, Literal
from veriforge.model.generate import GenerateFor, GenvarDecl
from veriforge.model.statements import IfStatement, NonblockingAssign, SeqBlock
from veriforge.transforms.tree_to_model import tree_to_design


def _parse_module(parser, source: str):
    tree = parser.build_tree(source)
    design = tree_to_design(tree, source_file="characterization.v")
    assert len(design.modules) == 1
    return design.modules[0]


def test_cross_domain_module_shape_is_stable(parser):
    source = """module top #(parameter W = 8) (
    input clk,
    input rst_n,
    input [W-1:0] d,
    output reg [W-1:0] q
);
wire [W-1:0] next;
assign next = d + 1'b1;

function [W-1:0] hold;
    input [W-1:0] value;
    begin
        hold = value;
    end
endfunction

genvar i;
generate
    for (i = 0; i < W; i = i + 1) begin : g_tap
        wire tapped;
    end
endgenerate

always @(posedge clk or negedge rst_n) begin
    if (!rst_n)
        q <= 0;
    else
        q <= hold(next);
end
endmodule"""
    module = _parse_module(parser, source)

    assert module.name == "top"
    assert module.loc.file == "characterization.v"
    assert [parameter.name for parameter in module.parameters] == ["W"]
    assert [port.name for port in module.ports] == ["clk", "rst_n", "d", "q"]
    assert [net.name for net in module.nets] == ["next"]
    assert len(module.continuous_assigns) == 1
    assert [function.name for function in module.functions] == ["hold"]
    assert [type(block).__name__ for block in module.generate_blocks] == ["GenvarDecl", "GenerateFor"]
    assert isinstance(module.generate_blocks[0], GenvarDecl)
    assert isinstance(module.generate_blocks[1], GenerateFor)

    always = module.always_blocks[0]
    assert always.parent is module
    assert always.sensitivity_type == SensitivityType.SEQUENTIAL
    assert [(edge.edge, edge.signal.name) for edge in always.sensitivity_list] == [
        ("posedge", "clk"),
        ("negedge", "rst_n"),
    ]

    body = always.body
    assert isinstance(body, SeqBlock)
    assert len(body.statements) == 1
    conditional = body.statements[0]
    assert isinstance(conditional, IfStatement)
    assert isinstance(conditional.then_body, NonblockingAssign)
    assert isinstance(conditional.else_body, NonblockingAssign)
    assert isinstance(conditional.else_body.rhs, FunctionCall)
    assert conditional.else_body.rhs.name == "hold"


def test_expression_precedence_characterization(parser):
    module = _parse_module(parser, "module expr; wire y, a, b, c, d; assign y = a + b * c << d; endmodule")
    rhs = module.continuous_assigns[0].rhs

    assert isinstance(rhs, BinaryOp)
    assert rhs.op == "<<"
    assert isinstance(rhs.left, BinaryOp)
    assert rhs.left.op == "+"
    assert isinstance(rhs.left.left, Identifier)
    assert rhs.left.left.name == "a"
    assert isinstance(rhs.left.right, BinaryOp)
    assert rhs.left.right.op == "*"
    assert isinstance(rhs.right, Identifier)
    assert rhs.right.name == "d"


def test_literal_location_and_parent_links_are_stable(parser):
    module = _parse_module(parser, "module loc #(parameter W = 8) (output [W-1:0] q); endmodule")
    parameter = module.parameters[0]
    port = module.ports[0]

    assert parameter.parent is module
    assert port.parent is module
    assert isinstance(parameter.default_value, Literal)
    assert parameter.default_value.value == 8
    assert parameter.loc.file == "characterization.v"
    assert port.loc.file == "characterization.v"


def test_primitive_gate_instances_are_stable(parser):
    module = _parse_module(parser, "module prim(input a, b, output y); and u_and (y, a, b); endmodule")
    instance = module.instances[0]

    assert instance.module_name == "and"
    assert instance.instance_name == "u_and"
    assert [connection.port_name for connection in instance.port_connections] == [None, None, None]
    assert [connection.is_named for connection in instance.port_connections] == [False, False, False]
    assert [
        connection.expression.name
        for connection in instance.port_connections
        if isinstance(connection.expression, Identifier)
    ] == [
        "y",
        "a",
        "b",
    ]
