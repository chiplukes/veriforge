"""Module generation strategies and the top-level ModuleGenerator."""

from __future__ import annotations

import random
from enum import Enum, auto
from typing import Callable

from ..model.behavioral import AlwaysBlock, SensitivityType
from ..model.design import Module
from ..model.expressions import Identifier
from ..model.statements import (
    BlockingAssign,
    NonblockingAssign,
    SensitivityEdge,
    Statement,
)
from ..model.assignments import ContinuousAssign

from ._expression_gen import ExpressionGenerator
from ._signal_context import SignalContext
from ._statement_gen import StatementGenerator


class Strategy(Enum):
    FEEDFORWARD = auto()  # inputs → continuous assigns → outputs
    REGISTERED = auto()  # inputs → regs → combinational → outputs
    MULTI_ALWAYS = auto()  # several always @* blocks
    CLOCKED_SEQUENTIAL = auto()  # clock + always @(posedge clk)
    WITH_FUNCTIONS = auto()  # functions called from expressions (future)
    NESTED_BLOCKS = auto()  # begin/end with local vars
    MIXED = auto()  # random mix

    def __str__(self) -> str:
        return self.name.lower()


_STRATEGY_WEIGHTS: dict[Strategy, float] = {
    Strategy.FEEDFORWARD: 2.0,
    Strategy.REGISTERED: 2.0,
    Strategy.MULTI_ALWAYS: 1.5,
    Strategy.CLOCKED_SEQUENTIAL: 1.5,
    Strategy.NESTED_BLOCKS: 1.0,
    Strategy.MIXED: 1.0,
}


