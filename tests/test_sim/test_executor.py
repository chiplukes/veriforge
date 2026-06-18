"""Tests for the statement executor.

Covers:
  - Blocking assignment (immediate update)
  - Non-blocking assignment (deferred to NBA)
  - Blocking vs non-blocking semantics (swap test)
  - If/else
  - Case/casex/casez
  - For/while/repeat/forever loops
  - Sequential blocks (begin...end)
  - Disable statement
  - System tasks ($display, $finish)
  - LHS targets (bit select, range select, concatenation)
  - Event trigger
  - Delay/event control (SuspendExecution)
"""

import pytest

from veriforge.model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Identifier,
    Literal,
    RangeSelect,
)
from veriforge.model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    DelayControl,
    DisableStatement,
    EventTrigger,
    ForeverLoop,
    ForLoop,
    IfStatement,
    NonblockingAssign,
    RepeatLoop,
    SeqBlock,
    SystemTaskCall,
    WhileLoop,
)
from veriforge.sim.evaluator import EvalContext
from veriforge.sim.executor import DisableBlock, StopExecution, StatementExecutor, SuspendExecution
from veriforge.sim.value import Value


@pytest.fixture
def ex():
    return StatementExecutor(loop_limit=1000)


@pytest.fixture
def ctx():
    return EvalContext(
        {
            "a": Value(5, width=8),
            "b": Value(3, width=8),
            "x": Value(0, width=8),
            "y": Value(0, width=8),
            "sel": Value(1, width=2),
            "count": Value(0, width=8),
            "bus": Value(0xAB, width=8),
        }
    )


# ── Blocking Assignment ──────────────────────────────────────────────


class TestBlockingAssign:
    def test_simple_assign(self, ex, ctx):
        stmt = BlockingAssign(Identifier("x"), Literal(42, width=8))
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 42

    def test_assign_from_signal(self, ex, ctx):
        stmt = BlockingAssign(Identifier("x"), Identifier("a"))
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 5

    def test_assign_expression(self, ex, ctx):
        stmt = BlockingAssign(Identifier("x"), BinaryOp("+", Identifier("a"), Identifier("b")))
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 8

    def test_blocking_visible_immediately(self, ex, ctx):
        """Blocking assigns are visible to subsequent statements."""
        block = SeqBlock(
            [
                BlockingAssign(Identifier("x"), Literal(10, width=8)),
                BlockingAssign(Identifier("y"), Identifier("x")),
            ]
        )
        ex.execute(block, ctx)
        assert ctx.read_signal("x") == 10
        assert ctx.read_signal("y") == 10  # sees updated x


# ── Non-Blocking Assignment ──────────────────────────────────────────


class TestNonblockingAssign:
    def test_nba_deferred(self, ex, ctx):
        stmt = NonblockingAssign(Identifier("x"), Literal(42, width=8))
        ex.execute(stmt, ctx)
        # Not yet applied
        assert ctx.read_signal("x") == 0
        assert len(ex.nba_queue) == 1

    def test_nba_apply(self, ex, ctx):
        stmt = NonblockingAssign(Identifier("x"), Literal(42, width=8))
        ex.execute(stmt, ctx)
        ex.apply_nba(ctx)
        assert ctx.read_signal("x") == 42
        assert len(ex.nba_queue) == 0

    def test_nba_not_visible_immediately(self, ex, ctx):
        """NBA reads see old values, not newly scheduled ones."""
        block = SeqBlock(
            [
                NonblockingAssign(Identifier("x"), Literal(10, width=8)),
                NonblockingAssign(Identifier("y"), Identifier("x")),
            ]
        )
        ex.execute(block, ctx)
        # x was 0 when y's RHS was evaluated
        ex.apply_nba(ctx)
        assert ctx.read_signal("x") == 10
        assert ctx.read_signal("y") == 0  # saw x=0, not x=10


