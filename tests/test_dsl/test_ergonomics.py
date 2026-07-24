"""Tests for DSL ergonomic shorthands (July 2026).

Covers: m.seq()/m.comb(), Signal.next, when()/select(), Expr.bits(),
cat() iterable flattening, bulk declarators, and the prelude module.
"""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_expression, emit_module
from veriforge.dsl import Module, cat, mux, posedge, select, when
from veriforge.model.behavioral import SensitivityType
from veriforge.model.statements import IfStatement, NonblockingAssign
from veriforge.sim import Simulator


# ---------------------------------------------------------------------------
# m.seq() / m.comb()
# ---------------------------------------------------------------------------


class TestSeqComb:
    def test_seq_plain_equals_always_posedge(self):
        with Module("t") as m:
            clk = m.input("clk")
            q = m.output_reg("q", 8)
            with m.seq(clk):
                q.next = q + 1
        mod = m.build()
        ab = mod.always_blocks[0]
        assert ab.sensitivity_type == SensitivityType.SEQUENTIAL
        assert [e.edge for e in ab.sensitivity_list] == ["posedge"]
        assert "always @(posedge clk)" in emit_module(mod)

    def test_seq_with_reset_skeleton(self):
        with Module("t") as m:
            clk = m.input("clk")
            rst = m.input("rst")
            count = m.output_reg("count", 8)
            state = m.reg("state", 2)
            with m.seq(clk, rst=rst, rst_vals={count: 0, state: 3}):
                count.next = count + 1
        mod = m.build()
        body = mod.always_blocks[0].body
        assert isinstance(body, IfStatement)
        text = emit_module(mod)
        assert "if (rst)" in text
        assert "count <= 0;" in text
        assert "state <= 3;" in text
        assert "count <= count + 1;" in text

    def test_seq_reset_active_low(self):
        with Module("t") as m:
            clk = m.input("clk")
            rstn = m.input("rst_n")
            q = m.output_reg("q", 8)
            with m.seq(clk, rst=rstn, rst_vals={q: 0}, rst_active_low=True):
                q.next = q + 1
        assert "if (!rst_n)" in emit_module(m.build())

    def test_seq_async_reset_sensitivity(self):
        with Module("t") as m:
            clk = m.input("clk")
            rstn = m.input("rst_n")
            q = m.output_reg("q", 8)
            with m.seq(clk, rst=rstn, rst_vals={q: 0}, rst_active_low=True, async_reset=True):
                q.next = q + 1
        edges = [(e.edge, e.signal.name) for e in m.build().always_blocks[0].sensitivity_list]
        assert edges == [("posedge", "clk"), ("negedge", "rst_n")]

    def test_seq_async_reset_active_high_sensitivity(self):
        with Module("t") as m:
            clk = m.input("clk")
            rst = m.input("rst")
            q = m.output_reg("q", 8)
            with m.seq(clk, rst=rst, rst_vals={q: 0}, async_reset=True):
                q.next = q + 1
        edges = [(e.edge, e.signal.name) for e in m.build().always_blocks[0].sensitivity_list]
        assert edges == [("posedge", "clk"), ("posedge", "rst")]

    def test_seq_rst_requires_rst_vals(self):
        with Module("t") as m:
            clk = m.input("clk")
            rst = m.input("rst")
            with pytest.raises(TypeError, match="rst_vals"):
                m.seq(clk, rst=rst)

    def test_seq_rst_vals_requires_rst(self):
        with Module("t") as m:
            clk = m.input("clk")
            q = m.output_reg("q", 8)
            with pytest.raises(TypeError, match="require rst="):
                m.seq(clk, rst_vals={q: 0})

    def test_comb_is_star_sensitivity(self):
        with Module("t") as m:
            a = m.input("a", 8)
            y = m.output_reg("y", 8)
            with m.comb():
                y @= a + 1
        mod = m.build()
        assert mod.always_blocks[0].sensitivity_type == SensitivityType.COMBINATIONAL
        assert "always @(*)" in emit_module(mod)

    def test_seq_reset_simulates(self):
        with Module("cnt") as m:
            clk = m.input("clk")
            rst = m.input("rst")
            count = m.output_reg("count", 8)
            with m.seq(clk, rst=rst, rst_vals={count: 0}):
                count.next = count + 1
        mod = m.build()
        sim = Simulator(mod)
        from veriforge.sim import Clock

        sim.fork(Clock(sim.signal("clk"), period=10))
        sim.drive("rst", 1)
        sim.run(max_time=25)
        assert int(sim.read("count")) == 0
        sim.drive("rst", 0)
        sim.run(max_time=105)
        assert int(sim.read("count")) > 0
        sim.drive("rst", 1)
        sim.run(max_time=125)
        assert int(sim.read("count")) == 0


