"""Tests for the Hardware Construction DSL."""

from __future__ import annotations

import pytest

from veriforge.codegen.verilog_emitter import emit_module, emit_expression
from veriforge.dsl import (
    Expr,
    Interface,
    Module,
    Signal,
    ashl,
    ashr,
    cat,
    case_eq,
    case_ne,
    clog2,
    land,
    lnot,
    lor,
    mux,
    negedge,
    posedge,
    reduce_and,
    reduce_or,
    reduce_xor,
    rep,
    signed,
    sim_time,
    unsigned,
)
from veriforge.model.behavioral import SensitivityType
from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    FunctionCall,
    Identifier,
    Literal,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from veriforge.model.statements import (
    BlockingAssign,
    CaseStatement,
    DelayControl,
    EventControl,
    IfStatement,
    NonblockingAssign,
    SeqBlock,
    SystemTaskCall,
)
from veriforge.sim import Clock, Simulator


# ===================================================================
# Port / signal declarations
# ===================================================================


class TestPortDeclaration:
    """Test port and signal declaration methods."""

    def test_input_scalar(self):
        m = Module("test")
        a = m.input("a")
        module = m.build()
        assert len(module.ports) == 1
        assert module.ports[0].name == "a"
        assert module.ports[0].direction.value == "input"
        assert module.ports[0].width is None
        assert isinstance(a, Signal)

    def test_input_vector(self):
        m = Module("test")
        m.input("data", width=8)
        module = m.build()
        port = module.ports[0]
        assert port.width is not None
        assert port.width.msb.value == 7
        assert port.width.lsb.value == 0

    def test_output_port(self):
        m = Module("test")
        m.output("y", width=4)
        module = m.build()
        port = module.ports[0]
        assert port.direction.value == "output"
        assert port.width.msb.value == 3

    def test_output_reg_port(self):
        m = Module("test")
        m.output_reg("q", width=8)
        module = m.build()
        port = module.ports[0]
        assert port.direction.value == "output"
        assert port.data_type == "reg"
        # Should also create a variable
        assert len(module.variables) == 1
        assert module.variables[0].name == "q"
        assert module.variables[0].kind.value == "reg"

    def test_inout_port(self):
        m = Module("test")
        m.inout("data", width=8)
        module = m.build()
        assert module.ports[0].direction.value == "inout"

    def test_wire(self):
        m = Module("test")
        m.wire("internal", width=16)
        module = m.build()
        assert len(module.nets) == 1
        assert module.nets[0].name == "internal"
        assert module.nets[0].kind.value == "wire"

    def test_reg(self):
        m = Module("test")
        m.reg("state", width=4)
        module = m.build()
        assert len(module.variables) == 1
        assert module.variables[0].name == "state"
        assert module.variables[0].kind.value == "reg"

    def test_integer(self):
        m = Module("test")
        m.integer("i")
        module = m.build()
        assert module.variables[0].kind.value == "integer"

    def test_signed_port(self):
        m = Module("test")
        m.input("d", width=8, signed=True)
        module = m.build()
        assert module.ports[0].signed is True

    def test_parameter(self):
        m = Module("test")
        m.parameter("WIDTH", default=8)
        module = m.build()
        assert len(module.parameters) == 1
        assert module.parameters[0].name == "WIDTH"
        assert module.parameters[0].default_value.value == 8
        assert module.parameters[0].is_local is False

    def test_localparam(self):
        m = Module("test")
        m.localparam("HALF", value=4)
        module = m.build()
        assert module.parameters[0].is_local is True
        assert module.parameters[0].default_value.value == 4

    def test_parameterized_width(self):
        m = Module("test")
        w = m.parameter("W", default=8)
        m.input("data", width=w)
        module = m.build()
        port = module.ports[0]
        # Width should be [W-1:0]
        assert isinstance(port.width.msb, BinaryOp)
        assert port.width.msb.op == "-"
        assert isinstance(port.width.msb.left, Identifier)
        assert port.width.msb.left.name == "W"


# ===================================================================
# Expression operators
# ===================================================================


class TestExpressionOperators:
    """Test operator overloading on Signal/Expr objects."""

    def test_add(self):
        m = Module("test")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        result = a + b
        assert isinstance(result, Expr)
        expr = result._as_expr()
        assert isinstance(expr, BinaryOp)
        assert expr.op == "+"

    def test_sub(self):
        m = Module("test")
        a = m.input("a")
        result = a - 1
        expr = result._as_expr()
        assert isinstance(expr, BinaryOp)
        assert expr.op == "-"

    def test_mul(self):
        m = Module("test")
        a = m.input("a")
        result = a * 2
        assert result._as_expr().op == "*"

    def test_floordiv(self):
        m = Module("test")
        a = m.input("a")
        result = a // 4
        assert result._as_expr().op == "/"

    def test_mod(self):
        m = Module("test")
        a = m.input("a")
        result = a % 3
        assert result._as_expr().op == "%"

    def test_power(self):
        m = Module("test")
        a = m.input("a")
        result = a**2
        assert result._as_expr().op == "**"

    def test_bitwise_and(self):
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        result = a & b
        assert result._as_expr().op == "&"

    def test_bitwise_or(self):
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        result = a | b
        assert result._as_expr().op == "|"

    def test_bitwise_xor(self):
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        result = a ^ b
        assert result._as_expr().op == "^"

    def test_invert(self):
        m = Module("test")
        a = m.input("a")
        result = ~a
        expr = result._as_expr()
        assert isinstance(expr, UnaryOp)
        assert expr.op == "~"

    def test_neg(self):
        m = Module("test")
        a = m.input("a")
        result = -a
        assert result._as_expr().op == "-"

    def test_lshift(self):
        m = Module("test")
        a = m.input("a")
        result = a << 2
        assert result._as_expr().op == "<<"

    def test_rshift(self):
        m = Module("test")
        a = m.input("a")
        result = a >> 1
        assert result._as_expr().op == ">>"

    def test_eq(self):
        m = Module("test")
        a = m.input("a")
        result = a == 0
        expr = result._as_expr()
        assert isinstance(expr, BinaryOp)
        assert expr.op == "=="

    def test_ne(self):
        m = Module("test")
        a = m.input("a")
        result = a != 0
        assert result._as_expr().op == "!="

    def test_lt(self):
        m = Module("test")
        a = m.input("a")
        result = a < 8
        assert result._as_expr().op == "<"

    def test_le(self):
        m = Module("test")
        a = m.input("a")
        result = a <= 8
        assert result._as_expr().op == "<="

    def test_gt(self):
        m = Module("test")
        a = m.input("a")
        result = a > 0
        assert result._as_expr().op == ">"

    def test_ge(self):
        m = Module("test")
        a = m.input("a")
        result = a >= 1
        assert result._as_expr().op == ">="

    def test_reverse_add(self):
        """Test int + Signal uses __radd__."""
        m = Module("test")
        a = m.input("a")
        result = 1 + a
        expr = result._as_expr()
        assert isinstance(expr, BinaryOp)
        assert expr.op == "+"
        # Left should be the literal 1
        assert isinstance(expr.left, Literal)
        assert expr.left.value == 1

    def test_reverse_sub(self):
        m = Module("test")
        a = m.input("a")
        result = 10 - a
        expr = result._as_expr()
        assert expr.op == "-"
        assert isinstance(expr.left, Literal)
        assert expr.left.value == 10

    def test_bit_select(self):
        m = Module("test")
        a = m.input("a", width=8)
        result = a[3]
        expr = result._as_expr()
        assert isinstance(expr, BitSelect)
        assert isinstance(expr.index, Literal)
        assert expr.index.value == 3

    def test_range_select(self):
        m = Module("test")
        a = m.input("a", width=8)
        result = a[7:4]
        expr = result._as_expr()
        assert isinstance(expr, RangeSelect)
        assert expr.msb.value == 7
        assert expr.lsb.value == 4

    def test_chained_expression(self):
        """Test complex expression: (a + b) & 0xFF"""
        m = Module("test")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        result = (a + b) & 0xFF
        expr = result._as_expr()
        assert isinstance(expr, BinaryOp)
        assert expr.op == "&"
        assert isinstance(expr.left, BinaryOp)
        assert expr.left.op == "+"

    def test_bool_raises(self):
        """Expr cannot be used as Python boolean."""
        m = Module("test")
        a = m.input("a")
        with pytest.raises(TypeError, match="Cannot use hardware expression"):
            bool(a)

    def test_hash_is_identity(self):
        """Signals can be used in sets/dicts."""
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        s = {a, b}
        assert len(s) == 2


# ===================================================================
# Continuous assignment
# ===================================================================


class TestContinuousAssign:
    """Test continuous assign declarations."""

    def test_simple_assign(self):
        m = Module("test")
        a = m.input("a")
        y = m.output("y")
        m.assign(y, a)
        module = m.build()
        assert len(module.continuous_assigns) == 1
        ca = module.continuous_assigns[0]
        assert isinstance(ca.lhs, Identifier)
        assert ca.lhs.name == "y"
        assert isinstance(ca.rhs, Identifier)
        assert ca.rhs.name == "a"

    def test_expression_assign(self):
        m = Module("test")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        s = m.output("sum", width=9)
        m.assign(s, a + b)
        module = m.build()
        ca = module.continuous_assigns[0]
        assert isinstance(ca.rhs, BinaryOp)
        assert ca.rhs.op == "+"

    def test_assign_with_literal(self):
        m = Module("test")
        y = m.output("y")
        m.assign(y, 0)
        module = m.build()
        ca = module.continuous_assigns[0]
        assert isinstance(ca.rhs, Literal)
        assert ca.rhs.value == 0


# ===================================================================
# Always blocks
# ===================================================================


