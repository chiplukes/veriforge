"""Tests for the Verilog → DSL translator (convert.to_dsl).

Tests verify that parsed Verilog model objects are correctly translated
into Python DSL source code.  Round-trip tests parse Verilog → model →
DSL code → exec → model and check equivalence.
"""

from __future__ import annotations

import textwrap

import pytest
from lark import Tree

from veriforge.codegen.verilog_emitter import emit_module
from veriforge.convert.to_dsl import (
    _expr_to_python,
    _sensitivity_args,
    _stmt_to_lines,
    _width_arg,
    design_to_dsl,
    module_to_dsl,
)
from veriforge.dsl import Module, cat, mux, posedge, rep
from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    Mintypmax,
    PartSelect,
    Range,
    RangeSelect,
    Replication,
    StringLiteral,
    TernaryOp,
    UnaryOp,
)
from veriforge.model.functions import FunctionDecl, TaskDecl
from veriforge.model.generate import GenerateBlock, GenerateFor
from veriforge.model.specify import SpecifyBlock
from veriforge.model.statements import (
    BlockingAssign,
    CaseStatement,
    DelayControl,
    DisableStatement,
    EventTrigger,
    ForeverLoop,
    IfStatement,
    NonblockingAssign,
    ParBlock,
    RepeatLoop,
    SeqBlock,
    SensitivityEdge,
    Statement,
    SystemTaskCall,
    TaskEnable,
    WaitStatement,
    WhileLoop,
)
from veriforge.model.variables import Variable, VariableKind
from veriforge.transforms import tree_to_design
from veriforge.verilog_parser import verilog_parser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def parser():
    return verilog_parser(start="module_declaration")


def _parse_module(parser, verilog: str):
    tree = parser.build_tree(text=verilog)
    design = tree_to_design(tree)
    return design.modules[0]


def _exec_dsl(code: str):
    """Execute DSL code and return the built module."""
    ns = {}
    exec(code, ns)  # noqa: S102
    return ns["module"]


# ===================================================================
# Expression translation
# ===================================================================


class TestExprToLiteral:
    """Test literal expression translation."""

    def test_int_literal(self):
        assert _expr_to_python(Literal(42)) == "42"

    def test_zero(self):
        assert _expr_to_python(Literal(0)) == "0"

    def test_negative_int(self):
        assert _expr_to_python(Literal(-1)) == "-1"

    def test_large_hex(self):
        lit = Literal(0xDEAD)
        lit.width = 16
        assert _expr_to_python(lit) == "0xdead"

    def test_small_no_hex(self):
        lit = Literal(255)
        lit.width = 8
        assert _expr_to_python(lit) == "255"

    def test_string_literal(self):
        assert _expr_to_python(StringLiteral("hello")) == "'hello'"


class TestExprToIdentifier:
    """Test identifier expression translation."""

    def test_simple(self):
        assert _expr_to_python(Identifier("clk")) == "clk"

    def test_hierarchical(self):
        ident = Identifier("a")
        ident.hierarchy = ["top", "sub", "sig"]
        assert _expr_to_python(ident) == "top.sub.sig"


class TestExprUnary:
    """Test unary operator translation."""

    def test_bitwise_not(self):
        result = _expr_to_python(UnaryOp("~", Identifier("a")))
        assert result == "~a"

    def test_negate(self):
        result = _expr_to_python(UnaryOp("-", Identifier("x")))
        assert result == "-x"

    def test_logical_not(self):
        result = _expr_to_python(UnaryOp("!", Identifier("valid")))
        assert result == "lnot(valid)"

    def test_reduce_and(self):
        result = _expr_to_python(UnaryOp("&", Identifier("sig")))
        assert result == "reduce_and(sig)"

    def test_reduce_or(self):
        result = _expr_to_python(UnaryOp("|", Identifier("sig")))
        assert result == "reduce_or(sig)"

    def test_reduce_xor(self):
        result = _expr_to_python(UnaryOp("^", Identifier("sig")))
        assert result == "reduce_xor(sig)"

    def test_reduce_nand(self):
        result = _expr_to_python(UnaryOp("~&", Identifier("sig")))
        assert result == "~reduce_and(sig)"

    def test_reduce_nor(self):
        result = _expr_to_python(UnaryOp("~|", Identifier("sig")))
        assert result == "~reduce_or(sig)"

    def test_reduce_xnor(self):
        result = _expr_to_python(UnaryOp("~^", Identifier("sig")))
        assert result == "~reduce_xor(sig)"

    def test_not_on_binop(self):
        result = _expr_to_python(UnaryOp("~", BinaryOp("+", Identifier("a"), Literal(1))))
        assert result == "~(a + 1)"

    def test_unsupported_unary_operator(self):
        result = _expr_to_python(UnaryOp("~!", Identifier("a")))
        assert result == "(a)  # UNSUPPORTED: unary ~!"


