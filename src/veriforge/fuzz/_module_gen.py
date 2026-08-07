"""Module generation strategies and the top-level ModuleGenerator."""

from __future__ import annotations

import random
from collections.abc import Iterable
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
from ..model.ports import PortDirection
from ..model.variables import Variable, VariableKind

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

        # Post-process output port types: if an output is driven ONLY by
        # continuous assigns (not always blocks), drop the "reg" data_type
        # so the emitter produces "output" (wire) instead of "output reg".
        assign_outputs: set[str] = {str(ca.lhs.name) for ca in ctx.continuous_assigns if hasattr(ca.lhs, "name")}
        always_outputs: set[str] = set()
        for ab in ctx.always_blocks:
            for sig in _collect_assigned_signals(ab.body):
                always_outputs.add(sig)

        for port in mod.ports:
            if port.direction == PortDirection.OUTPUT:
                if port.name in assign_outputs and port.name not in always_outputs:
                    port.data_type = None

        # Similarly for internal nets/variables: signals written only by
        # continuous assigns stay as nets (wire); signals written by always
        # blocks need to be variables (reg).  Move them accordingly.
        always_nets: set[str] = set()
        for ab in ctx.always_blocks:
            for sig in _collect_assigned_signals(ab.body):
                always_nets.add(sig)

        for net in list(mod.nets):
            if net.name in always_nets:
                mod.variables.append(Variable(net.name, VariableKind.REG, width=net.width, signed=net.signed))
                mod.nets.remove(net)

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
            # exclude=out.name: a continuous assign reading the same signal
            # it drives (`assign o = f(o);`) is a genuine combinational
            # feedback loop -- simulator-implementation-defined, not a
            # well-defined result.
            rhs = expr_gen.expr(self._rng, depth=self._rng.randint(2, 4), exclude=out.name)
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
        # exclude=out.name: same combinational self-feedback reasoning as
        # `_gen_feedforward` above. (The reg_assignments block above is
        # deliberately NOT excluded: `r <= r + 1`-style sequential self-
        # reference via nonblocking assignment is legitimate, well-defined
        # Verilog, not a race.)
        assigns: list[ContinuousAssign] = []
        for out in ctx.outputs:
            rhs = expr_gen.expr(self._rng, depth=self._rng.randint(2, 4), exclude=out.name)
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

        ctx.always_blocks = self._gen_combinational_always_blocks(ctx, stmt_gen, n_always)

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
        assign_wires = ctx.wires[: self._rng.randint(0, len(ctx.wires))]
        assigns: list[ContinuousAssign] = []
        for wire in assign_wires:
            # exclude=wire.name: same combinational self-feedback reasoning
            # as `_gen_feedforward`.
            rhs = expr_gen.expr(self._rng, depth=self._rng.randint(2, 3), exclude=wire.name)
            assigns.append(ContinuousAssign(wire.as_identifier(), rhs))

        # Some always @* blocks -- excludes every signal already claimed by
        # a continuous assign above, so a wire can never end up BOTH
        # continuously and procedurally assigned (illegal in Verilog:
        # Icarus rejects it as "Cannot perform procedural assignment to
        # variable ... because it is also continuously assigned").
        n_always = self._rng.randint(1, 3)
        already_assigned = {w.name for w in assign_wires}
        always_blocks = self._gen_combinational_always_blocks(ctx, stmt_gen, n_always, exclude=already_assigned)

        ctx.continuous_assigns = assigns
        ctx.always_blocks = always_blocks

    # ------------------------------------------------------------------
    # Shared helper
    # ------------------------------------------------------------------

    def _gen_combinational_always_blocks(
        self,
        ctx: SignalContext,
        stmt_gen: StatementGenerator,
        n_always: int,
        *,
        exclude: Iterable[str] = (),
    ) -> list[AlwaysBlock]:
        """Build *n_always* `always @(*)` blocks, each driving a distinct
        target signal.

        Never lets two blocks (or a block and a name in *exclude*, e.g. a
        signal already driven by a continuous assign) claim the same
        target -- a real multiple-driver conflict whose outcome is
        simulator-implementation-defined at best (two combinational
        processes racing to write the same signal) or an outright illegal
        continuous+procedural conflict at worst. Confirmed as the root
        cause of the one genuine cross-engine (reference vs vm) divergence
        found in an early fuzzer survey, traced back to two `always @(*)`
        blocks both driving the same reg.
        """
        always_blocks: list[AlwaysBlock] = []
        claimed = set(exclude)
        for _ in range(n_always):
            available = [s for s in ctx.all_writable() if s.name not in claimed]
            if not available:
                break
            target = self._rng.choice(available)
            claimed.add(target.name)
            stmt = stmt_gen._assign_to(self._rng, target)
            always_blocks.append(AlwaysBlock(stmt, sensitivity_list=[], sensitivity_type=SensitivityType.COMBINATIONAL))
        return always_blocks


def _collect_assigned_signals(stmt: Statement) -> set[str]:
    """Walk a statement tree and return signal names written via assignment."""
    from ..model.expressions import Identifier

    names: set[str] = set()
    stack: list = [stmt]
    while stack:
        node = stack.pop()
        if isinstance(node, (BlockingAssign, NonblockingAssign)):
            if isinstance(node.lhs, Identifier):
                names.add(node.lhs.name)
        if hasattr(node, "statements"):
            stack.extend(node.statements)
        if hasattr(node, "then_body") and node.then_body is not None:
            stack.append(node.then_body)
        if hasattr(node, "else_body") and node.else_body is not None:
            stack.append(node.else_body)
        if hasattr(node, "body") and node.body is not None:
            stack.append(node.body)
        if hasattr(node, "items"):
            for item in node.items:
                if item.body is not None:
                    stack.append(item.body)
        if hasattr(node, "init"):
            stack.append(node.init)
        if hasattr(node, "update"):
            stack.append(node.update)
    return names
