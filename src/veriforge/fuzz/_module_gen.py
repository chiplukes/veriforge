"""Module generation strategies and the top-level ModuleGenerator."""

from __future__ import annotations

import random
from collections.abc import Iterable
from enum import Enum, auto
from typing import Callable

from ..model.behavioral import AlwaysBlock, SensitivityType
from ..model.design import Design, Module
from ..model.expressions import Concatenation, Expression, Identifier, Literal, StreamingConcatenation
from ..model.instances import Instance, PortConnection
from ..model.statements import (
    BlockingAssign,
    NonblockingAssign,
    SensitivityEdge,
    Statement,
)
from ..model.assignments import ContinuousAssign
from ..model.ports import Port, PortDirection
from ..model.variables import Variable, VariableKind

from ._expression_gen import ExpressionGenerator
from ._signal_context import Signal, SignalContext
from ._statement_gen import StatementGenerator


class Strategy(Enum):
    FEEDFORWARD = auto()  # inputs → continuous assigns → outputs
    REGISTERED = auto()  # inputs → regs → combinational → outputs
    MULTI_ALWAYS = auto()  # several always @* blocks
    CLOCKED_SEQUENTIAL = auto()  # clock + always @(posedge clk)
    WITH_FUNCTIONS = auto()  # functions called from expressions (future)
    NESTED_BLOCKS = auto()  # begin/end with local vars
    MIXED = auto()  # random mix
    HIERARCHICAL = auto()  # child module instance + port connections

    def __str__(self) -> str:
        return self.name.lower()