class TestExprBinary:
    """Test binary operator translation."""

    def test_add(self):
        result = _expr_to_python(BinaryOp("+", Identifier("a"), Literal(1)))
        assert result == "a + 1"

    def test_sub(self):
        result = _expr_to_python(BinaryOp("-", Identifier("a"), Identifier("b")))
        assert result == "a - b"

    def test_mul(self):
        result = _expr_to_python(BinaryOp("*", Identifier("a"), Literal(2)))
        assert result == "a * 2"

    def test_div(self):
        result = _expr_to_python(BinaryOp("/", Identifier("a"), Literal(4)))
        assert result == "a // 4"

    def test_mod(self):
        result = _expr_to_python(BinaryOp("%", Identifier("a"), Literal(8)))
        assert result == "a % 8"

    def test_bitwise_and(self):
        result = _expr_to_python(BinaryOp("&", Identifier("a"), Identifier("mask")))
        assert result == "a & mask"

    def test_bitwise_or(self):
        result = _expr_to_python(BinaryOp("|", Identifier("a"), Identifier("b")))
        assert result == "a | b"

    def test_bitwise_xor(self):
        result = _expr_to_python(BinaryOp("^", Identifier("a"), Identifier("b")))
        assert result == "a ^ b"

    def test_bitwise_xnor_tilde_caret(self):
        result = _expr_to_python(BinaryOp("~^", Identifier("a"), Identifier("b")))
        assert result == "~(a ^ b)"

    def test_bitwise_xnor_caret_tilde(self):
        result = _expr_to_python(BinaryOp("^~", Identifier("a"), Identifier("b")))
        assert result == "~(a ^ b)"

    def test_arithmetic_shift_left(self):
        result = _expr_to_python(BinaryOp("<<<", Identifier("a"), Literal(3)))
        assert result == "ashl(a, 3)"

    def test_arithmetic_shift_right(self):
        result = _expr_to_python(BinaryOp(">>>", Identifier("a"), Literal(2)))
        assert result == "ashr(a, 2)"

    def test_shift_left(self):
        result = _expr_to_python(BinaryOp("<<", Identifier("a"), Literal(3)))
        assert result == "a << 3"

    def test_shift_right(self):
        result = _expr_to_python(BinaryOp(">>", Identifier("a"), Literal(2)))
        assert result == "a >> 2"

    def test_eq(self):
        result = _expr_to_python(BinaryOp("==", Identifier("a"), Literal(0)))
        assert result == "a == 0"

    def test_neq(self):
        result = _expr_to_python(BinaryOp("!=", Identifier("a"), Identifier("b")))
        assert result == "a != b"

    def test_case_eq(self):
        result = _expr_to_python(BinaryOp("===", Identifier("a"), Identifier("b")))
        assert result == "case_eq(a, b)"

    def test_case_ne(self):
        result = _expr_to_python(BinaryOp("!==", Identifier("a"), Identifier("b")))
        assert result == "case_ne(a, b)"

    def test_clog2_function(self):
        result = _expr_to_python(FunctionCall("$clog2", [Identifier("WIDTH")], is_system=True))
        assert result == "clog2(WIDTH)"

    def test_time_function(self):
        result = _expr_to_python(FunctionCall("$time", [], is_system=True))
        assert result == "sim_time()"

    def test_signed_function(self):
        result = _expr_to_python(FunctionCall("$signed", [Identifier("a")], is_system=True))
        assert result == "signed(a)"

    def test_unsigned_function(self):
        result = _expr_to_python(FunctionCall("$unsigned", [Identifier("a")], is_system=True))
        assert result == "unsigned(a)"

    def test_unsupported_binary_operator(self):
        result = _expr_to_python(BinaryOp("inside", Identifier("a"), Identifier("b")))
        assert result == "a  # UNSUPPORTED: operator inside"

    def test_lt(self):
        result = _expr_to_python(BinaryOp("<", Identifier("a"), Identifier("b")))
        assert result == "a < b"

    def test_le(self):
        result = _expr_to_python(BinaryOp("<=", Identifier("a"), Identifier("b")))
        assert result == "a <= b"

    def test_gt(self):
        result = _expr_to_python(BinaryOp(">", Identifier("a"), Identifier("b")))
        assert result == "a > b"

    def test_ge(self):
        result = _expr_to_python(BinaryOp(">=", Identifier("a"), Identifier("b")))
        assert result == "a >= b"

    def test_logical_and(self):
        result = _expr_to_python(BinaryOp("&&", Identifier("a"), Identifier("b")))
        assert result == "land(a, b)"

    def test_logical_or(self):
        result = _expr_to_python(BinaryOp("||", Identifier("a"), Identifier("b")))
        assert result == "lor(a, b)"

    def test_nested_binops_parens(self):
        expr = BinaryOp("+", BinaryOp("*", Identifier("a"), Literal(2)), Identifier("b"))
        result = _expr_to_python(expr)
        assert result == "(a * 2) + b"

    def test_power(self):
        result = _expr_to_python(BinaryOp("**", Literal(2), Identifier("n")))
        assert result == "2 ** n"


