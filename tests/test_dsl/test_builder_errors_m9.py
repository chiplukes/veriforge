"""Exception quality audit M9-M20: medium-priority DSL validation checks.

M9  - Duplicate instance name
M10 - Continuous assign to reg-type signal
M11 - Non-blocking assignment in combinational always block (warning)
M12 - Blocking assignment in sequential always block (warning)
M13 - Empty always / initial block (warning)
M16 - Assign to input port
M18 - Empty case statement
M23 - Duplicate interface prefix
"""

from __future__ import annotations

import warnings

import pytest

from veriforge.dsl.builder import Module, posedge
from veriforge.dsl.interface import Interface


# ===================================================================
# M9: Duplicate instance name
# ===================================================================


class TestDuplicateInstanceName:
    """M9: Using the same instance name twice raises ValueError."""

    def test_duplicate_raises(self):
        m = Module("top")
        clk = m.input("clk")
        m.instance("sub_mod", "u_sub", ports={"clk": clk})
        with pytest.raises(ValueError, match="Duplicate instance name"):
            m.instance("sub_mod", "u_sub", ports={"clk": clk})

    def test_different_modules_same_inst_name(self):
        m = Module("top")
        clk = m.input("clk")
        m.instance("mod_a", "u_x", ports={"clk": clk})
        with pytest.raises(ValueError, match="Duplicate instance name"):
            m.instance("mod_b", "u_x", ports={"clk": clk})

    def test_unique_names_ok(self):
        m = Module("top")
        clk = m.input("clk")
        m.instance("counter", "u_cnt0", ports={"clk": clk})
        m.instance("counter", "u_cnt1", ports={"clk": clk})
        mod = m.build()
        assert len(mod.instances) == 2

    def test_error_mentions_module_name(self):
        m = Module("my_top")
        m.input("clk")
        m.instance("sub", "u_dup")
        with pytest.raises(ValueError, match="my_top"):
            m.instance("sub", "u_dup")


# ===================================================================
# M10: Continuous assign to reg-type signal
# ===================================================================


class TestAssignToReg:
    """M10: m.assign() to a reg or output_reg raises ValueError."""

    def test_assign_to_internal_reg(self):
        m = Module("test")
        m.input("a")
        r = m.reg("r")
        with pytest.raises(ValueError, match="continuous-assign to reg"):
            m.assign(r, 1)

    def test_assign_to_output_reg(self):
        m = Module("test")
        a = m.input("a")
        q = m.output_reg("q")
        with pytest.raises(ValueError, match="continuous-assign to reg"):
            m.assign(q, a)

    def test_assign_to_wire_ok(self):
        m = Module("test")
        a = m.input("a")
        w = m.wire("w")
        m.assign(w, a)
        mod = m.build()
        assert len(mod.continuous_assigns) == 1

    def test_assign_to_output_ok(self):
        m = Module("test")
        a = m.input("a")
        q = m.output("q")
        m.assign(q, a)
        mod = m.build()
        assert len(mod.continuous_assigns) == 1


# ===================================================================
# M11: NBA in combinational always block
# ===================================================================


class TestNBAInCombinational:
    """M11: Non-blocking assignment in combinational always warns."""

    def test_nba_in_combinational_warns(self):
        m = Module("test")
        a = m.input("a")
        y = m.reg("y")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with m.always(a):
                y <<= a
        assert len(w) == 1
        assert "Non-blocking" in str(w[0].message)
        assert "combinational" in str(w[0].message)

    def test_nba_in_sequential_no_warn(self):
        m = Module("test")
        clk = m.input("clk")
        d = m.input("d")
        q = m.output_reg("q")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with m.always(posedge(clk)):
                q <<= d
        nba_warnings = [x for x in w if "Non-blocking" in str(x.message)]
        assert len(nba_warnings) == 0


# ===================================================================
# M12: Blocking assign in sequential always block
# ===================================================================


class TestBlockingInSequential:
    """M12: Blocking assignment in sequential always warns."""

    def test_blocking_in_sequential_warns(self):
        m = Module("test")
        clk = m.input("clk")
        d = m.input("d")
        q = m.output_reg("q")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with m.always(posedge(clk)):
                q @= d
        blocking_warnings = [x for x in w if "Blocking" in str(x.message)]
        assert len(blocking_warnings) == 1
        assert "sequential" in str(blocking_warnings[0].message)

    def test_blocking_in_combinational_no_warn(self):
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        y = m.reg("y")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with m.always(a, b):
                y @= a & b
        blocking_warnings = [x for x in w if "Blocking" in str(x.message)]
        assert len(blocking_warnings) == 0


