"""Tests for width inference analysis pass.

Tests the ``infer_widths`` / ``infer_expr_width`` functions that populate
``Expression.inferred_width`` following IEEE 1364-2005 rules.
"""

import pytest

from veriforge.analysis.width_inference import (
    infer_expr_width,
    infer_widths,
    infer_widths_in_module,
    _declaration_width,
    _range_width,
)
from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    FunctionCall,
    Identifier,
    Literal,
    Mintypmax,
    PartSelect,
    Range,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from veriforge.model.nets import Net, NetKind
from veriforge.model.parameters import Parameter
from veriforge.model.ports import Port, PortDirection
from veriforge.model.variables import Variable, VariableKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def lit(val=0, width=None):
    """Shorthand for Literal."""
    return Literal(val, width=width)


def ident(name, resolved=None):
    """Shorthand for Identifier with optional resolved declaration."""
    i = Identifier(name)
    if resolved is not None:
        i.resolved = resolved
    return i


def make_range(msb_val, lsb_val=0):
    """Create a Range from integer values."""
    return Range(Literal(msb_val), Literal(lsb_val))


# ===== Literal tests =====


class TestLiteralWidth:
    """Width inference for Literal expressions."""

    def test_sized_literal(self):
        assert infer_expr_width(lit(255, width=8)) == 8

    def test_sized_literal_1bit(self):
        assert infer_expr_width(lit(1, width=1)) == 1

    def test_sized_literal_32bit(self):
        assert infer_expr_width(lit(0, width=32)) == 32

    def test_unsized_literal_defaults_32(self):
        assert infer_expr_width(lit(42)) == 32

    def test_unsized_zero(self):
        assert infer_expr_width(lit(0)) == 32


# ===== Identifier tests =====


class TestIdentifierWidth:
    """Width inference for Identifier expressions resolved to declarations."""

    def test_port_scalar(self):
        port = Port("a", PortDirection.INPUT)
        assert infer_expr_width(ident("a", port)) == 1

    def test_port_8bit(self):
        port = Port("a", PortDirection.INPUT, width=make_range(7))
        assert infer_expr_width(ident("a", port)) == 8

    def test_port_16bit(self):
        port = Port("a", PortDirection.OUTPUT, width=make_range(15))
        assert infer_expr_width(ident("a", port)) == 16

    def test_net_scalar(self):
        net = Net("w", NetKind.WIRE)
        assert infer_expr_width(ident("w", net)) == 1

    def test_net_4bit(self):
        net = Net("w", NetKind.WIRE, width=make_range(3))
        assert infer_expr_width(ident("w", net)) == 4

    def test_variable_reg_8bit(self):
        var = Variable("r", VariableKind.REG, width=make_range(7))
        assert infer_expr_width(ident("r", var)) == 8

    def test_variable_integer(self):
        var = Variable("i", VariableKind.INTEGER)
        assert infer_expr_width(ident("i", var)) == 32

    def test_variable_time(self):
        var = Variable("t", VariableKind.TIME)
        assert infer_expr_width(ident("t", var)) == 64

    def test_parameter_unsized(self):
        param = Parameter("WIDTH", default_value=lit(8))
        assert infer_expr_width(ident("WIDTH", param)) == 32

    def test_parameter_with_range(self):
        param = Parameter("MASK", width=make_range(7))
        assert infer_expr_width(ident("MASK", param)) == 8

    def test_unresolved_identifier(self):
        """Unresolved identifier returns None."""
        assert infer_expr_width(ident("unknown")) is None


# ===== UnaryOp tests =====


class TestUnaryOpWidth:
    """Width inference for unary operations."""

    def test_bitwise_not(self):
        assert infer_expr_width(UnaryOp("~", lit(0, width=8))) == 8

    def test_negate(self):
        assert infer_expr_width(UnaryOp("-", lit(0, width=16))) == 16

    def test_logical_not(self):
        assert infer_expr_width(UnaryOp("!", lit(0, width=8))) == 1

    def test_reduction_and(self):
        assert infer_expr_width(UnaryOp("&", lit(0, width=8))) == 1

    def test_reduction_or(self):
        assert infer_expr_width(UnaryOp("|", lit(0, width=16))) == 1

    def test_reduction_xor(self):
        assert infer_expr_width(UnaryOp("^", lit(0, width=4))) == 1

    def test_reduction_nand(self):
        assert infer_expr_width(UnaryOp("~&", lit(0, width=8))) == 1

    def test_reduction_nor(self):
        assert infer_expr_width(UnaryOp("~|", lit(0, width=8))) == 1

    def test_reduction_xnor(self):
        assert infer_expr_width(UnaryOp("~^", lit(0, width=8))) == 1