class TestExprTernary:
    """Test ternary operator translation."""

    def test_basic(self):
        expr = TernaryOp(Identifier("sel"), Identifier("a"), Identifier("b"))
        assert _expr_to_python(expr) == "mux(sel, a, b)"


class TestExprConcat:
    """Test concatenation / replication translation."""

    def test_concat(self):
        expr = Concatenation([Identifier("a"), Identifier("b")])
        assert _expr_to_python(expr) == "cat(a, b)"

    def test_replication(self):
        expr = Replication(Literal(4), Identifier("bit"))
        assert _expr_to_python(expr) == "rep(4, bit)"


class TestExprSelect:
    """Test bit/range/part select translation."""

    def test_bit_select(self):
        expr = BitSelect(Identifier("data"), Literal(3))
        assert _expr_to_python(expr) == "data[3]"

    def test_range_select(self):
        expr = RangeSelect(Identifier("bus"), Literal(7), Literal(0))
        assert _expr_to_python(expr) == "bus[7:0]"

    def test_part_select_up(self):
        expr = PartSelect(Identifier("mem"), Identifier("ptr"), Literal(8), "+:")
        assert _expr_to_python(expr) == "mem.part_select(ptr, 8)"

    def test_part_select_down(self):
        expr = PartSelect(Identifier("mem"), Identifier("ptr"), Literal(8), "-:")
        assert _expr_to_python(expr) == "mem.part_select_down(ptr, 8)"


class TestExprMintypmax:
    """Test mintypmax expression translation."""

    def test_uses_typ(self):
        expr = Mintypmax(Literal(1), Literal(5), Literal(10))
        assert _expr_to_python(expr) == "5"

    def test_unknown_expression_node(self):
        class DummyExpr(Expression):
            __slots__ = ()

        assert _expr_to_python(DummyExpr()) == "None  # UNSUPPORTED expression: DummyExpr"


# ===================================================================
# Width arg helper
# ===================================================================


class TestWidthArg:
    """Test the _width_arg helper."""

    def test_none(self):
        assert _width_arg(None) is None

    def test_standard_range(self):
        # [7:0] → width=8
        assert _width_arg(Range(Literal(7), Literal(0))) == "8"

    def test_param_range(self):
        # [WIDTH-1:0] → width=WIDTH
        msb = BinaryOp("-", Identifier("WIDTH"), Literal(1))
        assert _width_arg(Range(msb, Literal(0))) == "WIDTH"

    def test_arbitrary_range(self):
        # [15:8] → 15 - 8 + 1
        assert _width_arg(Range(Literal(15), Literal(8))) == "15 - 8 + 1"

    def test_expression_zero_lsb(self):
        # [N:0] → N + 1
        assert _width_arg(Range(Identifier("N"), Literal(0))) == "N + 1"


# ===================================================================
# Statement translation
# ===================================================================


class TestStmtAssign:
    """Test assignment statement translation."""

    def test_nba(self):
        stmt = NonblockingAssign(Identifier("q"), Identifier("d"))
        lines = _stmt_to_lines(stmt)
        assert lines == ["q <<= d"]

    def test_blocking(self):
        stmt = BlockingAssign(Identifier("sum"), BinaryOp("+", Identifier("a"), Identifier("b")))
        lines = _stmt_to_lines(stmt)
        assert lines == ["sum @= a + b"]

    def test_nba_indented(self):
        stmt = NonblockingAssign(Identifier("q"), Literal(0))
        lines = _stmt_to_lines(stmt, depth=2)
        assert lines == ["        q <<= 0"]


