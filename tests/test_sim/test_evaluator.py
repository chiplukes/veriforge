"""Tests for the expression evaluator.

Tests the ExpressionEvaluator against the model's Expression types,
using EvalContext with pre-loaded signal values.
"""

import pytest

from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    FunctionCall,
    Identifier,
    Literal,
    PartSelect,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from veriforge.sim.evaluator import EvalContext, ExpressionEvaluator
from veriforge.sim.value import Value


@pytest.fixture
def ev():
    return ExpressionEvaluator()


@pytest.fixture
def ctx():
    """Context with some pre-loaded signals."""
    return EvalContext(
        {
            "a": Value(5, width=8),
            "b": Value(3, width=8),
            "clk": Value(1, width=1),
            "rst": Value(0, width=1),
            "bus": Value(0xAB, width=8),
            "wide": Value(0xDEAD, width=16),
            "sel": Value(2, width=2),
            "x_sig": Value.x(8),
        }
    )


# ── Literals ──────────────────────────────────────────────────────────


class TestLiterals:
    def test_integer_literal(self, ev, ctx):
        lit = Literal(42, width=8)
        r = ev.eval(lit, ctx)
        assert r == 42
        assert r.width == 8

    def test_literal_no_width(self, ev, ctx):
        lit = Literal(100)
        r = ev.eval(lit, ctx)
        assert r == 100
        assert r.width == 32  # default

    def test_literal_x(self, ev, ctx):
        lit = Literal(0, width=8, is_x=True)
        r = ev.eval(lit, ctx)
        assert r.is_x

    def test_literal_z(self, ev, ctx):
        lit = Literal(0, width=4, is_z=True)
        r = ev.eval(lit, ctx)
        assert r.is_x

    def test_literal_zero(self, ev, ctx):
        lit = Literal(0, width=1)
        r = ev.eval(lit, ctx)
        assert r == 0
        assert r.width == 1

    def test_literal_float(self, ev, ctx):
        lit = Literal(3.14, width=32)
        r = ev.eval(lit, ctx)
        assert r == 3  # truncated to int


# ── Identifiers ───────────────────────────────────────────────────────


class TestIdentifiers:
    def test_read_signal(self, ev, ctx):
        r = ev.eval(Identifier("a"), ctx)
        assert r == 5

    def test_unknown_signal(self, ev, ctx):
        r = ev.eval(Identifier("nonexistent"), ctx)
        assert r.is_x  # unknown signals return x


# ── Binary Operations ─────────────────────────────────────────────────


