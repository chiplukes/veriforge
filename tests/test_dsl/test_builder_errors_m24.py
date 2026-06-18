"""Exception quality audit M24-M33+: low-priority DSL validation checks (Phase 3).

M24 - Memory depth must be positive
M25 - Duplicate typedef name
M26 - Float values in expressions
M27 - Better type errors for list/dict/tuple/None
M28 - cat() with no arguments
M29 - rep() with non-positive count
M30 - Negative delay value
M32 - Duplicate continuous assign target (warning)
M33 - Interface duplicate signal name
M34 - Subscript direct assignment is a silent no-op (data[i] = x)
M35 - True-division raises with floor-div guidance (a / b)
"""

from __future__ import annotations

import warnings

import pytest

from veriforge.dsl.builder import Module, cat, posedge, rep
from veriforge.dsl.interface import Interface


# ===================================================================
# M24: Memory depth validation
# ===================================================================


class TestMemoryDepth:
    """M24: depth must be a positive integer for wire/reg memory arrays."""

    def test_reg_depth_zero_raises(self):
        m = Module("test")
        with pytest.raises(ValueError, match="depth must be positive"):
            m.reg("mem", width=8, depth=0)

    def test_reg_depth_negative_raises(self):
        m = Module("test")
        with pytest.raises(ValueError, match="depth must be positive"):
            m.reg("mem", width=8, depth=-1)

    def test_wire_depth_zero_raises(self):
        m = Module("test")
        with pytest.raises(ValueError, match="depth must be positive"):
            m.wire("w", width=8, depth=0)

    def test_wire_depth_negative_raises(self):
        m = Module("test")
        with pytest.raises(ValueError, match="depth must be positive"):
            m.wire("w", width=8, depth=-5)

    def test_reg_depth_positive_ok(self):
        m = Module("test")
        m.reg("mem", width=8, depth=16)
        mod = m.build()
        assert len(mod.variables) == 1

    def test_reg_depth_one_ok(self):
        m = Module("test")
        m.reg("mem", width=8, depth=1)
        mod = m.build()
        assert len(mod.variables) == 1

    def test_depth_float_raises(self):
        m = Module("test")
        with pytest.raises(TypeError, match="positive integer"):
            m.reg("mem", width=8, depth=3.0)

    def test_depth_bool_raises(self):
        m = Module("test")
        with pytest.raises(TypeError, match="positive integer"):
            m.reg("mem", width=8, depth=True)


# ===================================================================
# M25: Duplicate typedef name
# ===================================================================


class TestDuplicateTypedef:
    """M25: Declaring the same typedef name twice raises ValueError."""

    def test_duplicate_enum_raises(self):
        m = Module("test")
        m.typedef_enum("state_t", ["A", "B"])
        with pytest.raises(ValueError, match="already declared"):
            m.typedef_enum("state_t", ["C", "D"])

    def test_duplicate_struct_raises(self):
        m = Module("test")
        m.typedef_struct("bus_t", [("data", "logic", 8)])
        with pytest.raises(ValueError, match="already declared"):
            m.typedef_struct("bus_t", [("addr", "logic", 16)])

    def test_duplicate_union_raises(self):
        m = Module("test")
        m.typedef_union("word_t", [("word", "logic", 32)])
        with pytest.raises(ValueError, match="already declared"):
            m.typedef_union("word_t", [("half", "logic", 16)])

    def test_duplicate_alias_raises(self):
        m = Module("test")
        m.typedef_alias("byte_t", "logic [7:0]")
        with pytest.raises(ValueError, match="already declared"):
            m.typedef_alias("byte_t", "logic [15:0]")

    def test_duplicate_cross_type_raises(self):
        """Enum and struct with the same name -> error."""
        m = Module("test")
        m.typedef_enum("my_type", ["A", "B"])
        with pytest.raises(ValueError, match="already declared"):
            m.typedef_struct("my_type", [("x", "logic")])

    def test_different_names_ok(self):
        m = Module("test")
        m.typedef_enum("state_t", ["A", "B"])
        m.typedef_struct("bus_t", [("data", "logic", 8)])
        m.typedef_alias("byte_t", "logic [7:0]")
        mod = m.build()
        assert len(mod.typedefs) == 3

    def test_error_mentions_module_name(self):
        m = Module("my_mod")
        m.typedef_enum("my_type", ["A"])
        with pytest.raises(ValueError, match="my_mod"):
            m.typedef_enum("my_type", ["B"])


# ===================================================================
# M26: Float value rejection
# ===================================================================


