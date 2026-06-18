"""4-state (0, 1, x, z) bit-vector value type for Verilog simulation.

Each bit is encoded as a pair of bits across two integers (val, mask):
    bit state    val bit    mask bit
    ─────────    ───────    ────────
        0           0          0
        1           1          0
        x           0          1
        z           0          1

When mask == 0 (the common case — pure 0/1 signals), val is just a plain
Python int and arithmetic maps directly to integer operations.

Uses __slots__ throughout for Cython compatibility and memory efficiency.
"""

from __future__ import annotations

import re

# Width mask helper — produces an int with `width` 1-bits.
_WIDTH_CACHE: dict[int, int] = {}

# Cached Value.x() singletons for common widths (up to 64 bits).
# Since Values are never mutated in-place this is safe.
_X_CACHE: dict[int, "Value"] = {}


def _mask_for_width(width: int) -> int:
    """Return (1 << width) - 1, cached."""
    m = _WIDTH_CACHE.get(width)
    if m is None:
        m = (1 << width) - 1
        _WIDTH_CACHE[width] = m
    return m


class Value:  # cm:c8a1e6
    """4-state bit-vector value for Verilog simulation.

    Attributes:
        val:   Integer holding the 0/1 bit values.
        mask:  Integer where set bits indicate x or z.
        width: Bit width of the value.
    """

    __slots__ = ("mask", "type_info", "val", "width")

    def __init__(self, val: int = 0, *, width: int = 1, mask: int = 0, type_info: object | None = None) -> None:
        # Inline _mask_for_width to avoid function-call overhead on the
        # hottest path in the entire simulator (~500K calls per 10K cycles).
        wc = _WIDTH_CACHE
        wmask = wc.get(width)
        if wmask is None:
            wmask = (1 << width) - 1
            wc[width] = wmask
        self.val = val & wmask & ~mask
        self.mask = mask & wmask
        self.width = width
        self.type_info = type_info

    # ── Construction helpers ───────────────────────────────────────────

    @classmethod
    def from_int(cls, n: int, width: int = 32) -> Value:
        """Create a fully-defined value from a Python int."""
        return cls(n, width=width)

    @classmethod
    def x(cls, width: int = 1) -> Value:
        """Create an all-x value of given width (cached for common widths)."""
        cached = _X_CACHE.get(width)
        if cached is not None:
            return cached
        v = cls(0, width=width, mask=_mask_for_width(width))
        if width <= 64:
            _X_CACHE[width] = v
        return v

    @classmethod
    def z(cls, width: int = 1) -> Value:
        """Create an all-z value of given width (cached for common widths)."""
        # z and x share the same representation in our 4-state model.
        return cls.x(width)

    @classmethod
    def from_verilog(cls, text: str) -> Value:
        """Parse a Verilog literal string like "8'hFF", "4'b10xz", "32'd100".

        Supports:
            <width>'<base><digits>
            Plain decimal integers
        """
        text = text.strip().replace("_", "")

        # Plain integer
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            n = int(text)
            width = max(32, n.bit_length())
            return cls(n, width=width)

        # Verilog-style: <width>'[s]<base><digits>
        m = re.match(r"(\d+)?'([sS])?([bBoOdDhH])([0-9a-fA-FxXzZ?_]+)$", text)
        if not m:
            raise ValueError(f"Cannot parse Verilog literal: {text!r}")

        width_str, _, base_ch, digits = m.groups()
        width = int(width_str) if width_str else 32
        base_ch = base_ch.lower()

        if base_ch == "b":
            return cls._parse_binary(digits, width)
        elif base_ch == "o":
            return cls._parse_octal(digits, width)
        elif base_ch == "h":
            return cls._parse_hex(digits, width)
        elif base_ch == "d":
            # Decimal doesn't support x/z per digit in Verilog
            if any(c in digits.lower() for c in "xz?"):
                return cls.x(width)
            return cls(int(digits), width=width)
        else:
            raise ValueError(f"Unknown base: {base_ch!r}")

    @classmethod
    def _parse_binary(cls, digits: str, width: int) -> Value:
        val = 0
        mask = 0
        for ch in digits.lower():
            val <<= 1
            mask <<= 1
            if ch == "1":
                val |= 1
            elif ch in ("x", "z", "?"):
                mask |= 1
            # '0' is default
        return cls(val, width=width, mask=mask)

    @classmethod
    def _parse_hex(cls, digits: str, width: int) -> Value:
        val = 0
        mask = 0
        for ch in digits.lower():
            val <<= 4
            mask <<= 4
            if ch in ("x", "z", "?"):
                mask |= 0xF
            else:
                val |= int(ch, 16)
        return cls(val, width=width, mask=mask)

    @classmethod
    def _parse_octal(cls, digits: str, width: int) -> Value:
        val = 0
        mask = 0
        for ch in digits.lower():
            val <<= 3
            mask <<= 3
            if ch in ("x", "z", "?"):
                mask |= 0x7
            else:
                val |= int(ch, 8)
        return cls(val, width=width, mask=mask)

    # ── Properties ─────────────────────────────────────────────────────

    @property
    def is_defined(self) -> bool:
        """True if no bits are x or z."""
        return self.mask == 0

    @property
    def is_x(self) -> bool:
        """True if any bits are x/z."""
        return self.mask != 0

    # ── Conversion ─────────────────────────────────────────────────────

    def __int__(self) -> int:
        """Convert to Python int. Raises if any bits are x/z."""
        if self.mask:
            raise ValueError(f"Cannot convert value with x/z bits to int (mask=0x{self.mask:x})")
        return self.val

    def __bool__(self) -> bool:
        """True if any bit is 1 (and no x/z bits)."""
        if self.mask:
            raise ValueError("Cannot convert value with x/z bits to bool")
        return self.val != 0

    def __repr__(self) -> str:
        if self.mask == 0:
            return f"Value({self.val}, width={self.width})"
        return f"Value(val=0x{self.val:x}, mask=0x{self.mask:x}, width={self.width})"

    def __str__(self) -> str:
        """Display as Verilog-style binary with x/z."""
        chars = []
        for i in range(self.width - 1, -1, -1):
            if (self.mask >> i) & 1:
                chars.append("x")
            elif (self.val >> i) & 1:
                chars.append("1")
            else:
                chars.append("0")
        return f"{self.width}'b{''.join(chars)}"

    def to_hex(self) -> str:
        """Display as Verilog-style hex."""
        if self.mask == 0:
            nib = (self.width + 3) // 4
            return f"{self.width}'h{self.val:0{nib}x}"
        # Fall back to binary for mixed x/z
        return str(self)

    # ── Comparison ─────────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if isinstance(other, int):
            return self.mask == 0 and self.val == other
        if isinstance(other, Value):
            return self.val == other.val and self.mask == other.mask and self.width == other.width
        return NotImplemented

    def __ne__(self, other: object) -> bool:
        result = self.__eq__(other)
        if result is NotImplemented:
            return NotImplemented
        return not result

    def __hash__(self) -> int:
        return hash((self.val, self.mask, self.width))

    # ── Bit and range access ───────────────────────────────────────────

    def __getitem__(self, index: int | slice) -> Value:
        """Bit select or range select.

        - value[3]    → single-bit Value
        - value[7:4]  → range select (Verilog order: msb:lsb)
        """
        if isinstance(index, int):
            if index < 0 or index >= self.width:
                return Value.x(1)
            bit_v = (self.val >> index) & 1
            bit_m = (self.mask >> index) & 1
            return Value(bit_v, width=1, mask=bit_m)
        if isinstance(index, slice):
            # slice.start = MSB, slice.stop = LSB (Verilog order)
            msb = index.start if index.start is not None else self.width - 1
            lsb = index.stop if index.stop is not None else 0
            if msb < lsb:
                msb, lsb = lsb, msb
            w = msb - lsb + 1
            shifted_v = (self.val >> lsb) & _mask_for_width(w)
            shifted_m = (self.mask >> lsb) & _mask_for_width(w)
            return Value(shifted_v, width=w, mask=shifted_m)
        raise TypeError(f"Invalid index type: {type(index)}")

    def set_bit(self, index: int, bit_val: int) -> Value:
        """Return a new Value with bit at index set to 0 or 1."""
        if index < 0 or index >= self.width:
            raise IndexError(f"Bit index {index} out of range for width {self.width}")
        new_mask = self.mask & ~(1 << index)  # clear x/z for this bit
        if bit_val:
            new_val = self.val | (1 << index)
        else:
            new_val = self.val & ~(1 << index)
        return Value(new_val, width=self.width, mask=new_mask)

    def set_range(self, msb: int, lsb: int, part: Value) -> Value:
        """Return a new Value with bits [msb:lsb] replaced by part."""
        w = msb - lsb + 1
        range_mask = _mask_for_width(w) << lsb
        new_val = (self.val & ~range_mask) | ((part.val & _mask_for_width(w)) << lsb)
        new_mask = (self.mask & ~range_mask) | ((part.mask & _mask_for_width(w)) << lsb)
        return Value(new_val, width=self.width, mask=new_mask)

    # ── Arithmetic operators ───────────────────────────────────────────

    def __add__(self, other: Value | int) -> Value:
        other = _coerce(other, self.width)
        w = max(self.width, other.width)
        if self.mask or other.mask:
            return Value.x(w)
        return Value((self.val + other.val), width=w)

    def __radd__(self, other: int) -> Value:
        return self.__add__(other)

    def __sub__(self, other: Value | int) -> Value:
        other = _coerce(other, self.width)
        w = max(self.width, other.width)
        if self.mask or other.mask:
            return Value.x(w)
        return Value((self.val - other.val), width=w)

    def __rsub__(self, other: int) -> Value:
        return _coerce(other, self.width).__sub__(self)

    def __mul__(self, other: Value | int) -> Value:
        other = _coerce(other, self.width)
        # IEEE 1364-2005 §5.4.1: multiply result width = sum of operand widths
        w = self.width + other.width
        if self.mask or other.mask:
            return Value.x(w)
        return Value((self.val * other.val), width=w)

    def __rmul__(self, other: int) -> Value:
        return self.__mul__(other)

    def __mod__(self, other: Value | int) -> Value:
        other = _coerce(other, self.width)
        w = max(self.width, other.width)
        if self.mask or other.mask:
            return Value.x(w)
        if other.val == 0:
            return Value.x(w)
        return Value(self.val % other.val, width=w)

    def __floordiv__(self, other: Value | int) -> Value:
        other = _coerce(other, self.width)
        w = max(self.width, other.width)
        if self.mask or other.mask:
            return Value.x(w)
        if other.val == 0:
            return Value.x(w)
        return Value(self.val // other.val, width=w)

    def __neg__(self) -> Value:
        if self.mask:
            return Value.x(self.width)
        return Value((-self.val), width=self.width)

    # ── Bitwise operators ──────────────────────────────────────────────

    def __and__(self, other: Value | int) -> Value:
        other = _coerce(other, self.width)
        w = max(self.width, other.width)
        if not self.mask and not other.mask:
            return Value(self.val & other.val, width=w)
        # x & 0 = 0, x & 1 = x, x & x = x
        definite_zero = (~self.val & ~self.mask) | (~other.val & ~other.mask)
        new_mask = (self.mask | other.mask) & ~definite_zero & _mask_for_width(w)
        new_val = self.val & other.val & ~new_mask
        return Value(new_val, width=w, mask=new_mask)

    def __rand__(self, other: int) -> Value:
        return self.__and__(other)

    def __or__(self, other: Value | int) -> Value:
        other = _coerce(other, self.width)
        w = max(self.width, other.width)
        if not self.mask and not other.mask:
            return Value(self.val | other.val, width=w)
        # x | 1 = 1, x | 0 = x, x | x = x
        definite_one = (self.val & ~self.mask) | (other.val & ~other.mask)
        new_mask = (self.mask | other.mask) & ~definite_one & _mask_for_width(w)
        new_val = (self.val | other.val | definite_one) & ~new_mask
        return Value(new_val, width=w, mask=new_mask)

    def __ror__(self, other: int) -> Value:
        return self.__or__(other)

    def __xor__(self, other: Value | int) -> Value:
        other = _coerce(other, self.width)
        w = max(self.width, other.width)
        if self.mask or other.mask:
            new_mask = (self.mask | other.mask) & _mask_for_width(w)
            new_val = (self.val ^ other.val) & ~new_mask
            return Value(new_val, width=w, mask=new_mask)
        return Value(self.val ^ other.val, width=w)

    def __rxor__(self, other: int) -> Value:
        return self.__xor__(other)

    def __invert__(self) -> Value:
        if self.mask:
            return Value(~self.val & ~self.mask & _mask_for_width(self.width), width=self.width, mask=self.mask)
        return Value(~self.val, width=self.width)

    # ── Shift operators ────────────────────────────────────────────────

    def __lshift__(self, other: Value | int) -> Value:
        if isinstance(other, Value):
            if other.mask:
                return Value.x(self.width)
            other = other.val
        if self.mask:
            return Value(self.val << other, width=self.width, mask=self.mask << other)
        return Value(self.val << other, width=self.width)

    def __rshift__(self, other: Value | int) -> Value:
        if isinstance(other, Value):
            if other.mask:
                return Value.x(self.width)
            other = other.val
        if self.mask:
            return Value(self.val >> other, width=self.width, mask=self.mask >> other)
        return Value(self.val >> other, width=self.width)

    # ── Comparison operators (return 1-bit Value) ──────────────────────

    def _cmp(self, other: Value | int, op: str) -> Value:
        """Verilog comparison: returns 1-bit Value; x if either operand is x/z."""
        other = _coerce(other, self.width)
        if self.mask or other.mask:
            return Value.x(1)
        a, b = self.val, other.val
        if op == "==":
            result = a == b
        elif op == "!=":
            result = a != b
        elif op == "<":
            result = a < b
        elif op == "<=":
            result = a <= b
        elif op == ">":
            result = a > b
        elif op == ">=":
            result = a >= b
        else:
            raise ValueError(f"Unknown comparison operator: {op!r}")
        return Value(1 if result else 0, width=1)

    def eq(self, other: Value | int) -> Value:
        """Verilog == (x if either is x/z)."""
        return self._cmp(other, "==")

    def ne(self, other: Value | int) -> Value:
        """Verilog != (x if either is x/z)."""
        return self._cmp(other, "!=")

    def lt(self, other: Value | int) -> Value:
        """Verilog < (x if either is x/z)."""
        return self._cmp(other, "<")

    def le(self, other: Value | int) -> Value:
        """Verilog <= (x if either is x/z)."""
        return self._cmp(other, "<=")

    def gt(self, other: Value | int) -> Value:
        """Verilog > (x if either is x/z)."""
        return self._cmp(other, ">")

    def ge(self, other: Value | int) -> Value:
        """Verilog >= (x if either is x/z)."""
        return self._cmp(other, ">=")

    def case_eq(self, other: Value | int) -> Value:
        """Verilog === (case equality, compares x/z bits too)."""
        other = _coerce(other, self.width)
        result = self.val == other.val and self.mask == other.mask
        return Value(1 if result else 0, width=1)

    def case_ne(self, other: Value | int) -> Value:
        """Verilog !== (case inequality)."""
        other = _coerce(other, self.width)
        result = self.val != other.val or self.mask != other.mask
        return Value(1 if result else 0, width=1)

    # ── Reduction operators ────────────────────────────────────────────

    def reduce_and(self) -> Value:
        """Unary &v — 1 if all bits are 1."""
        wmask = _mask_for_width(self.width)
        if self.mask:
            return Value.x(1)
        return Value(1 if (self.val & wmask) == wmask else 0, width=1)

    def reduce_or(self) -> Value:
        """Unary |v — 1 if any bit is 1."""
        if self.mask:
            # If any non-masked bit is 1, result is 1
            if self.val & ~self.mask:
                return Value(1, width=1)
            return Value.x(1)
        return Value(1 if self.val else 0, width=1)

    def reduce_xor(self) -> Value:
        """Unary ^v — XOR of all bits."""
        if self.mask:
            return Value.x(1)
        bits = bin(self.val & _mask_for_width(self.width)).count("1")
        return Value(bits & 1, width=1)

    def reduce_nand(self) -> Value:
        """Unary ~& — NAND reduction."""
        r = self.reduce_and()
        return ~r

    def reduce_nor(self) -> Value:
        """Unary ~| — NOR reduction."""
        r = self.reduce_or()
        return ~r

    def reduce_xnor(self) -> Value:
        """Unary ~^ — XNOR reduction."""
        r = self.reduce_xor()
        return ~r

    # ── Logical operators (return 1-bit Value) ─────────────────────────

    def logical_and(self, other: Value | int) -> Value:
        """Verilog && — logical AND."""
        other = _coerce(other, self.width)
        if self.mask or other.mask:
            # If either is definitely 0, result is 0
            if not self.mask and self.val == 0:
                return Value(0, width=1)
            if not other.mask and other.val == 0:
                return Value(0, width=1)
            return Value.x(1)
        return Value(1 if (self.val and other.val) else 0, width=1)

    def logical_or(self, other: Value | int) -> Value:
        """Verilog || — logical OR."""
        other = _coerce(other, self.width)
        if self.mask or other.mask:
            # If either is definitely non-zero, result is 1
            if not self.mask and self.val != 0:
                return Value(1, width=1)
            if not other.mask and other.val != 0:
                return Value(1, width=1)
            return Value.x(1)
        return Value(1 if (self.val or other.val) else 0, width=1)

    def logical_not(self) -> Value:
        """Verilog ! — logical NOT."""
        if self.mask:
            return Value.x(1)
        return Value(1 if self.val == 0 else 0, width=1)

    # ── Concatenation / Replication ────────────────────────────────────

    def concat(self, *others: Value) -> Value:
        """Verilog {self, others...} — concatenation, self is MSB."""
        result_val = self.val
        result_mask = self.mask
        total_width = self.width
        for other in others:
            result_val = (result_val << other.width) | other.val
            result_mask = (result_mask << other.width) | other.mask
            total_width += other.width
        return Value(result_val, width=total_width, mask=result_mask)

    def replicate(self, count: int) -> Value:
        """Verilog {count{self}} — replication."""
        result_val = 0
        result_mask = 0
        total_width = self.width * count
        for _ in range(count):
            result_val = (result_val << self.width) | self.val
            result_mask = (result_mask << self.width) | self.mask
        return Value(result_val, width=total_width, mask=result_mask)

    # ── Width/sign manipulation ────────────────────────────────────────

    def resize(self, new_width: int) -> Value:
        """Zero-extend or truncate to new width."""
        return Value(self.val, width=new_width, mask=self.mask)

    def sign_extend(self, new_width: int) -> Value:
        """Sign-extend to new width (MSB is sign bit)."""
        if new_width <= self.width:
            return Value(self.val, width=new_width, mask=self.mask)
        if self.mask:
            # If sign bit is x/z, all extended bits are x/z
            if (self.mask >> (self.width - 1)) & 1:
                ext_mask = _mask_for_width(new_width) & ~_mask_for_width(self.width)
                return Value(self.val, width=new_width, mask=self.mask | ext_mask)
            # Sign bit is defined
            if (self.val >> (self.width - 1)) & 1:
                ext = _mask_for_width(new_width) & ~_mask_for_width(self.width)
                return Value(self.val | ext, width=new_width, mask=self.mask)
            return Value(self.val, width=new_width, mask=self.mask)
        # No x/z
        if (self.val >> (self.width - 1)) & 1:
            ext = _mask_for_width(new_width) & ~_mask_for_width(self.width)
            return Value(self.val | ext, width=new_width)
        return Value(self.val, width=new_width)

    def as_signed(self) -> int:
        """Interpret val as a signed integer of self.width bits."""
        if self.mask:
            raise ValueError("Cannot interpret x/z value as signed int")
        if (self.val >> (self.width - 1)) & 1:
            return self.val - (1 << self.width)
        return self.val

    # ── Power operator ─────────────────────────────────────────────────

    def __pow__(self, other: Value | int) -> Value:
        other = _coerce(other, self.width)
        w = max(self.width, other.width)
        if self.mask or other.mask:
            return Value.x(w)
        return Value(self.val**other.val, width=w)


def _coerce(v: Value | int, width: int) -> Value:
    """Coerce int to Value, pass Value through."""
    if isinstance(v, int):
        return Value(v, width=width)
    return v