# ===== BinaryOp tests =====


class TestBinaryOpWidth:
    """Width inference for binary operations."""

    # Arithmetic — max(left, right)
    def test_add_same_width(self):
        assert infer_expr_width(BinaryOp("+", lit(0, 8), lit(0, 8))) == 8

    def test_add_different_width(self):
        assert infer_expr_width(BinaryOp("+", lit(0, 8), lit(0, 4))) == 8

    def test_subtract(self):
        assert infer_expr_width(BinaryOp("-", lit(0, 16), lit(0, 8))) == 16

    def test_multiply(self):
        assert infer_expr_width(BinaryOp("*", lit(0, 8), lit(0, 8))) == 8

    def test_divide(self):
        assert infer_expr_width(BinaryOp("/", lit(0, 16), lit(0, 8))) == 16

    def test_modulo(self):
        assert infer_expr_width(BinaryOp("%", lit(0, 8), lit(0, 4))) == 8

    def test_power(self):
        assert infer_expr_width(BinaryOp("**", lit(0, 8), lit(2))) == 32

    # Bitwise — max(left, right)
    def test_bitwise_and(self):
        assert infer_expr_width(BinaryOp("&", lit(0, 8), lit(0, 4))) == 8

    def test_bitwise_or(self):
        assert infer_expr_width(BinaryOp("|", lit(0, 16), lit(0, 8))) == 16

    def test_bitwise_xor(self):
        assert infer_expr_width(BinaryOp("^", lit(0, 4), lit(0, 4))) == 4

    # Shift — left width
    def test_left_shift(self):
        assert infer_expr_width(BinaryOp("<<", lit(0, 16), lit(2))) == 16

    def test_right_shift(self):
        assert infer_expr_width(BinaryOp(">>", lit(0, 8), lit(3))) == 8

    def test_arithmetic_left_shift(self):
        assert infer_expr_width(BinaryOp("<<<", lit(0, 32), lit(4))) == 32

    def test_arithmetic_right_shift(self):
        assert infer_expr_width(BinaryOp(">>>", lit(0, 16), lit(2))) == 16

    # Comparison — 1 bit
    def test_equal(self):
        assert infer_expr_width(BinaryOp("==", lit(0, 8), lit(0, 8))) == 1

    def test_not_equal(self):
        assert infer_expr_width(BinaryOp("!=", lit(0, 8), lit(0, 8))) == 1

    def test_less_than(self):
        assert infer_expr_width(BinaryOp("<", lit(0, 8), lit(0, 8))) == 1

    def test_greater_than(self):
        assert infer_expr_width(BinaryOp(">", lit(0, 8), lit(0, 8))) == 1

    def test_case_equal(self):
        assert infer_expr_width(BinaryOp("===", lit(0, 8), lit(0, 8))) == 1

    def test_case_not_equal(self):
        assert infer_expr_width(BinaryOp("!==", lit(0, 8), lit(0, 8))) == 1

    # Logical — 1 bit
    def test_logical_and(self):
        assert infer_expr_width(BinaryOp("&&", lit(0, 8), lit(0, 8))) == 1

    def test_logical_or(self):
        assert infer_expr_width(BinaryOp("||", lit(0, 8), lit(0, 8))) == 1


# ===== TernaryOp tests =====


class TestTernaryOpWidth:
    """Width inference for ternary (conditional) expressions."""

    def test_same_width_branches(self):
        expr = TernaryOp(lit(1, 1), lit(0, 8), lit(0, 8))
        assert infer_expr_width(expr) == 8

    def test_different_width_branches(self):
        expr = TernaryOp(lit(1, 1), lit(0, 16), lit(0, 8))
        assert infer_expr_width(expr) == 16

    def test_condition_ignored_for_result_width(self):
        """Condition width doesn't affect result width."""
        expr = TernaryOp(lit(0, 8), lit(0, 4), lit(0, 4))
        assert infer_expr_width(expr) == 4