class ModuleGenerator:
    """Orchestrates generation of a complete Verilog module.

    Parameters
    ----------
    rng:
        Pre-seeded random instance — ensures reproducibility.
    """

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, strategy: Strategy | None = None) -> Module:
        """Generate a random module.

        If *strategy* is ``None``, picks one randomly weighted.
        """
        if strategy is None:
            strategy = self._pick_strategy()

        ctx = SignalContext()
        methods: dict[Strategy, Callable[[SignalContext, ExpressionGenerator, StatementGenerator], None]] = {
            Strategy.FEEDFORWARD: self._gen_feedforward,
            Strategy.REGISTERED: self._gen_registered,
            Strategy.MULTI_ALWAYS: self._gen_multi_always,
            Strategy.CLOCKED_SEQUENTIAL: self._gen_clocked_sequential,
            Strategy.NESTED_BLOCKS: self._gen_nested_blocks,
            Strategy.MIXED: self._gen_mixed,
        }

        expr_gen = ExpressionGenerator(ctx)
        stmt_gen = StatementGenerator(ctx, expr_gen)

        # Strategy fills the context with signals and body items
        methods[strategy](ctx, expr_gen, stmt_gen)

        # Assemble the Module
        mod = Module("t")
        for port in ctx.emit_ports(has_clock=strategy in (Strategy.CLOCKED_SEQUENTIAL, Strategy.REGISTERED)):
            mod.ports.append(port)
        for net in ctx.emit_nets():
            mod.nets.append(net)
        for var in ctx.emit_variables():
            mod.variables.append(var)

        # Always blocks and continuous assigns from strategy
        for ab in ctx.always_blocks:
            mod.always_blocks.append(ab)
        for ca in ctx.continuous_assigns:
            mod.continuous_assigns.append(ca)

        return mod

    # ------------------------------------------------------------------
    # Strategy dispatch
    # ------------------------------------------------------------------

    def _pick_strategy(self) -> Strategy:
        names = list(_STRATEGY_WEIGHTS)
        ws = [_STRATEGY_WEIGHTS[s] for s in names]
        return self._rng.choices(names, weights=ws, k=1)[0]

    # ------------------------------------------------------------------
    # Strategy: FEEDFORWARD
    # ------------------------------------------------------------------

    def _gen_feedforward(
        self,
        ctx: SignalContext,
        expr_gen: ExpressionGenerator,
        stmt_gen: StatementGenerator,
    ) -> None:
        """inputs → continuous assigns → outputs.  Simplest strategy."""
        n_inputs = self._rng.randint(2, 6)
        n_outputs = self._rng.randint(1, 3)
        for _ in range(n_inputs):
            ctx.add_input(self._rng)
        for _ in range(n_outputs):
            ctx.add_output(self._rng)

        assigns: list[ContinuousAssign] = []
        for out in ctx.outputs:
            rhs = expr_gen.expr(self._rng, depth=self._rng.randint(2, 4))
            assigns.append(ContinuousAssign(out.as_identifier(), rhs))

        ctx.continuous_assigns = assigns

    # ------------------------------------------------------------------
    # Strategy: REGISTERED
    # ------------------------------------------------------------------

    def _gen_registered(
        self,
        ctx: SignalContext,
        expr_gen: ExpressionGenerator,
        stmt_gen: StatementGenerator,
    ) -> None:
        """inputs → reg cloud → combinational outputs, with clock."""
        ctx.add_clock()
        n_inputs = self._rng.randint(2, 5)
        n_regs = self._rng.randint(2, 4)
        n_outputs = self._rng.randint(1, 3)
        for _ in range(n_inputs):
            ctx.add_input(self._rng)
        for _ in range(n_regs):
            ctx.add_reg(self._rng)
        for _ in range(n_outputs):
            ctx.add_output(self._rng)

        # Register update: always @(posedge clk) reg <= expr(inputs, regs)
        reg_assignments: list[Statement] = []
        for reg in ctx.regs:
            rhs = expr_gen.expr(self._rng, depth=self._rng.randint(2, 4))
            reg_assignments.append(NonblockingAssign(reg.as_identifier(), rhs))

        ab = AlwaysBlock(
            stmt_gen._seq_block_from_stmts(self._rng, reg_assignments),
            sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            sensitivity_type=SensitivityType.SEQUENTIAL,
        )

        # Combinational output: assign o = expr(regs, inputs)
        assigns: list[ContinuousAssign] = []
        for out in ctx.outputs:
            rhs = expr_gen.expr(self._rng, depth=self._rng.randint(2, 4))
            assigns.append(ContinuousAssign(out.as_identifier(), rhs))

        ctx.continuous_assigns = assigns
        ctx.always_blocks = [ab]

    # ------------------------------------------------------------------
    # Strategy: MULTI_ALWAYS
    # ------------------------------------------------------------------

    def _gen_multi_always(
        self,
        ctx: SignalContext,
        expr_gen: ExpressionGenerator,
        stmt_gen: StatementGenerator,
    ) -> None:
        """Multiple always @* blocks sharing internal wires/regs."""
        n_inputs = self._rng.randint(2, 5)
        n_wires = self._rng.randint(1, 3)
        n_regs = self._rng.randint(1, 3)
        n_outputs = self._rng.randint(1, 2)
        n_always = self._rng.randint(2, 4)
        for _ in range(n_inputs):
            ctx.add_input(self._rng)
        for _ in range(n_wires):
            ctx.add_wire(self._rng)
        for _ in range(n_regs):
            ctx.add_reg(self._rng)
        for _ in range(n_outputs):
            ctx.add_output(self._rng)

        always_blocks: list[AlwaysBlock] = []
        writable = ctx.all_writable()
        for _ in range(n_always):
            target = self._rng.choice(writable)
            stmt = stmt_gen._assign_to(self._rng, target)
            ab = AlwaysBlock(
                stmt,
                sensitivity_list=[],
                sensitivity_type=SensitivityType.COMBINATIONAL,
            )
            always_blocks.append(ab)

        ctx.always_blocks = always_blocks

    # ------------------------------------------------------------------
    # Strategy: CLOCKED_SEQUENTIAL
    # ------------------------------------------------------------------

    def _gen_clocked_sequential(
        self,
        ctx: SignalContext,
        expr_gen: ExpressionGenerator,
        stmt_gen: StatementGenerator,
    ) -> None:
        """Always @(posedge clk) with multiple nonblocking assignments."""
        ctx.add_clock()
        n_inputs = self._rng.randint(2, 5)
        n_regs = self._rng.randint(2, 5)
        n_outputs = self._rng.randint(1, 3)
        for _ in range(n_inputs):
            ctx.add_input(self._rng)
        for _ in range(n_regs):
            ctx.add_reg(self._rng)
        for _ in range(n_outputs):
            ctx.add_output(self._rng)

        # All assignments in one posedge clk block
        stmts: list[Statement] = []
        for reg in ctx.regs:
            rhs = expr_gen.expr(self._rng, depth=self._rng.randint(2, 4))
            stmts.append(NonblockingAssign(reg.as_identifier(), rhs))
        for out in ctx.outputs:
            rhs = expr_gen.expr(self._rng, depth=self._rng.randint(2, 4))
            stmts.append(NonblockingAssign(out.as_identifier(), rhs))

        self._rng.shuffle(stmts)
        ab = AlwaysBlock(
            stmt_gen._seq_block_from_stmts(self._rng, stmts),
            sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            sensitivity_type=SensitivityType.SEQUENTIAL,
        )
        ctx.always_blocks = [ab]

    # ------------------------------------------------------------------
    # Strategy: NESTED_BLOCKS
    # ------------------------------------------------------------------

    def _gen_nested_blocks(
        self,
        ctx: SignalContext,
        expr_gen: ExpressionGenerator,
        stmt_gen: StatementGenerator,
    ) -> None:
        """Always @(*) with deeply nested begin/end blocks and local vars."""
        n_inputs = self._rng.randint(2, 4)
        n_wires = self._rng.randint(1, 2)
        n_outputs = self._rng.randint(1, 2)
        for _ in range(n_inputs):
            ctx.add_input(self._rng)
        for _ in range(n_wires):
            ctx.add_wire(self._rng)
        for _ in range(n_outputs):
            ctx.add_output(self._rng)

        stmt = stmt_gen.stmt(self._rng, depth=self._rng.randint(2, 4))
        # Make sure at least one output is assigned
        for out in ctx.outputs:
            assign = stmt_gen._assign_to(self._rng, out)
            stmt = stmt_gen._seq_block_from_stmts(self._rng, [stmt, assign])

        ab = AlwaysBlock(
            stmt,
            sensitivity_list=[],
            sensitivity_type=SensitivityType.COMBINATIONAL,
        )
        ctx.always_blocks = [ab]

    # ------------------------------------------------------------------
    # Strategy: MIXED
    # ------------------------------------------------------------------

    def _gen_mixed(
        self,
        ctx: SignalContext,
        expr_gen: ExpressionGenerator,
        stmt_gen: StatementGenerator,
    ) -> None:
        """Random mix of assigns + always blocks + internal nets/regs."""
        n_inputs = self._rng.randint(2, 5)
        n_wires = self._rng.randint(1, 3)
        n_regs = self._rng.randint(1, 3)
        n_outputs = self._rng.randint(1, 3)
        for _ in range(n_inputs):
            ctx.add_input(self._rng)
        for _ in range(n_wires):
            ctx.add_wire(self._rng)
        for _ in range(n_regs):
            ctx.add_reg(self._rng)
        for _ in range(n_outputs):
            ctx.add_output(self._rng)

        # Some continuous assigns
        assigns: list[ContinuousAssign] = []
        for wire in ctx.wires[: self._rng.randint(0, len(ctx.wires))]:
            rhs = expr_gen.expr(self._rng, depth=self._rng.randint(2, 3))
            assigns.append(ContinuousAssign(wire.as_identifier(), rhs))

        # Some always @* blocks
        always_blocks: list[AlwaysBlock] = []
        writable = ctx.all_writable()
        n_always = self._rng.randint(1, 3)
        for _ in range(n_always):
            target = self._rng.choice(writable)
            stmt = stmt_gen._assign_to(self._rng, target)
            ab = AlwaysBlock(
                stmt,
                sensitivity_list=[],
                sensitivity_type=SensitivityType.COMBINATIONAL,
            )
            always_blocks.append(ab)

        ctx.continuous_assigns = assigns
        ctx.always_blocks = always_blocks
