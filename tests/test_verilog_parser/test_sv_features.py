"""Tests for SystemVerilog low-hanging fruit grammar extensions.

Tests cover:
- logic type declarations
- always_comb, always_ff, always_latch constructs
- unique case, priority case, unique0 case qualifiers
- SV integer types: bit, byte, shortint, int, longint
"""

import pytest

from tests.conftest import ParseHelper


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(scope="module")
def verilog_ph():
    """Parser for full verilog programs."""
    return ParseHelper("verilog")


@pytest.fixture(scope="module")
def module_ph():
    """Parser for module_declaration."""
    return ParseHelper("module_declaration")


@pytest.fixture(scope="module")
def statement_ph():
    """Parser for statements."""
    return ParseHelper("statement")


@pytest.fixture(scope="module")
def case_ph():
    """Parser for case_statement."""
    return ParseHelper("case_statement")


@pytest.fixture(scope="module")
def net_decl_ph():
    """Parser for net_declaration."""
    return ParseHelper("net_declaration")


@pytest.fixture(scope="module")
def item_decl_ph():
    """Parser for module_or_generate_item_declaration."""
    return ParseHelper("module_or_generate_item_declaration")


# ============================================================================
# logic type
# ============================================================================


class TestLogicType:
    """Test 'logic' as a net type and variable declaration."""

    def test_logic_net_declaration(self, net_decl_ph):
        """logic used as a net type in net_declaration."""
        tree = net_decl_ph.parse("logic my_signal;")
        assert tree is not None
        assert tree.data == "net_declaration"

    def test_logic_net_declaration_range(self, net_decl_ph):
        """logic with a bus range."""
        tree = net_decl_ph.parse("logic [7:0] data_bus;")
        assert tree is not None

    def test_logic_net_declaration_signed(self, net_decl_ph):
        """logic signed declaration."""
        tree = net_decl_ph.parse("logic signed [15:0] signed_val;")
        assert tree is not None

    def test_logic_multiple_signals(self, net_decl_ph):
        """logic with multiple signal names."""
        tree = net_decl_ph.parse("logic [3:0] a, b, c;")
        assert tree is not None

    def test_logic_variable_declaration(self, item_decl_ph):
        """logic used as a variable declaration (like reg)."""
        tree = item_decl_ph.parse("logic [31:0] operand_a;")
        assert tree is not None

    def test_logic_in_module(self, module_ph):
        """logic declaration inside a module."""
        code = """module test(input clk, output logic [7:0] data);
            logic [31:0] internal;
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_logic_port_declaration(self, module_ph):
        """logic type in ANSI-style port declaration."""
        code = """module test(
            input  logic       clk,
            input  logic       rst_n,
            output logic [7:0] data_o
        );
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_typedef_port_with_unpacked_dimension(self, module_ph):
        """typedef-based ANSI ports may carry dimensions before the identifier."""
        code = """module test #(
            parameter int unsigned NumIn = 3
        ) (
            input payload_t [NumIn-1:0] data_i
        );
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_typedef_internal_declaration_with_dimensions(self, module_ph):
        """typedef-based declarations may carry dimensions before the identifier."""
        code = """module test;
            payload_t [1:0][2:0] out_data;
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None


# ============================================================================
# always_comb
# ============================================================================


class TestAlwaysComb:
    """Test always_comb construct."""

    def test_always_comb_simple(self, module_ph):
        """Basic always_comb with single assignment."""
        code = """module test(input a, input b, output reg y);
            always_comb begin
                y = a & b;
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_always_comb_if_else(self, module_ph):
        """always_comb with if/else."""
        code = """module test(input sel, input a, input b, output reg y);
            always_comb begin
                if (sel)
                    y = a;
                else
                    y = b;
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_always_comb_case(self, module_ph):
        """always_comb with case statement."""
        code = """module test(input [1:0] sel, input a, input b, input c, input d, output reg y);
            always_comb begin
                case (sel)
                    2'b00: y = a;
                    2'b01: y = b;
                    2'b10: y = c;
                    default: y = d;
                endcase
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_always_comb_no_sensitivity(self, module_ph):
        """always_comb has no sensitivity list (unlike always @(*))."""
        code = """module test(input a, output reg y);
            always_comb y = a;
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_always_comb_tree_structure(self, verilog_ph):
        """Verify always_comb_construct appears in parse tree."""
        code = """module test(input a, output reg y);
            always_comb y = a;
        endmodule"""
        tree = verilog_ph.parse(code)
        constructs = list(tree.find_data("always_comb_construct"))
        assert len(constructs) == 1

    def test_always_comb_unnamed_block_local_decl(self, module_ph):
        """Unnamed begin/end blocks may declare local variables."""
        code = """module test(input logic a, output logic y);
            always_comb begin
                logic tmp;
                tmp = ~a;
                y = tmp;
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None


# ============================================================================
# always_ff
# ============================================================================


class TestAlwaysFF:
    """Test always_ff construct."""

    def test_always_ff_posedge_clk(self, module_ph):
        """always_ff with posedge clock."""
        code = """module test(input clk, input d, output reg q);
            always_ff @(posedge clk) begin
                q <= d;
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_always_ff_clk_and_reset(self, module_ph):
        """always_ff with clock and async reset."""
        code = """module test(input clk, input rst_n, input d, output reg q);
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    q <= 1'b0;
                else
                    q <= d;
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_always_ff_single_statement(self, module_ph):
        """always_ff with a single statement (no begin/end)."""
        code = """module test(input clk, input d, output reg q);
            always_ff @(posedge clk) q <= d;
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_always_ff_tree_structure(self, verilog_ph):
        """Verify always_ff_construct appears in parse tree."""
        code = """module test(input clk, input d, output reg q);
            always_ff @(posedge clk) q <= d;
        endmodule"""
        tree = verilog_ph.parse(code)
        constructs = list(tree.find_data("always_ff_construct"))
        assert len(constructs) == 1