class TestBinaryOps:
    def test_add(self, ev, ctx):
        expr = BinaryOp("+", Identifier("a"), Identifier("b"))
        assert ev.eval(expr, ctx) == 8

    def test_sub(self, ev, ctx):
        expr = BinaryOp("-", Identifier("a"), Identifier("b"))
        assert ev.eval(expr, ctx) == 2

    def test_mul(self, ev, ctx):
        expr = BinaryOp("*", Identifier("a"), Identifier("b"))
        assert ev.eval(expr, ctx) == 15

    def test_div(self, ev, ctx):
        expr = BinaryOp("/", Identifier("a"), Identifier("b"))
        assert ev.eval(expr, ctx) == 1  # 5 / 3 = 1

    def test_mod(self, ev, ctx):
        expr = BinaryOp("%", Identifier("a"), Identifier("b"))
        assert ev.eval(expr, ctx) == 2  # 5 % 3 = 2

    def test_power(self, ev, ctx):
        expr = BinaryOp("**", Identifier("b"), Literal(3, width=8))
        assert ev.eval(expr, ctx) == 27  # 3^3

    def test_bitwise_and(self, ev, ctx):
        expr = BinaryOp("&", Identifier("a"), Identifier("b"))
        assert ev.eval(expr, ctx) == (5 & 3)

    def test_bitwise_or(self, ev, ctx):
        expr = BinaryOp("|", Identifier("a"), Identifier("b"))
        assert ev.eval(expr, ctx) == (5 | 3)

    def test_bitwise_xor(self, ev, ctx):
        expr = BinaryOp("^", Identifier("a"), Identifier("b"))
        assert ev.eval(expr, ctx) == (5 ^ 3)

    def test_bitwise_xnor(self, ev, ctx):
        expr = BinaryOp("~^", Identifier("a"), Identifier("b"))
        r = ev.eval(expr, ctx)
        expected = ~(5 ^ 3) & 0xFF
        assert r.val == expected

    def test_lshift(self, ev, ctx):
        expr = BinaryOp("<<", Identifier("a"), Literal(2, width=8))
        assert ev.eval(expr, ctx) == (5 << 2) & 0xFF

    def test_rshift(self, ev, ctx):
        expr = BinaryOp(">>", Identifier("a"), Literal(1, width=8))
        assert ev.eval(expr, ctx) == 2  # 5 >> 1

    def test_eq(self, ev, ctx):
        expr = BinaryOp("==", Identifier("a"), Literal(5, width=8))
        assert int(ev.eval(expr, ctx)) == 1

    def test_ne(self, ev, ctx):
        expr = BinaryOp("!=", Identifier("a"), Identifier("b"))
        assert int(ev.eval(expr, ctx)) == 1

    def test_lt(self, ev, ctx):
        expr = BinaryOp("<", Identifier("b"), Identifier("a"))
        assert int(ev.eval(expr, ctx)) == 1  # 3 < 5

    def test_le(self, ev, ctx):
        expr = BinaryOp("<=", Identifier("a"), Identifier("a"))
        assert int(ev.eval(expr, ctx)) == 1

    def test_gt(self, ev, ctx):
        expr = BinaryOp(">", Identifier("a"), Identifier("b"))
        assert int(ev.eval(expr, ctx)) == 1

    def test_ge(self, ev, ctx):
        expr = BinaryOp(">=", Identifier("a"), Identifier("a"))
        assert int(ev.eval(expr, ctx)) == 1

    def test_case_eq(self, ev, ctx):
        expr = BinaryOp("===", Identifier("x_sig"), Identifier("x_sig"))
        assert int(ev.eval(expr, ctx)) == 1

    def test_case_ne(self, ev, ctx):
        expr = BinaryOp("!==", Identifier("a"), Identifier("x_sig"))
        assert int(ev.eval(expr, ctx)) == 1

    def test_logical_and(self, ev, ctx):
        expr = BinaryOp("&&", Identifier("a"), Identifier("b"))
        assert int(ev.eval(expr, ctx)) == 1

    def test_logical_and_false(self, ev, ctx):
        expr = BinaryOp("&&", Identifier("a"), Identifier("rst"))
        assert int(ev.eval(expr, ctx)) == 0

    def test_logical_or(self, ev, ctx):
        expr = BinaryOp("||", Identifier("rst"), Identifier("a"))
        assert int(ev.eval(expr, ctx)) == 1

    def test_eq_with_x(self, ev, ctx):
        expr = BinaryOp("==", Identifier("x_sig"), Literal(5, width=8))
        assert ev.eval(expr, ctx).is_x

    def test_nested_binary(self, ev, ctx):
        """(a + b) * 2"""
        inner = BinaryOp("+", Identifier("a"), Identifier("b"))
        expr = BinaryOp("*", inner, Literal(2, width=8))
        assert ev.eval(expr, ctx) == 16  # (5+3)*2


# ── Unary Operations ──────────────────────────────────────────────────


class TestUnaryOps:
    def test_bitwise_not(self, ev, ctx):
        expr = UnaryOp("~", Identifier("a"))
        r = ev.eval(expr, ctx)
        assert r.val == (~5 & 0xFF)

    def test_logical_not(self, ev, ctx):
        expr = UnaryOp("!", Identifier("a"))
        assert int(ev.eval(expr, ctx)) == 0  # !5 = 0

    def test_logical_not_zero(self, ev, ctx):
        expr = UnaryOp("!", Identifier("rst"))
        assert int(ev.eval(expr, ctx)) == 1  # !0 = 1

    def test_negate(self, ev, ctx):
        expr = UnaryOp("-", Identifier("a"))
        r = ev.eval(expr, ctx)
        assert r.val == ((-5) & 0xFF)

    def test_plus(self, ev, ctx):
        expr = UnaryOp("+", Identifier("a"))
        assert ev.eval(expr, ctx) == 5

    def test_reduce_and(self, ev, ctx):
        expr = UnaryOp("&", Identifier("a"))
        assert int(ev.eval(expr, ctx)) == 0  # &(00000101) = 0

    def test_reduce_and_all_ones(self, ev):
        ctx = EvalContext({"ff": Value(0xFF, width=8)})
        expr = UnaryOp("&", Identifier("ff"))
        assert int(ev.eval(expr, ctx)) == 1

    def test_reduce_or(self, ev, ctx):
        expr = UnaryOp("|", Identifier("a"))
        assert int(ev.eval(expr, ctx)) == 1  # |(00000101) = 1

    def test_reduce_or_zero(self, ev, ctx):
        expr = UnaryOp("|", Identifier("rst"))
        assert int(ev.eval(expr, ctx)) == 0

    def test_reduce_xor(self, ev, ctx):
        expr = UnaryOp("^", Identifier("a"))
        # 5 = 0b101 → 2 ones → even → xor = 0
        assert int(ev.eval(expr, ctx)) == 0

    def test_reduce_nand(self, ev, ctx):
        expr = UnaryOp("~&", Identifier("a"))
        assert int(ev.eval(expr, ctx)) == 1

    def test_reduce_nor(self, ev, ctx):
        expr = UnaryOp("~|", Identifier("rst"))
        assert int(ev.eval(expr, ctx)) == 1

    def test_reduce_xnor(self, ev, ctx):
        expr = UnaryOp("~^", Identifier("a"))
        assert int(ev.eval(expr, ctx)) == 1