class TestFloatRejection:
    """M26: Float values in hardware expressions raise TypeError with helpful message."""

    def test_float_in_assign_rhs(self):
        m = Module("test")
        m.input("a")
        w = m.wire("w")
        with pytest.raises(TypeError, match="float"):
            m.assign(w, 3.14)

    def test_float_in_binop(self):
        m = Module("test")
        a = m.input("a", width=8)
        with pytest.raises(TypeError, match="float"):
            _ = a + 1.5

    def test_float_suggests_int(self):
        m = Module("test")
        m.input("a")
        w = m.wire("w")
        with pytest.raises(TypeError, match="use int instead"):
            m.assign(w, 2.0)

    def test_int_ok(self):
        m = Module("test")
        m.input("a")
        w = m.wire("w")
        m.assign(w, 42)
        mod = m.build()
        assert len(mod.continuous_assigns) == 1


# ===================================================================
# M27: Better type errors for list/dict/tuple/None
# ===================================================================


class TestBadTypeErrors:
    """M27: Common wrong types get actionable error messages."""

    def test_list_in_expression(self):
        m = Module("test")
        a = m.input("a", width=8)
        with pytest.raises(TypeError, match=r"list.*cat"):
            _ = a + [1, 2, 3]  # noqa: RUF005

    def test_tuple_in_expression(self):
        m = Module("test")
        a = m.input("a", width=8)
        with pytest.raises(TypeError, match=r"tuple.*cat"):
            _ = a + (1, 2)  # noqa: RUF005

    def test_dict_in_expression(self):
        m = Module("test")
        a = m.input("a", width=8)
        with pytest.raises(TypeError, match="dict"):
            _ = a + {"key": "val"}

    def test_none_in_expression(self):
        m = Module("test")
        a = m.input("a", width=8)
        with pytest.raises(TypeError, match="None"):
            _ = a + None

    def test_none_in_assign(self):
        m = Module("test")
        m.input("a")
        w = m.wire("w")
        with pytest.raises(TypeError, match="None"):
            m.assign(w, None)


# ===================================================================
# M28: cat() with no arguments
# ===================================================================


class TestCatNoArgs:
    """M28: cat() with zero arguments raises ValueError."""

    def test_empty_cat_raises(self):
        with pytest.raises(ValueError, match="at least one argument"):
            cat()

    def test_single_arg_ok(self):
        m = Module("test")
        a = m.input("a")
        result = cat(a)
        assert result is not None

    def test_multiple_args_ok(self):
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        result = cat(a, b)
        assert result is not None


# ===================================================================
# M29: rep() with non-positive count
# ===================================================================


class TestRepCount:
    """M29: rep() with count <= 0 raises ValueError."""

    def test_rep_zero_raises(self):
        m = Module("test")
        a = m.input("a")
        with pytest.raises(ValueError, match="positive"):
            rep(0, a)

    def test_rep_negative_raises(self):
        m = Module("test")
        a = m.input("a")
        with pytest.raises(ValueError, match="positive"):
            rep(-1, a)

    def test_rep_one_ok(self):
        m = Module("test")
        a = m.input("a")
        result = rep(1, a)
        assert result is not None

    def test_rep_positive_ok(self):
        m = Module("test")
        a = m.input("a")
        result = rep(4, a)
        assert result is not None


# ===================================================================
# M30: Negative delay
# ===================================================================


class TestNegativeDelay:
    """M30: delay() with a negative integer raises ValueError."""

    def test_negative_delay_raises(self):
        m = Module("test")
        clk = m.input("clk")
        m.output_reg("q")
        with m.always(posedge(clk)):
            with pytest.raises(ValueError, match="non-negative"):
                m.delay(-10)

    def test_zero_delay_ok(self):
        m = Module("test")
        q = m.output_reg("q")
        with m.initial():
            m.delay(0)
            q @= 1
        m.build()  # should not raise

    def test_positive_delay_ok(self):
        m = Module("test")
        q = m.output_reg("q")
        with m.initial():
            m.delay(10)
            q @= 1
        m.build()  # should not raise


# ===================================================================
# M32: Duplicate continuous assign target (warning)
# ===================================================================


