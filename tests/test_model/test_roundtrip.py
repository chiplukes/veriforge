"""Tests for round-trip: parse → model → emit → re-parse → compare."""

from veriforge.codegen.verilog_emitter import emit_design, emit_expression, emit_module
from veriforge.model.expressions import BinaryOp, Identifier, Literal
from veriforge.transforms.tree_to_model import tree_to_design


def _roundtrip(parser, source: str) -> tuple:
    """Parse, emit, re-parse, return both models."""
    tree1 = parser.build_tree(source)
    design1 = tree_to_design(tree1)
    emitted = emit_design(design1)
    tree2 = parser.build_tree(emitted)
    design2 = tree_to_design(tree2)
    return design1, design2, emitted


class TestRoundTrip:
    """Round-trip validation tests."""

    def test_empty_module(self, parser):
        d1, d2, _ = _roundtrip(parser, "module empty; endmodule")
        assert d1.modules[0].name == d2.modules[0].name

    def test_ports_preserved(self, parser):
        d1, d2, _ = _roundtrip(parser, "module m(input a, output b, inout c); endmodule")
        m1, m2 = d1.modules[0], d2.modules[0]
        assert len(m1.ports) == len(m2.ports)
        for p1, p2 in zip(m1.ports, m2.ports):
            assert p1.name == p2.name
            assert p1.direction == p2.direction

    def test_parameters_preserved(self, parser):
        d1, d2, _ = _roundtrip(parser, "module m #(parameter W = 8, parameter D = 16) (); endmodule")
        m1, m2 = d1.modules[0], d2.modules[0]
        assert len(m1.parameters) == len(m2.parameters)
        for p1, p2 in zip(m1.parameters, m2.parameters):
            assert p1.name == p2.name
            assert p1.is_local == p2.is_local

    def test_nets_preserved(self, parser):
        d1, d2, _ = _roundtrip(parser, "module m; wire w; wire signed [7:0] bus; tri t; endmodule")
        m1, m2 = d1.modules[0], d2.modules[0]
        assert len(m1.nets) == len(m2.nets)
        for n1, n2 in zip(m1.nets, m2.nets):
            assert n1.name == n2.name
            assert n1.kind == n2.kind
            assert n1.signed == n2.signed

    def test_variables_preserved(self, parser):
        d1, d2, _ = _roundtrip(parser, "module m; reg r; reg [7:0] r2; integer i; real rl; endmodule")
        m1, m2 = d1.modules[0], d2.modules[0]
        assert len(m1.variables) == len(m2.variables)
        for v1, v2 in zip(m1.variables, m2.variables):
            assert v1.name == v2.name
            assert v1.kind == v2.kind

    def test_complex_roundtrip(self, parser):
        source = """module alu #(parameter WIDTH = 32) (
    input clk,
    input signed [WIDTH-1:0] a,
    output reg [WIDTH-1:0] result
);

localparam ZERO = 0;
wire [WIDTH-1:0] internal;
reg [31:0] temp;
integer count;

endmodule"""
        d1, d2, _emitted = _roundtrip(parser, source)
        m1, m2 = d1.modules[0], d2.modules[0]
        assert m1.name == m2.name
        assert len(m1.ports) == len(m2.ports)
        assert len(m1.nets) == len(m2.nets)
        assert len(m1.variables) == len(m2.variables)
        assert len(m1.parameters) == len(m2.parameters)

    def test_signed_base_literals_preserved(self, parser):
        source = """module foo();
    parameter A = 8'sb11111111;
    parameter B = 8'shFF;
endmodule"""
        d1, d2, emitted = _roundtrip(parser, source)
        p1a, p1b = d1.modules[0].parameters
        p2a, p2b = d2.modules[0].parameters
        assert p1a.default_value.original_text == "8'sb11111111"
        assert p1b.default_value.original_text == "8'shFF"
        assert p2a.default_value.original_text == "8'sb11111111"
        assert p2b.default_value.original_text == "8'shFF"
        assert "8'sb11111111" in emitted
        assert "8'shFF" in emitted

    def test_emitted_parses_cleanly(self, parser):
        """Verify emitted code can be parsed without errors."""
        source = "module m #(parameter W=8)(input [W-1:0] d, output reg q); wire [7:0] bus; endmodule"
        tree = parser.build_tree(source)
        design = tree_to_design(tree)
        emitted = emit_design(design)
        # This should not raise
        tree2 = parser.build_tree(emitted)
        assert tree2 is not None


class TestEmitExpression:
    """Test emit_expression for various expression types."""

    def test_literal_int(self):
        assert emit_expression(Literal(42, original_text="42")) == "42"

    def test_literal_hex(self):
        assert emit_expression(Literal(255, width=8, base="h", original_text="8'hFF")) == "8'hFF"

    def test_literal_signed_hex(self):
        assert emit_expression(Literal(255, width=8, base="h", signed=True, original_text="8'shFF")) == "8'shFF"

    def test_identifier(self):
        assert emit_expression(Identifier("WIDTH")) == "WIDTH"

    def test_binary_op(self):
        expr = BinaryOp("-", Identifier("W"), Literal(1, original_text="1"))
        assert emit_expression(expr) == "W - 1"


class TestEmitModule:
    """Test emit_module formatting."""

    def test_module_keyword(self, parser):
        tree = parser.build_tree("module foo; endmodule")
        design = tree_to_design(tree)
        output = emit_module(design.modules[0])
        assert output.startswith("module foo;")
        assert output.strip().endswith("endmodule")

    def test_port_formatting(self, parser):
        tree = parser.build_tree("module m(input a, output b); endmodule")
        design = tree_to_design(tree)
        output = emit_module(design.modules[0])
        assert "input a" in output
        assert "output b" in output