class TestAlwaysBlocks:
    """Test always block construction."""

    def test_sequential_posedge(self):
        m = Module("test")
        clk = m.input("clk")
        q = m.output_reg("q")
        d = m.input("d")
        with m.always(posedge(clk)):
            q <<= d
        module = m.build()
        assert len(module.always_blocks) == 1
        ab = module.always_blocks[0]
        assert ab.sensitivity_type == SensitivityType.SEQUENTIAL
        assert len(ab.sensitivity_list) == 1
        assert ab.sensitivity_list[0].edge == "posedge"

    def test_combinational_star(self):
        """Empty sensitivity = @(*)."""
        m = Module("test")
        a = m.input("a")
        y = m.output_reg("y")
        with m.always():
            y.set(a)
        module = m.build()
        ab = module.always_blocks[0]
        assert ab.sensitivity_type == SensitivityType.COMBINATIONAL
        assert len(ab.sensitivity_list) == 0

    def test_combinational_level(self):
        """Level-sensitive signals."""
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        y = m.output_reg("y")
        with m.always(a, b):
            y.set(a & b)
        module = m.build()
        ab = module.always_blocks[0]
        assert ab.sensitivity_type == SensitivityType.COMBINATIONAL
        assert len(ab.sensitivity_list) == 2

    def test_negedge_sensitivity(self):
        m = Module("test")
        clk = m.input("clk")
        q = m.output_reg("q")
        with m.always(negedge(clk)):
            q <<= 0
        module = m.build()
        ab = module.always_blocks[0]
        assert ab.sensitivity_list[0].edge == "negedge"

    def test_nba_creates_nonblocking(self):
        m = Module("test")
        clk = m.input("clk")
        q = m.output_reg("q")
        with m.always(posedge(clk)):
            q <<= 1
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, NonblockingAssign)

    def test_set_creates_blocking(self):
        m = Module("test")
        a = m.input("a")
        y = m.output_reg("y")
        with m.always():
            y.set(a)
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, BlockingAssign)

    def test_multiple_statements_wrapped(self):
        """Multiple statements in always become SeqBlock."""
        m = Module("test")
        clk = m.input("clk")
        a = m.output_reg("a")
        b = m.output_reg("b")
        with m.always(posedge(clk)):
            a <<= 0
            b <<= 1
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, SeqBlock)
        assert len(body.statements) == 2

    def test_nba_outside_block_raises(self):
        m = Module("test")
        a = m.input("a")
        with pytest.raises(RuntimeError, match="must be inside"):
            a <<= 0

    def test_set_outside_block_raises(self):
        m = Module("test")
        a = m.input("a")
        with pytest.raises(RuntimeError, match="must be inside"):
            a.set(0)

    def test_matmul_creates_blocking(self):
        """``signal @= expr`` creates a blocking assignment."""
        m = Module("test")
        a = m.input("a")
        y = m.output_reg("y")
        with m.always():
            y @= a
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, BlockingAssign)

    def test_matmul_outside_block_raises(self):
        m = Module("test")
        a = m.input("a")
        with pytest.raises(RuntimeError, match="must be inside"):
            a @= 0

    def test_matmul_with_literal(self):
        """``@=`` works with plain integer literals."""
        m = Module("test")
        y = m.output_reg("y", width=8)
        with m.always():
            y @= 0xFF
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, BlockingAssign)
        assert body.rhs.value == 0xFF

    def test_matmul_emits_blocking_verilog(self):
        """``@=`` emits ``=`` (blocking) in Verilog output."""
        m = Module("test")
        a = m.input("a")
        y = m.output_reg("y")
        with m.always():
            y @= a
        v = emit_module(m.build())
        # blocking uses '=' not '<='
        assert "y = a" in v

    def test_assign_blocking_method(self):
        """``m.assign_blocking(lhs, rhs)`` creates a blocking assignment."""
        m = Module("test")
        a = m.input("a")
        y = m.output_reg("y")
        with m.always():
            m.assign_blocking(y, a)
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, BlockingAssign)

    def test_assign_b_alias(self):
        """``m.assign_b`` is an alias for ``m.assign_blocking``."""
        m = Module("test")
        a = m.input("a")
        y = m.output_reg("y")
        with m.always():
            m.assign_b(y, a)
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, BlockingAssign)

    def test_assign_nonblocking_method(self):
        """``m.assign_nonblocking(lhs, rhs)`` creates a non-blocking assignment."""
        m = Module("test")
        clk = m.input("clk")
        q = m.output_reg("q")
        with m.always(posedge(clk)):
            m.assign_nonblocking(q, 1)
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, NonblockingAssign)

    def test_assign_nb_alias(self):
        """``m.assign_nb`` is an alias for ``m.assign_nonblocking``."""
        m = Module("test")
        clk = m.input("clk")
        q = m.output_reg("q")
        with m.always(posedge(clk)):
            m.assign_nb(q, 1)
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, NonblockingAssign)

    def test_assign_blocking_outside_block_raises(self):
        m = Module("test")
        a = m.input("a")
        y = m.output_reg("y")
        with pytest.raises(RuntimeError, match="must be inside"):
            m.assign_blocking(y, a)

    def test_assign_nonblocking_outside_block_raises(self):
        m = Module("test")
        clk = m.input("clk")
        q = m.output_reg("q")
        with pytest.raises(RuntimeError, match="must be inside"):
            m.assign_nonblocking(q, 1)


# ===================================================================
# Initial blocks
# ===================================================================


class TestInitialBlocks:
    """Test initial block construction."""

    def test_initial_block(self):
        m = Module("test")
        q = m.reg("q")
        with m.initial():
            q.set(0)
        module = m.build()
        assert len(module.initial_blocks) == 1
        body = module.initial_blocks[0].body
        assert isinstance(body, BlockingAssign)

    def test_matmul_in_initial(self):
        """``@=`` works inside initial blocks."""
        m = Module("test")
        q = m.reg("q")
        with m.initial():
            q @= 0
        module = m.build()
        body = module.initial_blocks[0].body
        assert isinstance(body, BlockingAssign)


# ===================================================================
# If / elif / else
# ===================================================================


class TestIfElse:
    """Test if/elif/else control flow construction."""

    def test_if_only(self):
        m = Module("test")
        clk = m.input("clk")
        rst = m.input("rst")
        q = m.output_reg("q")
        with m.always(posedge(clk)):
            with m.if_(rst):
                q <<= 0
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, IfStatement)
        assert body.else_body is None

    def test_if_else(self):
        m = Module("test")
        clk = m.input("clk")
        rst = m.input("rst")
        q = m.output_reg("q")
        d = m.input("d")
        with m.always(posedge(clk)):
            with m.if_(rst):
                q <<= 0
            with m.else_():
                q <<= d
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, IfStatement)
        assert body.else_body is not None

    def test_if_elif_else(self):
        m = Module("test")
        clk = m.input("clk")
        sel = m.input("sel", width=2)
        q = m.output_reg("q")
        with m.always(posedge(clk)):
            with m.if_(sel == 0):
                q <<= 0
            with m.elif_(sel == 1):
                q <<= 1
            with m.else_():
                q <<= 2
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, IfStatement)
        # elif becomes nested IfStatement in else
        assert isinstance(body.else_body, IfStatement)
        # else on the elif
        assert body.else_body.else_body is not None
        assert not isinstance(body.else_body.else_body, IfStatement)

    def test_multiple_elif(self):
        m = Module("test")
        clk = m.input("clk")
        sel = m.input("sel", width=2)
        q = m.output_reg("q")
        with m.always(posedge(clk)):
            with m.if_(sel == 0):
                q <<= 0
            with m.elif_(sel == 1):
                q <<= 1
            with m.elif_(sel == 2):
                q <<= 2
            with m.else_():
                q <<= 3
        module = m.build()
        body = module.always_blocks[0].body
        # if -> elif -> elif -> else
        assert isinstance(body.else_body, IfStatement)
        assert isinstance(body.else_body.else_body, IfStatement)
        assert not isinstance(body.else_body.else_body.else_body, IfStatement)

    def test_nested_if(self):
        """If inside if."""
        m = Module("test")
        clk = m.input("clk")
        a = m.input("a")
        b = m.input("b")
        q = m.output_reg("q")
        with m.always(posedge(clk)):
            with m.if_(a):
                with m.if_(b):
                    q <<= 1
        module = m.build()
        outer = module.always_blocks[0].body
        assert isinstance(outer, IfStatement)
        assert isinstance(outer.then_body, IfStatement)

    def test_if_with_multiple_stmts(self):
        m = Module("test")
        clk = m.input("clk")
        a = m.output_reg("a")
        b = m.output_reg("b")
        rst = m.input("rst")
        with m.always(posedge(clk)):
            with m.if_(rst):
                a <<= 0
                b <<= 0
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, IfStatement)
        assert isinstance(body.then_body, SeqBlock)
        assert len(body.then_body.statements) == 2


# ===================================================================
# Case statements
# ===================================================================


class TestCaseStatements:
    """Test case/casex/casez construction."""

    def test_basic_case(self):
        m = Module("test")
        sel = m.input("sel", width=2)
        y = m.output_reg("y", width=8)
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        with m.always():
            with m.case(sel) as c:
                with c.when(0):
                    y.set(a)
                with c.when(1):
                    y.set(b)
                with c.default():
                    y.set(0)
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, CaseStatement)
        assert body.case_type == "case"
        assert len(body.items) == 3
        assert body.items[2].is_default is True

    def test_case_multiple_values(self):
        m = Module("test")
        sel = m.input("sel", width=2)
        y = m.output_reg("y")
        with m.always():
            with m.case(sel) as c:
                with c.when(0, 1):
                    y.set(1)
                with c.when(2, 3):
                    y.set(0)
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, CaseStatement)
        item = body.items[0]
        assert len(item.values) == 2

    def test_casex(self):
        m = Module("test")
        sel = m.input("sel", width=2)
        y = m.output_reg("y")
        with m.always():
            with m.casex(sel) as c:
                with c.when(0):
                    y.set(0)
        module = m.build()
        body = module.always_blocks[0].body
        assert body.case_type == "casex"

    def test_casez(self):
        m = Module("test")
        sel = m.input("sel")
        y = m.output_reg("y")
        with m.always():
            with m.casez(sel) as c:
                with c.when(0):
                    y.set(0)
        module = m.build()
        assert module.always_blocks[0].body.case_type == "casez"


# ===================================================================
# Helper functions
# ===================================================================


