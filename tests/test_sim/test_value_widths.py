"""Tests for Value width promotion and demotion during arithmetic/logic operations.

Covers Verilog IEEE 1364-2005 width rules:
  - Arithmetic: result width = max(operand widths)
  - Multiply: result width = sum of operand widths
  - Comparison: result is always 1-bit
  - Resize: zero-extend or truncate
  - Sign extension
  - Assignment truncation / zero-extension via resize
  - Concatenation: result width = sum of operand widths
  - Shift: result width = left operand width
  - Ternary: result width = max(true_val, false_val)
  - x-propagation through mixed-width ops
"""

import pytest

from veriforge.sim.value import Value


# ── Arithmetic Width Promotion ────────────────────────────────────────


class TestArithmeticWidthPromotion:
    """Arithmetic ops with mixed-width operands should widen to max."""

    def test_add_8bit_plus_32bit(self):
        a = Value(0xFF, width=8)
        b = Value(1, width=32)
        r = a + b
        assert r == 0x100
        assert r.width == 32

    def test_add_32bit_plus_8bit(self):
        a = Value(1, width=32)
        b = Value(0xFF, width=8)
        r = a + b
        assert r == 0x100
        assert r.width == 32

    def test_sub_16bit_minus_8bit(self):
        a = Value(0x100, width=16)
        b = Value(1, width=8)
        r = a - b
        assert r == 0xFF
        assert r.width == 16

    def test_sub_8bit_minus_16bit(self):
        a = Value(5, width=8)
        b = Value(3, width=16)
        r = a - b
        assert r == 2
        assert r.width == 16

    def test_mul_result_width_is_sum(self):
        """IEEE: multiply result width = sum of operand widths."""
        a = Value(0xFF, width=8)
        b = Value(0xFF, width=8)
        r = a * b
        assert r == 0xFF * 0xFF
        assert r.width == 16

    def test_mul_mixed_widths(self):
        a = Value(3, width=4)
        b = Value(5, width=8)
        r = a * b
        assert r == 15
        assert r.width == 12

    def test_div_mixed_widths(self):
        a = Value(100, width=16)
        b = Value(3, width=8)
        r = a // b
        assert r == 33
        assert r.width == 16

    def test_mod_mixed_widths(self):
        a = Value(100, width=16)
        b = Value(7, width=8)
        r = a % b
        assert r == 2
        assert r.width == 16

    def test_power_mixed_widths(self):
        a = Value(2, width=8)
        b = Value(10, width=16)
        r = a**b
        assert r == 1024
        assert r.width == 16


# ── Bitwise Width Promotion ──────────────────────────────────────────


class TestBitwiseWidthPromotion:
    """Bitwise ops with mixed-width operands should widen to max."""

    def test_and_mixed_widths(self):
        a = Value(0xFF, width=8)
        b = Value(0x0F0F, width=16)
        r = a & b
        assert r == 0x000F
        assert r.width == 16

    def test_or_mixed_widths(self):
        a = Value(0x0F, width=8)
        b = Value(0xF000, width=16)
        r = a | b
        assert r == 0xF00F
        assert r.width == 16

    def test_xor_mixed_widths(self):
        a = Value(0xFF, width=8)
        b = Value(0x00FF, width=16)
        r = a ^ b
        assert r == 0
        assert r.width == 16

    def test_and_narrow_operand_zero_extends(self):
        """Zero-extension means upper bits of narrow operand are 0."""
        a = Value(0xFF, width=8)  # 8'hFF → extends to 16'h00FF
        b = Value(0xFFFF, width=16)
        r = a & b
        assert r == 0x00FF  # upper byte masked by zero-extended a
        assert r.width == 16


# ── Comparison Always Returns 1-bit ──────────────────────────────────


