"""Tests for Phase 2: Instance and Continuous Assign extraction."""
# ruff: noqa: PLR2004

from veriforge.codegen.verilog_emitter import emit_design, emit_module
from veriforge.model.assignments import ContinuousAssign
from veriforge.model.design import Design, Module
from veriforge.model.expressions import Concatenation, Identifier, Literal
from veriforge.model.instances import Instance, ParameterBinding, PortConnection
from veriforge.transforms.tree_to_model import tree_to_design


def _parse_module(parser, source: str) -> Module:
    """Helper: parse source and return the first Module."""
    tree = parser.build_tree(source)
    design = tree_to_design(tree, source_file="test.v")
    assert isinstance(design, Design)
    assert len(design.modules) == 1
    return design.modules[0]


# ---- Instance extraction tests ----


class TestInstanceNamedPorts:
    """Instances with named port connections."""

    def test_simple_named_ports(self, parser):
        source = """module top;
wire clk, rst;
counter u1 (.clk(clk), .rst(rst));
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.instances) == 1
        inst = m.instances[0]
        assert inst.module_name == "counter"
        assert inst.instance_name == "u1"
        assert len(inst.port_connections) == 2
        assert all(c.is_named for c in inst.port_connections)
        assert inst.port_connections[0].port_name == "clk"
        assert inst.port_connections[1].port_name == "rst"

    def test_named_port_expression(self, parser):
        source = """module top;
wire clk;
buf u1 (.a(clk));
endmodule"""
        m = _parse_module(parser, source)
        inst = m.instances[0]
        pc = inst.port_connections[0]
        assert pc.port_name == "a"
        assert isinstance(pc.expression, Identifier)
        assert pc.expression.name == "clk"

    def test_unconnected_port(self, parser):
        source = """module top;
wire clk;
counter u1 (.clk(clk), .unused());
endmodule"""
        m = _parse_module(parser, source)
        inst = m.instances[0]
        unused = [c for c in inst.port_connections if c.port_name == "unused"]
        assert len(unused) == 1
        assert unused[0].expression is None

    def test_instance_parent(self, parser):
        source = """module top;
counter u1 (.clk(clk));
endmodule"""
        m = _parse_module(parser, source)
        assert m.instances[0].parent is m

    def test_instance_loc(self, parser):
        source = """module top;
counter u1 (.clk(clk));
endmodule"""
        m = _parse_module(parser, source)
        inst = m.instances[0]
        assert inst.loc is not None
        assert inst.loc.file == "test.v"


class TestInstancePositionalPorts:
    """Instances with ordered (positional) port connections."""

    def test_simple_positional(self, parser):
        source = """module top;
wire clk, rst, cnt;
counter u2 (clk, rst, cnt);
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.instances) == 1
        inst = m.instances[0]
        assert inst.module_name == "counter"
        assert inst.instance_name == "u2"
        assert len(inst.port_connections) == 3
        assert all(not c.is_named for c in inst.port_connections)

    def test_positional_expressions(self, parser):
        source = """module top;
wire a, b;
mymod u1 (a, b);
endmodule"""
        m = _parse_module(parser, source)
        inst = m.instances[0]
        assert isinstance(inst.port_connections[0].expression, Identifier)


class TestInstanceParameters:
    """Instances with parameter overrides."""

    def test_named_params(self, parser):
        source = """module top;
counter #(.WIDTH(8), .DEPTH(16)) u1 (.clk(clk));
endmodule"""
        m = _parse_module(parser, source)
        inst = m.instances[0]
        assert len(inst.parameter_bindings) == 2
        assert inst.parameter_bindings[0].name == "WIDTH"
        assert isinstance(inst.parameter_bindings[0].value, Literal)
        assert inst.parameter_bindings[0].value.value == 8
        assert inst.parameter_bindings[1].name == "DEPTH"

    def test_positional_params(self, parser):
        """Positional params with named ports (positional params + positional ports
        create an Earley UDP ambiguity)."""
        source = """module top;
counter #(8, 16) u1 (.clk(clk), .rst(rst));
endmodule"""
        m = _parse_module(parser, source)
        inst = m.instances[0]
        assert len(inst.parameter_bindings) == 2
        assert inst.parameter_bindings[0].name is None
        assert isinstance(inst.parameter_bindings[0].value, Literal)

    def test_empty_param_override(self, parser):
        source = """module top;
child #() u1 (.clk(clk));
endmodule"""
        m = _parse_module(parser, source)
        inst = m.instances[0]
        assert inst.has_parameter_override is True
        assert inst.parameter_bindings == []


class TestInstanceArray:
    """Instance arrays: module_name inst [range] (ports);"""

    def test_instance_array(self, parser):
        source = """module top;
buf u [3:0] (.a(a), .b(b));
endmodule"""
        m = _parse_module(parser, source)
        inst = m.instances[0]
        assert inst.instance_array is not None
        assert isinstance(inst.instance_array.msb, Literal)
        assert inst.instance_array.msb.value == 3
        assert inst.instance_array.lsb.value == 0


