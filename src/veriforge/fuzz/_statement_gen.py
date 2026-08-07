"""Statement generator — produces Statement model objects randomly.

Mirrors test_differential_statements.py's ``_gen_stmt()`` but produces
typed ``Statement`` model-object trees instead of Verilog text strings.
"""

from __future__ import annotations

import random

from ..model.expressions import BinaryOp, Identifier, Literal
from ..model.statements import (
    BlockingAssign,
    CaseItem,
    CaseStatement,
    ForLoop,
    IfStatement,
    NonblockingAssign,
    SeqBlock,
    Statement,
    WhileLoop,
)
from ..model.variables import Variable, VariableKind

from ._expression_gen import ExpressionGenerator
from ._signal_context import SignalContext

_STMT_KINDS = ("leaf", "if_chain", "case_stmt", "for_loop", "while_loop", "seq_block")


class StatementGenerator:
    """Generates random ``Statement`` trees over a ``SignalContext``."""

    def __init__(self, ctx: SignalContext, expr_gen: ExpressionGenerator) -> None:
        self._ctx = ctx
        self._expr = expr_gen
        self._loop_counter = 0
        self._block_counter = 0

    # ------------------------------------------------------------------
    # Main generator
    # ------------------------------------------------------------------

    def stmt(self, rng: random.Random, depth: int) -> Statement:
        """Generate a random statement of up to *depth* nesting.

        At depth 0 (or sometimes randomly earlier), always returns a leaf
        assignment.
        """
        if depth <= 0:
            return self._leaf_assign(rng)

        kind = rng.choice(_STMT_KINDS)
        if kind == "leaf":
            return self._leaf_assign(rng)
        if kind == "if_chain":
            return self._if_chain(rng, depth)
        if kind == "case_stmt":
            return self._case_stmt(rng, depth)
        if kind == "for_loop":
            return self._for_loop(rng, depth)
        if kind == "while_loop":
            return self._while_loop(rng, depth)
        if kind == "seq_block":
            return self._seq_block(rng, depth)
        raise AssertionError(f"unknown kind: {kind}")

    # ------------------------------------------------------------------
    # Leaf assignment
    # ------------------------------------------------------------------

    def _leaf_assign(self, rng: random.Random) -> Statement:
        target = self._ctx.pick_writable(rng)
        # `exclude=target.name`: an RHS that directly reads the same signal
        # it's writing (e.g. `o <= f(o);`) forms a combinational loop whose
        # outcome is simulator-implementation-defined, not well-defined --
        # a real source of cross-engine/Icarus mismatches, not a bug in any
        # one engine.
        rhs = self._expr.expr(rng, depth=rng.randint(1, 4), exclude=target.name)
        if rng.random() < 0.5:
            return BlockingAssign(target.as_identifier(), rhs)
        return NonblockingAssign(target.as_identifier(), rhs)

    def _assign_to(self, rng: random.Random, target_signal) -> Statement:
        """Generate an assignment with RHS constrained, writing to a specific signal."""
        # Same self-reference reasoning as `_leaf_assign` above.
        rhs = self._expr.expr(rng, depth=rng.randint(1, 3), exclude=target_signal.name)
        if rng.random() < 0.5:
            return BlockingAssign(target_signal.as_identifier(), rhs)
        return NonblockingAssign(target_signal.as_identifier(), rhs)

    # ------------------------------------------------------------------
    # If / else-if chain
    # ------------------------------------------------------------------

    def _if_chain(self, rng: random.Random, depth: int) -> IfStatement:
        n = rng.choice((1, 2, 3))  # number of else-if + possible else
        inner = self._build_if_chain(rng, depth - 1, n)
        if inner is None:
            return IfStatement(Literal(1), self._leaf_assign(rng))
        return inner

    def _build_if_chain(self, rng: random.Random, depth: int, remaining: int) -> IfStatement | None:
        if remaining <= 0:
            return None
        cond = self._expr.expr(rng, depth=min(depth, 2))
        then_body = self.stmt(rng, depth - 1) if depth > 0 else self._leaf_assign(rng)

        # Decide whether this branch gets an else
        has_else = remaining == 1 and rng.random() < 0.6
        if has_else:
            else_body = self.stmt(rng, depth - 1) if depth > 0 else self._leaf_assign(rng)
            return IfStatement(cond, then_body, else_body)

        if remaining > 1:
            # Continue chain with else-if
            next_body = self._build_if_chain(rng, depth, remaining - 1)
            if next_body:
                return IfStatement(cond, then_body, next_body)

        return IfStatement(cond, then_body)

    # ------------------------------------------------------------------
    # Case statement
    # ------------------------------------------------------------------

    def _case_stmt(self, rng: random.Random, depth: int) -> CaseStatement:
        case_type = rng.choice(("case", "casex", "casez"))
        sel_signal = self._ctx.pick_readable(rng)
        sel_expr = sel_signal.as_identifier()
        sel_width = sel_signal.width

        n_items = rng.randint(2, 4)
        items: list[CaseItem] = []
        for _ in range(n_items):
            lit = self._gen_case_literal(rng, sel_width)
            body = self.stmt(rng, depth - 1) if depth > 0 else self._leaf_assign(rng)
            items.append(CaseItem([lit], body))

        # Always add a default
        items.append(CaseItem([], self._leaf_assign(rng), is_default=True))

        return CaseStatement(case_type, sel_expr, items)

    @staticmethod
    def _gen_case_literal(rng: random.Random, sel_width: int) -> Literal:
        """Generate a literal suitable for a case comparison.

        May deliberately use a different width than the selector to exercise
        width mismatches in case comparison (as in test_differential_statements.py).
        """
        if rng.random() < 0.3:
            w_use = sel_width
        else:
            w_use = rng.choice([sel_width, max(1, sel_width - 1), sel_width + 1, rng.randint(1, sel_width + 4)])

        mask = (1 << max(w_use, 1)) - 1
        val = rng.randint(0, mask)

        if rng.random() < 0.2:
            literal = Literal(val, width=w_use, base="b", signed=False)
        else:
            literal = Literal(val, width=w_use, base="d", signed=False)
        return literal

    # ------------------------------------------------------------------
    # For loop
    # ------------------------------------------------------------------

    def _for_loop(self, rng: random.Random, depth: int) -> ForLoop:
        self._loop_counter += 1
        var_name = f"lv{self._loop_counter}"
        loop_signal = self._ctx.add_reg(rng, width=32, signed=True)
        loop_signal.name = var_name

        start = rng.choice((0, 1, 2))
        end = start + rng.choice((4, 8, 16))

        init = BlockingAssign(
            Identifier(var_name),
            Literal(start),
        )
        cond = BinaryOp("<", Identifier(var_name), Literal(end))
        update = BlockingAssign(
            Identifier(var_name),
            BinaryOp("+", Identifier(var_name), Literal(1)),
        )

        # Reserve the loop's own control variable while generating the body:
        # otherwise the randomly generated body could itself pick `var_name`
        # as an assignment target (indistinguishable from any other reg to
        # the statement generator), clobbering the counter and turning a
        # bounded loop into an unbounded one -- confirmed as the cause of
        # observed "loop exceeded 100000 iterations" hangs.
        with self._ctx.reserve(var_name):
            body = self.stmt(rng, depth - 1) if depth > 0 else self._leaf_assign(rng)
        return ForLoop(init, cond, update, body, declares_var=True, signed_var=True)

    # ------------------------------------------------------------------
    # While loop
    # ------------------------------------------------------------------

    def _while_loop(self, rng: random.Random, depth: int) -> SeqBlock:
        self._loop_counter += 1
        ctrl_signal = self._ctx.add_reg(rng, width=8, signed=False)
        ctrl_signal.name = f"wc{self._loop_counter}"
        max_val = rng.choice((3, 5, 8))

        # Same reservation reasoning as `_for_loop` above.
        with self._ctx.reserve(ctrl_signal.name):
            body = self.stmt(rng, depth - 1) if depth > 0 else self._leaf_assign(rng)
        incr = BlockingAssign(
            ctrl_signal.as_identifier(),
            BinaryOp("+", ctrl_signal.as_identifier(), Literal(1)),
        )

        return SeqBlock(
            [
                BlockingAssign(ctrl_signal.as_identifier(), Literal(0)),
                WhileLoop(
                    BinaryOp("<", ctrl_signal.as_identifier(), Literal(max_val, width=8)),
                    SeqBlock([body, incr]),
                ),
            ],
        )

    # ------------------------------------------------------------------
    # Sequential block
    # ------------------------------------------------------------------

    def _seq_block(self, rng: random.Random, depth: int) -> SeqBlock:
        self._block_counter += 1
        n_stmts = rng.randint(2, 4)
        stmts: list[Statement] = []
        with self._ctx.scope():
            for _ in range(n_stmts):
                stmts.append(self.stmt(rng, depth - 1))
        return SeqBlock(stmts)

    def _seq_block_from_stmts(self, rng: random.Random, stmts: list[Statement]) -> SeqBlock:
        """Wrap a list of statements in a SeqBlock.

        Used by ModuleGenerator strategies to wrap flat lists of assignments
        in a begin/end block for always blocks.
        """
        if len(stmts) == 1:
            return stmts[0] if isinstance(stmts[0], SeqBlock) else SeqBlock(stmts)
        return SeqBlock(stmts)
