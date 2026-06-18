"""Tests for constant folding and parameter evaluation.

Tests the ``const_int()``, ``const_fold()``, and ``const_range_width()``
functions, including parameter resolution, system function evaluation,
and integration with the width inference pass.
"""

import pytest

from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
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
from veriforge.model.parameters import Parameter
from veriforge.analysis.const_fold import (
    const_fold,
    const_int,
    const_range_width,
    fold_constants,
    fold_constants_in_module,
    _binary_op,
    _unary_op,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _lit(val, *, width=None):
    """Shorthand for Literal."""
    return Literal(val, width=width)


def _binop(op, left, right):
    """Shorthand for BinaryOp with int values."""
    l = left if isinstance(left, Expression) else _lit(left)
    r = right if isinstance(right, Expression) else _lit(right)
    return BinaryOp(op, l, r)


def _unop(op, val):
    """Shorthand for UnaryOp with int value."""
    operand = val if isinstance(val, Expression) else _lit(val)
    return UnaryOp(op, operand)


def _param(name, default_value):
    """Shorthand for Parameter with default."""
    v = default_value if isinstance(default_value, Expression) else _lit(default_value)
    return Parameter(name, default_value=v)


def _ident(name, resolved=None):
    """Shorthand for Identifier with optional resolved target."""
    ident = Identifier(name)
    ident.resolved = resolved
    return ident


# ===== Literal folding =====


class TestLiteralFolding:
    def test_int_literal(self):
        assert const_int(_lit(42)) == 42

    def test_zero(self):
        assert const_int(_lit(0)) == 0

    def test_negative(self):
        assert const_int(_lit(-1)) == -1

    def test_large_value(self):
        assert const_int(_lit(0xDEADBEEF)) == 0xDEADBEEF

    def test_float_literal(self):
        assert const_int(Literal(3.7)) == 3

    def test_string_literal_returns_none(self):
        assert const_int(StringLiteral("hello")) is None

    def test_sized_literal(self):
        assert const_int(Literal(255, width=8)) == 255


# ===== Unary operators =====


class TestUnaryOps:
    def test_plus(self):
        assert const_int(_unop("+", 5)) == 5

    def test_negate(self):
        assert const_int(_unop("-", 5)) == -5

    def test_bitwise_not(self):
        assert const_int(_unop("~", 0)) == ~0

    def test_logical_not_true(self):
        assert const_int(_unop("!", 0)) == 1

    def test_logical_not_false(self):
        assert const_int(_unop("!", 7)) == 0

    def test_reduction_or_nonzero(self):
        assert const_int(_unop("|", 0xFF)) == 1

    def test_reduction_or_zero(self):
        assert const_int(_unop("|", 0)) == 0

    def test_reduction_nor_zero(self):
        assert const_int(_unop("~|", 0)) == 1

    def test_reduction_nor_nonzero(self):
        assert const_int(_unop("~|", 1)) == 0

    def test_reduction_xor_even_parity(self):
        # 0xFF = 8 ones → even parity → 0
        assert const_int(_unop("^", 0xFF)) == 0

    def test_reduction_xor_odd_parity(self):
        # 0x07 = 3 ones → odd parity → 1
        assert const_int(_unop("^", 0x07)) == 1

    def test_reduction_and_returns_none(self):
        # Cannot determine without width
        assert const_int(_unop("&", 0xFF)) is None

    def test_unknown_op_returns_none(self):
        assert _unary_op("???", 5) is None


# ===== Binary arithmetic =====


class TestBinaryArithmetic:
    def test_add(self):
        assert const_int(_binop("+", 3, 4)) == 7

    def test_sub(self):
        assert const_int(_binop("-", 10, 3)) == 7

    def test_mul(self):
        assert const_int(_binop("*", 6, 7)) == 42

    def test_div(self):
        assert const_int(_binop("/", 15, 4)) == 3  # truncating

    def test_div_by_zero(self):
        assert const_int(_binop("/", 15, 0)) is None

    def test_mod(self):
        assert const_int(_binop("%", 15, 4)) == 3

    def test_mod_by_zero(self):
        assert const_int(_binop("%", 15, 0)) is None

    def test_power(self):
        assert const_int(_binop("**", 2, 10)) == 1024

    def test_power_negative_exp(self):
        assert const_int(_binop("**", 2, -1)) == 0


# ===== Binary bitwise =====


class TestBinaryBitwise:
    def test_and(self):
        assert const_int(_binop("&", 0xFF, 0x0F)) == 0x0F

    def test_or(self):
        assert const_int(_binop("|", 0xF0, 0x0F)) == 0xFF

    def test_xor(self):
        assert const_int(_binop("^", 0xFF, 0x0F)) == 0xF0

    def test_xnor(self):
        result = const_int(_binop("~^", 0xFF, 0x0F))
        assert result == ~(0xFF ^ 0x0F)

    def test_xnor_alt(self):
        result = const_int(_binop("^~", 0xFF, 0x0F))
        assert result == ~(0xFF ^ 0x0F)


# ===== Binary shifts =====


class TestBinaryShifts:
    def test_left_shift(self):
        assert const_int(_binop("<<", 1, 4)) == 16

    def test_right_shift(self):
        assert const_int(_binop(">>", 16, 4)) == 1

    def test_arith_left_shift(self):
        assert const_int(_binop("<<<", 1, 4)) == 16

    def test_arith_right_shift(self):
        assert const_int(_binop(">>>", -16, 2)) == -4

    def test_negative_shift_returns_none(self):
        assert const_int(_binop("<<", 1, -1)) is None


# ===== Binary comparison =====


class TestBinaryComparison:
    def test_eq_true(self):
        assert const_int(_binop("==", 5, 5)) == 1

    def test_eq_false(self):
        assert const_int(_binop("==", 5, 6)) == 0

    def test_ne(self):
        assert const_int(_binop("!=", 5, 6)) == 1

    def test_lt(self):
        assert const_int(_binop("<", 3, 5)) == 1

    def test_le(self):
        assert const_int(_binop("<=", 5, 5)) == 1

    def test_gt(self):
        assert const_int(_binop(">", 5, 3)) == 1

    def test_ge(self):
        assert const_int(_binop(">=", 5, 5)) == 1

    def test_case_eq(self):
        assert const_int(_binop("===", 5, 5)) == 1

    def test_case_ne(self):
        assert const_int(_binop("!==", 5, 5)) == 0


# ===== Binary logical =====


class TestBinaryLogical:
    def test_and_both_true(self):
        assert const_int(_binop("&&", 1, 1)) == 1

    def test_and_one_false(self):
        assert const_int(_binop("&&", 1, 0)) == 0

    def test_or_one_true(self):
        assert const_int(_binop("||", 0, 1)) == 1

    def test_or_both_false(self):
        assert const_int(_binop("||", 0, 0)) == 0

    def test_unknown_binop(self):
        assert _binary_op("???", 5, 5) is None


# ===== Ternary =====


class TestTernaryOp:
    def test_condition_true(self):
        expr = TernaryOp(_lit(1), _lit(10), _lit(20))
        assert const_int(expr) == 10

    def test_condition_false(self):
        expr = TernaryOp(_lit(0), _lit(10), _lit(20))
        assert const_int(expr) == 20

    def test_nonzero_is_true(self):
        expr = TernaryOp(_lit(42), _lit(10), _lit(20))
        assert const_int(expr) == 10

    def test_unknown_condition(self):
        expr = TernaryOp(Identifier("x"), _lit(10), _lit(20))
        assert const_int(expr) is None


# ===== Parameter resolution =====


class TestParameterResolution:
    def test_simple_param(self):
        p = _param("WIDTH", 8)
        ident = _ident("WIDTH", resolved=p)
        assert const_int(ident) == 8

    def test_param_expression(self):
        """parameter DEPTH = 2 ** 4"""
        p = _param("DEPTH", _binop("**", 2, 4))
        ident = _ident("DEPTH", resolved=p)
        assert const_int(ident) == 16

    def test_param_chain(self):
        """parameter A = 8; parameter B = A - 1"""
        p_a = _param("A", 8)
        ident_a = _ident("A", resolved=p_a)
        p_b = Parameter("B", default_value=_binop("-", ident_a, _lit(1)))
        ident_b = _ident("B", resolved=p_b)
        assert const_int(ident_b) == 7

    def test_param_no_default(self):
        p = Parameter("X")
        ident = _ident("X", resolved=p)
        assert const_int(ident) is None

    def test_unresolved_ident(self):
        ident = Identifier("unknown")
        assert const_int(ident) is None

    def test_non_param_ident(self):
        """Identifier resolved to a Net (not constant)."""
        from veriforge.model.nets import Net, NetKind

        net = Net("w", kind=NetKind.WIRE)
        ident = _ident("w", resolved=net)
        assert const_int(ident) is None

    def test_circular_param_returns_none(self):
        """Circular parameter reference should not infinite-loop."""
        p_a = Parameter("A")
        ident_a = _ident("A", resolved=p_a)
        p_a.default_value = ident_a  # A refers to itself
        assert const_int(ident_a) is None


# ===== System functions =====


class TestSystemFunctions:
    def test_clog2_power_of_two(self):
        expr = FunctionCall("$clog2", [_lit(256)], is_system=True)
        assert const_int(expr) == 8

    def test_clog2_non_power(self):
        expr = FunctionCall("$clog2", [_lit(200)], is_system=True)
        assert const_int(expr) == 8  # ceil(log2(200)) = 8

    def test_clog2_one(self):
        expr = FunctionCall("$clog2", [_lit(1)], is_system=True)
        assert const_int(expr) == 0

    def test_clog2_zero(self):
        expr = FunctionCall("$clog2", [_lit(0)], is_system=True)
        assert const_int(expr) == 0

    def test_clog2_param_arg(self):
        """$clog2 with a parameter argument: $clog2(DEPTH)"""
        p = _param("DEPTH", 1024)
        ident = _ident("DEPTH", resolved=p)
        expr = FunctionCall("$clog2", [ident], is_system=True)
        assert const_int(expr) == 10

    def test_clog2_expression_arg(self):
        """$clog2(2 ** N) where N is a parameter."""
        p_n = _param("N", 5)
        ident_n = _ident("N", resolved=p_n)
        pow_expr = _binop("**", 2, ident_n)
        expr = FunctionCall("$clog2", [pow_expr], is_system=True)
        assert const_int(expr) == 5  # clog2(32) = 5

    def test_signed_passthrough(self):
        expr = FunctionCall("$signed", [_lit(42)], is_system=True)
        assert const_int(expr) == 42

    def test_unsigned_passthrough(self):
        expr = FunctionCall("$unsigned", [_lit(42)], is_system=True)
        assert const_int(expr) == 42

    def test_user_function_returns_none(self):
        expr = FunctionCall("my_func", [_lit(1)], is_system=False)
        assert const_int(expr) is None

    def test_unknown_system_func_returns_none(self):
        expr = FunctionCall("$unknown_func", [_lit(1)], is_system=True)
        assert const_int(expr) is None


# ===== Non-constant expressions =====


class TestNonConstant:
    def test_concat_returns_none(self):
        expr = Concatenation([_lit(1), _lit(2)])
        assert const_int(expr) is None

    def test_replication_returns_none(self):
        expr = Replication(_lit(4), _lit(0xFF))
        assert const_int(expr) is None

    def test_bit_select_returns_none(self):
        expr = BitSelect(Identifier("a"), _lit(3))
        assert const_int(expr) is None

    def test_range_select_returns_none(self):
        expr = RangeSelect(Identifier("a"), _lit(7), _lit(0))
        assert const_int(expr) is None

    def test_part_select_returns_none(self):
        expr = PartSelect(Identifier("a"), _lit(0), _lit(8), "+:")
        assert const_int(expr) is None


# ===== Mintypmax =====


class TestMintypmax:
    def test_uses_typ(self):
        expr = Mintypmax(_lit(1), _lit(5), _lit(10))
        assert const_int(expr) == 5


# ===== const_fold (returns Literal) =====


class TestConstFold:
    def test_returns_literal(self):
        result = const_fold(_binop("+", 3, 4))
        assert isinstance(result, Literal)
        assert result.value == 7

    def test_none_for_non_constant(self):
        result = const_fold(Identifier("x"))
        assert result is None


# ===== const_range_width =====


class TestConstRangeWidth:
    def test_none_is_scalar(self):
        assert const_range_width(None) == 1

    def test_literal_range(self):
        rng = Range(_lit(7), _lit(0))
        assert const_range_width(rng) == 8

    def test_param_range(self):
        """Range [WIDTH-1:0] where WIDTH=16."""
        p = _param("WIDTH", 16)
        ident = _ident("WIDTH", resolved=p)
        msb = _binop("-", ident, 1)
        lsb = _lit(0)
        rng = Range(msb, lsb)
        assert const_range_width(rng) == 16

    def test_expression_range(self):
        """Range [2*N-1:0] where N=4."""
        p = _param("N", 4)
        ident = _ident("N", resolved=p)
        msb = _binop("-", _binop("*", 2, ident), 1)
        lsb = _lit(0)
        rng = Range(msb, lsb)
        assert const_range_width(rng) == 8

    def test_unresolvable_range(self):
        rng = Range(Identifier("X"), _lit(0))
        assert const_range_width(rng) is None


# ===== Nested expressions =====


class TestNestedExpressions:
    def test_complex_arithmetic(self):
        """(3 + 4) * 2 - 1"""
        expr = _binop("-", _binop("*", _binop("+", 3, 4), 2), 1)
        assert const_int(expr) == 13

    def test_deeply_nested(self):
        """((2 ** 3) + 1) * 5"""
        expr = _binop("*", _binop("+", _binop("**", 2, 3), 1), 5)
        assert const_int(expr) == 45

    def test_mixed_with_params(self):
        """WIDTH * DEPTH + 1 where WIDTH=8, DEPTH=4"""
        p_w = _param("WIDTH", 8)
        p_d = _param("DEPTH", 4)
        expr = _binop("+", _binop("*", _ident("W", resolved=p_w), _ident("D", resolved=p_d)), 1)
        assert const_int(expr) == 33

    def test_ternary_with_comparison(self):
        """(A > B) ? A : B where A=10, B=20 → 20"""
        p_a = _param("A", 10)
        p_b = _param("B", 20)
        cond = _binop(">", _ident("A", resolved=p_a), _ident("B", resolved=p_b))
        expr = TernaryOp(cond, _ident("A", resolved=p_a), _ident("B", resolved=p_b))
        assert const_int(expr) == 20

    def test_clog2_of_param_expression(self):
        """$clog2(DEPTH * 2) where DEPTH=512"""
        p = _param("DEPTH", 512)
        inner = _binop("*", _ident("DEPTH", resolved=p), 2)
        expr = FunctionCall("$clog2", [inner], is_system=True)
        assert const_int(expr) == 10  # clog2(1024) = 10

    def test_partial_non_constant(self):
        """3 + signal → None"""
        expr = _binop("+", 3, Identifier("signal"))
        assert const_int(expr) is None

    def test_partially_foldable_ternary(self):
        """Non-constant condition → None"""
        expr = TernaryOp(Identifier("x"), _lit(10), _lit(20))
        assert const_int(expr) is None


# ===== Integration: width inference with const folding =====


class TestWidthInferenceIntegration:
    """Test that width inference now benefits from constant folding."""

    def test_parameterized_port_width(self):
        """Build a parsed design with parameter WIDTH and port [WIDTH-1:0] data."""
        from veriforge.verilog_parser import verilog_parser
        from veriforge.transforms import tree_to_design
        from veriforge.analysis import analyze_design, infer_widths

        vp = verilog_parser(start="module_declaration")
        tree = vp.build_tree(
            "module m #(parameter WIDTH = 8) (input [WIDTH-1:0] data, output [WIDTH-1:0] q); assign q = data; endmodule"
        )
        design = tree_to_design(tree)
        analyze_design(design)
        infer_widths(design)

        mod = design.modules[0]
        assign = mod.continuous_assigns[0]
        # RHS is 'data', an identifier resolved to port [WIDTH-1:0]
        # Width should be 8 thanks to constant folding of WIDTH-1
        assert assign.rhs.inferred_width == 8

    def test_clog2_in_param_default(self):
        """Parameter ADDR_W with $clog2 computed via const_int directly."""
        # The parser/transformer doesn't extract $clog2() in range contexts,
        # so we test the constant folder on a hand-built expression tree.
        p_depth = _param("DEPTH", 256)
        clog2_expr = FunctionCall("$clog2", [_ident("DEPTH", resolved=p_depth)], is_system=True)
        msb = _binop("-", clog2_expr, 1)
        rng = Range(msb, _lit(0))
        w = const_range_width(rng)
        assert w == 8  # $clog2(256) - 1 = 7; range [7:0] = 8 bits

    def test_replication_with_param_count(self):
        """Width inference for {N{data}} where N is a parameter."""
        from veriforge.analysis.width_inference import infer_expr_width

        p = _param("N", 4)
        data = Identifier("data")
        # Manually set inferred width for data
        data.inferred_width = 8
        rep = Replication(_ident("N", resolved=p), data)
        w = infer_expr_width(rep)
        assert w == 32  # 4 * 8

    def test_range_select_with_params(self):
        """Width of signal[HIGH:LOW] where HIGH/LOW are parameters."""
        from veriforge.analysis.width_inference import infer_expr_width

        p_h = _param("HIGH", 15)
        p_l = _param("LOW", 8)
        expr = RangeSelect(Identifier("data"), _ident("HIGH", resolved=p_h), _ident("LOW", resolved=p_l))
        w = infer_expr_width(expr)
        assert w == 8  # 15 - 8 + 1

    def test_part_select_with_param_width(self):
        """Width of signal[0 +: W] where W is a parameter."""
        from veriforge.analysis.width_inference import infer_expr_width

        p_w = _param("W", 4)
        expr = PartSelect(Identifier("data"), _lit(0), _ident("W", resolved=p_w), "+:")
        w = infer_expr_width(expr)
        assert w == 4


# ===== fold_constants (design-level) =====


class TestFoldConstants:
    """Test the top-level fold_constants / fold_constants_in_module."""

    def test_fold_constants_no_crash(self):
        """Verify fold_constants runs without error on a parsed design."""
        from veriforge.verilog_parser import verilog_parser
        from veriforge.transforms import tree_to_design
        from veriforge.analysis import analyze_design

        vp = verilog_parser(start="module_declaration")
        tree = vp.build_tree("module m #(parameter W = 8, parameter D = 2**W) (input [W-1:0] a); endmodule")
        design = tree_to_design(tree)
        analyze_design(design)
        fold_constants(design)  # should not raise


# ===== Edge cases =====


class TestEdgeCases:
    def test_depth_limit(self):
        """Deeply nested expression should not crash (depth limit)."""
        expr = _lit(1)
        for _ in range(100):
            expr = _binop("+", expr, 1)
        # Should not crash even if it exceeds MAX_DEPTH (returns None or value)
        result = const_int(expr)
        # With MAX_DEPTH=64, deep expressions return None
        # But since each level is only +1 deep, this should actually compute
        assert result == 101 or result is None  # either is acceptable

    def test_string_valued_literal(self):
        assert const_int(Literal("not_a_number")) is None
