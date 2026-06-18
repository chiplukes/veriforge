"""Tests for the 4-state Value type.

Covers:
  - Construction (int, from_verilog, x/z factory)
  - Arithmetic operators (+, -, *, /, %, **)
  - Bitwise operators (&, |, ^, ~)
  - Shift operators (<<, >>)
  - Comparison operators (==, !=, <, <=, >, >=, ===, !==)
  - Logical operators (&&, ||, !)
  - Reduction operators (&, |, ^, ~&, ~|, ~^)
  - Bit/range select
  - Concatenation/replication
  - X/Z propagation
  - Width handling and sign extension
"""

import pytest

from veriforge.sim.value import Value


# ── Construction ──────────────────────────────────────────────────────


class TestConstruction:
    def test_basic_int(self):
        v = Value(5, width=8)
        assert v.val == 5
        assert v.mask == 0
        assert v.width == 8
        assert v.is_defined

    def test_zero(self):
        v = Value(0, width=1)
        assert v.val == 0
        assert not v.is_x

    def test_truncation(self):
        """Values wider than width should be masked."""
        v = Value(0xFF, width=4)
        assert v.val == 0xF

    def test_x_factory(self):
        v = Value.x(8)
        assert not v.is_defined
        assert v.mask == 0xFF

    def test_z_factory(self):
        v = Value.z(4)
        assert v.is_x
        assert v.mask == 0xF

    def test_from_int(self):
        v = Value.from_int(42, 16)
        assert v.val == 42
        assert v.width == 16
        assert v.is_defined


class TestFromVerilog:
    def test_hex(self):
        v = Value.from_verilog("8'hFF")
        assert v.val == 0xFF
        assert v.width == 8

    def test_binary(self):
        v = Value.from_verilog("4'b1010")
        assert v.val == 0b1010
        assert v.width == 4

    def test_decimal(self):
        v = Value.from_verilog("8'd200")
        assert v.val == 200
        assert v.width == 8

    def test_octal(self):
        v = Value.from_verilog("6'o77")
        assert v.val == 0o77
        assert v.width == 6

    def test_binary_with_x(self):
        v = Value.from_verilog("4'b10x1")
        assert v.width == 4
        assert (v.mask >> 1) & 1 == 1  # bit 1 is x
        assert (v.val >> 0) & 1 == 1  # bit 0 is 1

    def test_hex_with_x(self):
        v = Value.from_verilog("8'hxF")
        assert v.width == 8
        assert v.mask & 0xF0 == 0xF0  # upper nibble is x
        assert v.val & 0x0F == 0x0F  # lower nibble is 0xF

    def test_plain_integer(self):
        v = Value.from_verilog("42")
        assert v.val == 42
        assert v.width >= 32

    def test_underscore_separator(self):
        v = Value.from_verilog("8'b1010_0101")
        assert v.val == 0b10100101

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            Value.from_verilog("not_a_number")


# ── Conversion ────────────────────────────────────────────────────────


class TestConversion:
    def test_int_conversion(self):
        v = Value(42, width=8)
        assert int(v) == 42

    def test_int_raises_on_x(self):
        v = Value.x(8)
        with pytest.raises(ValueError, match="x/z"):
            int(v)

    def test_bool_true(self):
        assert bool(Value(1, width=1))
        assert bool(Value(0xFF, width=8))

    def test_bool_false(self):
        assert not bool(Value(0, width=8))

    def test_bool_raises_on_x(self):
        with pytest.raises(ValueError, match="x/z"):
            bool(Value.x(1))

    def test_str_binary(self):
        v = Value(0b1010, width=4)
        assert str(v) == "4'b1010"

    def test_str_with_x(self):
        v = Value(0b10, width=4, mask=0b0100)
        s = str(v)
        assert "x" in s

    def test_to_hex(self):
        v = Value(0xFF, width=8)
        assert v.to_hex() == "8'hff"

    def test_repr_defined(self):
        v = Value(5, width=4)
        r = repr(v)
        assert "5" in r
        assert "4" in r

    def test_repr_x(self):
        v = Value(0, width=4, mask=0xF)
        r = repr(v)
        assert "mask" in r