class TestHelperFunctions:
    """Test public helper functions."""

    def test_posedge(self):
        m = Module("test")
        clk = m.input("clk")
        edge = posedge(clk)
        assert edge.edge == "posedge"
        assert isinstance(edge.signal, Identifier)
        assert edge.signal.name == "clk"

    def test_negedge(self):
        m = Module("test")
        clk = m.input("clk")
        edge = negedge(clk)
        assert edge.edge == "negedge"

    def test_cat(self):
        m = Module("test")
        a = m.input("a", width=4)
        b = m.input("b", width=4)
        result = cat(a, b)
        expr = result._as_expr()
        assert isinstance(expr, Concatenation)
        assert len(expr.parts) == 2

    def test_cat_with_int(self):
        m = Module("test")
        a = m.input("a", width=4)
        result = cat(a, 0)
        expr = result._as_expr()
        assert isinstance(expr, Concatenation)
        assert isinstance(expr.parts[1], Literal)

    def test_rep(self):
        m = Module("test")
        a = m.input("a")
        result = rep(4, a)
        expr = result._as_expr()
        assert isinstance(expr, Replication)
        assert expr.count.value == 4

    def test_mux(self):
        m = Module("test")
        sel = m.input("sel")
        a = m.input("a")
        b = m.input("b")
        result = mux(sel, a, b)
        expr = result._as_expr()
        assert isinstance(expr, TernaryOp)

    def test_land(self):
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        result = land(a, b)
        assert result._as_expr().op == "&&"

    def test_lor(self):
        m = Module("test")
        a = m.input("a")
        b = m.input("b")
        result = lor(a, b)
        assert result._as_expr().op == "||"

    def test_lnot(self):
        m = Module("test")
        a = m.input("a")
        result = lnot(a)
        expr = result._as_expr()
        assert isinstance(expr, UnaryOp)
        assert expr.op == "!"

    def test_reduce_and(self):
        m = Module("test")
        a = m.input("a", width=8)
        result = reduce_and(a)
        assert result._as_expr().op == "&"

    def test_reduce_or(self):
        m = Module("test")
        a = m.input("a", width=8)
        result = reduce_or(a)
        assert result._as_expr().op == "|"

    def test_reduce_xor(self):
        m = Module("test")
        a = m.input("a", width=8)
        result = reduce_xor(a)
        assert result._as_expr().op == "^"

    def test_ashl(self):
        m = Module("test")
        a = m.input("a", width=8)
        result = ashl(a, 2)
        assert result._as_expr().op == "<<<"

    def test_ashr(self):
        m = Module("test")
        a = m.input("a", width=8, signed=True)
        result = ashr(a, 1)
        assert result._as_expr().op == ">>>"

    def test_case_eq(self):
        m = Module("test")
        a = m.input("a", width=4)
        b = m.input("b", width=4)
        result = case_eq(a, b)
        assert result._as_expr().op == "==="

    def test_case_ne(self):
        m = Module("test")
        a = m.input("a", width=4)
        b = m.input("b", width=4)
        result = case_ne(a, b)
        assert result._as_expr().op == "!=="

    def test_clog2(self):
        m = Module("test")
        a = m.input("a", width=8)
        result = clog2(a)
        expr = result._as_expr()
        assert isinstance(expr, FunctionCall)
        assert expr.name == "$clog2"
        assert expr.is_system is True

    def test_signed(self):
        m = Module("test")
        a = m.input("a", width=8)
        result = signed(a)
        expr = result._as_expr()
        assert isinstance(expr, FunctionCall)
        assert expr.name == "$signed"
        assert expr.is_system is True

    def test_unsigned(self):
        m = Module("test")
        a = m.input("a", width=8)
        result = unsigned(a)
        expr = result._as_expr()
        assert isinstance(expr, FunctionCall)
        assert expr.name == "$unsigned"
        assert expr.is_system is True

    def test_sim_time(self):
        result = sim_time()
        expr = result._as_expr()
        assert isinstance(expr, FunctionCall)
        assert expr.name == "$time"
        assert expr.arguments == []
        assert expr.is_system is True


# ===================================================================
# LHS targets (bit select, range select, concat)
# ===================================================================


class TestLhsTargets:
    """Test assignment to bit selects, range selects, etc."""

    def test_nba_bit_select(self):
        m = Module("test")
        clk = m.input("clk")
        data = m.output_reg("data", width=8)
        with m.always(posedge(clk)):
            data[3] <<= 1
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, NonblockingAssign)
        assert isinstance(body.lhs, BitSelect)
        assert body.lhs.index.value == 3

    def test_nba_range_select(self):
        m = Module("test")
        clk = m.input("clk")
        data = m.output_reg("data", width=16)
        with m.always(posedge(clk)):
            data[7:0] <<= 0xFF
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body.lhs, RangeSelect)
        assert body.lhs.msb.value == 7
        assert body.lhs.lsb.value == 0

    def test_blocking_bit_select(self):
        m = Module("test")
        a = m.input("a")
        y = m.output_reg("y", width=8)
        with m.always():
            y[0].set(a)
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, BlockingAssign)
        assert isinstance(body.lhs, BitSelect)

    def test_matmul_bit_select(self):
        """``data[i] @= expr`` creates blocking assign with BitSelect LHS."""
        m = Module("test")
        a = m.input("a")
        y = m.output_reg("y", width=8)
        with m.always():
            y[0] @= a
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, BlockingAssign)
        assert isinstance(body.lhs, BitSelect)

    def test_matmul_range_select(self):
        """``data[hi:lo] @= expr`` creates blocking assign with RangeSelect LHS."""
        m = Module("test")
        data = m.output_reg("data", width=16)
        with m.always():
            data[7:0] @= 0xFF
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, BlockingAssign)
        assert isinstance(body.lhs, RangeSelect)
        assert body.lhs.msb.value == 7
        assert body.lhs.lsb.value == 0

    def test_nba_concat_lhs(self):
        m = Module("test")
        clk = m.input("clk")
        a = m.output_reg("a")
        b = m.output_reg("b")
        c = m.input("c", width=2)
        with m.always(posedge(clk)):
            lhs = cat(a, b)
            lhs <<= c
        module = m.build()
        body = module.always_blocks[0].body
        assert isinstance(body, NonblockingAssign)
        assert isinstance(body.lhs, Concatenation)


# ===================================================================
# Verilog emission
# ===================================================================


class TestVerilogEmission:
    """Test emitting DSL-built modules to Verilog."""

    def test_empty_module(self):
        m = Module("empty")
        module = m.build()
        v = emit_module(module)
        assert "module empty;" in v
        assert "endmodule" in v

    def test_adder(self):
        m = Module("adder")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        s = m.output("sum", width=9)
        m.assign(s, a + b)
        v = emit_module(m.build())
        assert "module adder" in v
        assert "input [7:0] a" in v
        assert "input [7:0] b" in v
        assert "output [8:0] sum" in v
        assert "assign sum = a + b;" in v

    def test_counter(self):
        with Module("counter") as m:
            clk = m.input("clk")
            rst = m.input("rst")
            count = m.output_reg("count", width=8)
            with m.always(posedge(clk)):
                with m.if_(rst):
                    count <<= 0
                with m.else_():
                    count <<= count + 1
        v = emit_module(m.build())
        assert "always @(posedge clk)" in v
        assert "count <= 0;" in v
        assert "count <= count + 1;" in v
        assert "if (rst)" in v
        assert "else" in v

    def test_mux4(self):
        with Module("mux4") as m:
            sel = m.input("sel", width=2)
            a = m.input("a", width=8)
            b = m.input("b", width=8)
            c = m.input("c", width=8)
            d = m.input("d", width=8)
            y = m.output_reg("y", width=8)
            with m.always():
                with m.case(sel) as cs:
                    with cs.when(0):
                        y.set(a)
                    with cs.when(1):
                        y.set(b)
                    with cs.when(2):
                        y.set(c)
                    with cs.default():
                        y.set(d)
        v = emit_module(m.build())
        assert "case (sel)" in v
        assert "0: y = a;" in v
        assert "1: y = b;" in v
        assert "2: y = c;" in v
        assert "default: y = d;" in v
        assert "endcase" in v

    def test_shift_register(self):
        with Module("shift_reg") as m:
            clk = m.input("clk")
            d = m.input("d")
            q = m.output_reg("q", width=4)
            with m.always(posedge(clk)):
                q <<= cat(q[2:0], d)
        v = emit_module(m.build())
        assert "q <= {q[2:0], d};" in v

    def test_with_parameter(self):
        m = Module("param_mod")
        w = m.parameter("WIDTH", default=8)
        m.input("data", width=w)
        v = emit_module(m.build())
        assert "parameter WIDTH = 8" in v
        assert "WIDTH - 1" in v

    def test_expression_emission(self):
        """Test that complex expressions emit correctly."""
        m = Module("test")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        # Build: (a + b) & 0xFF
        result = (a + b) & 0xFF
        text = emit_expression(result._as_expr())
        assert "a + b" in text
        assert "& 255" in text


# ===================================================================
# Context manager pattern
# ===================================================================


class TestContextManager:
    """Test context manager usage patterns."""

    def test_with_module(self):
        with Module("test") as m:
            m.input("a")
        module = m.build()
        assert module.name == "test"

    def test_build_without_with(self):
        m = Module("test")
        m.input("a")
        module = m.build()
        assert module.name == "test"

    def test_build_with_unclosed_block_raises(self):
        m = Module("test")
        m._push_block()  # simulate unclosed block
        with pytest.raises(RuntimeError, match="unclosed"):
            m.build()

    def test_else_without_if_raises(self):
        m = Module("test")
        clk = m.input("clk")
        with m.always(posedge(clk)):
            with pytest.raises(RuntimeError, match="must immediately follow"):
                with m.else_():
                    pass

    def test_elif_without_if_raises(self):
        m = Module("test")
        clk = m.input("clk")
        with m.always(posedge(clk)):
            with pytest.raises(RuntimeError, match="must immediately follow"):
                with m.elif_(1):
                    pass


# ===================================================================
# Generate via Python loops
# ===================================================================


class TestPythonGenerate:
    """Test generating hardware via Python loops."""

    def test_unrolled_assigns(self):
        """Python for-loop creates multiple assigns."""
        m = Module("test")
        inputs = [m.input(f"in_{i}") for i in range(4)]
        outputs = [m.output(f"out_{i}") for i in range(4)]
        for i in range(4):
            m.assign(outputs[i], ~inputs[i])
        module = m.build()
        assert len(module.ports) == 8
        assert len(module.continuous_assigns) == 4

    def test_parameterized_adder_tree(self):
        """Use Python to build a parameterized structure."""
        m = Module("adder_tree")
        a = [m.input(f"a{i}", width=8) for i in range(4)]
        s01 = m.wire("s01", width=9)
        s23 = m.wire("s23", width=9)
        total = m.output("total", width=10)
        m.assign(s01, a[0] + a[1])
        m.assign(s23, a[2] + a[3])
        m.assign(total, s01 + s23)
        module = m.build()
        assert len(module.continuous_assigns) == 3