# ===== Concatenation tests =====


class TestConcatenationWidth:
    """Width inference for concatenation expressions."""

    def test_two_parts(self):
        expr = Concatenation([lit(0, 8), lit(0, 4)])
        assert infer_expr_width(expr) == 12

    def test_three_parts(self):
        expr = Concatenation([lit(0, 8), lit(0, 8), lit(0, 8)])
        assert infer_expr_width(expr) == 24

    def test_single_part(self):
        expr = Concatenation([lit(0, 16)])
        assert infer_expr_width(expr) == 16

    def test_with_unknown_part(self):
        """If any part has unknown width, total is None."""
        expr = Concatenation([lit(0, 8), ident("x")])  # x is unresolved
        assert infer_expr_width(expr) is None


# ===== Replication tests =====


class TestReplicationWidth:
    """Width inference for replication expressions."""

    def test_replicate_4x3(self):
        expr = Replication(lit(4), lit(0, 3))
        assert infer_expr_width(expr) == 12

    def test_replicate_2x8(self):
        expr = Replication(lit(2), lit(0, 8))
        assert infer_expr_width(expr) == 16

    def test_non_constant_count(self):
        """Non-constant replication count → None."""
        expr = Replication(ident("N"), lit(0, 4))
        assert infer_expr_width(expr) is None


# ===== Select tests =====


class TestSelectWidth:
    """Width inference for bit/range/part select expressions."""

    def test_bit_select(self):
        assert infer_expr_width(BitSelect(lit(0, 8), lit(3))) == 1

    def test_range_select(self):
        assert infer_expr_width(RangeSelect(lit(0, 8), lit(7), lit(4))) == 4

    def test_range_select_single_bit(self):
        assert infer_expr_width(RangeSelect(lit(0, 8), lit(3), lit(3))) == 1

    def test_range_select_full(self):
        assert infer_expr_width(RangeSelect(lit(0, 16), lit(15), lit(0))) == 16

    def test_part_select_ascending(self):
        assert infer_expr_width(PartSelect(lit(0, 32), lit(8), lit(4), "+:")) == 4

    def test_part_select_descending(self):
        assert infer_expr_width(PartSelect(lit(0, 32), lit(15), lit(8), "-:")) == 8


# ===== FunctionCall tests =====


class TestFunctionCallWidth:
    """Width inference for function calls."""

    def test_clog2(self):
        assert infer_expr_width(FunctionCall("$clog2", [lit(256)], is_system=True)) == 32

    def test_time(self):
        assert infer_expr_width(FunctionCall("$time", [], is_system=True)) == 64

    def test_random(self):
        assert infer_expr_width(FunctionCall("$random", [], is_system=True)) == 32

    def test_signed_preserves_width(self):
        expr = FunctionCall("$signed", [lit(0, 8)], is_system=True)
        assert infer_expr_width(expr) == 8

    def test_unsigned_preserves_width(self):
        expr = FunctionCall("$unsigned", [lit(0, 16)], is_system=True)
        assert infer_expr_width(expr) == 16

    def test_user_function_unknown(self):
        assert infer_expr_width(FunctionCall("my_func", [lit(0)], is_system=False)) is None


# ===== StringLiteral tests =====


class TestStringLiteralWidth:
    """Width inference for string literals."""

    def test_hello(self):
        assert infer_expr_width(StringLiteral("hello")) == 40

    def test_empty_string(self):
        assert infer_expr_width(StringLiteral("")) == 0

    def test_single_char(self):
        assert infer_expr_width(StringLiteral("A")) == 8


# ===== Mintypmax tests =====


class TestMintymaxWidth:
    """Width inference for min:typ:max expressions."""

    def test_mintypmax_uses_typ(self):
        expr = Mintypmax(lit(0, 8), lit(0, 16), lit(0, 32))
        assert infer_expr_width(expr) == 16


# ===== Nested expression tests =====