# ── Equality ──────────────────────────────────────────────────────────


class TestEquality:
    def test_eq_int(self):
        assert Value(5, width=8) == 5
        assert not (Value(5, width=8) == 6)

    def test_eq_value(self):
        assert Value(5, width=8) == Value(5, width=8)

    def test_eq_different_width(self):
        # Different widths should not be equal even if val is same
        assert Value(5, width=8) != Value(5, width=16)

    def test_ne(self):
        assert Value(5, width=8) != Value(6, width=8)
        assert Value(5, width=8) != 6

    def test_eq_x_not_equal_to_int(self):
        assert not (Value.x(8) == 0)

    def test_hash(self):
        a = Value(5, width=8)
        b = Value(5, width=8)
        assert hash(a) == hash(b)


# ── Arithmetic ────────────────────────────────────────────────────────


class TestArithmetic:
    def test_add(self):
        a = Value(3, width=8)
        b = Value(4, width=8)
        r = a + b
        assert r == 7
        assert r.width == 8

    def test_add_int(self):
        a = Value(10, width=8)
        assert (a + 5) == 15
        assert (5 + a) == 15

    def test_add_overflow(self):
        """Addition wraps at width."""
        a = Value(0xFF, width=8)
        r = a + Value(1, width=8)
        assert r.val == 0  # 0xFF + 1 = 0x100, masked to 8 bits

    def test_sub(self):
        a = Value(10, width=8)
        b = Value(3, width=8)
        assert (a - b) == 7

    def test_mul(self):
        a = Value(3, width=8)
        b = Value(4, width=8)
        assert (a * b) == 12

    def test_div(self):
        a = Value(10, width=8)
        b = Value(3, width=8)
        assert (a // b) == 3

    def test_div_by_zero(self):
        a = Value(10, width=8)
        b = Value(0, width=8)
        assert (a // b).is_x

    def test_mod(self):
        a = Value(10, width=8)
        b = Value(3, width=8)
        assert (a % b) == 1

    def test_mod_by_zero(self):
        a = Value(10, width=8)
        b = Value(0, width=8)
        assert (a % b).is_x

    def test_neg(self):
        a = Value(5, width=8)
        r = -a
        assert r.val == ((-5) & 0xFF)  # Two's complement

    def test_power(self):
        a = Value(2, width=16)
        b = Value(10, width=16)
        assert (a**b) == 1024

    def test_arithmetic_x_propagation(self):
        """Any x/z operand produces x result."""
        a = Value.x(8)
        b = Value(5, width=8)
        assert (a + b).is_x
        assert (a - b).is_x
        assert (a * b).is_x
        assert (a // b).is_x


# ── Bitwise ───────────────────────────────────────────────────────────


class TestBitwise:
    def test_and(self):
        a = Value(0b1100, width=4)
        b = Value(0b1010, width=4)
        assert (a & b) == 0b1000

    def test_or(self):
        a = Value(0b1100, width=4)
        b = Value(0b1010, width=4)
        assert (a | b) == 0b1110

    def test_xor(self):
        a = Value(0b1100, width=4)
        b = Value(0b1010, width=4)
        assert (a ^ b) == 0b0110

    def test_invert(self):
        a = Value(0b1010, width=4)
        r = ~a
        assert r.val == 0b0101

    def test_and_with_int(self):
        a = Value(0xFF, width=8)
        assert (a & 0x0F) == 0x0F
        assert (0x0F & a) == 0x0F

    def test_and_x_with_zero(self):
        """x & 0 = 0 (known result despite x)."""
        a = Value.x(4)
        b = Value(0, width=4)
        r = a & b
        assert r.val == 0
        assert r.mask == 0  # result is fully defined!

    def test_and_x_with_one(self):
        """x & 1 = x (still unknown)."""
        a = Value.x(1)
        b = Value(1, width=1)
        r = a & b
        assert r.is_x

    def test_or_x_with_one(self):
        """x | 1 = 1 (known result despite x)."""
        a = Value.x(4)
        b = Value(0xF, width=4)
        r = a | b
        assert r.val == 0xF
        assert r.mask == 0  # result is fully defined!

    def test_or_x_with_zero(self):
        """x | 0 = x (still unknown)."""
        a = Value.x(1)
        b = Value(0, width=1)
        r = a | b
        assert r.is_x

    def test_invert_x(self):
        v = Value(0b10, width=4, mask=0b0100)
        r = ~v
        # Defined bits inverted, x bits stay x
        assert (r.mask >> 2) & 1 == 1  # bit 2 still x


# ── Shift ─────────────────────────────────────────────────────────────


class TestShift:
    def test_lshift(self):
        a = Value(1, width=8)
        assert (a << 3) == 8

    def test_rshift(self):
        a = Value(8, width=8)
        assert (a >> 3) == 1

    def test_lshift_value(self):
        a = Value(1, width=8)
        b = Value(4, width=8)
        assert (a << b) == 16

    def test_shift_x_amount(self):
        a = Value(0xFF, width=8)
        b = Value.x(4)
        assert (a << b).is_x
        assert (a >> b).is_x


# ── Comparison ────────────────────────────────────────────────────────


class TestComparison:
    def test_eq(self):
        a = Value(5, width=8)
        b = Value(5, width=8)
        r = a.eq(b)
        assert r.width == 1
        assert int(r) == 1

    def test_ne(self):
        a = Value(5, width=8)
        b = Value(6, width=8)
        assert int(a.ne(b)) == 1

    def test_lt(self):
        assert int(Value(3, width=8).lt(Value(5, width=8))) == 1
        assert int(Value(5, width=8).lt(Value(3, width=8))) == 0

    def test_le(self):
        assert int(Value(3, width=8).le(Value(3, width=8))) == 1
        assert int(Value(3, width=8).le(Value(5, width=8))) == 1

    def test_gt(self):
        assert int(Value(5, width=8).gt(Value(3, width=8))) == 1

    def test_ge(self):
        assert int(Value(5, width=8).ge(Value(5, width=8))) == 1

    def test_compare_x_returns_x(self):
        a = Value.x(8)
        b = Value(5, width=8)
        assert a.eq(b).is_x
        assert a.lt(b).is_x

    def test_case_eq_matches_x(self):
        """=== compares x/z bits too."""
        a = Value.x(4)
        b = Value.x(4)
        assert int(a.case_eq(b)) == 1

    def test_case_eq_defined(self):
        a = Value(5, width=8)
        b = Value(5, width=8)
        assert int(a.case_eq(b)) == 1

    def test_case_ne(self):
        a = Value(5, width=8)
        b = Value(6, width=8)
        assert int(a.case_ne(b)) == 1


# ── Logical ───────────────────────────────────────────────────────────


class TestLogical:
    def test_logical_and(self):
        a = Value(1, width=1)
        b = Value(1, width=1)
        assert int(a.logical_and(b)) == 1

    def test_logical_and_false(self):
        a = Value(5, width=8)
        b = Value(0, width=8)
        assert int(a.logical_and(b)) == 0

    def test_logical_or(self):
        a = Value(0, width=8)
        b = Value(5, width=8)
        assert int(a.logical_or(b)) == 1

    def test_logical_not(self):
        assert int(Value(0, width=8).logical_not()) == 1
        assert int(Value(5, width=8).logical_not()) == 0

    def test_logical_and_x_with_zero(self):
        """x && 0 = 0."""
        a = Value.x(8)
        b = Value(0, width=8)
        r = a.logical_and(b)
        assert not r.is_x
        assert int(r) == 0

    def test_logical_or_x_with_nonzero(self):
        """x || 1 = 1."""
        a = Value.x(8)
        b = Value(5, width=8)
        r = a.logical_or(b)
        assert not r.is_x
        assert int(r) == 1


# ── Reduction ─────────────────────────────────────────────────────────


class TestReduction:
    def test_reduce_and(self):
        assert int(Value(0xF, width=4).reduce_and()) == 1
        assert int(Value(0xE, width=4).reduce_and()) == 0

    def test_reduce_or(self):
        assert int(Value(0, width=4).reduce_or()) == 0
        assert int(Value(1, width=4).reduce_or()) == 1

    def test_reduce_xor(self):
        assert int(Value(0b1010, width=4).reduce_xor()) == 0  # even number of 1s
        assert int(Value(0b1011, width=4).reduce_xor()) == 1  # odd number of 1s

    def test_reduce_nand(self):
        assert int(Value(0xF, width=4).reduce_nand()) == 0
        assert int(Value(0xE, width=4).reduce_nand()) == 1

    def test_reduce_nor(self):
        assert int(Value(0, width=4).reduce_nor()) == 1
        assert int(Value(1, width=4).reduce_nor()) == 0

    def test_reduce_xnor(self):
        assert int(Value(0b1010, width=4).reduce_xnor()) == 1
        assert int(Value(0b1011, width=4).reduce_xnor()) == 0

    def test_reduce_and_x(self):
        assert Value.x(4).reduce_and().is_x

    def test_reduce_or_partial_x(self):
        """If any non-x bit is 1, reduce_or is 1."""
        v = Value(0b01, width=4, mask=0b1100)
        r = v.reduce_or()
        assert not r.is_x
        assert int(r) == 1


# ── Bit/Range Select ─────────────────────────────────────────────────


class TestBitRangeSelect:
    def test_bit_select(self):
        v = Value(0b1010, width=4)
        assert int(v[0]) == 0
        assert int(v[1]) == 1
        assert int(v[2]) == 0
        assert int(v[3]) == 1

    def test_bit_select_out_of_range(self):
        v = Value(0xF, width=4)
        r = v[10]
        assert r.is_x

    def test_range_select(self):
        v = Value(0b11010110, width=8)
        r = v[7:4]
        assert r.width == 4
        assert r.val == 0b1101

    def test_range_select_lower(self):
        v = Value(0b11010110, width=8)
        r = v[3:0]
        assert r.width == 4
        assert r.val == 0b0110

    def test_set_bit(self):
        v = Value(0, width=4)
        v2 = v.set_bit(2, 1)
        assert v2.val == 0b0100
        assert v.val == 0  # original unchanged

    def test_set_range(self):
        v = Value(0, width=8)
        part = Value(0xF, width=4)
        v2 = v.set_range(7, 4, part)
        assert v2.val == 0xF0


# ── Concatenation / Replication ───────────────────────────────────────


class TestConcatReplicate:
    def test_concat_two(self):
        a = Value(0b11, width=2)
        b = Value(0b00, width=2)
        r = a.concat(b)
        assert r.width == 4
        assert r.val == 0b1100

    def test_concat_three(self):
        a = Value(0b1, width=1)
        b = Value(0b01, width=2)
        c = Value(0b110, width=3)
        r = a.concat(b, c)
        assert r.width == 6
        assert r.val == 0b101110

    def test_replicate(self):
        v = Value(0b10, width=2)
        r = v.replicate(4)
        assert r.width == 8
        assert r.val == 0b10101010

    def test_concat_with_x(self):
        a = Value(0b1, width=1)
        b = Value.x(2)
        r = a.concat(b)
        assert r.width == 3
        assert r.mask & 0b011 == 0b11  # lower 2 bits are x


# ── Width / Sign ──────────────────────────────────────────────────────


class TestWidthSign:
    def test_resize_extend(self):
        v = Value(0xF, width=4)
        r = v.resize(8)
        assert r.width == 8
        assert r.val == 0xF

    def test_resize_truncate(self):
        v = Value(0xFF, width=8)
        r = v.resize(4)
        assert r.width == 4
        assert r.val == 0xF

    def test_sign_extend_positive(self):
        v = Value(0b0101, width=4)
        r = v.sign_extend(8)
        assert r.width == 8
        assert r.val == 0b00000101

    def test_sign_extend_negative(self):
        v = Value(0b1010, width=4)  # MSB=1 → negative
        r = v.sign_extend(8)
        assert r.width == 8
        assert r.val == 0b11111010

    def test_as_signed(self):
        v = Value(0b1010, width=4)
        assert v.as_signed() == -6  # 0b1010 as 4-bit signed = -6

    def test_as_signed_positive(self):
        v = Value(0b0101, width=4)
        assert v.as_signed() == 5

    def test_as_signed_x_raises(self):
        with pytest.raises(ValueError):
            Value.x(4).as_signed()