class TestStmtIf:
    """Test if/elif/else statement translation."""

    def test_simple_if(self):
        stmt = IfStatement(
            Identifier("rst"),
            NonblockingAssign(Identifier("q"), Literal(0)),
            None,
        )
        lines = _stmt_to_lines(stmt)
        assert lines[0] == "with m.if_(rst):"
        assert lines[1] == "    q <<= 0"

    def test_if_else(self):
        stmt = IfStatement(
            Identifier("rst"),
            NonblockingAssign(Identifier("q"), Literal(0)),
            NonblockingAssign(Identifier("q"), Identifier("d")),
        )
        lines = _stmt_to_lines(stmt)
        assert "with m.if_(rst):" in lines
        assert "with m.else_():" in lines

    def test_if_elif_else(self):
        inner_if = IfStatement(
            Identifier("en"),
            NonblockingAssign(Identifier("q"), Identifier("d")),
            NonblockingAssign(Identifier("q"), Identifier("q")),
        )
        stmt = IfStatement(
            Identifier("rst"),
            NonblockingAssign(Identifier("q"), Literal(0)),
            inner_if,
        )
        lines = _stmt_to_lines(stmt)
        assert "with m.if_(rst):" in lines
        assert "with m.elif_(en):" in lines
        assert "with m.else_():" in lines


class TestStmtCase:
    """Test case statement translation."""

    def test_basic_case(self):
        from veriforge.model.statements import CaseItem

        items = [
            CaseItem([Literal(0)], NonblockingAssign(Identifier("out"), Literal(1))),
            CaseItem([Literal(1)], NonblockingAssign(Identifier("out"), Literal(2))),
            CaseItem(None, NonblockingAssign(Identifier("out"), Literal(0)), is_default=True),
        ]
        stmt = CaseStatement("case", Identifier("sel"), items)
        lines = _stmt_to_lines(stmt)
        assert lines[0] == "with m.case(sel) as _c:"
        assert any("_c.when(0)" in ln for ln in lines)
        assert any("_c.default()" in ln for ln in lines)


class TestStmtSeqBlock:
    """Test that begin/end blocks are flattened."""

    def test_flatten(self):
        blk = SeqBlock(
            [
                NonblockingAssign(Identifier("a"), Literal(0)),
                NonblockingAssign(Identifier("b"), Literal(1)),
            ]
        )
        lines = _stmt_to_lines(blk)
        assert len(lines) == 2
        assert lines[0] == "a <<= 0"
        assert lines[1] == "b <<= 1"


class TestStmtSystemTask:
    """Test system task call translation."""

    def test_display(self):
        stmt = SystemTaskCall("$display", [StringLiteral("hello %d"), Identifier("x")])
        lines = _stmt_to_lines(stmt)
        assert lines == ["m.display('hello %d', x)"]

    def test_finish(self):
        stmt = SystemTaskCall("$finish", [])
        lines = _stmt_to_lines(stmt)
        assert lines == ["m.finish()"]

    def test_readmemh(self):
        stmt = SystemTaskCall("$readmemh", [StringLiteral("data.hex"), Identifier("mem")])
        lines = _stmt_to_lines(stmt)
        assert lines == ["m.readmemh('data.hex', mem)"]

    def test_unsupported_task(self):
        stmt = SystemTaskCall("$fwrite", [Identifier("fd"), StringLiteral("text")])
        lines = _stmt_to_lines(stmt)
        assert lines == ["# UNSUPPORTED: $fwrite(fd, 'text')"]


class TestStmtDelay:
    """Test delay control translation."""

    def test_delay_no_body(self):
        stmt = DelayControl(Literal(10), None)
        lines = _stmt_to_lines(stmt)
        assert lines == ["m.delay(10)"]

    def test_delay_with_body(self):
        stmt = DelayControl(Literal(5), NonblockingAssign(Identifier("clk"), UnaryOp("~", Identifier("clk"))))
        lines = _stmt_to_lines(stmt)
        assert lines[0] == "with m.delay(5):"


class TestStmtUnsupported:
    """Test unsupported statement translation (comments)."""

    def test_while_loop(self):
        stmt = WhileLoop(Identifier("go"), None)
        lines = _stmt_to_lines(stmt)
        assert lines == ["# UNSUPPORTED: while loop"]

    def test_forever_loop(self):
        stmt = ForeverLoop(None)
        lines = _stmt_to_lines(stmt)
        assert lines == ["# UNSUPPORTED: forever loop"]

    def test_repeat_loop(self):
        stmt = RepeatLoop(Literal(10), None)
        lines = _stmt_to_lines(stmt)
        assert lines == ["# UNSUPPORTED: repeat loop"]

    def test_wait_statement(self):
        stmt = WaitStatement(Identifier("ready"), None)
        lines = _stmt_to_lines(stmt)
        assert lines == ["# UNSUPPORTED: wait(ready)"]

    def test_disable(self):
        stmt = DisableStatement("my_block")
        lines = _stmt_to_lines(stmt)
        assert lines == ["# UNSUPPORTED: disable my_block"]

    def test_event_trigger(self):
        stmt = EventTrigger("done")
        lines = _stmt_to_lines(stmt)
        assert lines == ["# UNSUPPORTED: -> done"]

    def test_task_enable(self):
        stmt = TaskEnable("my_task", [Identifier("a")])
        lines = _stmt_to_lines(stmt)
        assert lines == ["# UNSUPPORTED: my_task(a)"]

    def test_par_block(self):
        stmt = ParBlock([NonblockingAssign(Identifier("a"), Literal(0))], name="fork_blk")
        lines = _stmt_to_lines(stmt)
        assert lines == ["# UNSUPPORTED: fork/join block", "a <<= 0"]

    def test_unknown_statement_node(self):
        class DummyStmt(Statement):
            __slots__ = ()

        assert _stmt_to_lines(DummyStmt()) == ["# UNSUPPORTED statement: DummyStmt"]