class TestMultipleInstances:
    """Multiple instances in a module."""

    def test_two_instances(self, parser):
        source = """module top;
wire clk, rst;
counter u1 (.clk(clk));
counter u2 (.clk(clk));
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.instances) == 2
        assert m.instances[0].instance_name == "u1"
        assert m.instances[1].instance_name == "u2"


# ---- Continuous assign tests ----


class TestContinuousAssign:
    """Continuous assignment extraction."""

    def test_simple_assign(self, parser):
        source = """module top;
wire a, y;
assign y = a;
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.continuous_assigns) == 1
        ca = m.continuous_assigns[0]
        assert isinstance(ca, ContinuousAssign)
        assert isinstance(ca.lhs, Identifier)
        assert ca.lhs.name == "y"
        assert isinstance(ca.rhs, Identifier)
        assert ca.rhs.name == "a"

    def test_assign_with_operator(self, parser):
        source = """module top;
wire a, b, y;
assign y = a & b;
endmodule"""
        m = _parse_module(parser, source)
        ca = m.continuous_assigns[0]
        assert isinstance(ca.lhs, Identifier)
        assert ca.lhs.name == "y"
        # RHS is a binary expression
        assert ca.rhs is not None

    def test_assign_parent(self, parser):
        source = """module top;
wire y;
assign y = 1'b0;
endmodule"""
        m = _parse_module(parser, source)
        assert m.continuous_assigns[0].parent is m

    def test_assign_loc(self, parser):
        source = """module top;
wire y;
assign y = 1'b0;
endmodule"""
        m = _parse_module(parser, source)
        ca = m.continuous_assigns[0]
        assert ca.loc is not None
        assert ca.loc.file == "test.v"

    def test_multiple_assigns(self, parser):
        source = """module top;
wire a, b, y, z;
assign y = a;
assign z = b;
endmodule"""
        m = _parse_module(parser, source)
        assert len(m.continuous_assigns) == 2

    def test_concatenation_lvalue(self, parser):
        source = """module top;
wire a, b, c;
assign {a, b} = {c, c};
endmodule"""
        m = _parse_module(parser, source)
        ca = m.continuous_assigns[0]
        assert isinstance(ca.lhs, Concatenation)
        assert len(ca.lhs.parts) == 2


# ---- Instance model class tests ----


class TestInstanceModel:
    """Test Instance model class directly."""

    def test_instance_repr(self):
        inst = Instance("counter", "u1")
        assert "counter" in repr(inst)
        assert "u1" in repr(inst)

    def test_instance_to_dict(self):
        inst = Instance(
            "counter",
            "u1",
            port_connections=[
                PortConnection(port_name="clk", expression=Identifier("sys_clk"), is_named=True),
            ],
        )
        d = inst.to_dict()
        assert d["module_name"] == "counter"
        assert d["instance_name"] == "u1"
        assert len(d["port_connections"]) == 1

    def test_instance_to_dict_empty_param_override(self):
        inst = Instance("counter", "u1", has_parameter_override=True)
        d = inst.to_dict()
        assert d["has_parameter_override"] is True

    def test_port_connection_repr_named(self):
        pc = PortConnection(port_name="clk", expression=Identifier("sys_clk"), is_named=True)
        assert ".clk(" in repr(pc)

    def test_port_connection_repr_unconnected(self):
        pc = PortConnection(port_name="data", expression=None, is_named=True)
        assert "unconnected" in repr(pc)

    def test_port_connection_repr_positional(self):
        pc = PortConnection(expression=Identifier("clk"), is_named=False)
        assert "PortConnection(" in repr(pc)

    def test_parameter_binding_repr_named(self):
        pb = ParameterBinding(name="WIDTH", value=Literal(8, original_text="8"))
        assert ".WIDTH(" in repr(pb)

    def test_parameter_binding_repr_positional(self):
        pb = ParameterBinding(value=Literal(8, original_text="8"))
        assert "ParameterBinding(" in repr(pb)

    def test_instance_child_nodes(self):
        inst = Instance(
            "m",
            "u1",
            parameter_bindings=[ParameterBinding(name="W", value=Literal(8))],
            port_connections=[PortConnection(port_name="a", expression=Identifier("x"), is_named=True)],
        )
        children = inst._child_nodes()
        assert len(children) == 2

    def test_continuous_assign_repr(self):
        ca = ContinuousAssign(Identifier("y"), Identifier("a"))
        assert "y" in repr(ca)
        assert "a" in repr(ca)

    def test_continuous_assign_to_dict(self):
        ca = ContinuousAssign(Identifier("y"), Identifier("a"))
        d = ca.to_dict()
        assert "lhs" in d
        assert "rhs" in d

    def test_continuous_assign_child_nodes(self):
        ca = ContinuousAssign(Identifier("y"), Identifier("a"))
        children = ca._child_nodes()
        assert len(children) == 2


