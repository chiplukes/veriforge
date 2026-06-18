"""
Test grammar rules from Section A.6 - Behavioral statements.

These tests verify the parsing of:
- Continuous assignments (A.6.1)
- Procedural blocks and assignments (A.6.2)
- Parallel/sequential blocks (A.6.3)
- Statements (A.6.4-A.6.9)
"""

import pytest


@pytest.mark.section_a6
class TestContinuousAssignments:
    """Test continuous assignment statements."""

    def test_simple_assign(self, module_parser):
        """Test simple continuous assignment."""
        code = "module foo(input a, output y); assign y = a; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None
        assigns = list(tree.find_data("continuous_assign"))
        assert len(assigns) == 1

    def test_assign_with_operator(self, module_parser):
        """Test assignment with binary operator."""
        code = "module foo(input a, b, output y); assign y = a & b; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_assign_with_delay(self, module_parser):
        """Test assignment with delay."""
        code = "module foo(input a, output y); assign #10 y = a; endmodule"
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_multiple_assigns(self, module_parser):
        """Test multiple continuous assignments."""
        code = """
        module foo(input a, b, output y, z);
            assign y = a & b;
            assign z = a | b;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a6
class TestProceduralBlocks:
    """Test procedural blocks (always, initial)."""

    def test_always_posedge(self, module_parser):
        """Test always block with posedge sensitivity."""
        code = """
        module foo(input clk, d, output reg q);
            always @(posedge clk) q <= d;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None
        always_blocks = list(tree.find_data("always_construct"))
        assert len(always_blocks) == 1

    def test_always_negedge(self, module_parser):
        """Test always block with negedge sensitivity."""
        code = """
        module foo(input clk, d, output reg q);
            always @(negedge clk) q <= d;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_always_combinational(self, module_parser):
        """Test combinational always block."""
        code = """
        module foo(input a, b, output reg y);
            always @(a or b) y = a & b;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_always_star(self, module_parser):
        """Test always block with wildcard sensitivity."""
        code = """
        module foo(input a, b, output reg y);
            always @(*) y = a & b;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_initial_block(self, module_parser):
        """Test initial block."""
        code = """
        module foo();
            reg [7:0] mem;
            initial mem = 8'h00;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None
        initial_blocks = list(tree.find_data("initial_construct"))
        assert len(initial_blocks) == 1


@pytest.mark.section_a6
class TestBlockingNonblocking:
    """Test blocking and non-blocking assignments."""

    def test_blocking_assignment(self, module_parser):
        """Test blocking assignment (=)."""
        code = """
        module foo(input a, output reg y);
            always @(a) y = a;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_nonblocking_assignment(self, module_parser):
        """Test non-blocking assignment (<=)."""
        code = """
        module foo(input clk, d, output reg q);
            always @(posedge clk) q <= d;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a6
class TestConditionalStatements:
    """Test conditional statements (if, case)."""

    def test_if_statement(self, module_parser):
        """Test simple if statement."""
        code = """
        module foo(input sel, a, b, output reg y);
            always @(*) if (sel) y = a; else y = b;
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_if_else_if(self, module_parser):
        """Test if-else-if chain."""
        code = """
        module foo(input [1:0] sel, input a, b, c, output reg y);
            always @(*) begin
                if (sel == 2'b00) y = a;
                else if (sel == 2'b01) y = b;
                else y = c;
            end
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_case_statement(self, module_parser):
        """Test case statement."""
        code = """
        module foo(input [1:0] sel, input a, b, c, d, output reg y);
            always @(*) begin
                case (sel)
                    2'b00: y = a;
                    2'b01: y = b;
                    2'b10: y = c;
                    default: y = d;
                endcase
            end
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a6
class TestLoopStatements:
    """Test loop statements (for, while, repeat)."""

    def test_for_loop(self, module_parser):
        """Test for loop."""
        code = """
        module foo();
            integer i;
            reg [7:0] mem [0:15];
            initial begin
                for (i = 0; i < 16; i = i + 1) mem[i] = 0;
            end
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_while_loop(self, module_parser):
        """Test while loop."""
        code = """
        module foo();
            integer count;
            initial begin
                count = 0;
                while (count < 10) count = count + 1;
            end
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


@pytest.mark.section_a6
class TestSequentialBlocks:
    """Test begin-end blocks."""

    def test_simple_begin_end(self, module_parser):
        """Test simple begin-end block."""
        code = """
        module foo(input clk, output reg [1:0] q);
            always @(posedge clk) begin
                q[0] <= 1'b1;
                q[1] <= 1'b0;
            end
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None

    def test_named_block(self, module_parser):
        """Test named begin-end block."""
        code = """
        module foo(input clk, output reg q);
            always @(posedge clk) begin : my_block
                q <= 1'b1;
            end
        endmodule
        """
        tree = module_parser.build_tree(code)
        assert tree is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