# ── Ternary ───────────────────────────────────────────────────────────


class TestTernary:
    def test_true_branch(self, ev, ctx):
        expr = TernaryOp(Identifier("clk"), Identifier("a"), Identifier("b"))
        assert ev.eval(expr, ctx) == 5  # clk=1 → a=5

    def test_false_branch(self, ev, ctx):
        expr = TernaryOp(Identifier("rst"), Identifier("a"), Identifier("b"))
        assert ev.eval(expr, ctx) == 3  # rst=0 → b=3

    def test_x_condition(self, ev, ctx):
        expr = TernaryOp(Identifier("x_sig"), Literal(0xFF, width=8), Literal(0x00, width=8))
        r = ev.eval(expr, ctx)
        # x condition → merge: 0xFF and 0x00 differ on all bits → all x
        assert r.is_x

    def test_x_condition_same_branches(self, ev, ctx):
        """When both branches are the same, x condition still yields a defined result."""
        expr = TernaryOp(Identifier("x_sig"), Literal(42, width=8), Literal(42, width=8))
        r = ev.eval(expr, ctx)
        assert r == 42

    def test_nested_ternary(self, ev, ctx):
        """sel ? a : (clk ? b : 0)"""
        inner = TernaryOp(Identifier("clk"), Identifier("b"), Literal(0, width=8))
        outer = TernaryOp(Identifier("sel"), Identifier("a"), inner)
        # sel=2 (nonzero→true) → a=5
        assert ev.eval(outer, ctx) == 5


# ── Concatenation ─────────────────────────────────────────────────────


class TestConcatenation:
    def test_concat_two(self, ev, ctx):
        expr = Concatenation([Identifier("clk"), Identifier("rst")])
        r = ev.eval(expr, ctx)
        assert r.width == 2
        assert r.val == 0b10  # {1, 0}

    def test_concat_literals(self, ev, ctx):
        expr = Concatenation([Literal(0xA, width=4), Literal(0xB, width=4)])
        r = ev.eval(expr, ctx)
        assert r.width == 8
        assert r.val == 0xAB

    def test_concat_empty(self, ev, ctx):
        expr = Concatenation([])
        r = ev.eval(expr, ctx)
        assert r.width == 0


# ── Replication ───────────────────────────────────────────────────────


class TestReplication:
    def test_replicate(self, ev, ctx):
        expr = Replication(Literal(4, width=8), Literal(0b10, width=2))
        r = ev.eval(expr, ctx)
        assert r.width == 8
        assert r.val == 0b10101010

    def test_replicate_x_count(self, ev, ctx):
        expr = Replication(Identifier("x_sig"), Literal(0b1, width=1))
        r = ev.eval(expr, ctx)
        assert r.is_x


# ── Bit Select ────────────────────────────────────────────────────────


class TestBitSelect:
    def test_bit_select(self, ev, ctx):
        expr = BitSelect(Identifier("a"), Literal(0, width=8))
        r = ev.eval(expr, ctx)
        assert r.width == 1
        assert int(r) == 1  # a=5=0b101, bit 0 = 1

    def test_bit_select_high(self, ev, ctx):
        expr = BitSelect(Identifier("a"), Literal(2, width=8))
        assert int(ev.eval(expr, ctx)) == 1  # bit 2 of 5 = 1

    def test_bit_select_x_index(self, ev, ctx):
        expr = BitSelect(Identifier("a"), Identifier("x_sig"))
        assert ev.eval(expr, ctx).is_x


# ── Range Select ──────────────────────────────────────────────────────


class TestRangeSelect:
    def test_range_select(self, ev, ctx):
        expr = RangeSelect(Identifier("bus"), Literal(7, width=8), Literal(4, width=8))
        r = ev.eval(expr, ctx)
        assert r.width == 4
        assert r.val == 0xA  # 0xAB[7:4] = 0xA

    def test_range_select_lower(self, ev, ctx):
        expr = RangeSelect(Identifier("bus"), Literal(3, width=8), Literal(0, width=8))
        r = ev.eval(expr, ctx)
        assert r.val == 0xB  # 0xAB[3:0] = 0xB

    def test_range_full(self, ev, ctx):
        expr = RangeSelect(Identifier("bus"), Literal(7, width=8), Literal(0, width=8))
        r = ev.eval(expr, ctx)
        assert r.val == 0xAB
        assert r.width == 8


# ── Part Select ───────────────────────────────────────────────────────