# ============================================================================
# always_latch
# ============================================================================


class TestAlwaysLatch:
    """Test always_latch construct."""

    def test_always_latch_basic(self, module_ph):
        """Basic always_latch for latch inference."""
        code = """module test(input en, input d, output reg q);
            always_latch begin
                if (en)
                    q <= d;
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_always_latch_tree_structure(self, verilog_ph):
        """Verify always_latch_construct appears in parse tree."""
        code = """module test(input en, input d, output reg q);
            always_latch begin
                if (en) q <= d;
            end
        endmodule"""
        tree = verilog_ph.parse(code)
        constructs = list(tree.find_data("always_latch_construct"))
        assert len(constructs) == 1


class TestForkJoinBlockDecls:
    """Test local declarations inside unnamed fork/join blocks."""

    def test_unnamed_fork_block_local_decl(self, module_ph):
        code = """module test;
            initial fork
                logic tmp;
                tmp = 1'b1;
            join
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None


# ============================================================================
# unique case / priority case
# ============================================================================


class TestCaseQualifiers:
    """Test unique/priority/unique0 case qualifiers."""

    def test_unique_case(self, case_ph):
        """unique case statement."""
        code = "unique case (sel) 2'b00: y = a; 2'b01: y = b; default: y = 0; endcase"
        tree = case_ph.parse(code)
        assert tree is not None
        qualifiers = list(tree.find_data("case_qualifier"))
        assert len(qualifiers) == 1

    def test_priority_case(self, case_ph):
        """priority case statement."""
        code = "priority case (sel) 2'b00: y = a; default: y = 0; endcase"
        tree = case_ph.parse(code)
        assert tree is not None
        qualifiers = list(tree.find_data("case_qualifier"))
        assert len(qualifiers) == 1

    def test_unique0_case(self, case_ph):
        """unique0 case statement."""
        code = "unique0 case (sel) 2'b00: y = a; 2'b01: y = b; endcase"
        tree = case_ph.parse(code)
        assert tree is not None

    def test_unique_casex(self, case_ph):
        """unique casex."""
        code = "unique casex (data) 4'b1???: high = 1; default: high = 0; endcase"
        tree = case_ph.parse(code)
        assert tree is not None

    def test_unique_casez(self, case_ph):
        """unique casez."""
        code = "unique casez (data) 4'b1???: high = 1; default: high = 0; endcase"
        tree = case_ph.parse(code)
        assert tree is not None

    def test_priority_casez(self, case_ph):
        """priority casez."""
        code = "priority casez (data) 4'b1???: high = 1; default: high = 0; endcase"
        tree = case_ph.parse(code)
        assert tree is not None

    def test_plain_case_still_works(self, case_ph):
        """Verify regular case without qualifier still works."""
        code = "case (sel) 2'b00: y = a; default: y = 0; endcase"
        tree = case_ph.parse(code)
        assert tree is not None
        qualifiers = list(tree.find_data("case_qualifier"))
        assert len(qualifiers) == 0

    def test_unique_case_in_always_comb(self, module_ph):
        """unique case inside always_comb (very common pattern in real RTL)."""
        code = """module test(input [1:0] sel, input a, input b, output reg y);
            always_comb begin
                unique case (sel)
                    2'b00: y = a;
                    2'b01: y = b;
                    default: y = 1'b0;
                endcase
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None


class TestInsideOperator:
    """Test SystemVerilog inside operator parsing."""

    def test_inside_range_in_if(self, module_ph):
        code = """module test(input [2:0] x, output logic y);
            always_comb begin
                if (x inside {[1:3]})
                    y = 1'b1;
                else
                    y = 1'b0;
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None


# ============================================================================
# SV integer types: bit, byte, shortint, int, longint
# ============================================================================