# ===================================================================
# Simulation integration
# ===================================================================


class TestSimulationIntegration:
    """Test that DSL-built modules can be simulated."""

    def test_simulate_adder(self):
        """Build an adder via DSL and simulate it."""
        m = Module("adder")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        s = m.output("sum", width=9)
        m.assign(s, a + b)
        module = m.build()
        sim = Simulator(module)
        sim.drive("a", 10)
        sim.drive("b", 20)
        sim.run(lambda s: None, max_time=100)
        assert sim.read("sum") == 30

    def test_simulate_counter(self):
        """Build a counter via DSL and simulate it."""
        with Module("counter") as m:
            clk = m.input("clk")
            rst = m.input("rst")
            count = m.output_reg("count", width=8)
            with m.always(posedge(clk)):
                with m.if_(rst):
                    count <<= 0
                with m.else_():
                    count <<= count + 1
        module = m.build()
        sim = Simulator(module)
        sim.fork(Clock(sim.signal("clk"), period=10))

        def test(s):
            s.drive("rst", 1)

        sim.run(test, max_time=5)
        # After reset, count should be 0
        assert sim.read("count") == 0

    def test_simulate_mux(self):
        """Build a mux via DSL and simulate it."""
        m = Module("mux2")
        sel = m.input("sel")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        y = m.output_reg("y", width=8)
        with m.always():
            with m.case(sel) as c:
                with c.when(0):
                    y.set(a)
                with c.when(1):
                    y.set(b)
        module = m.build()
        sim = Simulator(module)
        sim.drive("a", 42)
        sim.drive("b", 99)
        sim.drive("sel", 0)
        sim.run(lambda s: None, max_time=100)
        assert sim.read("y") == 42

        sim2 = Simulator(module)
        sim2.drive("a", 42)
        sim2.drive("b", 99)
        sim2.drive("sel", 1)
        sim2.run(lambda s: None, max_time=100)
        assert sim2.read("y") == 99


# ===================================================================
# Module instantiation
# ===================================================================


class TestInstantiation:
    """Test module instantiation via DSL."""

    def test_simple_instance(self):
        m = Module("top")
        clk = m.input("clk")
        rst = m.input("rst")
        cnt = m.wire("cnt", width=8)
        m.instance("counter", "u_cnt", ports={"clk": clk, "rst": rst, "count": cnt})
        module = m.build()
        assert len(module.instances) == 1
        inst = module.instances[0]
        assert inst.module_name == "counter"
        assert inst.instance_name == "u_cnt"
        assert len(inst.port_connections) == 3
        assert inst.port_connections[0].port_name == "clk"
        assert inst.port_connections[0].is_named is True

    def test_instance_with_parameters(self):
        m = Module("top")
        clk = m.input("clk")
        m.instance("counter", "u_cnt", ports={"clk": clk}, parameters={"WIDTH": 16})
        module = m.build()
        inst = module.instances[0]
        assert inst.has_parameter_override is True
        assert len(inst.parameter_bindings) == 1
        assert inst.parameter_bindings[0].name == "WIDTH"
        assert inst.parameter_bindings[0].value.value == 16

    def test_unconnected_port(self):
        m = Module("top")
        clk = m.input("clk")
        m.instance("counter", "u_cnt", ports={"clk": clk, "count": None})
        module = m.build()
        inst = module.instances[0]
        count_conn = next(c for c in inst.port_connections if c.port_name == "count")
        assert count_conn.expression is None

    def test_no_ports_no_params(self):
        m = Module("top")
        m.instance("osc", "u_osc")
        module = m.build()
        inst = module.instances[0]
        assert len(inst.port_connections) == 0
        assert len(inst.parameter_bindings) == 0

    def test_multiple_instances(self):
        m = Module("top")
        clk = m.input("clk")
        for i in range(4):
            w = m.wire(f"cnt_{i}", width=8)
            m.instance("counter", f"u_cnt_{i}", ports={"clk": clk, "count": w})
        module = m.build()
        assert len(module.instances) == 4
        assert module.instances[2].instance_name == "u_cnt_2"

    def test_instance_emits_verilog(self):
        m = Module("top")
        clk = m.input("clk")
        rst = m.input("rst")
        cnt = m.wire("cnt", width=8)
        m.instance("counter", "u_cnt", ports={"clk": clk, "rst": rst, "count": cnt})
        v = emit_module(m.build())
        assert "counter u_cnt" in v
        assert ".clk(clk)" in v
        assert ".rst(rst)" in v
        assert ".count(cnt)" in v

    def test_instance_with_param_emits(self):
        m = Module("top")
        clk = m.input("clk")
        m.instance("counter", "u_cnt", ports={"clk": clk}, parameters={"WIDTH": 16})
        v = emit_module(m.build())
        assert "#(" in v
        assert ".WIDTH(16)" in v


# ===================================================================
# Comments
# ===================================================================


class TestComments:
    """Test comment attachment in DSL-generated Verilog."""

    def test_port_trailing_comment(self):
        """Port comments appear as trailing ``// text`` on the port line."""
        m = Module("test")
        m.input("clk").comment("System clock")
        m.output("q")
        v = emit_module(m.build())
        assert "// System clock" in v

    def test_wire_leading_comment(self):
        """Wire comments appear as ``// text`` on the line above."""
        m = Module("test")
        m.wire("w").comment("Internal bus")
        v = emit_module(m.build())
        lines = v.split("\n")
        for i, line in enumerate(lines):
            if "wire w" in line:
                assert i > 0 and "// Internal bus" in lines[i - 1]
                break
        else:
            raise AssertionError("wire w not found in output")

    def test_reg_leading_comment(self):
        """Reg comments appear as ``// text`` on the line above."""
        m = Module("test")
        m.reg("state", width=2).comment("FSM state")
        v = emit_module(m.build())
        lines = v.split("\n")
        for i, line in enumerate(lines):
            if "reg" in line and "state" in line:
                assert i > 0 and "// FSM state" in lines[i - 1]
                break
        else:
            raise AssertionError("reg state not found in output")

    def test_always_comment(self):
        """``comment=`` on always block appears above it."""
        m = Module("test")
        clk = m.input("clk")
        q = m.output_reg("q")
        with m.always(posedge(clk), comment="State register"):
            q <<= 0
        v = emit_module(m.build())
        assert "// State register" in v

    def test_initial_comment(self):
        """``comment=`` on initial block appears above it."""
        m = Module("test")
        q = m.reg("q")
        with m.initial(comment="Reset values"):
            q @= 0
        v = emit_module(m.build())
        assert "// Reset values" in v

    def test_assign_comment(self):
        """``comment=`` on assign appears above it."""
        m = Module("test")
        a = m.input("a")
        y = m.output("y")
        m.assign(y, a, comment="Pass-through")
        v = emit_module(m.build())
        assert "// Pass-through" in v

    def test_comment_chaining(self):
        """``signal.comment()`` returns self for chaining."""
        m = Module("test")
        clk = m.input("clk").comment("100 MHz")
        assert isinstance(clk, Signal)

    def test_multiple_comments(self):
        """Multiple ``.comment()`` calls add multiple lines."""
        m = Module("test")
        m.reg("count", width=8).comment("Counter register").comment("Wraps at 255")
        v = emit_module(m.build())
        assert "// Counter register" in v
        assert "// Wraps at 255" in v

    def test_output_reg_comment_on_port(self):
        """output_reg comment appears on the port, not duplicated on the reg."""
        m = Module("test")
        m.output_reg("q").comment("Output register")
        v = emit_module(m.build())
        assert v.count("// Output register") == 1


class TestStandaloneComments:
    """Test m.comment() standalone comments in DSL-generated Verilog."""

    def test_comment_before_assign(self):
        """m.comment() appears above the next assign."""
        m = Module("test")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        y = m.output("y", width=9)
        m.comment("Adder output")
        m.assign(y, a + b)
        v = emit_module(m.build())
        lines = v.split("\n")
        for i, line in enumerate(lines):
            if "assign" in line and "y" in line:
                assert i > 0 and "// Adder output" in lines[i - 1]
                break
        else:
            raise AssertionError("assign y not found in output")

    def test_comment_before_wire(self):
        """m.comment() appears above the next wire declaration."""
        m = Module("test")
        m.comment("Internal bus")
        m.wire("bus", width=8)
        v = emit_module(m.build())
        lines = v.split("\n")
        for i, line in enumerate(lines):
            if "wire" in line and "bus" in line:
                assert i > 0 and "// Internal bus" in lines[i - 1]
                break
        else:
            raise AssertionError("wire bus not found in output")

    def test_comment_before_reg(self):
        """m.comment() appears above the next reg declaration."""
        m = Module("test")
        m.comment("State variable")
        m.reg("state", width=2)
        v = emit_module(m.build())
        lines = v.split("\n")
        for i, line in enumerate(lines):
            if "reg" in line and "state" in line:
                assert i > 0 and "// State variable" in lines[i - 1]
                break
        else:
            raise AssertionError("reg state not found in output")

    def test_comment_before_always(self):
        """m.comment() appears above the next always block."""
        m = Module("test")
        clk = m.input("clk")
        q = m.output_reg("q")
        m.comment("Main state machine")
        with m.always(posedge(clk)):
            q <<= 0
        v = emit_module(m.build())
        assert "// Main state machine" in v

    def test_comment_before_initial(self):
        """m.comment() appears above the next initial block."""
        m = Module("test")
        q = m.reg("q")
        m.comment("Initialization")
        with m.initial():
            q @= 0
        v = emit_module(m.build())
        assert "// Initialization" in v

    def test_comment_before_instance(self):
        """m.comment() appears above the next instance."""
        m = Module("test")
        clk = m.input("clk")
        m.comment("Counter instance")
        m.instance("counter", "u_cnt", ports={"clk": clk})
        v = emit_module(m.build())
        lines = v.split("\n")
        for i, line in enumerate(lines):
            if "counter" in line and "u_cnt" in line:
                assert i > 0 and "// Counter instance" in lines[i - 1]
                break
        else:
            raise AssertionError("counter u_cnt not found in output")

    def test_comment_before_port(self):
        """m.comment() before a port appears as leading comment in port list."""
        m = Module("test")
        m.comment("Clock input")
        m.input("clk")
        m.output("q")
        v = emit_module(m.build())
        assert "// Clock input" in v

    def test_multiple_section_comments(self):
        """Multiple m.comment() calls create section headers in the output."""
        m = Module("test")
        a = m.input("a", width=8)
        b = m.input("b", width=8)
        s = m.wire("s", width=9)
        y = m.output("y", width=9)
        m.comment("Stage 1")
        m.assign(s, a + b)
        m.comment("Stage 2")
        m.assign(y, s)
        v = emit_module(m.build())
        assert "// Stage 1" in v
        assert "// Stage 2" in v
        # Stage 1 should come before Stage 2
        assert v.index("// Stage 1") < v.index("// Stage 2")

    def test_comment_with_explicit_comment_kwarg(self):
        """m.comment() and comment= kwarg both appear, m.comment() first."""
        m = Module("test")
        a = m.input("a")
        y = m.output("y")
        m.comment("Section: outputs")
        m.assign(y, a, comment="Pass-through")
        v = emit_module(m.build())
        assert "// Section: outputs" in v
        assert "// Pass-through" in v
        assert v.index("// Section: outputs") < v.index("// Pass-through")

    def test_comment_consumed_by_next_item(self):
        """A pending comment is consumed by the next item, not duplicated."""
        m = Module("test")
        m.comment("Only once")
        m.wire("w1")
        m.wire("w2")
        v = emit_module(m.build())
        assert v.count("// Only once") == 1