class TestComparisonWidth:
    """Comparisons go through the evaluator's BinaryOp, returning 1-bit Values."""

    @pytest.fixture
    def ev(self):
        from veriforge.sim.evaluator import ExpressionEvaluator

        return ExpressionEvaluator()

    @pytest.fixture
    def ctx(self):
        from veriforge.sim.evaluator import EvalContext

        return EvalContext(
            {
                "a8": Value(3, width=8),
                "b16": Value(5, width=16),
                "c32": Value(10, width=32),
                "d8": Value(5, width=8),
            }
        )

    def test_lt_returns_1bit(self, ev, ctx):
        from veriforge.model.expressions import BinaryOp, Identifier

        r = ev.eval(BinaryOp("<", Identifier("a8"), Identifier("b16")), ctx)
        assert r.width == 1
        assert r == 1

    def test_gt_returns_1bit(self, ev, ctx):
        from veriforge.model.expressions import BinaryOp, Identifier

        r = ev.eval(BinaryOp(">", Identifier("c32"), Identifier("d8")), ctx)
        assert r.width == 1
        assert r == 1

    def test_eq_returns_1bit(self, ev, ctx):
        from veriforge.model.expressions import BinaryOp, Identifier

        r = ev.eval(BinaryOp("==", Identifier("d8"), Identifier("b16")), ctx)
        assert r.width == 1
        assert r == 1  # both are 5

    def test_le_returns_1bit(self, ev, ctx):
        from veriforge.model.expressions import BinaryOp, Identifier

        r = ev.eval(BinaryOp("<=", Identifier("d8"), Identifier("b16")), ctx)
        assert r.width == 1
        assert r == 1

    def test_ge_returns_1bit(self, ev, ctx):
        from veriforge.model.expressions import BinaryOp, Identifier

        r = ev.eval(BinaryOp(">=", Identifier("b16"), Identifier("d8")), ctx)
        assert r.width == 1
        assert r == 1

    def test_ne_returns_1bit(self, ev, ctx):
        from veriforge.model.expressions import BinaryOp, Identifier

        r = ev.eval(BinaryOp("!=", Identifier("a8"), Identifier("b16")), ctx)
        assert r.width == 1
        assert r == 1


# ── Resize (Assignment Width Adjustment) ─────────────────────────────


class TestResizeAssignment:
    """Simulate assignment to narrower/wider targets."""

    def test_truncate_32bit_to_8bit(self):
        """Assigning 32-bit value to 8-bit reg truncates upper bits."""
        v = Value(0xDEADBEEF, width=32)
        r = v.resize(8)
        assert r.width == 8
        assert r.val == 0xEF

    def test_truncate_16bit_to_4bit(self):
        v = Value(0xABCD, width=16)
        r = v.resize(4)
        assert r.width == 4
        assert r.val == 0xD

    def test_zero_extend_8bit_to_32bit(self):
        """Assigning 8-bit value to 32-bit reg zero-extends."""
        v = Value(0xFF, width=8)
        r = v.resize(32)
        assert r.width == 32
        assert r.val == 0xFF

    def test_same_width_is_identity(self):
        v = Value(42, width=8)
        r = v.resize(8)
        assert r.width == 8
        assert r.val == 42

    def test_truncate_preserves_lower_bits(self):
        """Ensure only lower bits are kept, not shifted."""
        v = Value(0b11001010, width=8)
        r = v.resize(4)
        assert r.val == 0b1010

    def test_extend_preserves_all_bits(self):
        v = Value(0b11001010, width=8)
        r = v.resize(16)
        assert r.val == 0b11001010

    def test_truncate_x_value(self):
        """Truncating x preserves x status."""
        v = Value.x(16)
        r = v.resize(8)
        assert r.width == 8
        assert r.is_x


# ── Sign Extension ───────────────────────────────────────────────────


class TestSignExtension:
    """sign_extend should replicate MSB into upper bits."""

    def test_positive_8_to_16(self):
        v = Value(0b0111_1111, width=8)  # +127
        r = v.sign_extend(16)
        assert r.width == 16
        assert r.val == 127

    def test_negative_8_to_16(self):
        v = Value(0b1000_0000, width=8)  # -128
        r = v.sign_extend(16)
        assert r.width == 16
        assert r.val == 0xFF80  # 16-bit two's complement -128

    def test_negative_4_to_8(self):
        v = Value(0b1010, width=4)  # -6 in 4-bit
        r = v.sign_extend(8)
        assert r.width == 8
        assert r.val == 0xFA  # -6 in 8-bit

    def test_sign_extend_same_width(self):
        v = Value(0b1010, width=4)
        r = v.sign_extend(4)
        assert r.width == 4
        assert r.val == 0b1010

    def test_sign_extend_truncate(self):
        """sign_extend with smaller width truncates (resize behavior)."""
        v = Value(0xFF, width=8)
        r = v.sign_extend(4)
        assert r.width == 4
        assert r.val == 0xF

    def test_sign_extend_x_sign_bit(self):
        """If sign bit is x, extended bits should be x."""
        v = Value(0, width=4, mask=0b1000)  # sign bit is x
        r = v.sign_extend(8)
        assert r.width == 8
        assert r.mask & 0xF0  # upper nibble has x bits