class TestPartSelect:
    def test_part_select_ascending(self, ev, ctx):
        # bus[0 +: 4] → bits 3:0
        expr = PartSelect(Identifier("bus"), Literal(0, width=8), Literal(4, width=8), "+:")
        r = ev.eval(expr, ctx)
        assert r.val == 0xB  # 0xAB[3:0]

    def test_part_select_descending(self, ev, ctx):
        # bus[7 -: 4] → bits 7:4
        expr = PartSelect(Identifier("bus"), Literal(7, width=8), Literal(4, width=8), "-:")
        r = ev.eval(expr, ctx)
        assert r.val == 0xA  # 0xAB[7:4]


# ── Function Call ─────────────────────────────────────────────────────


class TestFunctionCall:
    def test_clog2(self, ev, ctx):
        expr = FunctionCall("$clog2", [Literal(256, width=32)], is_system=True)
        assert int(ev.eval(expr, ctx)) == 8

    def test_clog2_non_power(self, ev, ctx):
        expr = FunctionCall("$clog2", [Literal(5, width=32)], is_system=True)
        assert int(ev.eval(expr, ctx)) == 3

    def test_clog2_one(self, ev, ctx):
        expr = FunctionCall("$clog2", [Literal(1, width=32)], is_system=True)
        assert int(ev.eval(expr, ctx)) == 0

    def test_clog2_zero(self, ev, ctx):
        expr = FunctionCall("$clog2", [Literal(0, width=32)], is_system=True)
        assert int(ev.eval(expr, ctx)) == 0

    def test_bits(self, ev, ctx):
        expr = FunctionCall("$bits", [Identifier("bus")], is_system=True)
        assert int(ev.eval(expr, ctx)) == 8

    def test_unknown_function(self, ev, ctx):
        expr = FunctionCall("$unknown_func", [], is_system=True)
        assert ev.eval(expr, ctx).is_x


# ── String Literal ────────────────────────────────────────────────────


class TestStringLiteral:
    def test_string_literal(self, ev, ctx):
        expr = StringLiteral("AB")
        r = ev.eval(expr, ctx)
        assert r.width == 16
        assert r.val == (ord("A") << 8) | ord("B")


# ── Complex expressions ──────────────────────────────────────────────


class TestComplexExpressions:
    def test_adder_expression(self, ev, ctx):
        """a + b  (simple adder)"""
        expr = BinaryOp("+", Identifier("a"), Identifier("b"))
        assert ev.eval(expr, ctx) == 8

    def test_mux_expression(self, ev, ctx):
        """sel ? a : b  (2:1 mux)"""
        expr = TernaryOp(Identifier("sel"), Identifier("a"), Identifier("b"))
        # sel=2 (nonzero) → a=5
        assert ev.eval(expr, ctx) == 5

    def test_counter_increment(self, ev, ctx):
        """a + 1"""
        expr = BinaryOp("+", Identifier("a"), Literal(1, width=8))
        assert ev.eval(expr, ctx) == 6

    def test_mask_and_shift(self, ev, ctx):
        """(bus >> 4) & 0xF"""
        shifted = BinaryOp(">>", Identifier("bus"), Literal(4, width=8))
        masked = BinaryOp("&", shifted, Literal(0xF, width=8))
        assert ev.eval(masked, ctx) == 0xA

    def test_concatenation_in_expression(self, ev, ctx):
        """{a[0], b[0]}"""
        a0 = BitSelect(Identifier("a"), Literal(0, width=8))
        b0 = BitSelect(Identifier("b"), Literal(0, width=8))
        expr = Concatenation([a0, b0])
        r = ev.eval(expr, ctx)
        assert r.width == 2
        # a=5 → a[0]=1, b=3 → b[0]=1
        assert r.val == 0b11

    def test_equality_chain(self, ev, ctx):
        """(a == 5) && (b == 3)"""
        eq_a = BinaryOp("==", Identifier("a"), Literal(5, width=8))
        eq_b = BinaryOp("==", Identifier("b"), Literal(3, width=8))
        expr = BinaryOp("&&", eq_a, eq_b)
        assert int(ev.eval(expr, ctx)) == 1

    def test_deep_nesting(self, ev, ctx):
        """((a + b) * 2) - 1"""
        add = BinaryOp("+", Identifier("a"), Identifier("b"))
        mul = BinaryOp("*", add, Literal(2, width=8))
        sub = BinaryOp("-", mul, Literal(1, width=8))
        assert ev.eval(sub, ctx) == 15  # (5+3)*2 - 1

    def test_reduction_in_condition(self, ev, ctx):
        """|a ? 1 : 0  (is 'a' nonzero?)"""
        red = UnaryOp("|", Identifier("a"))
        expr = TernaryOp(red, Literal(1, width=1), Literal(0, width=1))
        assert int(ev.eval(expr, ctx)) == 1
