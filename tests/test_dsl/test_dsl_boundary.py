"""Tests for Python/DSL boundary semantics and Expr guards.

Validates that common mistakes when mixing Python with the DSL
are caught with clear error messages.
"""

from __future__ import annotations

import pytest

from veriforge.dsl import Module, posedge


# ── Expr.__bool__ guard ─────────────────────────────────────────────


class TestExprBool:
    """Expr objects cannot be used as Python booleans."""

    def test_bool_raises_typeerror(self):
        with Module("t") as m:
            a = m.input("a")
            with pytest.raises(TypeError, match="Cannot use hardware expression as Python boolean"):
                bool(a)

    def test_if_signal_raises(self):
        with Module("t") as m:
            a = m.input("a")
            with pytest.raises(TypeError, match=r"m\.if_"):
                if a:
                    pass

    def test_and_signal_raises(self):
        with Module("t") as m:
            a = m.input("a")
            b = m.input("b")
            with pytest.raises(TypeError, match="Python boolean"):
                _ = a and b

    def test_or_signal_raises(self):
        with Module("t") as m:
            a = m.input("a")
            b = m.input("b")
            with pytest.raises(TypeError, match="Python boolean"):
                _ = a or b

    def test_not_signal_raises(self):
        with Module("t") as m:
            a = m.input("a")
            with pytest.raises(TypeError, match="Python boolean"):
                not a  # noqa: B018

    def test_expr_result_bool_raises(self):
        with Module("t") as m:
            a = m.input("a", width=8)
            expr = a + 1
            with pytest.raises(TypeError, match="Python boolean"):
                bool(expr)


# ── Expr.__iter__ guard ─────────────────────────────────────────────


class TestExprIter:
    """Expr objects cannot be iterated."""

    def test_iter_raises_typeerror(self):
        with Module("t") as m:
            a = m.input("a", width=8)
            with pytest.raises(TypeError, match="Cannot iterate"):
                iter(a)

    def test_for_loop_raises(self):
        with Module("t") as m:
            a = m.input("a", width=8)
            with pytest.raises(TypeError, match="Cannot iterate"):
                for _ in a:
                    pass

    def test_list_conversion_raises(self):
        with Module("t") as m:
            a = m.input("a", width=8)
            with pytest.raises(TypeError, match="Cannot iterate"):
                list(a)

    def test_tuple_conversion_raises(self):
        with Module("t") as m:
            a = m.input("a", width=8)
            with pytest.raises(TypeError, match="Cannot iterate"):
                tuple(a)

    def test_unpacking_raises(self):
        with Module("t") as m:
            a = m.input("a", width=8)
            with pytest.raises(TypeError, match="Cannot iterate"):
                _, _ = a


# ── Expr.__len__ guard ──────────────────────────────────────────────


class TestExprLen:
    """Expr objects do not support len()."""

    def test_len_raises_typeerror(self):
        with Module("t") as m:
            a = m.input("a", width=8)
            with pytest.raises(TypeError, match="Cannot call len"):
                len(a)

    def test_len_on_expr_raises(self):
        with Module("t") as m:
            a = m.input("a", width=8)
            b = m.input("b", width=8)
            with pytest.raises(TypeError, match="Cannot call len"):
                len(a + b)


# ── Build-time vs sim-time behavior ─────────────────────────────────


class TestBuildTimeSemantics:
    """Python code inside DSL blocks executes at build time."""

    def test_python_for_creates_multiple_wires(self):
        """Python for loop creates multiple wire declarations."""
        with Module("t") as m:
            for i in range(4):
                m.wire(f"w{i}", width=8)
        mod = m.build()
        wire_names = [n.name for n in mod.nets]
        assert "w0" in wire_names
        assert "w1" in wire_names
        assert "w2" in wire_names
        assert "w3" in wire_names

    def test_python_if_for_conditional_generation(self):
        """Python if with a plain int controls what hardware is generated."""
        width = 8
        with Module("t") as m:
            a = m.input("a", width=width)
            if width > 4:
                q = m.output("q", width=width)
                m.assign(q, a)
            else:
                q = m.output("q")
                m.assign(q, a[0])
        mod = m.build()
        # With width=8, should have 8-bit output
        out = next(p for p in mod.ports if p.name == "q")
        assert out.width is not None

    def test_python_dict_for_config(self):
        """Python dicts can configure hardware at build time."""
        config = {"width": 16, "name": "data"}
        with Module("t") as m:
            d = m.input(config["name"], width=config["width"])
            q = m.output("q", width=config["width"])
            m.assign(q, d)
        mod = m.build()
        assert any(p.name == "data" for p in mod.ports)

    def test_display_creates_system_task(self):
        """m.display() creates a $display system task node."""
        from veriforge.model.statements import SystemTaskCall

        with Module("t") as m:
            clk = m.input("clk")
            cnt = m.output_reg("cnt", width=8)
            with m.always(posedge(clk)):
                m.display("count = %d", cnt)
                cnt <<= cnt + 1
        mod = m.build()
        # Check that a $display system task was created
        body = mod.always_blocks[0].body
        stmts = body.statements if hasattr(body, "statements") else [body]
        has_display = any(isinstance(s, SystemTaskCall) and s.task_name == "$display" for s in stmts)
        assert has_display


# ── operator overloading correctness ────────────────────────────────


class TestOperatorOverloading:
    """Verify that arithmetic on Expr objects produces expression trees, not values."""

    def test_addition_creates_expr(self):
        from veriforge.dsl.builder import Expr

        with Module("t") as m:
            a = m.input("a", width=8)
            b = m.input("b", width=8)
            result = a + b
            assert isinstance(result, Expr)

    def test_comparison_creates_expr(self):
        from veriforge.dsl.builder import Expr

        with Module("t") as m:
            a = m.input("a", width=8)
            result = a == 5
            assert isinstance(result, Expr)

    def test_bitwise_creates_expr(self):
        from veriforge.dsl.builder import Expr

        with Module("t") as m:
            a = m.input("a", width=8)
            result = a & 0xFF
            assert isinstance(result, Expr)