class TestDuplicateAssignTarget:
    """M32: Multiple continuous assigns to the same wire produces a warning."""

    def test_duplicate_assign_warns(self):
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        w = m.wire("w")
        m.assign(w, a)
        m.assign(w, b)
        with warnings.catch_warnings(record=True) as w_list:
            warnings.simplefilter("always")
            m.build()
        driver_warnings = [x for x in w_list if "multiple drivers" in str(x.message)]
        assert len(driver_warnings) == 1
        assert "2 continuous assignments" in str(driver_warnings[0].message)

    def test_single_assign_no_warn(self):
        m = Module("test")
        a = m.input("a")
        w = m.wire("w")
        m.assign(w, a)
        with warnings.catch_warnings(record=True) as w_list:
            warnings.simplefilter("always")
            m.build()
        driver_warnings = [x for x in w_list if "multiple drivers" in str(x.message)]
        assert len(driver_warnings) == 0

    def test_different_targets_no_warn(self):
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        w1 = m.wire("w1")
        w2 = m.wire("w2")
        m.assign(w1, a)
        m.assign(w2, b)
        with warnings.catch_warnings(record=True) as w_list:
            warnings.simplefilter("always")
            m.build()
        driver_warnings = [x for x in w_list if "multiple drivers" in str(x.message)]
        assert len(driver_warnings) == 0

    def test_warning_mentions_signal_name(self):
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        w = m.wire("out_w")
        m.assign(w, a)
        m.assign(w, b)
        with warnings.catch_warnings(record=True) as w_list:
            warnings.simplefilter("always")
            m.build()
        driver_warnings = [x for x in w_list if "multiple drivers" in str(x.message)]
        assert len(driver_warnings) == 1
        assert "out_w" in str(driver_warnings[0].message)


# ===================================================================
# M33: Interface duplicate signal name
# ===================================================================


class TestInterfaceDuplicateSignal:
    """M33: Declaring the same signal name twice in an Interface raises ValueError."""

    def test_duplicate_signal_raises(self):
        intf = Interface("test_intf")
        intf.signal("tvalid", src="master")
        with pytest.raises(ValueError, match="already declared"):
            intf.signal("tvalid", src="slave")

    def test_duplicate_same_src_raises(self):
        intf = Interface("test_intf")
        intf.signal("tdata", width=8, src="master")
        with pytest.raises(ValueError, match="already declared"):
            intf.signal("tdata", width=16, src="master")

    def test_error_mentions_interface_name(self):
        intf = Interface("my_bus")
        intf.signal("x", src="master")
        with pytest.raises(ValueError, match="my_bus"):
            intf.signal("x", src="slave")

    def test_different_names_ok(self):
        intf = (
            Interface("test_intf")
            .signal("tvalid", src="master")
            .signal("tready", src="slave")
            .signal("tdata", width=8, src="master")
        )


# ===================================================================
# M34: Subscript direct assignment is a silent no-op
# ===================================================================


class TestSubscriptDirectAssignment:
    """M34: data[i] = x must raise; data[i] <<= x and data[i] @= x are correct."""

    def _make_module_with_reg(self):
        m = Module("test")
        clk = m.input("clk")
        data = m.reg("data", width=8, depth=4)
        return m, clk, data

    def test_direct_assign_int_raises(self):
        m, clk, data = self._make_module_with_reg()
        with m.always(posedge(clk)):
            with pytest.raises(TypeError, match="<<= "):
                data[0] = 42

    def test_direct_assign_expr_raises(self):
        m, clk, data = self._make_module_with_reg()
        x = m.input("x", width=8)
        with m.always(posedge(clk)):
            with pytest.raises(TypeError, match="<<= "):
                data[0] = x

    def test_nba_subscript_ok(self):
        """data[i] <<= x is the correct form and must not raise."""
        m, clk, data = self._make_module_with_reg()
        x = m.input("x", width=8)
        with m.always(posedge(clk)):
            data[0] <<= x  # must not raise

    def test_blocking_subscript_ok(self):
        """data[i] @= x is the correct form and must not raise."""
        m, clk, data = self._make_module_with_reg()
        x = m.input("x", width=8)
        with m.always(posedge(clk)):
            data[0] @= x  # must not raise

    def test_chained_nba_subscripts_ok(self):
        """Multiple augmented subscript assigns in one block must all succeed."""
        m, clk, data = self._make_module_with_reg()
        x = m.input("x", width=8)
        with m.always(posedge(clk)):
            data[0] <<= x
            data[1] <<= x
            data[2] <<= x


# ===================================================================
# M35: True-division raises with floor-div guidance
# ===================================================================


class TestTrueDivision:
    """M35: a / b must raise with guidance to use //."""

    def test_truediv_raises(self):
        m = Module("test")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        with pytest.raises(TypeError, match="//"):
            _ = a / b

    def test_rtruediv_raises(self):
        m = Module("test")
        a = m.input("a", width=8)
        with pytest.raises(TypeError, match="//"):
            _ = 8 / a

    def test_floordiv_ok(self):
        """// must work and produce the Verilog / operator."""
        from veriforge.model.expressions import BinaryOp as _BinOp

        m = Module("test")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        result = a // b
        assert isinstance(result._expr, _BinOp)
        assert result._expr.op == "/"