# ===================================================================
# Sensitivity list
# ===================================================================


class TestSensitivity:
    """Test sensitivity list argument translation."""

    def test_posedge(self):
        edges = [SensitivityEdge("posedge", Identifier("clk"))]
        assert _sensitivity_args(edges) == "posedge(clk)"

    def test_negedge(self):
        edges = [SensitivityEdge("negedge", Identifier("rst_n"))]
        assert _sensitivity_args(edges) == "negedge(rst_n)"

    def test_posedge_negedge(self):
        edges = [
            SensitivityEdge("posedge", Identifier("clk")),
            SensitivityEdge("negedge", Identifier("rst_n")),
        ]
        assert _sensitivity_args(edges) == "posedge(clk), negedge(rst_n)"

    def test_level_sensitive(self):
        edges = [SensitivityEdge("level", Identifier("data"))]
        assert _sensitivity_args(edges) == "data"


# ===================================================================
# Module-level translation (DSL-built modules)
# ===================================================================


class TestModuleToDsl:
    """Test full module_to_dsl on DSL-built modules."""

    def test_empty_module(self):
        m = Module("empty")
        mod = m.build()
        code = module_to_dsl(mod)
        assert 'Module("empty")' in code
        assert "module = m.build()" in code

    def test_simple_wire(self):
        m = Module("test")
        a = m.input("a")
        b = m.output("b")
        m.assign(b, a)
        mod = m.build()
        code = module_to_dsl(mod)
        assert 'm.input("a")' in code
        assert 'm.output("b")' in code
        assert "m.assign(" in code

    def test_counter_module(self):
        m = Module("counter")
        clk = m.input("clk")
        rst = m.input("rst")
        count = m.output_reg("count", width=8)
        with m.always(posedge(clk)):
            with m.if_(rst):
                count <<= 0
            with m.else_():
                count <<= count + 1
        mod = m.build()
        code = module_to_dsl(mod)
        assert 'Module("counter")' in code
        assert "m.output_reg" in code
        assert "m.always(posedge(clk))" in code
        assert "m.if_" in code
        assert "m.else_" in code
        assert "<<=" in code

    def test_parameters(self):
        m = Module("param_mod")
        w = m.parameter("WIDTH", default=8)
        m.input("data", width=w)
        mod = m.build()
        code = module_to_dsl(mod)
        assert "m.parameter" in code
        assert "WIDTH" in code

    def test_instance(self):
        m = Module("top")
        clk = m.input("clk")
        m.instance("sub_mod", "u0", ports={"clk": clk})
        mod = m.build()
        code = module_to_dsl(mod)
        assert 'm.instance("sub_mod", "u0"' in code

    def test_initial_block(self):
        m = Module("tb")
        sig = m.reg("sig")
        with m.initial():
            sig @= 0
        mod = m.build()
        code = module_to_dsl(mod)
        assert "m.initial()" in code

    def test_case_statement(self):
        m = Module("mux4")
        sel = m.input("sel", width=2)
        a = m.input("a")
        b = m.input("b")
        c = m.input("c")
        d = m.input("d")
        y = m.output_reg("y")
        with m.always():
            with m.case(sel) as cs:
                with cs.when(0):
                    y @= a
                with cs.when(1):
                    y @= b
                with cs.when(2):
                    y @= c
                with cs.default():
                    y @= d
        mod = m.build()
        code = module_to_dsl(mod)
        assert "m.case(sel)" in code
        assert "_c.when(" in code
        assert "_c.default()" in code