# ---- Emitter tests ----


class TestEmitInstance:
    """Test instance emission."""

    def test_emit_named_ports(self, parser):
        source = """module top;
counter u1 (.clk(clk), .rst(rst));
endmodule"""
        m = _parse_module(parser, source)
        output = emit_module(m)
        assert "counter" in output
        assert "u1" in output
        assert ".clk(clk)" in output
        assert ".rst(rst)" in output

    def test_emit_named_params(self, parser):
        source = """module top;
counter #(.WIDTH(8)) u1 (.clk(clk));
endmodule"""
        m = _parse_module(parser, source)
        output = emit_module(m)
        assert "#(" in output
        assert ".WIDTH(8)" in output

    def test_emit_empty_param_override(self, parser):
        source = """module top;
counter #() u1 (.clk(clk));
endmodule"""
        m = _parse_module(parser, source)
        output = emit_module(m)
        assert "counter #() u1" in output

    def test_emit_unconnected_port(self, parser):
        source = """module top;
counter u1 (.clk(clk), .unused());
endmodule"""
        m = _parse_module(parser, source)
        output = emit_module(m)
        assert ".unused()" in output


class TestEmitContinuousAssign:
    """Test continuous assign emission."""

    def test_emit_simple_assign(self, parser):
        source = """module top;
wire a, y;
assign y = a;
endmodule"""
        m = _parse_module(parser, source)
        output = emit_module(m)
        assert "assign y = a;" in output


# ---- Round-trip tests ----


class TestRoundTripPhase2:
    """Round-trip: parse → model → emit → re-parse → compare."""

    def _roundtrip(self, parser, source: str) -> tuple:
        tree1 = parser.build_tree(source)
        design1 = tree_to_design(tree1, source_file="test.v")
        emitted = emit_design(design1)
        tree2 = parser.build_tree(emitted)
        design2 = tree_to_design(tree2, source_file="test2.v")
        return design1, design2, emitted

    def test_roundtrip_named_ports(self, parser):
        source = """module top;
counter u1 (.clk(clk), .rst(rst));
endmodule"""
        d1, d2, _emitted = self._roundtrip(parser, source)
        m1, m2 = d1.modules[0], d2.modules[0]
        assert len(m1.instances) == len(m2.instances)
        assert m1.instances[0].module_name == m2.instances[0].module_name
        assert m1.instances[0].instance_name == m2.instances[0].instance_name
        assert len(m1.instances[0].port_connections) == len(m2.instances[0].port_connections)

    def test_roundtrip_simple_assign(self, parser):
        source = """module top;
wire a, y;
assign y = a;
endmodule"""
        d1, d2, _emitted = self._roundtrip(parser, source)
        m1, m2 = d1.modules[0], d2.modules[0]
        assert len(m1.continuous_assigns) == len(m2.continuous_assigns)

    def test_roundtrip_params_and_instances(self, parser):
        source = """module top;
counter #(.WIDTH(8)) u1 (.clk(clk), .data(data));
endmodule"""
        d1, d2, _emitted = self._roundtrip(parser, source)
        m1, m2 = d1.modules[0], d2.modules[0]
        assert len(m1.instances[0].parameter_bindings) == len(m2.instances[0].parameter_bindings)

    def test_roundtrip_empty_param_override(self, parser):
        source = """module top;
counter #() u1 (.clk(clk), .data(data));
endmodule"""
        d1, d2, emitted = self._roundtrip(parser, source)
        assert d1.modules[0].instances[0].has_parameter_override is True
        assert d2.modules[0].instances[0].has_parameter_override is True
        assert "counter #() u1" in emitted

    def test_roundtrip_emitted_parses(self, parser):
        source = """module top;
wire clk, rst, cnt;
wire [7:0] data;
counter #(.WIDTH(8)) u1 (.clk(clk), .rst(rst), .count(cnt));
assign data = 8'hFF;
endmodule"""
        _, _, emitted = self._roundtrip(parser, source)
        # Verify emitted code parses without error
        tree = parser.build_tree(emitted)
        assert tree is not None

    def test_roundtrip_complex(self, parser):
        """Complex module with instances, assigns, nets, ports."""
        source = """module top (
    input clk,
    input rst,
    output [7:0] data
);

wire [7:0] internal;
wire sel;

counter #(.WIDTH(8)) u1 (.clk(clk), .rst(rst), .count(internal));
assign data = internal;

endmodule"""
        d1, d2, _emitted = self._roundtrip(parser, source)
        m1, m2 = d1.modules[0], d2.modules[0]
        assert m1.name == m2.name
        assert len(m1.ports) == len(m2.ports)
        assert len(m1.nets) == len(m2.nets)
        assert len(m1.instances) == len(m2.instances)
        assert len(m1.continuous_assigns) == len(m2.continuous_assigns)