class TestPortDefaultValues:
    """Test port default/initial values."""

    def test_output_reg_init(self):
        """output_reg with init= emits a default value."""
        m = Module("test")
        m.input("clk")
        m.output_reg("q", width=8, init=0)
        v = emit_module(m.build())
        assert "output reg [7:0] q = 0" in v

    def test_output_reg_init_nonzero(self):
        """output_reg with init= emits a nonzero default value."""
        m = Module("test")
        m.output_reg("q", width=8, init=255)
        v = emit_module(m.build())
        assert "output reg [7:0] q = 255" in v

    def test_output_init(self):
        """output with init= emits a default value."""
        m = Module("test")
        m.output("y", init=1)
        v = emit_module(m.build())
        assert "output y = 1" in v

    def test_input_no_init_by_default(self):
        """input without init= does not emit a default value."""
        m = Module("test")
        m.input("clk")
        v = emit_module(m.build())
        assert "= " not in v


class TestMemoryArrays:
    """Test memory array support (depth= parameter)."""

    def test_reg_memory(self):
        """reg with depth= emits memory dimensions."""
        m = Module("test")
        m.reg("mem", width=8, depth=256)
        v = emit_module(m.build())
        assert "reg [7:0] mem [0:255];" in v

    def test_wire_memory(self):
        """wire with depth= emits memory dimensions."""
        m = Module("test")
        m.wire("bus_array", width=16, depth=4)
        v = emit_module(m.build())
        assert "wire [15:0] bus_array [0:3];" in v

    def test_reg_depth_1(self):
        """reg with depth=1 emits [0:0] dimension."""
        m = Module("test")
        m.reg("single", width=8, depth=1)
        v = emit_module(m.build())
        assert "reg [7:0] single [0:0];" in v

    def test_reg_no_depth(self):
        """reg without depth= has no dimensions."""
        m = Module("test")
        m.reg("plain", width=8)
        v = emit_module(m.build())
        assert "[0:" not in v

    def test_memory_in_always(self):
        """Memory arrays can be indexed and assigned in always blocks."""
        m = Module("test")
        clk = m.input("clk")
        addr = m.input("addr", width=8)
        data = m.input("data", width=8)
        mem = m.reg("mem", width=8, depth=256)
        with m.always(posedge(clk)):
            mem[addr] <<= data
        v = emit_module(m.build())
        assert "reg [7:0] mem [0:255];" in v
        assert "mem[addr] <= data;" in v


class TestNetInitialValues:
    """Test net initial value support."""

    def test_wire_init(self):
        """wire with init= emits an initial value."""
        m = Module("test")
        m.wire("w", width=8, init=0)
        v = emit_module(m.build())
        assert "wire [7:0] w = 0;" in v

    def test_wire_init_nonzero(self):
        """wire with init= emits a nonzero initial value."""
        m = Module("test")
        m.wire("w", init=1)
        v = emit_module(m.build())
        assert "wire w = 1;" in v

    def test_reg_init(self):
        """reg with init= emits an initial value."""
        m = Module("test")
        m.reg("r", width=4, init=0)
        v = emit_module(m.build())
        assert "reg [3:0] r = 0;" in v

    def test_no_init_by_default(self):
        """wire/reg without init= has no initial value."""
        m = Module("test")
        m.wire("w")
        m.reg("r")
        v = emit_module(m.build())
        assert "= " not in v


class TestPartSelect:
    """Test part-select expressions (+: and -:)."""

    def test_ascending_part_select(self):
        """signal.part_select(base, width) emits [base +: width]."""
        m = Module("test")
        data = m.input("data", width=32)
        y = m.output("y", width=8)
        idx = m.input("idx", width=5)
        m.assign(y, data.part_select(idx, 8))
        v = emit_module(m.build())
        assert "data[idx +: 8]" in v

    def test_descending_part_select(self):
        """signal.part_select_down(base, width) emits [base -: width]."""
        m = Module("test")
        data = m.input("data", width=32)
        y = m.output("y", width=8)
        idx = m.input("idx", width=5)
        m.assign(y, data.part_select_down(idx, 8))
        v = emit_module(m.build())
        assert "data[idx -: 8]" in v

    def test_part_select_in_always(self):
        """Part selects work as LHS in always blocks via assign_nb."""
        m = Module("test")
        clk = m.input("clk")
        data = m.reg("data", width=32)
        byte_val = m.input("byte_val", width=8)
        with m.always(posedge(clk)):
            m.assign_nb(data.part_select(0, 8), byte_val)
        v = emit_module(m.build())
        assert "data[0 +: 8] <= byte_val;" in v

    def test_part_select_expression(self):
        """Part select on an expression (via Expr)."""
        m = Module("test")
        data = m.input("data", width=32)
        y = m.output("y", width=8)
        m.assign(y, data.part_select(8, 8))
        v = emit_module(m.build())
        assert "data[8 +: 8]" in v


class TestBlockComments:
    """Test block comment support (/* ... */)."""

    def test_block_comment(self):
        """m.comment(text, block=True) emits /* text */."""
        m = Module("test")
        a = m.input("a")
        y = m.output("y")
        m.comment("License header", block=True)
        m.assign(y, a)
        v = emit_module(m.build())
        assert "/* License header */" in v

    def test_line_comment_default(self):
        """m.comment(text) emits // text by default."""
        m = Module("test")
        a = m.input("a")
        y = m.output("y")
        m.comment("Line comment")
        m.assign(y, a)
        v = emit_module(m.build())
        assert "// Line comment" in v
        assert "/*" not in v

    def test_mixed_comments(self):
        """Block and line comments can be mixed."""
        m = Module("test")
        a = m.input("a")
        y = m.output("y")
        m.comment("Block comment here", block=True)
        m.comment("Line comment here")
        m.assign(y, a)
        v = emit_module(m.build())
        assert "/* Block comment here */" in v
        assert "// Line comment here" in v
        # Block comment comes before line comment
        assert v.index("/* Block comment here */") < v.index("// Line comment here")


class TestSynthesisAttributes:
    """Test synthesis attribute support (* attr *)."""

    def test_reg_attribute(self):
        """signal.attr() emits (* attr *) above the reg."""
        m = Module("test")
        m.reg("state", width=3).attr("fsm_encoding", "one_hot")
        v = emit_module(m.build())
        assert '(* fsm_encoding = "one_hot" *)' in v
        # Attribute should appear before the reg declaration
        lines = v.split("\n")
        for i, line in enumerate(lines):
            if "reg" in line and "state" in line:
                assert i > 0 and "fsm_encoding" in lines[i - 1]
                break

    def test_wire_attribute(self):
        """signal.attr() emits (* attr *) above the wire."""
        m = Module("test")
        m.wire("clk_buf").attr("keep", "true")
        v = emit_module(m.build())
        assert '(* keep = "true" *)' in v

    def test_attribute_no_value(self):
        """signal.attr(name) with no value emits (* name *)."""
        m = Module("test")
        m.wire("w").attr("dont_touch")
        v = emit_module(m.build())
        assert "(* dont_touch *)" in v

    def test_multiple_attributes(self):
        """Multiple .attr() calls combine into one (* ... *) line."""
        m = Module("test")
        m.reg("state", width=3).attr("fsm_encoding", "one_hot").attr("full_case")
        v = emit_module(m.build())
        assert '(* fsm_encoding = "one_hot", full_case *)' in v

    def test_attribute_chaining(self):
        """.attr() returns self for chaining with .comment()."""
        m = Module("test")
        sig = m.reg("state", width=3).attr("fsm_encoding", "one_hot").comment("FSM state")
        assert isinstance(sig, Signal)

    def test_port_attribute(self):
        """Attributes on ports appear in the port list."""
        m = Module("test")
        m.input("clk")
        m.input("data", width=8).attr("io_standard", "LVCMOS33")
        v = emit_module(m.build())
        assert '(* io_standard = "LVCMOS33" *)' in v

    def test_attribute_with_comment(self):
        """Attributes and comments can coexist on the same signal."""
        m = Module("test")
        m.reg("state", width=3).attr("fsm_encoding", "one_hot").comment("FSM state")
        v = emit_module(m.build())
        assert '(* fsm_encoding = "one_hot" *)' in v
        assert "// FSM state" in v


# ===================================================================
# Interface / bus abstraction
# ===================================================================


def _make_axi_stream(data_width: int = 8) -> Interface:
    """Helper: create a standard AXI-Stream interface template."""
    return (
        Interface("axi_stream")
        .signal("tvalid", src="master")
        .signal("tready", src="slave")
        .signal("tdata", width=data_width, src="master")
        .signal("tlast", src="master")
    )


class TestInterface:
    """Tests for the Interface template class."""

    def test_basic_creation(self):
        """Interface stores signals with name, width, src, signed."""
        intf = _make_axi_stream()
        assert intf.name == "axi_stream"
        assert len(intf._signals) == 4
        assert intf._signals[0].name == "tvalid"
        assert intf._signals[2].width == 8
        assert intf._signals[1].src == "slave"

    def test_chaining_returns_self(self):
        """signal() returns self for chaining."""
        intf = Interface("test")
        result = intf.signal("a", src="master")
        assert result is intf

    def test_invalid_src_raises(self):
        """Invalid src value raises ValueError."""
        intf = Interface("test")
        with pytest.raises(ValueError, match="src must be"):
            intf.signal("a", src="monitor")

    def test_signed_signal(self):
        """Signed flag is stored on interface signals."""
        intf = Interface("test").signal("data", width=16, src="master", signed=True)
        assert intf._signals[0].signed is True

    def test_repr(self):
        """repr shows name and signal names."""
        intf = _make_axi_stream()
        r = repr(intf)
        assert "axi_stream" in r
        assert "tvalid" in r