class TestModuleUnsupported:
    """Test exact unsupported output shapes at module scope."""

    def test_unsupported_function_decl(self):
        mod = Module("with_func").build()
        mod.functions.append(FunctionDecl("next_val"))
        code = module_to_dsl(mod)
        assert "# UNSUPPORTED: function next_val" in code.splitlines()

    def test_unsupported_task_decl(self):
        mod = Module("with_task").build()
        mod.tasks.append(TaskDecl("drive"))
        code = module_to_dsl(mod)
        assert "# UNSUPPORTED: task drive" in code.splitlines()

    def test_unsupported_generate_block(self):
        mod = Module("with_generate").build()
        mod.generate_blocks.append(
            GenerateFor("i", Literal(0), Literal(1), Literal(1), GenerateBlock()),
        )
        code = module_to_dsl(mod)
        assert "# UNSUPPORTED: generate block (GenerateFor)" in code.splitlines()

    def test_unsupported_specify_block(self):
        mod = Module("with_specify").build()
        mod.specify_blocks.append(SpecifyBlock(Tree("specify_block", [])))
        code = module_to_dsl(mod)
        assert "# UNSUPPORTED: specify block" in code.splitlines()

    @pytest.mark.parametrize(
        ("kind", "name", "expected"),
        [
            (VariableKind.REAL, "gain", "# UNSUPPORTED: real gain"),
            (VariableKind.REALTIME, "stamp_rt", "# UNSUPPORTED: realtime stamp_rt"),
            (VariableKind.TIME, "stamp", "# UNSUPPORTED: time stamp"),
            (VariableKind.EVENT, "done_evt", "# UNSUPPORTED: event done_evt"),
        ],
    )
    def test_unsupported_variable_kinds(self, kind, name, expected):
        mod = Module("with_vars").build()
        mod.variables.append(Variable(name, kind=kind))
        code = module_to_dsl(mod)
        assert expected in code.splitlines()


class TestModuleToDslRoundTrip:
    """Round-trip: DSL → build → translate → exec → build → compare Verilog."""

    def _round_trip(self, build_fn):
        """Build module, translate to DSL code, exec code, compare."""
        mod1 = build_fn()
        code = module_to_dsl(mod1)
        mod2 = _exec_dsl(code)
        v1 = emit_module(mod1)
        v2 = emit_module(mod2)
        assert v1 == v2, (
            f"Verilog mismatch:\n--- Original ---\n{v1}\n--- Round-trip ---\n{v2}\n--- DSL code ---\n{code}"
        )
        return code

    def test_passthrough(self):
        def build():
            m = Module("pass_through")
            inp = m.input("data_in", width=8)
            out = m.output("data_out", width=8)
            m.assign(out, inp)
            return m.build()

        self._round_trip(build)

    def test_counter(self):
        def build():
            m = Module("counter")
            clk = m.input("clk")
            rst = m.input("rst")
            count = m.output_reg("count", width=8)
            with m.always(posedge(clk)):
                with m.if_(rst):
                    count <<= 0
                with m.else_():
                    count <<= count + 1
            return m.build()

        self._round_trip(build)

    def test_mux2(self):
        def build():
            m = Module("mux2")
            sel = m.input("sel")
            a = m.input("a", width=8)
            b = m.input("b", width=8)
            y = m.output("y", width=8)
            m.assign(y, mux(sel, a, b))
            return m.build()

        self._round_trip(build)

    def test_concat_rep(self):
        def build():
            m = Module("concat_test")
            a = m.input("a", width=4)
            b = m.input("b", width=4)
            c = m.output("c", width=8)
            d = m.output("d", width=16)
            m.assign(c, cat(a, b))
            m.assign(d, rep(2, cat(a, b)))
            return m.build()

        self._round_trip(build)

    def test_sr_latch(self):
        def build():
            m = Module("sr_latch")
            s = m.input("s")
            r = m.input("r")
            q = m.output_reg("q")
            with m.always():
                with m.if_(s):
                    q @= 1
                with m.elif_(r):
                    q @= 0
            return m.build()

        self._round_trip(build)

    def test_shift_register(self):
        def build():
            m = Module("shift_reg")
            clk = m.input("clk")
            din = m.input("din")
            dout = m.output_reg("dout", width=8)
            with m.always(posedge(clk)):
                dout <<= cat(dout[6:0], din)
            return m.build()

        self._round_trip(build)

    def test_priority_encoder(self):
        def build():
            m = Module("pri_enc")
            inp = m.input("inp", width=4)
            out = m.output_reg("out", width=2)
            valid = m.output_reg("valid")
            with m.always():
                out @= 0
                valid @= 0
                with m.if_(inp[3]):
                    out @= 3
                    valid @= 1
                with m.elif_(inp[2]):
                    out @= 2
                    valid @= 1
                with m.elif_(inp[1]):
                    out @= 1
                    valid @= 1
                with m.elif_(inp[0]):
                    out @= 0
                    valid @= 1
            return m.build()

        self._round_trip(build)


# ===================================================================
# Parsed Verilog round-trips
# ===================================================================