class TestBlockingVsNonblocking:
    def test_swap_blocking_fails(self, ex, ctx):
        """Blocking: a=b; b=a; → both become b's original value."""
        ctx.write_signal("a", Value(0xAA, width=8))
        ctx.write_signal("b", Value(0xBB, width=8))
        block = SeqBlock(
            [
                BlockingAssign(Identifier("a"), Identifier("b")),
                BlockingAssign(Identifier("b"), Identifier("a")),
            ]
        )
        ex.execute(block, ctx)
        assert ctx.read_signal("a") == 0xBB
        assert ctx.read_signal("b") == 0xBB  # NOT swapped

    def test_swap_nonblocking_works(self, ex, ctx):
        """Non-blocking: a<=b; b<=a; → proper swap."""
        ctx.write_signal("a", Value(0xAA, width=8))
        ctx.write_signal("b", Value(0xBB, width=8))
        block = SeqBlock(
            [
                NonblockingAssign(Identifier("a"), Identifier("b")),
                NonblockingAssign(Identifier("b"), Identifier("a")),
            ]
        )
        ex.execute(block, ctx)
        ex.apply_nba(ctx)
        assert ctx.read_signal("a") == 0xBB
        assert ctx.read_signal("b") == 0xAA  # properly swapped


# ── If Statement ──────────────────────────────────────────────────────


