"""
Test grammar rules from Section A.1 - Source text.

These tests verify the parsing of:
- Library text (A.1.1)
- Verilog source text (A.1.2)
- Module parameters and ports (A.1.3)
- Module items (A.1.4)
- Configuration source text (A.1.5)
"""

import pytest


@pytest.mark.section_a1
class TestSourceTextBasics:
    """Test basic source text parsing."""

    def test_empty_module(self, parser):
        """Test parsing an empty module."""
        tree = parser.build_tree("module empty(); endmodule")
        assert tree is not None
        assert tree.data == "verilog"

    def test_module_with_identifier(self, parser):
        """Test module with various identifier types."""
        tree = parser.build_tree("module my_module123(); endmodule")
        assert tree is not None
        modules = list(tree.find_data("module_declaration"))
        assert len(modules) == 1

    def test_multiple_modules(self, parser):
        """Test parsing multiple modules in one file."""
        code = """
        module mod1(); endmodule
        module mod2(); endmodule
        module mod3(); endmodule
        """
        tree = parser.build_tree(code)
        assert tree is not None
        modules = list(tree.find_data("module_declaration"))
        assert len(modules) == 3


@pytest.mark.section_a1
class TestModulePorts:
    """Test module port declarations."""

    def test_old_style_ports(self, module_parser):
        """Test old-style port declaration (names only)."""
        code = "module foo(a, b, c); input a; output b, c; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_ansi_style_ports(self, module_parser):
        """Test ANSI-style port declarations."""
        code = "module foo(input a, output b); endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_port_with_range(self, module_parser):
        """Test ports with bit range."""
        code = "module foo(input [7:0] data, output [15:0] result); endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_inout_port(self, module_parser):
        """Test bidirectional port."""
        code = "module foo(inout [7:0] bidir); endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_port_concatenation(self, module_parser):
        """Test port with concatenation expression."""
        code = "module foo(.data({a, b})); endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a1
class TestModuleParameters:
    """Test module parameter declarations."""

    def test_single_parameter(self, module_parser):
        """Test module with single parameter."""
        code = "module foo #(parameter WIDTH=8) (); endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_multiple_parameters(self, module_parser):
        """Test module with multiple parameters."""
        code = "module foo #(parameter WIDTH=8, parameter DEPTH=16) (); endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_parameter_with_type(self, module_parser):
        """Test parameter with explicit type."""
        code = "module foo #(parameter integer COUNT=100) (); endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_parameter_in_port_range(self, module_parser):
        """Test parameter used in port range."""
        code = "module foo #(parameter W=8) (input [W-1:0] data); endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a1
class TestModuleItems:
    """Test various module items."""

    def test_wire_declaration(self, module_parser):
        """Test wire declaration inside module."""
        code = "module foo(); wire [7:0] data; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_reg_declaration(self, module_parser):
        """Test reg declaration inside module."""
        code = "module foo(); reg [7:0] counter; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_assign_statement(self, module_parser):
        """Test continuous assignment."""
        code = "module foo(input a, b, output y); assign y = a & b; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_always_block(self, module_parser):
        """Test always block."""
        code = """
        module foo(input clk, d, output reg q);
            always @(posedge clk) q <= d;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
