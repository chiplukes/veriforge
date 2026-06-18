"""Tests for the 4-state Value type."""

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
        assert (v.mask >> 1) & 1 == 1
        assert (v.val >> 0) & 1 == 1

    def test_hex_with_x(self):
        v = Value.from_verilog("8'hxF")
        assert v.width == 8
        assert v.mask & 0xF0 == 0xF0
        assert v.val & 0x0F == 0x0F

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
        assert int(Value(42, width=8)) == 42

    def test_int_raises_on_x(self):
        with pytest.raises(ValueError, match="x/z"):
            int(Value.x(8))

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
        assert "x" in str(v)

    def test_to_hex(self):
        v = Value(0xFF, width=8)
        assert v.to_hex() == "8'hff"

    def test_repr_defined(self):
        r = repr(Value(5, width=4))
        assert "5" in r
        assert "4" in r

    def test_repr_x(self):
        r = repr(Value(0, width=4, mask=0xF))
        assert "mask" in r


# ── Equality ──────────────────────────────────────────────────────────


class TestEquality:
    def test_eq_int(self):
        assert Value(5, width=8) == 5
        assert not (Value(5, width=8) == 6)

    def test_eq_value(self):
        assert Value(5, width=8) == Value(5, width=8)

    def test_eq_different_width(self):
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
        r = Value(3, width=8) + Value(4, width=8)
        assert r == 7

    def test_add_int(self):
        a = Value(10, width=8)
        assert (a + 5) == 15
        assert (5 + a) == 15

    def test_add_overflow(self):
        r = Value(0xFF, width=8) + Value(1, width=8)
        assert r.val == 0

    def test_sub(self):
        assert (Value(10, width=8) - Value(3, width=8)) == 7

    def test_mul(self):
        assert (Value(3, width=8) * Value(4, width=8)) == 12

    def test_div(self):
        assert (Value(10, width=8) // Value(3, width=8)) == 3

    def test_div_by_zero(self):
        assert (Value(10, width=8) // Value(0, width=8)).is_x

    def test_mod(self):
        assert (Value(10, width=8) % Value(3, width=8)) == 1

    def test_mod_by_zero(self):
        assert (Value(10, width=8) % Value(0, width=8)).is_x

    def test_neg(self):
        r = -Value(5, width=8)
        assert r.val == ((-5) & 0xFF)

    def test_power(self):
        assert (Value(2, width=16) ** Value(10, width=16)) == 1024

    def test_arithmetic_x_propagation(self):
        a = Value.x(8)
        b = Value(5, width=8)
        assert (a + b).is_x
        assert (a - b).is_x
        assert (a * b).is_x
        assert (a // b).is_x


# ── Bitwise ───────────────────────────────────────────────────────────


class TestBitwise:
    def test_and(self):
        assert (Value(0b1100, width=4) & Value(0b1010, width=4)) == 0b1000

    def test_or(self):
        assert (Value(0b1100, width=4) | Value(0b1010, width=4)) == 0b1110

    def test_xor(self):
        assert (Value(0b1100, width=4) ^ Value(0b1010, width=4)) == 0b0110

    def test_invert(self):
        assert (~Value(0b1010, width=4)).val == 0b0101

    def test_and_with_int(self):
        a = Value(0xFF, width=8)
        assert (a & 0x0F) == 0x0F
        assert (0x0F & a) == 0x0F

    def test_and_x_with_zero(self):
        r = Value.x(4) & Value(0, width=4)
        assert r.val == 0
        assert r.mask == 0

    def test_and_x_with_one(self):
        r = Value.x(1) & Value(1, width=1)
        assert r.is_x

    def test_or_x_with_one(self):
        r = Value.x(4) | Value(0xF, width=4)
        assert r.val == 0xF
        assert r.mask == 0

    def test_or_x_with_zero(self):
        r = Value.x(1) | Value(0, width=1)
        assert r.is_x

    def test_invert_x(self):
        v = Value(0b10, width=4, mask=0b0100)
        r = ~v
        assert (r.mask >> 2) & 1 == 1


# ── Shift ─────────────────────────────────────────────────────────────


class TestShift:
    def test_lshift(self):
        assert (Value(1, width=8) << 3) == 8

    def test_rshift(self):
        assert (Value(8, width=8) >> 3) == 1

    def test_lshift_value(self):
        assert (Value(1, width=8) << Value(4, width=8)) == 16

    def test_shift_x_amount(self):
        a = Value(0xFF, width=8)
        b = Value.x(4)
        assert (a << b).is_x
        assert (a >> b).is_x


# ── Comparison ────────────────────────────────────────────────────────


class TestComparison:
    def test_eq(self):
        r = Value(5, width=8).eq(Value(5, width=8))
        assert r.width == 1
        assert int(r) == 1

    def test_ne(self):
        assert int(Value(5, width=8).ne(Value(6, width=8))) == 1

    def test_lt(self):
        assert int(Value(3, width=8).lt(Value(5, width=8))) == 1
        assert int(Value(5, width=8).lt(Value(3, width=8))) == 0

    def test_le(self):
        assert int(Value(3, width=8).le(Value(3, width=8))) == 1

    def test_gt(self):
        assert int(Value(5, width=8).gt(Value(3, width=8))) == 1

    def test_ge(self):
        assert int(Value(5, width=8).ge(Value(5, width=8))) == 1

    def test_compare_x_returns_x(self):
        a = Value.x(8)
        assert a.eq(Value(5, width=8)).is_x
        assert a.lt(Value(5, width=8)).is_x

    def test_case_eq_matches_x(self):
        assert int(Value.x(4).case_eq(Value.x(4))) == 1

    def test_case_eq_defined(self):
        assert int(Value(5, width=8).case_eq(Value(5, width=8))) == 1

    def test_case_ne(self):
        assert int(Value(5, width=8).case_ne(Value(6, width=8))) == 1


# ── Logical ───────────────────────────────────────────────────────────


class TestLogical:
    def test_logical_and(self):
        assert int(Value(1, width=1).logical_and(Value(1, width=1))) == 1

    def test_logical_and_false(self):
        assert int(Value(5, width=8).logical_and(Value(0, width=8))) == 0

    def test_logical_or(self):
        assert int(Value(0, width=8).logical_or(Value(5, width=8))) == 1

    def test_logical_not(self):
        assert int(Value(0, width=8).logical_not()) == 1
        assert int(Value(5, width=8).logical_not()) == 0

    def test_logical_and_x_with_zero(self):
        r = Value.x(8).logical_and(Value(0, width=8))
        assert not r.is_x
        assert int(r) == 0

    def test_logical_or_x_with_nonzero(self):
        r = Value.x(8).logical_or(Value(5, width=8))
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
        assert int(Value(0b1010, width=4).reduce_xor()) == 0
        assert int(Value(0b1011, width=4).reduce_xor()) == 1

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
        assert Value(0xF, width=4)[10].is_x

    def test_range_select(self):
        r = Value(0b11010110, width=8)[7:4]
        assert r.width == 4
        assert r.val == 0b1101

    def test_range_select_lower(self):
        r = Value(0b11010110, width=8)[3:0]
        assert r.width == 4
        assert r.val == 0b0110

    def test_set_bit(self):
        v2 = Value(0, width=4).set_bit(2, 1)
        assert v2.val == 0b0100

    def test_set_range(self):
        v2 = Value(0, width=8).set_range(7, 4, Value(0xF, width=4))
        assert v2.val == 0xF0


# ── Concatenation / Replication ───────────────────────────────────────


class TestConcatReplicate:
    def test_concat_two(self):
        r = Value(0b11, width=2).concat(Value(0b00, width=2))
        assert r.width == 4
        assert r.val == 0b1100

    def test_concat_three(self):
        r = Value(0b1, width=1).concat(Value(0b01, width=2), Value(0b110, width=3))
        assert r.width == 6
        assert r.val == 0b101110

    def test_replicate(self):
        r = Value(0b10, width=2).replicate(4)
        assert r.width == 8
        assert r.val == 0b10101010

    def test_concat_with_x(self):
        r = Value(0b1, width=1).concat(Value.x(2))
        assert r.width == 3
        assert r.mask & 0b011 == 0b11


# ── Width / Sign ──────────────────────────────────────────────────────


class TestWidthSign:
    def test_resize_extend(self):
        r = Value(0xF, width=4).resize(8)
        assert r.width == 8
        assert r.val == 0xF

    def test_resize_truncate(self):
        r = Value(0xFF, width=8).resize(4)
        assert r.width == 4
        assert r.val == 0xF

    def test_sign_extend_positive(self):
        r = Value(0b0101, width=4).sign_extend(8)
        assert r.width == 8
        assert r.val == 0b00000101

    def test_sign_extend_negative(self):
        r = Value(0b1010, width=4).sign_extend(8)
        assert r.width == 8
        assert r.val == 0b11111010

    def test_as_signed(self):
        assert Value(0b1010, width=4).as_signed() == -6

    def test_as_signed_positive(self):
        assert Value(0b0101, width=4).as_signed() == 5

    def test_as_signed_x_raises(self):
        with pytest.raises(ValueError):
            Value.x(4).as_signed()