class TestInterfaceBinding:
    """Tests for Module.interface() — binding an interface to a module as ports."""

    def test_master_role_creates_correct_ports(self):
        """Master role: src=master → output, src=slave → input."""
        intf = _make_axi_stream()
        m = Module("producer")
        m.input("clk")
        m_axis = m.interface("m_axis", intf, role="master")
        mod = m.build()
        v = emit_module(mod)
        assert "output m_axis_tvalid" in v
        assert "input m_axis_tready" in v
        assert "output [7:0] m_axis_tdata" in v
        assert "output m_axis_tlast" in v

    def test_slave_role_flips_directions(self):
        """Slave role: src=master → input, src=slave → output."""
        intf = _make_axi_stream()
        m = Module("consumer")
        m.input("clk")
        s_axis = m.interface("s_axis", intf, role="slave")
        mod = m.build()
        v = emit_module(mod)
        assert "input s_axis_tvalid" in v
        assert "output s_axis_tready" in v
        assert "input [7:0] s_axis_tdata" in v
        assert "input s_axis_tlast" in v

    def test_reg_flag_creates_output_reg(self):
        """reg=True makes output signals use output_reg."""
        intf = _make_axi_stream()
        m = Module("producer")
        m.input("clk")
        m.interface("m_axis", intf, role="master", reg=True)
        v = emit_module(m.build())
        assert "output reg m_axis_tvalid" in v
        assert "output reg [7:0] m_axis_tdata" in v
        assert "output reg m_axis_tlast" in v
        # Inputs are never reg
        assert "input m_axis_tready" in v

    def test_attribute_access(self):
        """BoundInterface provides attribute access to individual signals."""
        intf = _make_axi_stream()
        m = Module("test")
        m_axis = m.interface("m_axis", intf, role="master")
        assert isinstance(m_axis.tvalid, Signal)
        assert isinstance(m_axis.tdata, Signal)
        assert m_axis.tvalid._name == "m_axis_tvalid"
        assert m_axis.tdata._name == "m_axis_tdata"

    def test_attribute_access_bad_name_raises(self):
        """Accessing a non-existent signal raises AttributeError."""
        intf = _make_axi_stream()
        m = Module("test")
        m_axis = m.interface("m_axis", intf, role="master")
        with pytest.raises(AttributeError, match="no signal 'bogus'"):
            _ = m_axis.bogus

    def test_signals_usable_in_always_block(self):
        """Interface signals can be used in always blocks."""
        intf = _make_axi_stream()
        m = Module("producer")
        clk = m.input("clk")
        m_axis = m.interface("m_axis", intf, role="master", reg=True)
        count = m.reg("count", width=8)
        with m.always(posedge(clk)):
            m_axis.tvalid <<= 1
            m_axis.tdata <<= count
            m_axis.tlast <<= count == 255
        v = emit_module(m.build())
        assert "m_axis_tvalid <= 1" in v
        assert "m_axis_tdata <= count" in v

    def test_invalid_role_raises(self):
        """Invalid role raises ValueError."""
        intf = _make_axi_stream()
        m = Module("test")
        with pytest.raises(ValueError, match="role must be"):
            m.interface("m_axis", intf, role="monitor")

    def test_parameterized_width(self):
        """Interface width parameter affects generated ports."""
        intf32 = _make_axi_stream(data_width=32)
        m = Module("wide_producer")
        m.input("clk")
        m.interface("m_axis", intf32, role="master")
        v = emit_module(m.build())
        assert "output [31:0] m_axis_tdata" in v


class TestInterfacePortMap:
    """Tests for BoundInterface.port_map() — instance connection helper."""

    def test_port_map_default_prefix(self):
        """port_map() uses the bound prefix by default."""
        intf = _make_axi_stream()
        m = Module("test")
        m_axis = m.interface("m_axis", intf, role="master")
        pm = m_axis.port_map()
        assert "m_axis_tvalid" in pm
        assert "m_axis_tready" in pm
        assert "m_axis_tdata" in pm
        assert "m_axis_tlast" in pm
        assert len(pm) == 4
        assert all(isinstance(v, Signal) for v in pm.values())

    def test_port_map_custom_prefix(self):
        """port_map(prefix) overrides the key prefix."""
        intf = _make_axi_stream()
        m = Module("test")
        m_axis = m.interface("m_axis", intf, role="master")
        pm = m_axis.port_map("s_axis")
        assert "s_axis_tvalid" in pm
        assert "s_axis_tdata" in pm
        assert "m_axis_tvalid" not in pm

    def test_port_map_in_instance(self):
        """port_map() works with ** expansion in Module.instance()."""
        intf = _make_axi_stream()
        # Build a top module that wires producer to consumer via internal bus
        top = Module("top")
        clk = top.input("clk")
        axis = top.wire_interface("axis", intf)
        top.instance(
            "producer",
            "i_prod",
            ports={
                "clk": clk,
                **axis.port_map("m_axis"),
            },
        )
        top.instance(
            "consumer",
            "i_cons",
            ports={
                "clk": clk,
                **axis.port_map("s_axis"),
            },
        )
        v = emit_module(top.build())
        assert "producer i_prod" in v
        assert ".m_axis_tvalid(axis_tvalid)" in v
        assert ".m_axis_tdata(axis_tdata)" in v
        assert "consumer i_cons" in v
        assert ".s_axis_tvalid(axis_tvalid)" in v
        assert ".s_axis_tdata(axis_tdata)" in v


class TestWireInterface:
    """Tests for Module.wire_interface() — internal bus wires."""

    def test_creates_wires(self):
        """wire_interface creates wire declarations, not ports."""
        intf = _make_axi_stream()
        m = Module("top")
        m.input("clk")
        axis = m.wire_interface("axis", intf)
        v = emit_module(m.build())
        assert "wire axis_tvalid" in v
        assert "wire axis_tready" in v
        assert "wire [7:0] axis_tdata" in v
        assert "wire axis_tlast" in v
        # Should NOT appear in port list
        assert "input axis_tvalid" not in v
        assert "output axis_tvalid" not in v

    def test_attribute_access(self):
        """wire_interface signals are accessible via attributes."""
        intf = _make_axi_stream()
        m = Module("top")
        axis = m.wire_interface("axis", intf)
        assert axis.tvalid._name == "axis_tvalid"
        assert axis.tdata._name == "axis_tdata"

    def test_assign_to_wire_interface_signal(self):
        """Wire interface signals can be used in continuous assignments."""
        intf = _make_axi_stream()
        m = Module("top")
        clk = m.input("clk")
        axis = m.wire_interface("axis", intf)
        m.assign(axis.tvalid, 1)
        v = emit_module(m.build())
        assert "assign axis_tvalid = 1" in v


class TestInterfaceEndToEnd:
    """End-to-end tests: full producer-consumer wiring via interfaces."""

    def test_full_axi_stream_system(self):
        """Complete AXI-Stream producer/consumer/top with interface wiring."""
        axi_s = _make_axi_stream(data_width=8)

        # --- Producer ---
        prod = Module("axi_producer")
        clk_p = prod.input("clk")
        rst_p = prod.input("rst")
        m_axis = prod.interface("m_axis", axi_s, role="master", reg=True)
        cnt = prod.reg("cnt", width=8)
        with prod.always(posedge(clk_p)):
            with prod.if_(rst_p):
                m_axis.tvalid <<= 0
                m_axis.tdata <<= 0
                m_axis.tlast <<= 0
                cnt <<= 0
            with prod.else_():
                m_axis.tvalid <<= 1
                m_axis.tdata <<= cnt
                m_axis.tlast <<= cnt == 255
                cnt <<= cnt + 1
        v_prod = emit_module(prod.build())
        assert "module axi_producer" in v_prod
        assert "output reg m_axis_tvalid" in v_prod
        assert "output reg [7:0] m_axis_tdata" in v_prod
        assert "input m_axis_tready" in v_prod

        # --- Consumer ---
        cons = Module("axi_consumer")
        clk_c = cons.input("clk")
        s_axis = cons.interface("s_axis", axi_s, role="slave")
        cons.assign(s_axis.tready, 1)
        v_cons = emit_module(cons.build())
        assert "module axi_consumer" in v_cons
        assert "input s_axis_tvalid" in v_cons
        assert "output s_axis_tready" in v_cons
        assert "input [7:0] s_axis_tdata" in v_cons
        assert "assign s_axis_tready = 1" in v_cons

        # --- Top ---
        top = Module("top")
        clk = top.input("clk")
        rst = top.input("rst")
        axis = top.wire_interface("axis", axi_s)
        top.instance(
            "axi_producer",
            "i_prod",
            ports={
                "clk": clk,
                "rst": rst,
                **axis.port_map("m_axis"),
            },
        )
        top.instance(
            "axi_consumer",
            "i_cons",
            ports={
                "clk": clk,
                **axis.port_map("s_axis"),
            },
        )
        v_top = emit_module(top.build())
        assert "wire axis_tvalid" in v_top
        assert "wire [7:0] axis_tdata" in v_top
        assert ".m_axis_tvalid(axis_tvalid)" in v_top
        assert ".s_axis_tvalid(axis_tvalid)" in v_top
        assert ".m_axis_tdata(axis_tdata)" in v_top
        assert ".s_axis_tdata(axis_tdata)" in v_top

    def test_wishbone_interface(self):
        """Non-AXI interface works the same way (Wishbone bus)."""
        wb = (
            Interface("wishbone")
            .signal("cyc", src="master")
            .signal("stb", src="master")
            .signal("we", src="master")
            .signal("adr", width=32, src="master")
            .signal("dat_w", width=32, src="master")
            .signal("dat_r", width=32, src="slave")
            .signal("ack", src="slave")
        )
        m = Module("wb_master")
        m.input("clk")
        wbm = m.interface("wbm", wb, role="master")
        v = emit_module(m.build())
        assert "output wbm_cyc" in v
        assert "output wbm_stb" in v
        assert "output wbm_we" in v
        assert "output [31:0] wbm_adr" in v
        assert "output [31:0] wbm_dat_w" in v
        assert "input [31:0] wbm_dat_r" in v
        assert "input wbm_ack" in v

    def test_signed_interface_signals(self):
        """Signed signals in interface produce signed ports."""
        intf = Interface("signed_bus").signal("data", width=16, src="master", signed=True).signal("ack", src="slave")
        m = Module("test")
        m.input("clk")
        bus = m.interface("bus", intf, role="master")
        v = emit_module(m.build())
        assert "output signed [15:0] bus_data" in v

    def test_interface_with_comments(self):
        """Comments can be attached to interface signals."""
        intf = _make_axi_stream()
        m = Module("test")
        m.input("clk")
        m_axis = m.interface("m_axis", intf, role="master")
        m_axis.tvalid.comment("Data valid")
        m_axis.tdata.comment("Payload")
        v = emit_module(m.build())
        assert "// Data valid" in v
        assert "// Payload" in v

    def test_interface_with_attributes(self):
        """Synthesis attributes can be attached to interface signals."""
        intf = _make_axi_stream()
        m = Module("test")
        m.input("clk")
        m_axis = m.interface("m_axis", intf, role="master")
        m_axis.tdata.attr("mark_debug", "true")
        v = emit_module(m.build())
        assert '(* mark_debug = "true" *)' in v

    def test_multiple_interfaces_on_module(self):
        """A module can have multiple interface bindings."""
        intf = _make_axi_stream()
        m = Module("bridge")
        m.input("clk")
        s_axis = m.interface("s_axis", intf, role="slave")
        m_axis = m.interface("m_axis", intf, role="master")
        v = emit_module(m.build())
        # Slave side
        assert "input s_axis_tvalid" in v
        assert "output s_axis_tready" in v
        # Master side
        assert "output m_axis_tvalid" in v
        assert "input m_axis_tready" in v

    def test_factory_function_pattern(self):
        """Interface templates can be created via factory functions."""

        def axi_stream(data_width: int = 8) -> Interface:
            return (
                Interface("axi_stream")
                .signal("tvalid", src="master")
                .signal("tready", src="slave")
                .signal("tdata", width=data_width, src="master")
                .signal("tlast", src="master")
            )

        m = Module("test")
        m.input("clk")
        m.interface("m_axis", axi_stream(data_width=32), role="master")
        v = emit_module(m.build())
        assert "output [31:0] m_axis_tdata" in v