# ── Concatenation Width ──────────────────────────────────────────────


class TestConcatenationWidth:
    """Concatenation result width = sum of operand widths."""

    def test_concat_8_8_is_16(self):
        a = Value(0xAB, width=8)
        b = Value(0xCD, width=8)
        r = a.concat(b)
        assert r.width == 16
        assert r.val == 0xABCD

    def test_concat_4_8_is_12(self):
        a = Value(0xA, width=4)
        b = Value(0xBC, width=8)
        r = a.concat(b)
        assert r.width == 12
        assert r.val == 0xABC

    def test_concat_1_1_is_2(self):
        a = Value(1, width=1)
        b = Value(0, width=1)
        r = a.concat(b)
        assert r.width == 2
        assert r.val == 0b10

    def test_concat_preserves_all_bits(self):
        a = Value(0xFF, width=8)
        b = Value(0xFF, width=8)
        r = a.concat(b)
        assert r.val == 0xFFFF


# ── Shift Width ──────────────────────────────────────────────────────


class TestShiftWidth:
    """Shift result width = left operand width."""

    def test_lshift_preserves_width(self):
        a = Value(1, width=8)
        r = a << Value(4, width=4)
        assert r.width == 8
        assert r.val == 0x10

    def test_rshift_preserves_width(self):
        a = Value(0xF0, width=8)
        r = a >> Value(4, width=4)
        assert r.width == 8
        assert r.val == 0x0F

    def test_lshift_overflow_truncates(self):
        """Shifting beyond width should lose bits (masked)."""
        a = Value(0xFF, width=8)
        r = a << Value(4, width=4)
        assert r.width == 8
        assert r.val == 0xF0

    def test_rshift_fills_zeros(self):
        a = Value(0xFF, width=8)
        r = a >> Value(4, width=4)
        assert r.width == 8
        assert r.val == 0x0F


# ── x-Propagation Through Mixed-Width Ops ────────────────────────────


class TestXPropagationMixedWidth:
    """x values should propagate correctly through mixed-width operations."""

    def test_add_x_narrow_plus_wide(self):
        a = Value.x(8)
        b = Value(5, width=16)
        r = a + b
        assert r.is_x
        assert r.width == 16

    def test_sub_wide_minus_x_narrow(self):
        a = Value(10, width=16)
        b = Value.x(8)
        r = a - b
        assert r.is_x
        assert r.width == 16

    def test_and_x_with_zero_gives_zero(self):
        """x & 0 = 0 (Verilog spec)."""
        a = Value.x(8)
        b = Value(0, width=8)
        r = a & b
        assert r == 0

    def test_or_x_with_all_ones_gives_ones(self):
        """x | 1 = 1 (Verilog spec)."""
        a = Value.x(8)
        b = Value(0xFF, width=8)
        r = a | b
        assert r == 0xFF

    def test_mul_x_propagates(self):
        r = Value.x(4) * Value(3, width=8)
        assert r.is_x
        assert r.width == 12


# ── Evaluator Width Promotion (Expression-Level) ─────────────────────


class TestEvaluatorWidthPromotion:
    """Test that the ExpressionEvaluator correctly handles mixed widths."""

    @pytest.fixture
    def ev(self):
        from veriforge.sim.evaluator import ExpressionEvaluator

        return ExpressionEvaluator()

    @pytest.fixture
    def ctx(self):
        from veriforge.sim.evaluator import EvalContext

        return EvalContext(
            {
                "narrow": Value(0xFF, width=8),
                "wide": Value(1, width=32),
                "reg8": Value(0, width=8),
                "reg32": Value(0, width=32),
            }
        )

    def test_add_narrow_wide(self, ev, ctx):
        """narrow + wide should produce 32-bit result."""
        from veriforge.model.expressions import BinaryOp, Identifier

        expr = BinaryOp("+", Identifier("narrow"), Identifier("wide"))
        r = ev.eval(expr, ctx)
        assert r == 0x100
        assert r.width == 32

    def test_sub_wide_narrow(self, ev, ctx):
        from veriforge.model.expressions import BinaryOp, Identifier

        expr = BinaryOp("-", Identifier("wide"), Identifier("narrow"))
        r = ev.eval(expr, ctx)
        # 1 - 255 unsigned = large value (underflow wraps)
        assert r.width == 32

    def test_bitand_narrow_wide(self, ev, ctx):
        from veriforge.model.expressions import BinaryOp, Identifier

        expr = BinaryOp("&", Identifier("narrow"), Identifier("wide"))
        r = ev.eval(expr, ctx)
        assert r == 1  # 0xFF & 0x00000001
        assert r.width == 32

    def test_comparison_always_1bit(self, ev, ctx):
        from veriforge.model.expressions import BinaryOp, Identifier

        expr = BinaryOp("<", Identifier("narrow"), Identifier("wide"))
        r = ev.eval(expr, ctx)
        assert r.width == 1

    def test_ternary_mixed_widths(self, ev, ctx):
        """Ternary result uses max(true, false) width."""
        from veriforge.model.expressions import Identifier, Literal, TernaryOp

        expr = TernaryOp(
            Literal(1, width=1),
            Identifier("narrow"),  # 8-bit
            Identifier("wide"),  # 32-bit
        )
        r = ev.eval(expr, ctx)
        assert r.val == 0xFF