class TestParsedVerilog:
    """Test translating parsed Verilog code to DSL."""

    def test_simple_assign(self, parser):
        verilog = textwrap.dedent("""\
            module buf_gate (
                input a,
                output b
            );
                assign b = a;
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert 'Module("buf_gate")' in code
        assert 'm.input("a")' in code
        assert 'm.output("b")' in code
        assert "m.assign(" in code
        # Execute the DSL code to verify it's valid Python
        mod2 = _exec_dsl(code)
        assert mod2.name == "buf_gate"
        assert len(mod2.ports) == 2

    def test_always_ff(self, parser):
        verilog = textwrap.dedent("""\
            module dff (
                input clk,
                input d,
                output reg q
            );
                always @(posedge clk)
                    q <= d;
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "posedge" in code
        assert "<<=" in code
        mod2 = _exec_dsl(code)
        assert mod2.name == "dff"
        assert len(mod2.always_blocks) == 1

    def test_if_else(self, parser):
        verilog = textwrap.dedent("""\
            module dff_rst (
                input clk,
                input rst,
                input d,
                output reg q
            );
                always @(posedge clk)
                    if (rst)
                        q <= 0;
                    else
                        q <= d;
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "m.if_" in code
        assert "m.else_" in code
        mod2 = _exec_dsl(code)
        assert mod2.name == "dff_rst"

    def test_wire_declaration(self, parser):
        verilog = textwrap.dedent("""\
            module with_wire (
                input a,
                input b,
                output c
            );
                wire internal;
                assign internal = a & b;
                assign c = internal;
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "m.wire" in code
        mod2 = _exec_dsl(code)
        assert mod2.name == "with_wire"

    def test_multi_bit_ports(self, parser):
        verilog = textwrap.dedent("""\
            module adder (
                input [7:0] a,
                input [7:0] b,
                output [8:0] sum
            );
                assign sum = a + b;
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "width=8" in code
        assert "width=9" in code
        mod2 = _exec_dsl(code)
        assert mod2.name == "adder"

    def test_instance(self, parser):
        verilog = textwrap.dedent("""\
            module top (
                input clk,
                output q
            );
                dff u0 (.clk(clk), .d(1'b1), .q(q));
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "m.instance" in code
        assert '"dff"' in code
        assert '"u0"' in code
        mod2 = _exec_dsl(code)
        assert len(mod2.instances) == 1

    def test_case_statement(self, parser):
        verilog = textwrap.dedent("""\
            module mux4 (
                input [1:0] sel,
                input a, b, c, d,
                output reg y
            );
                always @(*)
                    case (sel)
                        2'b00: y = a;
                        2'b01: y = b;
                        2'b10: y = c;
                        default: y = d;
                    endcase
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "m.case" in code
        mod2 = _exec_dsl(code)
        assert mod2.name == "mux4"

    def test_parameter(self, parser):
        verilog = textwrap.dedent("""\
            module param_mod #(
                parameter WIDTH = 8
            ) (
                input [WIDTH-1:0] data,
                output [WIDTH-1:0] out
            );
                assign out = data;
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "m.parameter" in code
        assert "WIDTH" in code
        mod2 = _exec_dsl(code)
        assert mod2.name == "param_mod"

    def test_initial_block(self, parser):
        verilog = textwrap.dedent("""\
            module tb;
                reg clk;
                initial begin
                    clk = 0;
                end
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "m.initial" in code
        mod2 = _exec_dsl(code)
        assert len(mod2.initial_blocks) == 1

    def test_concatenation(self, parser):
        verilog = textwrap.dedent("""\
            module concat_test (
                input [3:0] a,
                input [3:0] b,
                output [7:0] c
            );
                assign c = {a, b};
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "cat(" in code
        mod2 = _exec_dsl(code)
        assert mod2.name == "concat_test"

    def test_ternary_op(self, parser):
        verilog = textwrap.dedent("""\
            module mux2 (
                input sel,
                input [7:0] a,
                input [7:0] b,
                output [7:0] y
            );
                assign y = sel ? a : b;
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "mux(" in code
        mod2 = _exec_dsl(code)
        assert len(mod2.continuous_assigns) == 1

    def test_system_task(self, parser):
        verilog = textwrap.dedent("""\
            module tb_display;
                reg [7:0] val;
                initial begin
                    val = 42;
                    $display("val = %d", val);
                    $finish;
                end
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "m.display" in code
        assert "m.finish" in code
        mod2 = _exec_dsl(code)
        assert len(mod2.initial_blocks) == 1

    def test_reg_internal(self, parser):
        verilog = textwrap.dedent("""\
            module with_reg (
                input clk,
                input din,
                output reg [7:0] dout
            );
                reg [7:0] temp;
                always @(posedge clk) begin
                    temp <= {temp[6:0], din};
                    dout <= temp;
                end
            endmodule
        """)
        mod = _parse_module(parser, verilog)
        code = module_to_dsl(mod)
        assert "m.reg" in code
        mod2 = _exec_dsl(code)
        assert mod2.name == "with_reg"


# ===================================================================
# design_to_dsl
# ===================================================================


class TestDesignToDsl:
    """Test design_to_dsl (multiple modules)."""

    def test_multi_module(self):
        m1 = Module("mod_a")
        m1.input("a")
        m1.output("b")
        mod1 = m1.build()

        m2 = Module("mod_b")
        m2.input("x")
        m2.output("y")
        mod2 = m2.build()

        from veriforge.model.design import Design

        design = Design()
        design.modules = [mod1, mod2]
        code = design_to_dsl(design)
        assert "Module: mod_a" in code
        assert "Module: mod_b" in code
        assert code.count("module = m.build()") == 2


# ===================================================================
# Import collection
# ===================================================================


class TestImportCollection:
    """Test that generated imports match used features."""

    def test_basic_imports(self):
        m = Module("test")
        m.input("a")
        m.output("b")
        mod = m.build()
        code = module_to_dsl(mod)
        assert "from veriforge.dsl import Module" in code

    def test_posedge_import(self):
        m = Module("test")
        clk = m.input("clk")
        q = m.output_reg("q")
        with m.always(posedge(clk)):
            q <<= 0
        mod = m.build()
        code = module_to_dsl(mod)
        assert "posedge" in code.split("\n")[0]

    def test_cat_import(self):
        m = Module("test")
        a = m.input("a", width=4)
        b = m.input("b", width=4)
        c = m.output("c", width=8)
        m.assign(c, cat(a, b))
        mod = m.build()
        code = module_to_dsl(mod)
        assert "cat" in code.split("\n")[0]

    def test_mux_import(self):
        m = Module("test")
        sel = m.input("sel")
        a = m.input("a")
        b = m.input("b")
        y = m.output("y")
        m.assign(y, mux(sel, a, b))
        mod = m.build()
        code = module_to_dsl(mod)
        assert "mux" in code.split("\n")[0]

    def test_reduction_nand_import(self):
        m = Module("test")
        a = m.input("a", width=8)
        y = m.output("y")
        m.assign(y, UnaryOp("~&", a._as_expr()))
        mod = m.build()
        code = module_to_dsl(mod)
        assert "reduce_and" in code.split("\n")[0]

    def test_arithmetic_shift_helper_imports(self):
        mod = _parse_module(
            verilog_parser(start="module_declaration"),
            """
            module shift_helpers(
                input signed [7:0] a,
                output signed [7:0] y
            );
                assign y = a >>> 2;
            endmodule
            """,
        )
        code = module_to_dsl(mod)
        assert "ashr" in code.split("\n")[0]

    def test_case_equality_helper_imports(self):
        mod = _parse_module(
            verilog_parser(start="module_declaration"),
            """
            module case_helpers(
                input [3:0] a,
                input [3:0] b,
                output y
            );
                assign y = a === b;
            endmodule
            """,
        )
        code = module_to_dsl(mod)
        assert "case_eq" in code.split("\n")[0]

    def test_clog2_helper_imports(self):
        mod = _parse_module(
            verilog_parser(start="module_declaration"),
            """
            module clog2_helpers(
                input [7:0] a,
                output [31:0] y
            );
                assign y = $clog2(a);
            endmodule
            """,
        )
        code = module_to_dsl(mod)
        assert "clog2" in code.split("\n")[0]

    def test_time_helper_imports(self):
        mod = _parse_module(
            verilog_parser(start="module_declaration"),
            """
            module time_helpers(
                output [31:0] y
            );
                assign y = $time;
            endmodule
            """,
        )
        code = module_to_dsl(mod)
        assert "sim_time" in code.split("\n")[0]

    def test_signed_helper_imports(self):
        mod = _parse_module(
            verilog_parser(start="module_declaration"),
            """
            module signed_helpers(
                input [7:0] a,
                output signed [7:0] y
            );
                assign y = $signed(a);
            endmodule
            """,
        )
        code = module_to_dsl(mod)
        assert "signed" in code.split("\n")[0]

    def test_unsigned_helper_imports(self):
        mod = _parse_module(
            verilog_parser(start="module_declaration"),
            """
            module unsigned_helpers(
                input signed [7:0] a,
                output [7:0] y
            );
                assign y = $unsigned(a);
            endmodule
            """,
        )
        code = module_to_dsl(mod)
        assert "unsigned" in code.split("\n")[0]