# ===================================================================
# String literals
# ===================================================================


class TestStringLiterals:
    """Test string literal support in the DSL."""

    def test_to_expr_node_string(self):
        """A Python str becomes a StringLiteral expression."""
        from veriforge.dsl.builder import _to_expr_node

        node = _to_expr_node("hello")
        assert isinstance(node, StringLiteral)
        assert node.value == "hello"

    def test_string_literal_emission(self):
        """StringLiteral emits double-quoted string."""
        assert emit_expression(StringLiteral("world")) == '"world"'

    def test_string_in_display(self):
        """String arguments to $display are emitted correctly."""
        m = Module("test")
        m.input("clk")
        with m.initial():
            m.display("Hello, World!")
        v = emit_module(m.build())
        assert '$display("Hello, World!")' in v

    def test_empty_string(self):
        """Empty strings work correctly."""
        from veriforge.dsl.builder import _to_expr_node

        node = _to_expr_node("")
        assert isinstance(node, StringLiteral)
        assert node.value == ""
        assert emit_expression(node) == '""'


# ===================================================================
# System tasks
# ===================================================================


class TestSystemTasks:
    """Test system task DSL methods ($display, $finish, etc.)."""

    def test_display_string(self):
        """$display with a string argument."""
        m = Module("test")
        with m.initial():
            m.display("test message")
        v = emit_module(m.build())
        assert '$display("test message")' in v

    def test_display_mixed_args(self):
        """$display with mixed string and signal arguments."""
        m = Module("test")
        count = m.output("count", width=8)
        with m.initial():
            m.display("count = %d", count)
        v = emit_module(m.build())
        assert '$display("count = %d", count)' in v

    def test_display_integer_arg(self):
        """$display with an integer argument."""
        m = Module("test")
        with m.initial():
            m.display("value is %d", 42)
        v = emit_module(m.build())
        assert '$display("value is %d", 42)' in v

    def test_write(self):
        """$write system task."""
        m = Module("test")
        with m.initial():
            m.write("no newline")
        v = emit_module(m.build())
        assert '$write("no newline")' in v

    def test_monitor(self):
        """$monitor system task."""
        m = Module("test")
        sig = m.output("sig")
        with m.initial():
            m.monitor("sig=%b", sig)
        v = emit_module(m.build())
        assert '$monitor("sig=%b", sig)' in v

    def test_finish(self):
        """$finish with no arguments."""
        m = Module("test")
        with m.initial():
            m.finish()
        v = emit_module(m.build())
        assert "$finish;" in v

    def test_stop(self):
        """$stop with no arguments."""
        m = Module("test")
        with m.initial():
            m.stop()
        v = emit_module(m.build())
        assert "$stop;" in v

    def test_readmemh(self):
        """$readmemh with filename and memory signal."""
        m = Module("test")
        mem = m.reg("mem", width=8, depth=256)
        with m.initial():
            m.readmemh("data.hex", mem)
        v = emit_module(m.build())
        assert '$readmemh("data.hex", mem)' in v

    def test_readmemb(self):
        """$readmemb with filename and memory signal."""
        m = Module("test")
        mem = m.reg("mem", width=8, depth=256)
        with m.initial():
            m.readmemb("data.bin", mem)
        v = emit_module(m.build())
        assert '$readmemb("data.bin", mem)' in v

    def test_system_task_outside_block_raises(self):
        """System tasks must be inside always/initial blocks."""
        m = Module("test")
        with pytest.raises(RuntimeError, match="must be inside"):
            m.display("oops")

    def test_finish_outside_block_raises(self):
        """$finish outside block raises RuntimeError."""
        m = Module("test")
        with pytest.raises(RuntimeError, match="must be inside"):
            m.finish()

    def test_multiple_system_tasks_in_block(self):
        """Multiple system tasks in one block."""
        m = Module("test")
        sig = m.output("sig", width=4)
        with m.initial():
            m.display("start")
            m.display("sig = %b", sig)
            m.finish()
        v = emit_module(m.build())
        assert '$display("start")' in v
        assert '$display("sig = %b", sig)' in v
        assert "$finish;" in v

    def test_display_no_args(self):
        """$display with no arguments prints a newline."""
        m = Module("test")
        with m.initial():
            m.display()
        v = emit_module(m.build())
        assert "$display;" in v

    def test_system_task_model_structure(self):
        """System task creates correct model nodes."""
        m = Module("test")
        with m.initial():
            m.display("hi", 42)
        mod = m.build()
        initial_block = mod.initial_blocks[0]
        stmt = initial_block.body
        # May be wrapped in a SeqBlock
        if isinstance(stmt, SeqBlock):
            stmt = stmt.statements[0]
        assert isinstance(stmt, SystemTaskCall)
        assert stmt.task_name == "$display"
        assert len(stmt.arguments) == 2
        assert isinstance(stmt.arguments[0], StringLiteral)
        assert stmt.arguments[0].value == "hi"
        assert isinstance(stmt.arguments[1], Literal)
        assert stmt.arguments[1].value == 42


# ===================================================================
# Delay control
# ===================================================================


class TestDelayControl:
    """Test delay control DSL methods (#delay)."""

    def test_standalone_delay(self):
        """Standalone m.delay(10) emits bare #10."""
        m = Module("test")
        with m.initial():
            m.delay(10)
        v = emit_module(m.build())
        assert "#10" in v

    def test_delay_with_body(self):
        """with m.delay(100): wraps body statements."""
        m = Module("test")
        sig = m.output_reg("sig")
        with m.initial():
            with m.delay(100):
                sig @= 1
        v = emit_module(m.build())
        assert "#100" in v
        assert "sig" in v

    def test_delay_model_standalone(self):
        """Standalone delay creates DelayControl with no body."""
        m = Module("test")
        with m.initial():
            m.delay(5)
        mod = m.build()
        stmt = mod.initial_blocks[0].body
        if isinstance(stmt, SeqBlock):
            stmt = stmt.statements[0]
        assert isinstance(stmt, DelayControl)
        assert stmt.body is None
        assert isinstance(stmt.delay, Literal)
        assert stmt.delay.value == 5

    def test_delay_model_with_body(self):
        """Delay with body creates DelayControl wrapping a statement."""
        m = Module("test")
        sig = m.output_reg("sig")
        with m.initial():
            with m.delay(50):
                sig @= 1
        mod = m.build()
        stmt = mod.initial_blocks[0].body
        if isinstance(stmt, SeqBlock):
            stmt = stmt.statements[0]
        assert isinstance(stmt, DelayControl)
        assert stmt.body is not None

    def test_delay_outside_block_raises(self):
        """m.delay() outside always/initial raises RuntimeError."""
        m = Module("test")
        with pytest.raises(RuntimeError, match="must be inside"):
            m.delay(10)

    def test_multiple_delays(self):
        """Multiple delays in sequence."""
        m = Module("test")
        sig = m.output_reg("sig")
        with m.initial():
            sig @= 0
            m.delay(10)
            sig @= 1
            m.delay(20)
            sig @= 0
        v = emit_module(m.build())
        assert "#10" in v
        assert "#20" in v

    def test_delay_with_signal_value(self):
        """Delay value can be a signal reference."""
        m = Module("test")
        period = m.input("period", width=8)
        with m.initial():
            m.delay(period)
        v = emit_module(m.build())
        assert "#period" in v

    def test_delay_and_system_task(self):
        """Delay followed by a system task in a testbench pattern."""
        m = Module("test")
        with m.initial():
            m.delay(100)
            m.display("done")
            m.finish()
        v = emit_module(m.build())
        assert "#100" in v
        assert '$display("done")' in v
        assert "$finish;" in v


# ===================================================================
# Event control
# ===================================================================