# ── Assignment Width Adjustment in Executor ──────────────────────────


class TestExecutorAssignmentWidth:
    """Test that assignments resize values to match target width."""

    @pytest.fixture
    def ex(self):
        from veriforge.sim.executor import StatementExecutor

        return StatementExecutor(loop_limit=1000)

    @pytest.fixture
    def ctx(self):
        from veriforge.sim.evaluator import EvalContext

        return EvalContext(
            {
                "reg8": Value(0, width=8),
                "reg16": Value(0, width=16),
                "reg32": Value(0, width=32),
            }
        )

    def test_assign_wide_to_narrow_truncates(self, ex, ctx):
        """Assigning 32-bit expression to 8-bit reg truncates."""
        from veriforge.model.expressions import Literal, Identifier
        from veriforge.model.statements import BlockingAssign

        stmt = BlockingAssign(Identifier("reg8"), Literal(0xDEADBEEF, width=32))
        ex.execute(stmt, ctx)
        assert ctx.read_signal("reg8") == 0xEF
        assert ctx.read_signal("reg8").width == 8

    def test_assign_narrow_to_wide_zero_extends(self, ex, ctx):
        """Assigning 8-bit expression to 32-bit reg zero-extends."""
        from veriforge.model.expressions import Literal, Identifier
        from veriforge.model.statements import BlockingAssign

        stmt = BlockingAssign(Identifier("reg32"), Literal(0xFF, width=8))
        ex.execute(stmt, ctx)
        assert ctx.read_signal("reg32") == 0xFF
        assert ctx.read_signal("reg32").width == 32

    def test_assign_expression_result_resized(self, ex, ctx):
        """Result of 8+16 bit add (16-bit) assigned to 32-bit reg is zero-extended."""
        from veriforge.model.expressions import BinaryOp, Literal, Identifier
        from veriforge.model.statements import BlockingAssign

        # 8'hFF + 16'h0001 = 16'h0100, assigned to 32-bit reg
        expr = BinaryOp("+", Literal(0xFF, width=8), Literal(1, width=16))
        stmt = BlockingAssign(Identifier("reg32"), expr)
        ex.execute(stmt, ctx)
        assert ctx.read_signal("reg32") == 0x100
        assert ctx.read_signal("reg32").width == 32

    def test_new_signal_gets_rhs_width(self, ex, ctx):
        """Writing to undeclared signal creates it with RHS value's width."""
        from veriforge.model.expressions import Literal, Identifier
        from veriforge.model.statements import BlockingAssign

        stmt = BlockingAssign(Identifier("new_sig"), Literal(0xABCD, width=16))
        ex.execute(stmt, ctx)
        assert ctx.read_signal("new_sig") == 0xABCD
        assert ctx.read_signal("new_sig").width == 16

    def test_new_signal_32bit_not_1bit(self, ex, ctx):
        """Bug #10 regression: new signal must use RHS width, not default to 1-bit."""
        from veriforge.model.expressions import Literal, Identifier
        from veriforge.model.statements import BlockingAssign

        stmt = BlockingAssign(Identifier("loop_var"), Literal(0, width=32))
        ex.execute(stmt, ctx)
        assert ctx.read_signal("loop_var").width == 32
        # Now write a larger value — should not truncate to 1-bit
        stmt2 = BlockingAssign(Identifier("loop_var"), Literal(31, width=32))
        ex.execute(stmt2, ctx)
        assert ctx.read_signal("loop_var") == 31