class TestIfStatement:
    def test_true_branch(self, ex, ctx):
        stmt = IfStatement(
            Identifier("a"),  # a=5 → true
            BlockingAssign(Identifier("x"), Literal(1, width=8)),
            BlockingAssign(Identifier("x"), Literal(2, width=8)),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 1

    def test_false_branch(self, ex, ctx):
        ctx.write_signal("a", Value(0, width=8))
        stmt = IfStatement(
            Identifier("a"),  # a=0 → false
            BlockingAssign(Identifier("x"), Literal(1, width=8)),
            BlockingAssign(Identifier("x"), Literal(2, width=8)),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 2

    def test_no_else(self, ex, ctx):
        ctx.write_signal("a", Value(0, width=8))
        stmt = IfStatement(
            Identifier("a"),
            BlockingAssign(Identifier("x"), Literal(1, width=8)),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 0  # unchanged

    def test_nested_if(self, ex, ctx):
        inner = IfStatement(
            BinaryOp("==", Identifier("b"), Literal(3, width=8)),
            BlockingAssign(Identifier("x"), Literal(99, width=8)),
        )
        outer = IfStatement(Identifier("a"), inner)
        ex.execute(outer, ctx)
        assert ctx.read_signal("x") == 99  # a=5→true, b==3→true

    def test_x_condition(self, ex, ctx):
        """x/z condition → neither branch taken (treated as false)."""
        ctx.write_signal("a", Value.x(8))
        stmt = IfStatement(
            Identifier("a"),
            BlockingAssign(Identifier("x"), Literal(1, width=8)),
            BlockingAssign(Identifier("x"), Literal(2, width=8)),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 2  # else branch (x is not true)


# ── Case Statement ───────────────────────────────────────────────────


class TestCaseStatement:
    def test_case_match(self, ex, ctx):
        stmt = CaseStatement(
            "case",
            Identifier("sel"),
            [
                CaseItem([Literal(0, width=2)], BlockingAssign(Identifier("x"), Literal(10, width=8))),
                CaseItem([Literal(1, width=2)], BlockingAssign(Identifier("x"), Literal(20, width=8))),
                CaseItem([Literal(2, width=2)], BlockingAssign(Identifier("x"), Literal(30, width=8))),
                CaseItem(None, BlockingAssign(Identifier("x"), Literal(99, width=8)), is_default=True),
            ],
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 20  # sel=1

    def test_case_default(self, ex, ctx):
        ctx.write_signal("sel", Value(3, width=2))
        stmt = CaseStatement(
            "case",
            Identifier("sel"),
            [
                CaseItem([Literal(0, width=2)], BlockingAssign(Identifier("x"), Literal(10, width=8))),
                CaseItem(None, BlockingAssign(Identifier("x"), Literal(99, width=8)), is_default=True),
            ],
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 99

    def test_case_no_match(self, ex, ctx):
        ctx.write_signal("sel", Value(3, width=2))
        stmt = CaseStatement(
            "case",
            Identifier("sel"),
            [
                CaseItem([Literal(0, width=2)], BlockingAssign(Identifier("x"), Literal(10, width=8))),
            ],
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 0  # unchanged

    def test_casex_dont_care(self, ex, ctx):
        """casex: x/z bits are don't-care."""
        ctx.write_signal("sel", Value(0b10, width=2))
        stmt = CaseStatement(
            "casex",
            Identifier("sel"),
            [
                CaseItem(
                    [Literal(0, width=2, is_x=True)],  # 2'bxx → matches anything
                    BlockingAssign(Identifier("x"), Literal(42, width=8)),
                ),
            ],
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 42

    def test_case_multiple_values(self, ex, ctx):
        """case item with multiple values: 0, 1: ..."""
        stmt = CaseStatement(
            "case",
            Identifier("sel"),
            [
                CaseItem(
                    [Literal(0, width=2), Literal(1, width=2)],
                    BlockingAssign(Identifier("x"), Literal(77, width=8)),
                ),
                CaseItem(None, BlockingAssign(Identifier("x"), Literal(88, width=8)), is_default=True),
            ],
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 77  # sel=1 matches


# ── For Loop ──────────────────────────────────────────────────────────


class TestForLoop:
    def test_simple_for(self, ex, ctx):
        """for (i=0; i<5; i=i+1) count = count + 1;"""
        ctx.write_signal("i", Value(0, width=8))
        stmt = ForLoop(
            init=BlockingAssign(Identifier("i"), Literal(0, width=8)),
            condition=BinaryOp("<", Identifier("i"), Literal(5, width=8)),
            update=BlockingAssign(Identifier("i"), BinaryOp("+", Identifier("i"), Literal(1, width=8))),
            body=BlockingAssign(
                Identifier("count"),
                BinaryOp("+", Identifier("count"), Literal(1, width=8)),
            ),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("count") == 5
        assert ctx.read_signal("i") == 5

    def test_for_loop_limit(self, ex, ctx):
        """Infinite for loop should hit the limit."""
        ctx.write_signal("i", Value(0, width=8))
        stmt = ForLoop(
            init=BlockingAssign(Identifier("i"), Literal(0, width=8)),
            condition=Literal(1, width=1),  # always true
            update=BlockingAssign(Identifier("i"), Identifier("i")),
            body=BlockingAssign(Identifier("count"), Identifier("count")),
        )
        with pytest.raises(RuntimeError, match="exceeded"):
            ex.execute(stmt, ctx)

    def test_for_increment_operator(self, ex, ctx):
        """for (i=0; i<3; i++) count = count + 1;  (i++ parsed as i = i + 1)"""
        ctx.write_signal("i", Value(0, width=8))
        stmt = ForLoop(
            init=BlockingAssign(Identifier("i"), Literal(0, width=8)),
            condition=BinaryOp("<", Identifier("i"), Literal(3, width=8)),
            update=BlockingAssign(Identifier("i"), BinaryOp("+", Identifier("i"), Literal(1, width=8))),
            body=BlockingAssign(
                Identifier("count"),
                BinaryOp("+", Identifier("count"), Literal(1, width=8)),
            ),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("count") == 3
        assert ctx.read_signal("i") == 3

    def test_for_decrement_operator(self, ex, ctx):
        """for (i=3; i>0; i--) count = count + 1;  (i-- parsed as i = i - 1)"""
        ctx.write_signal("i", Value(3, width=8))
        stmt = ForLoop(
            init=BlockingAssign(Identifier("i"), Literal(3, width=8)),
            condition=BinaryOp(">", Identifier("i"), Literal(0, width=8)),
            update=BlockingAssign(Identifier("i"), BinaryOp("-", Identifier("i"), Literal(1, width=8))),
            body=BlockingAssign(
                Identifier("count"),
                BinaryOp("+", Identifier("count"), Literal(1, width=8)),
            ),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("count") == 3
        assert ctx.read_signal("i") == 0

    def test_for_compound_assignment(self, ex, ctx):
        """for (i=0; i<10; i+=2) count = count + 1;  (i+=2 parsed as i = i + 2)"""
        ctx.write_signal("i", Value(0, width=8))
        stmt = ForLoop(
            init=BlockingAssign(Identifier("i"), Literal(0, width=8)),
            condition=BinaryOp("<", Identifier("i"), Literal(10, width=8)),
            update=BlockingAssign(Identifier("i"), BinaryOp("+", Identifier("i"), Literal(2, width=8))),
            body=BlockingAssign(
                Identifier("count"),
                BinaryOp("+", Identifier("count"), Literal(1, width=8)),
            ),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("count") == 5
        assert ctx.read_signal("i") == 10

    def test_for_signed_var_decrement_terminates(self, ex, ctx):
        """for (int i=3; i>=0; i--) — signed loop must terminate when i goes negative."""
        ctx.write_signal("i", Value(3, width=32))
        stmt = ForLoop(
            init=BlockingAssign(Identifier("i"), Literal(3, width=32)),
            condition=BinaryOp(">=", Identifier("i"), Literal(0, width=32)),
            update=BlockingAssign(Identifier("i"), BinaryOp("-", Identifier("i"), Literal(1, width=32))),
            body=BlockingAssign(
                Identifier("count"),
                BinaryOp("+", Identifier("count"), Literal(1, width=8)),
            ),
            signed_var=True,
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("count") == 4  # i=3,2,1,0 → 4 iterations

    def test_for_signed_var_false_no_early_exit(self, ex, ctx):
        """signed_var=False (default) does not add early exit on unsigned underflow."""
        ctx.write_signal("i", Value(3, width=8))
        stmt = ForLoop(
            init=BlockingAssign(Identifier("i"), Literal(3, width=8)),
            condition=BinaryOp(">", Identifier("i"), Literal(0, width=8)),
            update=BlockingAssign(Identifier("i"), BinaryOp("-", Identifier("i"), Literal(1, width=8))),
            body=BlockingAssign(
                Identifier("count"),
                BinaryOp("+", Identifier("count"), Literal(1, width=8)),
            ),
            signed_var=False,
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("count") == 3

    def test_for_undeclared_variable_gets_rhs_width(self, ex, ctx):
        """When loop variable isn't pre-declared, _write_target creates it with RHS width."""
        # Don't pre-declare "j" — it should be auto-created with 32-bit width
        stmt = ForLoop(
            init=BlockingAssign(Identifier("j"), Literal(0, width=32)),
            condition=BinaryOp("<", Identifier("j"), Literal(5, width=32)),
            update=BlockingAssign(Identifier("j"), BinaryOp("+", Identifier("j"), Literal(1, width=32))),
            body=BlockingAssign(
                Identifier("count"),
                BinaryOp("+", Identifier("count"), Literal(1, width=8)),
            ),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("count") == 5
        assert ctx.read_signal("j") == 5
        assert ctx.read_signal("j").width == 32


# ── While Loop ────────────────────────────────────────────────────────


class TestWhileLoop:
    def test_while(self, ex, ctx):
        """while (count < 3) count = count + 1;"""
        stmt = WhileLoop(
            condition=BinaryOp("<", Identifier("count"), Literal(3, width=8)),
            body=BlockingAssign(
                Identifier("count"),
                BinaryOp("+", Identifier("count"), Literal(1, width=8)),
            ),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("count") == 3

    def test_while_false_initially(self, ex, ctx):
        ctx.write_signal("count", Value(10, width=8))
        stmt = WhileLoop(
            condition=BinaryOp("<", Identifier("count"), Literal(3, width=8)),
            body=BlockingAssign(Identifier("x"), Literal(1, width=8)),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 0  # body never executed


# ── Repeat Loop ───────────────────────────────────────────────────────


class TestRepeatLoop:
    def test_repeat(self, ex, ctx):
        stmt = RepeatLoop(
            count=Literal(5, width=8),
            body=BlockingAssign(
                Identifier("count"),
                BinaryOp("+", Identifier("count"), Literal(1, width=8)),
            ),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("count") == 5


# ── Forever Loop ──────────────────────────────────────────────────────


class TestForeverLoop:
    def test_forever_limit(self, ex, ctx):
        stmt = ForeverLoop(
            body=BlockingAssign(
                Identifier("count"),
                BinaryOp("+", Identifier("count"), Literal(1, width=8)),
            )
        )
        with pytest.raises(RuntimeError, match="exceeded"):
            ex.execute(stmt, ctx)


# ── Sequential Block ─────────────────────────────────────────────────


class TestSeqBlock:
    def test_seq_block(self, ex, ctx):
        block = SeqBlock(
            [
                BlockingAssign(Identifier("x"), Literal(1, width=8)),
                BlockingAssign(Identifier("y"), Literal(2, width=8)),
            ]
        )
        ex.execute(block, ctx)
        assert ctx.read_signal("x") == 1
        assert ctx.read_signal("y") == 2

    def test_empty_block(self, ex, ctx):
        ex.execute(SeqBlock([]), ctx)  # should not raise


# ── Disable Statement ────────────────────────────────────────────────


class TestDisable:
    def test_disable_named_block(self, ex, ctx):
        block = SeqBlock(
            [
                BlockingAssign(Identifier("x"), Literal(1, width=8)),
                DisableStatement("myblock"),
                BlockingAssign(Identifier("x"), Literal(2, width=8)),  # should not execute
            ],
            name="myblock",
        )
        ex.execute(block, ctx)
        assert ctx.read_signal("x") == 1  # stopped after first assign

    def test_disable_propagates(self, ex, ctx):
        """Disable for a non-matching block propagates upward."""
        block = SeqBlock(
            [
                DisableStatement("outer"),
            ],
            name="inner",
        )
        with pytest.raises(DisableBlock):
            ex.execute(block, ctx)


# ── System Tasks ──────────────────────────────────────────────────────


class TestSystemTasks:
    def test_display(self, ex, ctx):
        stmt = SystemTaskCall("$display", [Identifier("a"), Identifier("b")])
        ex.execute(stmt, ctx)
        assert len(ex.display_output) == 1
        assert "5" in ex.display_output[0]
        assert "3" in ex.display_output[0]

    def test_finish(self, ex, ctx):
        stmt = SystemTaskCall("$finish")
        with pytest.raises(StopExecution):
            ex.execute(stmt, ctx)


# ── Event Trigger ─────────────────────────────────────────────────────


class TestEventTrigger:
    def test_event_toggle(self, ex, ctx):
        ctx.write_signal("evt", Value(0, width=1))
        stmt = EventTrigger("evt")
        ex.execute(stmt, ctx)
        assert ctx.read_signal("evt") == 1
        ex.execute(stmt, ctx)
        assert ctx.read_signal("evt") == 0


# ── Delay/Event Control ──────────────────────────────────────────────


class TestSuspension:
    def test_delay_raises_suspend(self, ex, ctx):
        stmt = DelayControl(Literal(10, width=32))
        with pytest.raises(SuspendExecution) as exc_info:
            ex.execute(stmt, ctx)
        assert exc_info.value.delay == 10


# ── LHS Targets ──────────────────────────────────────────────────────


class TestLhsTargets:
    def test_bit_select_lhs(self, ex, ctx):
        ctx.write_signal("bus", Value(0x00, width=8))
        stmt = BlockingAssign(
            BitSelect(Identifier("bus"), Literal(3, width=8)),
            Literal(1, width=1),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("bus").val == 0b00001000

    def test_range_select_lhs(self, ex, ctx):
        ctx.write_signal("bus", Value(0x00, width=8))
        stmt = BlockingAssign(
            RangeSelect(Identifier("bus"), Literal(7, width=8), Literal(4, width=8)),
            Literal(0xF, width=4),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("bus").val == 0xF0

    def test_concatenation_lhs(self, ex, ctx):
        ctx.write_signal("x", Value(0, width=4))
        ctx.write_signal("y", Value(0, width=4))
        stmt = BlockingAssign(
            Concatenation([Identifier("x"), Identifier("y")]),
            Literal(0xAB, width=8),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("x") == 0xA  # upper 4 bits
        assert ctx.read_signal("y") == 0xB  # lower 4 bits

    def test_nba_bit_select(self, ex, ctx):
        ctx.write_signal("bus", Value(0xFF, width=8))
        stmt = NonblockingAssign(
            BitSelect(Identifier("bus"), Literal(0, width=8)),
            Literal(0, width=1),
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("bus").val == 0xFF  # not yet applied
        ex.apply_nba(ctx)
        assert ctx.read_signal("bus").val == 0xFE  # bit 0 cleared


# ── Complex Scenarios ─────────────────────────────────────────────────


class TestComplexScenarios:
    def test_counter_logic(self, ex, ctx):
        """Simulate: if (rst) count = 0; else count = count + 1;"""
        ctx.write_signal("rst", Value(0, width=1))
        ctx.write_signal("count", Value(0, width=8))

        stmt = IfStatement(
            Identifier("rst"),
            BlockingAssign(Identifier("count"), Literal(0, width=8)),
            BlockingAssign(Identifier("count"), BinaryOp("+", Identifier("count"), Literal(1, width=8))),
        )

        # Count up 3 times
        for expected in range(1, 4):
            ex.execute(stmt, ctx)
            assert ctx.read_signal("count") == expected

        # Reset
        ctx.write_signal("rst", Value(1, width=1))
        ex.execute(stmt, ctx)
        assert ctx.read_signal("count") == 0

    def test_mux_case(self, ex, ctx):
        """case (sel) 0: y=a; 1: y=b; 2: y=count; default: y=0; endcase"""
        ctx.write_signal("sel", Value(2, width=2))
        ctx.write_signal("count", Value(42, width=8))

        stmt = CaseStatement(
            "case",
            Identifier("sel"),
            [
                CaseItem([Literal(0, width=2)], BlockingAssign(Identifier("y"), Identifier("a"))),
                CaseItem([Literal(1, width=2)], BlockingAssign(Identifier("y"), Identifier("b"))),
                CaseItem([Literal(2, width=2)], BlockingAssign(Identifier("y"), Identifier("count"))),
                CaseItem(None, BlockingAssign(Identifier("y"), Literal(0, width=8)), is_default=True),
            ],
        )
        ex.execute(stmt, ctx)
        assert ctx.read_signal("y") == 42

    def test_accumulate_loop(self, ex, ctx):
        """sum = 0; for(i=1; i<=5; i=i+1) sum = sum + i;"""
        ctx.write_signal("sum", Value(0, width=16))
        ctx.write_signal("i", Value(0, width=8))

        block = SeqBlock(
            [
                BlockingAssign(Identifier("sum"), Literal(0, width=16)),
                ForLoop(
                    init=BlockingAssign(Identifier("i"), Literal(1, width=8)),
                    condition=BinaryOp("<=", Identifier("i"), Literal(5, width=8)),
                    update=BlockingAssign(Identifier("i"), BinaryOp("+", Identifier("i"), Literal(1, width=8))),
                    body=BlockingAssign(
                        Identifier("sum"),
                        BinaryOp("+", Identifier("sum"), Identifier("i")),
                    ),
                ),
            ]
        )
        ex.execute(block, ctx)
        assert ctx.read_signal("sum") == 15  # 1+2+3+4+5

    def test_pipeline_registers(self, ex, ctx):
        """Non-blocking pipeline: q1<=d; q2<=q1; q3<=q2;"""
        ctx.write_signal("d", Value(0xAA, width=8))
        ctx.write_signal("q1", Value(0x11, width=8))
        ctx.write_signal("q2", Value(0x22, width=8))
        ctx.write_signal("q3", Value(0x33, width=8))

        block = SeqBlock(
            [
                NonblockingAssign(Identifier("q1"), Identifier("d")),
                NonblockingAssign(Identifier("q2"), Identifier("q1")),
                NonblockingAssign(Identifier("q3"), Identifier("q2")),
            ]
        )
        ex.execute(block, ctx)
        ex.apply_nba(ctx)

        # Each register should have the PREVIOUS value of its source
        assert ctx.read_signal("q1") == 0xAA  # d
        assert ctx.read_signal("q2") == 0x11  # old q1
        assert ctx.read_signal("q3") == 0x22  # old q2