_STRATEGY_WEIGHTS: dict[Strategy, float] = {
    Strategy.FEEDFORWARD: 2.0,
    Strategy.REGISTERED: 2.0,
    Strategy.MULTI_ALWAYS: 1.5,
    Strategy.CLOCKED_SEQUENTIAL: 1.5,
    Strategy.NESTED_BLOCKS: 1.0,
    Strategy.MIXED: 1.0,
    Strategy.HIERARCHICAL: 1.0,
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
        # Stashed by _gen_hierarchical (side channel, not returned by
        # generate() itself since its signature stays single-Module for
        # every existing caller): generate_design() reads this right after
        # calling generate() to recover the child module when the picked
        # strategy was HIERARCHICAL.
        self._last_child_module: Module | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self, strategy: Strategy | None = None, *, name: str = "t") -> Module:
        """Generate a random module.

        If *strategy* is ``None``, picks one randomly weighted. *name*
        defaults to ``"t"`` (the fuzz runner's hardcoded top-module lookup)
        -- only overridden by ``_gen_hierarchical``'s own recursive call to
        build a distinctly-named child module.
        """
        if strategy is None:
            strategy = self._pick_strategy()

        self._last_child_module = None
        ctx = SignalContext()
        self._gen_params(ctx)
        methods: dict[Strategy, Callable[[SignalContext, ExpressionGenerator, StatementGenerator], None]] = {
            Strategy.FEEDFORWARD: self._gen_feedforward,
            Strategy.REGISTERED: self._gen_registered,
            Strategy.MULTI_ALWAYS: self._gen_multi_always,
            Strategy.CLOCKED_SEQUENTIAL: self._gen_clocked_sequential,
            Strategy.NESTED_BLOCKS: self._gen_nested_blocks,
            Strategy.MIXED: self._gen_mixed,
            Strategy.HIERARCHICAL: self._gen_hierarchical,
        }

        expr_gen = ExpressionGenerator(ctx)
        stmt_gen = StatementGenerator(ctx, expr_gen)

        # Strategy fills the context with signals and body items
        methods[strategy](ctx, expr_gen, stmt_gen)

        # Assemble the Module
        mod = Module(name)
        for port in ctx.emit_ports(has_clock=strategy in (Strategy.CLOCKED_SEQUENTIAL, Strategy.REGISTERED)):
            mod.ports.append(port)
        for net in ctx.emit_nets():
            mod.nets.append(net)
        for var in ctx.emit_variables():
            mod.variables.append(var)
        for param in ctx.emit_parameters():
            mod.parameters.append(param)
        for inst in ctx.instances:
            mod.instances.append(inst)

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

    def generate_design(self, strategy: Strategy | None = None) -> Design:
        """Generate a full `Design` -- single source of truth for "what got
        generated", used uniformly by callers that need to emit/simulate the
        result (which always requires the child module's own definition
        too, once one exists).

        `Design(modules=[mod])` for every ordinary (single-module)
        strategy; `Design(modules=[child, parent])` when the picked
        strategy was `HIERARCHICAL` (detected via `_last_child_module`,
        stashed by `_gen_hierarchical` during the `generate()` call just
        below -- see its own docstring). `generate()` itself deliberately
        keeps returning a single `Module` so existing callers/tests that
        use it directly are unaffected.
        """
        mod = self.generate(strategy)
        if self._last_child_module is not None:
            return Design(modules=[self._last_child_module, mod])
        return Design(modules=[mod])

    # ------------------------------------------------------------------
    # Strategy dispatch
    # ------------------------------------------------------------------

    def _pick_strategy(self) -> Strategy:
        names = list(_STRATEGY_WEIGHTS)
        ws = [_STRATEGY_WEIGHTS[s] for s in names]
        return self._rng.choices(names, weights=ws, k=1)[0]

    def _gen_params(self, ctx: SignalContext) -> None:
        """Declare 0-3 module-level parameters before any other generation.

        Shared across every strategy (called once, up front) rather than
        being strategy-specific: `SignalContext.all_readable()` already
        includes declared parameters in its leaf pool, so plain expressions,
        concats, and streaming-concats all start drawing from them for free
        once they exist -- no per-strategy wiring needed.
        """
        n_params = self._rng.randint(0, 3)
        for _ in range(n_params):
            width = ctx.pick_width(self._rng)
            signed = self._rng.choice([True, False])
            value = self._rng.randint(0, (1 << width) - 1)
            ctx.add_param(ctx._next_name("p"), width, signed, value)

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
    # Strategy: HIERARCHICAL
    # ------------------------------------------------------------------

    def _gen_hierarchical(
        self,
        ctx: SignalContext,
        expr_gen: ExpressionGenerator,
        stmt_gen: StatementGenerator,
    ) -> None:
        """Child module (built from a flat strategy) + parent module +
        `Instance` connecting them.

        Targets the "wide concatenation feeding a module port" shape that
        caused several real compiled-engine bugs this session
        (`axis_regslice.v`'s `{tuser,tlast,tdata}` skid buffer, see
        `notes/roadmap.md`): some fraction of the child's input port
        connections are forced to be a `Concatenation`/`StreamingConcatenation`
        built from the parent's own signals, and some fraction of the
        parent's own outputs are woven from the instance's output wires the
        same way ("concat *out of* a port").

        No width-matching is forced on port-connection actuals beyond the
        output side (which Verilog requires to bind to a net) -- Verilog's
        own implicit truncation/extension at a port connection is exactly
        the same as at any continuous-assign RHS, and every other strategy
        here already leaves plain assign RHS width totally unconstrained
        the same way, so this stays consistent with the rest of the file
        rather than introducing an extra, inconsistent rule just for ports.

        Non-goals for v1 (deliberately out of scope, keeps this bounded):
        parameterized/overridden port widths on the instantiation; nested
        hierarchy beyond one level (the child is always built from a flat,
        non-`HIERARCHICAL` strategy); named vs. positional connection style
        isn't a controlled axis (always named here, for readability).
        """
        # 1. Child module: any flat strategy, fresh SignalContext, distinct name.
        child_strategies = [s for s in _STRATEGY_WEIGHTS if s != Strategy.HIERARCHICAL]
        child_strategy = self._rng.choice(child_strategies)
        child = self.generate(strategy=child_strategy, name="c")
        self._last_child_module = child

        # 2. Parent-side signals to seed port-actual expressions with, plus
        # the instance itself: fresh output wires for every child output
        # port (required -- an output port must bind to a net, never a reg
        # or arbitrary expression), and a port-actual expression -- some
        # fraction of the time forced to a concat/streaming-concat -- for
        # every child input port.
        n_pre_inputs = self._rng.randint(1, 4)
        for _ in range(n_pre_inputs):
            ctx.add_input(self._rng)

        port_connections: list[PortConnection] = []
        inst_output_wires: list[Signal] = []
        for port in child.ports:
            if port.direction == PortDirection.OUTPUT:
                width, signed = _port_width_signed(port)
                w = ctx.add_wire(self._rng, width=width, signed=signed)
                inst_output_wires.append(w)
                actual: Expression = w.as_identifier()
            else:
                depth = self._rng.randint(1, 3)
                roll = self._rng.random()
                if roll < 0.30:
                    actual = expr_gen._concat(self._rng, depth, ())
                elif roll < 0.45:
                    actual = expr_gen._streaming_concat(self._rng, depth, ())
                else:
                    actual = expr_gen.expr(self._rng, depth)
            port_connections.append(PortConnection(port_name=port.name, expression=actual, is_named=True))

        inst = Instance(child.name, ctx._next_name("u"), port_connections=port_connections)
        ctx.instances.append(inst)

        # 3. Parent's own outputs -- some fraction woven from the instance's
        # output wires via concat/streaming-concat ("concat *out of* a
        # port"); otherwise a plain expression, for baseline coverage.
        n_outputs = self._rng.randint(1, 2)
        assigns: list[ContinuousAssign] = []
        for _ in range(n_outputs):
            out = ctx.add_output(self._rng)
            if inst_output_wires and self._rng.random() < 0.5:
                parts: list[Expression] = [w.as_identifier() for w in inst_output_wires]
                if len(parts) == 1:
                    parts.append(expr_gen.leaf(self._rng))
                rhs: Expression = StreamingConcatenation(parts) if self._rng.random() < 0.3 else Concatenation(parts)
            else:
                rhs = expr_gen.expr(self._rng, depth=self._rng.randint(1, 3))
            assigns.append(ContinuousAssign(out.as_identifier(), rhs))
        ctx.continuous_assigns = assigns

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


def _port_width_signed(port: Port) -> tuple[int, bool]:
    """Recover a port's (width, signed) as plain Python values.

    `Port.width` is a `Range` of `Expression`s (or `None` for an implied
    1-bit port), not an int -- but every port this function is ever called
    on was just built moments ago by `Signal.as_port()` from this same
    module (`Signal.as_range()` always emits `Range(Literal(width - 1),
    Literal(0))`), so a literal `Range` is the only shape that can actually
    occur here. Falls back to 1-bit unsigned rather than raising if that
    ever stops being true (e.g. a future caller reuses this against a
    hand-written or parsed module with a non-literal width).
    """
    if port.width is None:
        return 1, port.signed
    msb, lsb = port.width.msb, port.width.lsb
    if isinstance(msb, Literal) and isinstance(lsb, Literal):
        return abs(int(msb.value) - int(lsb.value)) + 1, port.signed
    return 1, port.signed


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