class TestSVIntegerTypes:
    """Test SystemVerilog integer data types."""

    def test_bit_declaration(self, item_decl_ph):
        """bit type declaration."""
        tree = item_decl_ph.parse("bit [3:0] nibble;")
        assert tree is not None

    def test_bit_single(self, item_decl_ph):
        """Single-bit 'bit' declaration."""
        tree = item_decl_ph.parse("bit flag;")
        assert tree is not None

    def test_byte_declaration(self, item_decl_ph):
        """byte type declaration."""
        tree = item_decl_ph.parse("byte status;")
        assert tree is not None

    def test_shortint_declaration(self, item_decl_ph):
        """shortint type declaration."""
        tree = item_decl_ph.parse("shortint offset;")
        assert tree is not None

    def test_int_declaration(self, item_decl_ph):
        """int type declaration."""
        tree = item_decl_ph.parse("int count;")
        assert tree is not None

    def test_int_unsigned(self, item_decl_ph):
        """int unsigned declaration (common in for loops)."""
        tree = item_decl_ph.parse("int unsigned i;")
        assert tree is not None

    def test_longint_declaration(self, item_decl_ph):
        """longint type declaration."""
        tree = item_decl_ph.parse("longint timestamp;")
        assert tree is not None

    def test_sv_types_in_module(self, module_ph):
        """SV integer types inside a module."""
        code = """module test();
            bit [7:0] data;
            byte status;
            shortint offset;
            int count;
            longint timestamp;
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_logic_declaration_item(self, item_decl_ph):
        """logic as a variable declaration through module_or_generate_item_declaration."""
        tree = item_decl_ph.parse("logic [15:0] addr;")
        assert tree is not None


# ============================================================================
# Integration: Real-world-like SV patterns
# ============================================================================


class TestSVIntegration:
    """Test realistic SV code patterns from production RTL."""

    def test_ibex_style_alu_snippet(self, module_ph):
        """Pattern inspired by Ibex ALU: logic declarations + always_comb + unique case."""
        code = """module alu(
            input  wire [31:0] operand_a,
            input  wire [31:0] operand_b,
            input  wire [3:0]  operator,
            output reg  [31:0] result
        );
            logic [31:0] adder_result;
            logic [31:0] logic_result;

            assign adder_result = operand_a + operand_b;
            assign logic_result = operand_a & operand_b;

            always_comb begin
                unique case (operator)
                    4'b0000: result = adder_result;
                    4'b0001: result = logic_result;
                    default: result = 32'h0;
                endcase
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_ibex_style_register(self, module_ph):
        """Pattern inspired by Ibex: always_ff with async reset."""
        code = """module register(
            input  wire        clk,
            input  wire        rst_n,
            input  wire [31:0] d,
            output reg  [31:0] q
        );
            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    q <= 32'h0;
                else
                    q <= d;
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_mixed_verilog_sv_module(self, module_ph):
        """Module mixing Verilog-2005 and SV features."""
        code = """module mixed(
            input  wire       clk,
            input  wire       rst_n,
            input  wire [7:0] data_in,
            output reg  [7:0] data_out
        );
            wire [7:0] intermediate;
            logic [7:0] processed;
            reg [7:0] stored;

            assign intermediate = data_in ^ 8'hFF;

            always_comb begin
                processed = intermediate + 8'h01;
            end

            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    stored <= 8'h0;
                else
                    stored <= processed;
            end

            assign data_out = stored;
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_priority_case_mux(self, module_ph):
        """Priority case for priority-encoded mux."""
        code = """module priority_mux(
            input  wire [3:0] req,
            output reg  [1:0] grant
        );
            always_comb begin
                priority casez (req)
                    4'b1???: grant = 2'b11;
                    4'b01??: grant = 2'b10;
                    4'b001?: grant = 2'b01;
                    4'b0001: grant = 2'b00;
                    default: grant = 2'b00;
                endcase
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_always_latch_enable(self, module_ph):
        """always_latch for transparent latch."""
        code = """module latch(
            input  wire       en,
            input  wire [7:0] d,
            output reg  [7:0] q
        );
            always_latch begin
                if (en)
                    q <= d;
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None

    def test_multiple_always_blocks(self, module_ph):
        """Module with multiple SV always block types."""
        code = """module multi_always(
            input  wire       clk,
            input  wire       rst_n,
            input  wire       en,
            input  wire [7:0] a,
            input  wire [7:0] b,
            output reg  [7:0] sum,
            output reg  [7:0] latched,
            output reg  [7:0] registered
        );
            always_comb begin
                sum = a + b;
            end

            always_latch begin
                if (en) latched <= sum;
            end

            always_ff @(posedge clk or negedge rst_n) begin
                if (!rst_n)
                    registered <= 8'h0;
                else
                    registered <= sum;
            end
        endmodule"""
        tree = module_ph.parse(code)
        assert tree is not None