# ---------------------------------------------------------------------------
# Signal.next
# ---------------------------------------------------------------------------


class TestNextProperty:
    def test_next_creates_nba(self):
        with Module("t") as m:
            clk = m.input("clk")
            q = m.output_reg("q", 8)
            with m.always(posedge(clk)):
                q.next = q + 1
        mod = m.build()
        stmt = mod.always_blocks[0].body
        assert isinstance(stmt, NonblockingAssign)
        assert "q <= q + 1;" in emit_module(mod)

    def test_next_on_subscript(self):
        with Module("t") as m:
            clk = m.input("clk")
            data = m.reg("data", 8)
            i = m.input("i", 3)
            with m.always(posedge(clk)):
                data[i].next = 1
        assert "data[i] <= 1;" in emit_module(m.build())

    def test_next_outside_block_raises(self):
        with Module("t") as m:
            q = m.output_reg("q", 8)
            with pytest.raises(RuntimeError, match="always or initial"):
                q.next = 1

    def test_next_read_raises(self):
        with Module("t") as m:
            q = m.output_reg("q", 8)
            with pytest.raises(TypeError, match="write-only"):
                _ = q.next


# ---------------------------------------------------------------------------
# when() / select()
# ---------------------------------------------------------------------------


class TestWhenSelect:
    def test_when_otherwise_folds_to_nested_ternary(self):
        with Module("t") as m:
            a = m.input("a", 8)
            b = m.input("b", 8)
            expr = when(a == 0, 1).when(a == 1, b).otherwise(0)
        assert emit_expression(expr._as_expr()) == "a == 0 ? 1 : a == 1 ? b : 0"

    def test_when_matches_nested_mux(self):
        with Module("t") as m:
            a = m.input("a", 8)
            b = m.input("b", 8)
            chained = when(a == 0, 1).when(a == 1, b).otherwise(0)
            nested = mux(a == 0, 1, mux(a == 1, b, 0))
        assert emit_expression(chained._as_expr()) == emit_expression(nested._as_expr())

    def test_when_chain_is_immutable(self):
        with Module("t") as m:
            a = m.input("a", 8)
            base = when(a == 0, 1)
            base.when(a == 1, 2)  # discarded — must not mutate base
        assert emit_expression(base.otherwise(0)._as_expr()) == "a == 0 ? 1 : 0"

    def test_unclosed_when_in_assign_raises(self):
        with Module("t") as m:
            a = m.input("a", 8)
            y = m.output("y", 8)
            with pytest.raises(TypeError, match="otherwise"):
                m.assign(y, when(a == 0, 1))

    def test_select_builds_equality_chain(self):
        with Module("t") as m:
            sel = m.input("sel", 2)
            a = m.input("a", 8)
            b = m.input("b", 8)
            expr = select(sel, {0: a, 1: b}, default=0)
        assert emit_expression(expr._as_expr()) == "sel == 0 ? a : sel == 1 ? b : 0"

    def test_select_empty_raises(self):
        with Module("t") as m:
            sel = m.input("sel", 2)
            with pytest.raises(ValueError, match="at least one case"):
                select(sel, {}, default=0)

    def test_select_simulates(self):
        with Module("t") as m:
            sel = m.input("sel", 2)
            y = m.output("y", 8)
            m.assign(y, select(sel, {0: 10, 1: 20, 2: 30}, default=99))
        sim = Simulator(m.build())
        for s, expected in [(0, 10), (1, 20), (2, 30), (3, 99)]:
            sim.drive("sel", s)
            sim.settle()
            assert int(sim.read("y")) == expected


