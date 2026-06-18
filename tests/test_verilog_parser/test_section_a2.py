"""
Test grammar rules from Section A.2 - Declarations.

These tests verify the parsing of:
- Declaration types (A.2.1)
- Declaration data types (A.2.2)
- Declaration lists (A.2.3)
- Declaration assignments (A.2.4)
- Declaration ranges (A.2.5)
- Function/task declarations (A.2.6/A.2.7)
- Block item declarations (A.2.8)
"""

import pytest


@pytest.mark.section_a2
class TestParameterDeclarations:
    """Test parameter declarations."""

    def test_simple_parameter(self, module_parser):
        """Test simple parameter declaration."""
        code = "module foo(); parameter WIDTH = 8; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None
        params = list(tree.find_data("parameter_declaration"))
        assert len(params) >= 1

    def test_localparam(self, module_parser):
        """Test localparam declaration."""
        code = "module foo(); localparam DEPTH = 16; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_parameter_with_range(self, module_parser):
        """Test parameter with bit range."""
        code = "module foo(); parameter [7:0] INIT_VAL = 8'hFF; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_parameter_with_type(self, module_parser):
        """Test parameter with type specifier."""
        code = "module foo(); parameter integer COUNT = 100; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a2
class TestNetDeclarations:
    """Test net declarations."""

    def test_simple_wire(self, module_parser):
        """Test simple wire declaration."""
        code = "module foo(); wire a; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_wire_with_range(self, module_parser):
        """Test wire with bit range."""
        code = "module foo(); wire [7:0] data; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_multiple_wires(self, module_parser):
        """Test multiple wire declarations."""
        code = "module foo(); wire a, b, c; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_tri_net(self, module_parser):
        """Test tri net type."""
        code = "module foo(); tri [7:0] bus; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_wire_with_assignment(self, module_parser):
        """Test wire with inline assignment."""
        code = "module foo(input a, b); wire y = a & b; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a2
class TestRegDeclarations:
    """Test reg declarations."""

    def test_simple_reg(self, module_parser):
        """Test simple reg declaration."""
        code = "module foo(); reg q; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_reg_with_range(self, module_parser):
        """Test reg with bit range."""
        code = "module foo(); reg [7:0] counter; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_reg_array(self, module_parser):
        """Test reg array (memory)."""
        code = "module foo(); reg [7:0] mem [0:255]; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_signed_reg(self, module_parser):
        """Test signed reg declaration."""
        code = "module foo(); reg signed [7:0] sdata; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a2
class TestIntegerDeclarations:
    """Test integer/real declarations."""

    def test_integer_declaration(self, module_parser):
        """Test integer declaration."""
        code = "module foo(); integer count; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_multiple_integers(self, module_parser):
        """Test multiple integer declarations."""
        code = "module foo(); integer i, j, k; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_real_declaration(self, module_parser):
        """Test real declaration."""
        code = "module foo(); real voltage; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_time_declaration(self, module_parser):
        """Test time declaration."""
        code = "module foo(); time timestamp; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a2
class TestFunctionDeclarations:
    """Test function declarations."""

    def test_simple_function(self, module_parser):
        """Test simple function declaration."""
        code = """
        module foo();
            function [7:0] add;
                input [7:0] a, b;
                add = a + b;
            endfunction
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_function_with_reg(self, module_parser):
        """Test function with local reg."""
        code = """
        module foo();
            function [7:0] compute;
                input [7:0] x;
                reg [7:0] temp;
                begin
                    temp = x + 1;
                    compute = temp;
                end
            endfunction
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_automatic_function(self, module_parser):
        """Test automatic (recursive) function."""
        code = """
        module foo();
            function automatic [31:0] factorial;
                input [31:0] n;
                if (n <= 1)
                    factorial = 1;
                else
                    factorial = n * factorial(n - 1);
            endfunction
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a2
class TestTaskDeclarations:
    """Test task declarations."""

    def test_simple_task(self, module_parser):
        """Test simple task declaration."""
        code = """
        module foo();
            task do_something;
                input [7:0] data;
                begin
                    $display("Data: %h", data);
                end
            endtask
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_task_with_outputs(self, module_parser):
        """Test task with output ports."""
        code = """
        module foo();
            task compute;
                input [7:0] a, b;
                output [8:0] sum;
                sum = a + b;
            endtask
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a2
class TestGenvarDeclarations:
    """Test generate variable declarations."""

    def test_genvar(self, module_parser):
        """Test genvar declaration."""
        code = """
        module foo();
            genvar i;
            generate
                for (i = 0; i < 4; i = i + 1) begin : gen_block
                    wire w;
                end
            endgenerate
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