class TestNestedExpressions:
    """Width inference for complex nested expressions."""

    def test_add_of_shifted(self):
        """(a << 1) + b where a=8, b=8 → 8."""
        a = lit(0, 8)
        b = lit(0, 8)
        expr = BinaryOp("+", BinaryOp("<<", a, lit(1)), b)
        assert infer_expr_width(expr) == 8

    def test_concat_with_binary(self):
        """{a + b, c} → 8 + 4 = 12."""
        expr = Concatenation([BinaryOp("+", lit(0, 8), lit(0, 8)), lit(0, 4)])
        assert infer_expr_width(expr) == 12

    def test_ternary_with_concat(self):
        """sel ? {a,b} : c where {a,b}=12, c=8 → 12."""
        expr = TernaryOp(lit(0, 1), Concatenation([lit(0, 8), lit(0, 4)]), lit(0, 8))
        assert infer_expr_width(expr) == 12

    def test_chained_comparisons(self):
        """(a == b) && (c > d) → 1."""
        left = BinaryOp("==", lit(0, 8), lit(0, 8))
        right = BinaryOp(">", lit(0, 16), lit(0, 16))
        expr = BinaryOp("&&", left, right)
        assert infer_expr_width(expr) == 1

    def test_replicated_bit_select(self):
        """{4{a[0]}} → 4."""
        expr = Replication(lit(4), BitSelect(lit(0, 8), lit(0)))
        assert infer_expr_width(expr) == 4


# ===== Helper function tests =====


class TestHelperFunctions:
    """Tests for internal helper functions."""

    def test_range_width_none(self):
        assert _range_width(None) == 1

    def test_range_width_8bit(self):
        assert _range_width(make_range(7, 0)) == 8

    def test_range_width_1bit(self):
        assert _range_width(make_range(0, 0)) == 1

    def test_declaration_width_port(self):
        assert _declaration_width(Port("x", PortDirection.INPUT, width=make_range(7))) == 8

    def test_declaration_width_scalar_port(self):
        assert _declaration_width(Port("x", PortDirection.INPUT)) == 1

    def test_declaration_width_integer_var(self):
        assert _declaration_width(Variable("i", VariableKind.INTEGER)) == 32

    def test_declaration_width_net(self):
        assert _declaration_width(Net("w", width=make_range(15))) == 16


# ===== Integration tests with parsed Verilog =====


class TestIntegrationParsed:
    """Width inference on parsed Verilog designs."""

    def test_simple_assign(self):
        from veriforge.verilog_parser import verilog_parser
        from veriforge.transforms import tree_to_design
        from veriforge.analysis import analyze_design

        vp = verilog_parser(start="module_declaration")
        tree = vp.build_tree("module m(input [7:0] a, input [7:0] b, output [7:0] y); assign y = a + b; endmodule")
        design = tree_to_design(tree)
        analyze_design(design)
        infer_widths(design)

        assign = design.modules[0].continuous_assigns[0]
        assert assign.rhs.inferred_width == 8
        assert assign.lhs.inferred_width == 8

    def test_comparison_width(self):
        from veriforge.verilog_parser import verilog_parser
        from veriforge.transforms import tree_to_design
        from veriforge.analysis import analyze_design

        vp = verilog_parser(start="module_declaration")
        tree = vp.build_tree("module m(input [7:0] a, input [7:0] b, output eq); assign eq = (a == b); endmodule")
        design = tree_to_design(tree)
        analyze_design(design)
        infer_widths(design)

        assign = design.modules[0].continuous_assigns[0]
        assert assign.rhs.inferred_width == 1

    def test_ternary_in_assign(self):
        from veriforge.verilog_parser import verilog_parser
        from veriforge.transforms import tree_to_design
        from veriforge.analysis import analyze_design

        vp = verilog_parser(start="module_declaration")
        tree = vp.build_tree(
            "module m(input sel, input [15:0] a, input [7:0] b, output [15:0] y);\n  assign y = sel ? a : b;\nendmodule"
        )
        design = tree_to_design(tree)
        analyze_design(design)
        infer_widths(design)

        assign = design.modules[0].continuous_assigns[0]
        # Ternary takes max(16, 8) = 16
        assert assign.rhs.inferred_width == 16


# ===== Caching / idempotency =====


class TestCaching:
    """Width inference should be idempotent and cache results."""

    def test_double_inference_same_result(self):
        expr = BinaryOp("+", lit(0, 8), lit(0, 4))
        w1 = infer_expr_width(expr)
        w2 = infer_expr_width(expr)
        assert w1 == w2 == 8

    def test_inferred_width_set(self):
        expr = lit(0, 16)
        assert expr.inferred_width is None  # before
        infer_expr_width(expr)
        assert expr.inferred_width == 16  # after