# ---------------------------------------------------------------------------
# Expr.bits()
# ---------------------------------------------------------------------------


class TestBits:
    def test_bits_lsb_ascending(self):
        with Module("t") as m:
            a = m.input("a", 32)
            expr = a.bits(lsb=8, width=16)
        assert emit_expression(expr._as_expr()) == "a[8 +: 16]"

    def test_bits_msb_descending(self):
        with Module("t") as m:
            a = m.input("a", 32)
            expr = a.bits(msb=31, width=8)
        assert emit_expression(expr._as_expr()) == "a[31 -: 8]"

    def test_bits_requires_exactly_one_base(self):
        with Module("t") as m:
            a = m.input("a", 32)
            with pytest.raises(TypeError, match="exactly one"):
                a.bits(width=8)
            with pytest.raises(TypeError, match="exactly one"):
                a.bits(lsb=0, msb=31, width=8)


# ---------------------------------------------------------------------------
# cat() flattening
# ---------------------------------------------------------------------------


class TestCatFlattening:
    def test_cat_single_list(self):
        with Module("t") as m:
            a = m.input("a", 8)
            b = m.input("b", 8)
            expr = cat([a, b])
        assert emit_expression(expr._as_expr()) == "{a, b}"

    def test_cat_mixed_and_nested(self):
        with Module("t") as m:
            a = m.input("a", 8)
            b = m.input("b", 8)
            c = m.input("c", 8)
            expr = cat(a, [b, (c, 1)])
        assert emit_expression(expr._as_expr()) == "{a, b, c, 1}"

    def test_cat_empty_list_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            cat([])


# ---------------------------------------------------------------------------
# Bulk declarators
# ---------------------------------------------------------------------------


class TestBulkDeclarators:
    def test_inputs_with_widths(self):
        with Module("t") as m:
            clk, rst = m.inputs("clk rst")
            a, b = m.inputs("a:8, b:16")
        mod = m.build()
        widths = {p.name: p.width for p in mod.ports}
        assert widths["clk"] is None
        assert emit_expression(widths["a"].msb) == "7"
        assert emit_expression(widths["b"].msb) == "15"

    def test_all_bulk_kinds(self):
        with Module("t") as m:
            m.inputs("clk")
            (o,) = m.outputs("o:4")
            (q,) = m.output_regs("q:4")
            (w,) = m.wires("w:4")
            (r,) = m.regs("r:4")
        text = emit_module(m.build())
        assert "output [3:0] o" in text
        assert "output reg [3:0] q" in text
        assert "wire [3:0] w;" in text
        assert "reg [3:0] r;" in text

    def test_bad_width_raises(self):
        with Module("t") as m:
            with pytest.raises(ValueError, match="Invalid width"):
                m.inputs("a:x")
            with pytest.raises(ValueError, match="positive"):
                m.inputs("a:0")
            with pytest.raises(ValueError, match="empty"):
                m.inputs("  ")

    def test_duplicate_name_still_caught(self):
        with Module("t") as m:
            m.inputs("a")
            with pytest.raises(ValueError, match="already declared"):
                m.inputs("a:8")


# ---------------------------------------------------------------------------
# Prelude
# ---------------------------------------------------------------------------


class TestPrelude:
    def test_prelude_exports(self):
        from veriforge.dsl import prelude

        for name in prelude.__all__:
            assert hasattr(prelude, name), f"prelude missing {name}"

    def test_prelude_star_import_builds_module(self):
        namespace: dict = {}
        exec(  # noqa: S102 — deliberate star-import smoke test
            "from veriforge.dsl.prelude import *\n"
            "with Module('t') as m:\n"
            "    clk = m.input('clk')\n"
            "    q = m.output_reg('q', 8)\n"
            "    with m.seq(clk):\n"
            "        q.next = q + 1\n"
            "mod = m.build()\n",
            namespace,
        )
        assert namespace["mod"].name == "t"