# ===================================================================
# M13: Empty always / initial block
# ===================================================================


class TestEmptyBlock:
    """M13: Empty always/initial blocks produce a warning."""

    def test_empty_always_warns(self):
        m = Module("test")
        clk = m.input("clk")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with m.always(posedge(clk)):
                pass
        empty_warnings = [x for x in w if "Empty always" in str(x.message)]
        assert len(empty_warnings) == 1

    def test_empty_initial_warns(self):
        m = Module("test")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with m.initial():
                pass
        empty_warnings = [x for x in w if "Empty initial" in str(x.message)]
        assert len(empty_warnings) == 1

    def test_non_empty_always_no_warn(self):
        m = Module("test")
        clk = m.input("clk")
        q = m.output_reg("q")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            with m.always(posedge(clk)):
                q <<= 0
        empty_warnings = [x for x in w if "Empty" in str(x.message)]
        assert len(empty_warnings) == 0


# ===================================================================
# M16: Assign to input port
# ===================================================================


class TestAssignToInput:
    """M16: Continuous assignment to an input port raises ValueError."""

    def test_assign_to_input_raises(self):
        m = Module("test")
        a = m.input("a")
        with pytest.raises(ValueError, match="input port"):
            m.assign(a, 1)

    def test_assign_to_input_mentions_name(self):
        m = Module("test")
        clk = m.input("clk")
        with pytest.raises(ValueError, match="clk"):
            m.assign(clk, 0)

    def test_assign_from_input_ok(self):
        """Using an input as RHS is fine."""
        m = Module("test")
        a = m.input("a")
        q = m.output("q")
        m.assign(q, a)
        mod = m.build()
        assert len(mod.continuous_assigns) == 1


# ===================================================================
# M18: Empty case statement
# ===================================================================


class TestEmptyCase:
    """M18: Case with no when/default items raises RuntimeError."""

    def test_empty_case_raises(self):
        m = Module("test")
        clk = m.input("clk")
        sel = m.input("sel", width=2)
        m.output_reg("out")
        with m.always(posedge(clk)):
            with pytest.raises(RuntimeError, match="Empty case"):
                with m.case(sel):
                    pass

    def test_empty_casex_raises(self):
        m = Module("test")
        clk = m.input("clk")
        sel = m.input("sel", width=2)
        m.output_reg("out")
        with m.always(posedge(clk)):
            with pytest.raises(RuntimeError, match="Empty case"):
                with m.casex(sel):
                    pass

    def test_case_with_when_ok(self):
        m = Module("test")
        clk = m.input("clk")
        sel = m.input("sel", width=2)
        out = m.output_reg("out")
        with m.always(posedge(clk)):
            with m.case(sel) as c:
                with c.when(0):
                    out <<= 0
                with c.default():
                    out <<= 1
        m.build()  # should not raise


# ===================================================================
# M23: Duplicate interface prefix
# ===================================================================


class TestDuplicateInterfacePrefix:
    """M23: Binding the same interface prefix twice raises ValueError."""

    def test_duplicate_prefix_raises(self):
        intf = Interface("axis").signal("tvalid", src="master").signal("tready", src="slave")
        m = Module("test")
        m.interface("m_axis", intf, role="master")
        with pytest.raises(ValueError, match="already bound"):
            m.interface("m_axis", intf, role="slave")

    def test_duplicate_wire_interface_prefix_raises(self):
        intf = Interface("axis").signal("tvalid", src="master").signal("tready", src="slave")
        m = Module("test")
        m.wire_interface("bus", intf)
        with pytest.raises(ValueError, match="already bound"):
            m.wire_interface("bus", intf)

    def test_duplicate_mixed_raises(self):
        """Port interface and wire interface with same prefix."""
        intf = Interface("axis").signal("tvalid", src="master")
        m = Module("test")
        m.interface("sig", intf, role="master")
        with pytest.raises(ValueError, match="already bound"):
            m.wire_interface("sig", intf)

    def test_different_prefixes_ok(self):
        intf = Interface("axis").signal("tvalid", src="master").signal("tready", src="slave")
        m = Module("test")
        m.interface("tx", intf, role="master")
        m.interface("rx", intf, role="slave")
        mod = m.build()
        assert len(mod.ports) == 4  # 2 signals x 2 interfaces