class TestEventControl:
    """Test event control DSL methods (@(posedge/negedge))."""

    def test_wait_posedge(self):
        """m.wait_posedge(clk) emits @(posedge clk)."""
        m = Module("test")
        clk = m.input("clk")
        with m.initial():
            m.wait_posedge(clk)
        v = emit_module(m.build())
        assert "@(posedge clk)" in v

    def test_wait_negedge(self):
        """m.wait_negedge(rst) emits @(negedge rst)."""
        m = Module("test")
        rst = m.input("rst")
        with m.initial():
            m.wait_negedge(rst)
        v = emit_module(m.build())
        assert "@(negedge rst)" in v

    def test_wait_posedge_outside_block_raises(self):
        """wait_posedge outside block raises RuntimeError."""
        m = Module("test")
        clk = m.input("clk")
        with pytest.raises(RuntimeError, match="must be inside"):
            m.wait_posedge(clk)

    def test_wait_negedge_outside_block_raises(self):
        """wait_negedge outside block raises RuntimeError."""
        m = Module("test")
        rst = m.input("rst")
        with pytest.raises(RuntimeError, match="must be inside"):
            m.wait_negedge(rst)

    def test_wait_event_standalone(self):
        """Standalone wait_event emits bare @(posedge clk)."""
        m = Module("test")
        clk = m.input("clk")
        with m.initial():
            m.wait_event(posedge(clk))
        v = emit_module(m.build())
        assert "@(posedge clk)" in v

    def test_wait_event_with_body(self):
        """with m.wait_event(posedge(clk)): wraps body."""
        m = Module("test")
        clk = m.input("clk")
        sig = m.output_reg("sig")
        with m.initial():
            with m.wait_event(posedge(clk)):
                sig @= 1
        v = emit_module(m.build())
        assert "@(posedge clk)" in v

    def test_wait_event_model_standalone(self):
        """Standalone wait_event creates EventControl with no body."""
        m = Module("test")
        clk = m.input("clk")
        with m.initial():
            m.wait_event(posedge(clk))
        mod = m.build()
        stmt = mod.initial_blocks[0].body
        if isinstance(stmt, SeqBlock):
            stmt = stmt.statements[0]
        assert isinstance(stmt, EventControl)
        assert stmt.body is None
        assert len(stmt.events) == 1
        assert stmt.events[0].edge == "posedge"

    def test_wait_event_model_with_body(self):
        """wait_event with body creates EventControl wrapping statement."""
        m = Module("test")
        clk = m.input("clk")
        sig = m.output_reg("sig")
        with m.initial():
            with m.wait_event(posedge(clk)):
                sig @= 1
        mod = m.build()
        stmt = mod.initial_blocks[0].body
        if isinstance(stmt, SeqBlock):
            stmt = stmt.statements[0]
        assert isinstance(stmt, EventControl)
        assert stmt.body is not None

    def test_wait_event_outside_block_raises(self):
        """wait_event outside block raises RuntimeError."""
        m = Module("test")
        clk = m.input("clk")
        with pytest.raises(RuntimeError, match="must be inside"):
            m.wait_event(posedge(clk))

    def test_wait_event_multiple_edges(self):
        """wait_event with multiple sensitivity edges."""
        m = Module("test")
        clk = m.input("clk")
        rst = m.input("rst")
        with m.initial():
            m.wait_event(posedge(clk), negedge(rst))
        v = emit_module(m.build())
        assert "@(posedge clk" in v
        assert "negedge rst" in v

    def test_posedge_negedge_in_sequence(self):
        """Posedge and negedge waits in sequence."""
        m = Module("test")
        clk = m.input("clk")
        sig = m.output_reg("sig")
        with m.initial():
            m.wait_posedge(clk)
            sig @= 1
            m.wait_posedge(clk)
            sig @= 0
        v = emit_module(m.build())
        assert v.count("@(posedge clk)") == 2

    def test_testbench_pattern(self):
        """Full testbench pattern: initial block with timing control."""
        m = Module("tb")
        clk = m.input("clk")
        data = m.output_reg("data", width=8)
        with m.initial():
            data @= 0
            m.delay(10)
            data @= 0xFF
            m.wait_posedge(clk)
            m.display("data = %h", data)
            m.delay(100)
            m.finish()
        v = emit_module(m.build())
        assert "data = 8'b0" in v or "data <= 8'b0" in v or "data" in v
        assert "#10" in v
        assert "@(posedge clk)" in v
        assert '$display("data = %h", data)' in v
        assert "#100" in v
        assert "$finish;" in v


# ===================================================================
# Error quality checks (M1–M8)
# ===================================================================


class TestDuplicateSignal:
    """M1: Declaring a signal with a name that already exists raises ValueError."""

    def test_duplicate_input(self):
        m = Module("test")
        m.input("clk")
        with pytest.raises(ValueError, match="already declared"):
            m.input("clk")

    def test_duplicate_output(self):
        m = Module("test")
        m.output("data")
        with pytest.raises(ValueError, match="already declared"):
            m.output("data")

    def test_duplicate_wire(self):
        m = Module("test")
        m.wire("w")
        with pytest.raises(ValueError, match="already declared"):
            m.wire("w")

    def test_duplicate_reg(self):
        m = Module("test")
        m.reg("r")
        with pytest.raises(ValueError, match="already declared"):
            m.reg("r")

    def test_duplicate_cross_kind(self):
        """Input and wire with the same name should collide."""
        m = Module("test")
        m.input("sig")
        with pytest.raises(ValueError, match="already declared"):
            m.wire("sig")

    def test_duplicate_parameter(self):
        m = Module("test")
        m.parameter("WIDTH")
        with pytest.raises(ValueError, match="already declared"):
            m.parameter("WIDTH")

    def test_duplicate_localparam(self):
        m = Module("test")
        m.localparam("DEPTH")
        with pytest.raises(ValueError, match="already declared"):
            m.localparam("DEPTH")

    def test_duplicate_integer(self):
        m = Module("test")
        m.integer("i")
        with pytest.raises(ValueError, match="already declared"):
            m.integer("i")

    def test_duplicate_inout(self):
        m = Module("test")
        m.inout("bus")
        with pytest.raises(ValueError, match="already declared"):
            m.inout("bus")

    def test_duplicate_output_reg(self):
        m = Module("test")
        m.output_reg("q")
        with pytest.raises(ValueError, match="already declared"):
            m.output_reg("q")


class TestAssignInsideBlock:
    """M2: m.assign() inside an always/initial block raises RuntimeError."""

    def test_assign_inside_always(self):
        m = Module("test")
        a = m.input("a")
        y = m.output("y")
        with pytest.raises(RuntimeError, match="cannot be used inside"):
            with m.always():
                m.assign(y, a)

    def test_assign_inside_initial(self):
        m = Module("test")
        sig = m.output_reg("sig")
        with pytest.raises(RuntimeError, match="cannot be used inside"):
            with m.initial():
                m.assign(sig, 0)


class TestControlFlowOutsideBlock:
    """M3: if_/elif_/else_/case outside always/initial raises RuntimeError."""

    def test_if_outside_block(self):
        m = Module("test")
        a = m.input("a")
        with pytest.raises(RuntimeError, match="must be inside"):
            with m.if_(a):
                pass

    def test_elif_outside_block(self):
        m = Module("test")
        a = m.input("a")
        with pytest.raises(RuntimeError, match="must be inside"):
            with m.elif_(a):
                pass

    def test_else_outside_block(self):
        m = Module("test")
        with pytest.raises(RuntimeError, match="must be inside"):
            with m.else_():
                pass

    def test_case_outside_block(self):
        m = Module("test")
        sel = m.input("sel", width=2)
        with pytest.raises(RuntimeError, match="must be inside"):
            with m.case(sel):
                pass

    def test_casex_outside_block(self):
        m = Module("test")
        sel = m.input("sel", width=2)
        with pytest.raises(RuntimeError, match="must be inside"):
            with m.casex(sel):
                pass

    def test_casez_outside_block(self):
        m = Module("test")
        sel = m.input("sel", width=2)
        with pytest.raises(RuntimeError, match="must be inside"):
            with m.casez(sel):
                pass


class TestInvalidIdentifier:
    """M4: Invalid Verilog identifiers raise ValueError."""

    def test_invalid_module_name(self):
        with pytest.raises(ValueError, match="Invalid Verilog identifier"):
            Module("123bad")

    def test_invalid_module_name_space(self):
        with pytest.raises(ValueError, match="Invalid Verilog identifier"):
            Module("my module")

    def test_invalid_signal_name(self):
        m = Module("test")
        with pytest.raises(ValueError, match="Invalid Verilog identifier"):
            m.input("1signal")

    def test_invalid_wire_name_hyphen(self):
        m = Module("test")
        with pytest.raises(ValueError, match="Invalid Verilog identifier"):
            m.wire("my-wire")

    def test_empty_name(self):
        with pytest.raises(ValueError, match="Invalid Verilog identifier"):
            Module("")

    def test_valid_names(self):
        """Valid Verilog identifiers should not raise."""
        m = Module("_my_module")
        m.input("clk")
        m.input("_rst")
        m.wire("data$out")
        m.reg("count_0")
        m.parameter("WIDTH")


class TestWidthValidation:
    """M5: width <= 0 raises ValueError."""

    def test_zero_width(self):
        m = Module("test")
        with pytest.raises(ValueError, match="must be positive"):
            m.input("sig", width=0)

    def test_negative_width(self):
        m = Module("test")
        with pytest.raises(ValueError, match="must be positive"):
            m.wire("w", width=-1)


class TestInvalidCaseType:
    """M6: Invalid case_type raises ValueError."""

    def test_bad_case_type(self):
        m = Module("test")
        sel = m.input("sel")
        with pytest.raises(ValueError, match="Invalid case_type"):
            with m.always():
                with m.case(sel, case_type="casefoo"):
                    pass


class TestNestedBlocks:
    """M8: Nesting always/initial blocks raises RuntimeError."""

    def test_nested_always(self):
        m = Module("test")
        clk = m.input("clk")
        with pytest.raises(RuntimeError, match="Cannot nest"):
            with m.always(posedge(clk)):
                with m.always():
                    pass

    def test_nested_initial(self):
        m = Module("test")
        with pytest.raises(RuntimeError, match="Cannot nest"):
            with m.initial():
                with m.initial():
                    pass

    def test_initial_inside_always(self):
        m = Module("test")
        clk = m.input("clk")
        with pytest.raises(RuntimeError, match="Cannot nest"):
            with m.always(posedge(clk)):
                with m.initial():
                    pass

    def test_always_inside_initial(self):
        m = Module("test")
        with pytest.raises(RuntimeError, match="Cannot nest"):
            with m.initial():
                with m.always():
                    pass
