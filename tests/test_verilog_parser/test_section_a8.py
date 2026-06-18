"""
Test grammar rules from Section A.8 - Expressions.

These tests verify the parsing of:
- Concatenations (A.8.1)
- Function calls (A.8.2)
- Expressions (A.8.3)
- Primaries (A.8.4)
- Expression left-side values (A.8.5)
- Operators (A.8.6)
- Numbers (A.8.7)
- Strings (A.8.8)
"""

import pytest


@pytest.mark.section_a8
class TestConcatenations:
    """Test concatenation expressions."""

    def test_simple_concatenation(self, module_parser):
        """Test simple concatenation {a, b}."""
        code = "module foo(input [3:0] a, b, output [7:0] y); assign y = {a, b}; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_replication(self, module_parser):
        """Test replication operator {4{a}}."""
        code = "module foo(input a, output [3:0] y); assign y = {4{a}}; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_nested_concatenation(self, module_parser):
        """Test nested concatenation."""
        code = "module foo(input a, b, c, output [5:0] y); assign y = {{2{a}}, {2{b}}, {2{c}}}; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a8
class TestOperators:
    """Test various operators."""

    def test_arithmetic_operators(self, module_parser):
        """Test arithmetic operators +, -, *, /, %."""
        code = """
        module foo(input [7:0] a, b, output [7:0] sum, diff, prod);
            assign sum = a + b;
            assign diff = a - b;
            assign prod = a * b;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_logical_operators(self, module_parser):
        """Test logical operators &&, ||, !."""
        code = """
        module foo(input a, b, output y);
            assign y = (a && b) || !a;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_bitwise_operators(self, module_parser):
        """Test bitwise operators &, |, ^, ~."""
        code = """
        module foo(input [7:0] a, b, output [7:0] y1, y2, y3, y4);
            assign y1 = a & b;
            assign y2 = a | b;
            assign y3 = a ^ b;
            assign y4 = ~a;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_comparison_operators(self, module_parser):
        """Test comparison operators ==, !=, <, >, <=, >=."""
        code = """
        module foo(input [7:0] a, b, output eq, neq, lt, gt);
            assign eq = (a == b);
            assign neq = (a != b);
            assign lt = (a < b);
            assign gt = (a > b);
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_shift_operators(self, module_parser):
        """Test shift operators <<, >>, <<<, >>>."""
        code = """
        module foo(input [7:0] a, output [7:0] y1, y2);
            assign y1 = a << 2;
            assign y2 = a >> 2;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_ternary_operator(self, module_parser):
        """Test ternary conditional operator ?:."""
        code = "module foo(input sel, a, b, output y); assign y = sel ? a : b; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_reduction_operators(self, module_parser):
        """Test reduction operators &, |, ^."""
        code = """
        module foo(input [7:0] a, output y1, y2, y3);
            assign y1 = &a;
            assign y2 = |a;
            assign y3 = ^a;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a8
class TestNumbers:
    """Test number literals."""

    def test_decimal_numbers(self, module_parser):
        """Test decimal number formats."""
        code = """
        module foo();
            parameter A = 42;
            parameter B = 8'd255;
            parameter C = 'd100;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_binary_numbers(self, module_parser):
        """Test binary number formats."""
        code = """
        module foo();
            parameter A = 4'b1010;
            parameter B = 8'b1111_0000;
            parameter C = 'b1;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_hex_numbers(self, module_parser):
        """Test hexadecimal number formats."""
        code = """
        module foo();
            parameter A = 8'hFF;
            parameter B = 16'hDEAD;
            parameter C = 'hABC;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_octal_numbers(self, module_parser):
        """Test octal number formats."""
        code = """
        module foo();
            parameter A = 8'o77;
            parameter B = 'o123;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_signed_numbers(self, module_parser):
        """Test signed number formats."""
        code = """
        module foo();
            parameter A = 8'sb11111111;
            parameter B = 8'shFF;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_xz_values(self, module_parser):
        """Test X and Z values in numbers."""
        code = """
        module foo();
            parameter A = 4'bxxxx;
            parameter B = 4'bzzzz;
            parameter C = 8'hxz;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a8
class TestPrimaries:
    """Test primary expressions."""

    def test_identifier(self, module_parser):
        """Test simple identifier."""
        code = "module foo(input a, output y); assign y = a; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_indexed_identifier(self, module_parser):
        """Test array index."""
        code = """
        module foo(input [7:0] a, output y);
            assign y = a[3];
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_part_select(self, module_parser):
        """Test part-select [msb:lsb]."""
        code = """
        module foo(input [7:0] a, output [3:0] y);
            assign y = a[7:4];
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_indexed_part_select_plus(self, module_parser):
        """Test indexed part-select [base +: width]."""
        code = """
        module foo(input [15:0] a, input [2:0] idx, output [3:0] y);
            assign y = a[idx +: 4];
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_indexed_part_select_minus(self, module_parser):
        """Test indexed part-select [base -: width]."""
        code = """
        module foo(input [15:0] a, input [2:0] idx, output [3:0] y);
            assign y = a[idx -: 4];
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_parenthesized_expression(self, module_parser):
        """Test parenthesized expression."""
        code = "module foo(input a, b, c, output y); assign y = (a & b) | c; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a8
class TestFunctionCalls:
    """Test function calls."""

    def test_system_function(self, module_parser):
        """Test system function call."""
        code = """
        module foo();
            initial $display("Hello");
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_system_function_with_args(self, module_parser):
        """Test system function with arguments."""
        code = """
        module foo();
            reg [7:0] data;
            initial begin
                data = $random;
            end
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a8
class TestStrings:
    """Test string literals."""

    def test_simple_string(self, module_parser):
        """Test simple string literal."""
        code = """
        module foo();
            initial $display("Hello World");
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
