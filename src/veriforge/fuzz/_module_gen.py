"""Module generation strategies and the top-level ModuleGenerator."""

from __future__ import annotations

import copy

import random
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable

from ..model.behavioral import AlwaysBlock, SensitivityType
from ..model.design import Design, Module
from ..model.functions import FunctionDecl, TaskDecl
from ..model.expressions import (
    BinaryOp,
    BitSelect,
    Concatenation,
    Expression,
    FunctionCall,
    Identifier,
    Literal,
    Range,
    RangeSelect,
    StreamingConcatenation,
    StringLiteral,
    TernaryOp,
    UnaryOp,
    WidthCast,
)
from ..model.instances import Instance, PortConnection
from ..model.statements import (
    BlockingAssign,
    NonblockingAssign,
    SensitivityEdge,
    SeqBlock,
    SystemTaskCall,
    TaskEnable,
    Statement,
)
from ..model.assignments import ContinuousAssign
from ..model.generate import GenerateBlock, GenerateFor
from ..model.nets import Net, NetKind
from ..model.ports import Port, PortDirection
from ..model.variables import INTEGER_ATOM_WIDTHS, Variable, VariableKind

from ._expression_gen import ExpressionGenerator
from ._runner_marks import DISPLAY_MARK
from ._signal_context import Signal, SignalContext
from ._statement_gen import StatementGenerator


_REAL_PROBABILITY = 0.2  # (feedforward designs with `real` variables)


class Strategy(Enum):
    FEEDFORWARD = auto()  # inputs → continuous assigns → outputs
    REGISTERED = auto()  # inputs → regs → combinational → outputs
    MULTI_ALWAYS = auto()  # several always @* blocks
    CLOCKED_SEQUENTIAL = auto()  # clock + always @(posedge clk)
    WITH_FUNCTIONS = auto()  # user functions called from expressions
    NESTED_BLOCKS = auto()  # begin/end with local vars
    MIXED = auto()  # random mix
    HIERARCHICAL = auto()  # child module instance + port connections
    WITH_TASKS = auto()  # tasks with output ports + an impure function, clocked
    MEMORY = auto()  # memories: non-zero/negative bases, both directions, 2-D, wide words

    def __str__(self) -> str:
        return self.name.lower()


_STRATEGY_WEIGHTS: dict[Strategy, float] = {
    Strategy.FEEDFORWARD: 2.0,
    Strategy.REGISTERED: 2.0,
    Strategy.MULTI_ALWAYS: 1.5,
    Strategy.CLOCKED_SEQUENTIAL: 1.5,
    Strategy.WITH_FUNCTIONS: 1.5,
    Strategy.WITH_TASKS: 1.0,
    Strategy.NESTED_BLOCKS: 1.0,
    Strategy.MIXED: 1.0,
    Strategy.HIERARCHICAL: 1.0,
    Strategy.MEMORY: 1.0,
}

# `$display` observation (see `ModuleGenerator._maybe_display`).
_DISPLAY_PROBABILITY = 0.4
# No %e/%f/%g: Icarus converts a partly-x signed value to a double by its
# own rule (bits below the lowest x, then sign-fill), which IEEE leaves
# undefined; those formats have dedicated Icarus-checked tests instead
# (tests/test_sim/test_display_format.py, test_fuzz_findings.py).
_DISPLAY_SPECS = (
    "%d", "%0d", "%h", "%0h", "%b", "%0b", "%o", "%x", "%5d", "%08h", "%3h", "%m",
)  # fmt: skip


def _int_literal(value: int) -> Expression:
    """A plain decimal (`3`, `-2` as unary minus)."""
    if value < 0:
        return UnaryOp("-", Literal(-value, base="d", signed=True, original_text=str(-value)))
    return Literal(value, base="d", signed=True, original_text=str(value))


# Function port/return widths: <= 64 bits, the compiled engine's limit for
# user-function ports/returns (known_issues.md).
_FUNC_WIDTHS = (1, 2, 3, 4, 7, 8, 12, 16, 21, 31, 32, 33, 48, 63, 64)


@dataclass
class _FuncSpec:
    """A generated function, as `ExpressionGenerator._call` needs it."""

    name: str
    arg_widths: list[int]


class ModuleGenerator:
    """Orchestrates generation of a complete Verilog module.

    Parameters
    ----------
    rng:
        Pre-seeded random instance — ensures reproducibility.
    """

    def __init__(self, rng: random.Random, *, memory_in_range: bool = False, x_literals: bool = True) -> None:
        self._rng = rng
        # False: no x literal anywhere -- for a 2-state oracle (Verilator),
        # which reads a design's x constant as an arbitrary value; a 2-state
        # variable or port then turns the reference's x into a "defined" 0.
        self._x_literals = x_literals
        # MEMORY strategy: every index in range, for an oracle whose
        # out-of-range accesses are implementation-defined (Verilator wraps
        # an out-of-range write onto a valid word).
        self._memory_in_range = memory_in_range
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
            Strategy.WITH_FUNCTIONS: self._gen_with_functions,
            Strategy.WITH_TASKS: self._gen_with_tasks,
            Strategy.MEMORY: self._gen_memory,
        }

        expr_gen = ExpressionGenerator(ctx, x_literals=self._x_literals)
        stmt_gen = StatementGenerator(ctx, expr_gen)

        # Strategy fills the context with signals and body items
        methods[strategy](ctx, expr_gen, stmt_gen)
        _ensure_comb_blocks_read_a_signal(ctx)

        # Assemble the Module
        mod = Module(name)
        for port in ctx.emit_ports(has_clock=any(sig.name == "clk" for sig in ctx.inputs)):
            mod.ports.append(port)
        for net in ctx.emit_nets():
            mod.nets.append(net)
        for var in ctx.emit_variables():
            mod.variables.append(var)
        for memory in ctx.memories:
            (mod.nets if isinstance(memory, Net) else mod.variables).append(memory)
        mod.generate_blocks.extend(ctx.generate_blocks)
        for param in ctx.emit_parameters():
            mod.parameters.append(param)
        for inst in ctx.instances:
            mod.instances.append(inst)

        # Always blocks and continuous assigns from strategy
        for ab in ctx.always_blocks:
            mod.always_blocks.append(ab)
        for ca in ctx.continuous_assigns:
            mod.continuous_assigns.append(ca)
        mod.functions.extend(ctx.functions)
        mod.tasks.extend(ctx.tasks)

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
                if port.name in assign_outputs and port.name not in always_outputs and port.data_type == "reg":
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
                # Preserve `logic`-ness across the wire->variable promotion:
                # a `logic` net written by an always block stays `logic`
                # (valid SV -- `logic` is usable both as a net and a
                # variable), it must not silently become plain `reg`.
                var_kind = VariableKind.LOGIC if net.kind == NetKind.LOGIC else VariableKind.REG
                mod.variables.append(Variable(net.name, var_kind, width=net.width, signed=net.signed))
                mod.nets.remove(net)

        if not self._x_literals:
            # No x anywhere (a 2-state oracle): registers start at 0 too.
            # Verilator starts them at 0 where veriforge reads x, and a 2-state
            # variable (`shortint r7 <= f(r5)`) turned that x into a "defined"
            # value Verilator couldn't match.
            zero = Literal(0, width=1, original_text="1'b0")
            for var in mod.variables:
                if var.initial_value is None and not var.dimensions:
                    var.initial_value = zero
            for port in mod.ports:
                if (
                    port.direction == PortDirection.OUTPUT
                    and port.name in always_outputs
                    and port.default_value is None
                ):
                    port.default_value = zero

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
        if self._rng.random() < _REAL_PROBABILITY:
            self._add_reals(ctx)

    def _add_reals(self, ctx: SignalContext) -> None:
        """`real` variables (sim/reals.py) computed in an `always @*` from
        inputs and real literals, rounded into one output. Narrow inputs and
        no division keep every value well inside a double and a 64-bit
        integer, where Icarus, Verilator and the engines agree exactly."""
        rng = self._rng
        inputs = [s for s in ctx.inputs if s.name != "clk" and s.width <= 16]  # noqa: PLR2004
        # (At most 64 bits: Icarus turns a negative real that rounds to 0
        # into garbage in a wider vector -- known_issues.md.)
        outputs = [s for s in ctx.outputs if s.width <= 64]  # noqa: PLR2004
        if not inputs or not outputs:
            return
        names = [f"rv{k}" for k in range(rng.randint(1, 2))]

        def real_literal() -> Literal:
            value = rng.choice((0.5, 1.5, -2.25, 3.0, 0.125, 1000.0, -0.75, 2.5, 1e-3, 7.0))
            return Literal(value, original_text=repr(value))

        def input_operand() -> Expression:
            sig = rng.choice(inputs)
            if not sig.signed:
                return sig.as_identifier()
            # Icarus converts a partly-x *signed* value to real by its own
            # rule (`8'sb1000_00x1` -> -1.0; IEEE and veriforge read x as 0:
            # -127.0 -- known_issues.md), so a signed input goes through a
            # guard: an x input reads 0 everywhere.
            zero = Literal(0, width=sig.width, signed=True, base="d", original_text=f"{sig.width}'sd0")
            return TernaryOp(FunctionCall("$isunknown", [sig.as_identifier()]), zero, sig.as_identifier())

        def operand(earlier: list[str]) -> Expression:
            kind = rng.random()
            if kind < 0.45:  # noqa: PLR2004
                return input_operand()
            if kind < 0.75 or not earlier:  # noqa: PLR2004
                return real_literal()
            return Identifier(rng.choice(earlier))

        def real_expr(earlier: list[str], depth: int) -> Expression:
            if depth == 0:
                return operand(earlier)
            kind = rng.random()
            if kind < 0.15:  # noqa: PLR2004
                return UnaryOp("-", real_expr(earlier, depth - 1))
            if kind < 0.3:  # noqa: PLR2004
                cond = BinaryOp(rng.choice(("<", "<=", ">", ">=", "==", "!=")), operand(earlier), real_literal())
                return TernaryOp(cond, real_expr(earlier, depth - 1), real_expr(earlier, depth - 1))
            # (a product's other factor is a literal: values stay small)
            op = rng.choice(("+", "-", "*", "+", "-"))
            return BinaryOp(op, real_expr(earlier, depth - 1), real_literal() if op == "*" else operand(earlier))

        stmts: list[Statement] = []
        for k, name in enumerate(names):
            ctx.memories.append(Variable(name, VariableKind.REAL))
            rhs = real_expr(names[:k], rng.randint(1, 2))
            if k == 0:
                # (Reading an input: `_ensure_comb_blocks_read_a_signal`
                # would patch in `| (i & 1'b0)`, illegal on a real.)
                rhs = BinaryOp(rng.choice(("+", "-")), rhs, input_operand())
            stmts.append(BlockingAssign(Identifier(name), rhs))
        ctx.always_blocks.append(
            AlwaysBlock(SeqBlock(stmts), sensitivity_list=[], sensitivity_type=SensitivityType.COMBINATIONAL)
        )
        out = rng.choice(outputs)
        rhs = BinaryOp(rng.choice(("+", "-", "*")), Identifier(names[-1]), real_literal())
        ctx.continuous_assigns = [
            ca for ca in ctx.continuous_assigns if not (isinstance(ca.lhs, Identifier) and ca.lhs.name == out.name)
        ]
        ctx.continuous_assigns.append(ContinuousAssign(out.as_identifier(), rhs))

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
        rhss: list[Expression] = []
        for reg in ctx.regs:
            rhs = expr_gen.expr(self._rng, depth=self._rng.randint(2, 4))
            rhss.append(rhs)
            reg_assignments.append(NonblockingAssign(reg.as_identifier(), rhs))
        if len(ctx.regs) >= 2 and self._rng.random() < 0.3:
            # Two of them as one streaming-concatenation target (unpacking,
            # supported since 2026-10-15): `{<<n{ra, rb}} <= T'(e)` -- a
            # source exactly as wide as the targets (Verilator consumes a
            # wider one differently, known_issues.md).
            ra, rb = ctx.regs[0], ctx.regs[1]
            total = ra.width + rb.width
            source = WidthCast(Literal(total, width=32), rhss[0])
            targets: list[Expression] = [ra.as_identifier(), rb.as_identifier()]
            if self._rng.random() < 0.7:
                # (No 65: Verilator unpacked one wrongly -- known_issues.md.)
                size = self._rng.choice((1, 2, 3, 4, 8, 100))
                lhs: Expression = StreamingConcatenation(targets, Literal(size, width=32, base="d", signed=False))
            else:
                lhs = Concatenation(targets, from_streaming=True)
            reg_assignments[:2] = [NonblockingAssign(lhs, source)]

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
        stmts.extend(self._maybe_display(expr_gen))
        ab = AlwaysBlock(
            stmt_gen._seq_block_from_stmts(self._rng, stmts),
            sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            sensitivity_type=SensitivityType.SEQUENTIAL,
        )
        ctx.always_blocks = [ab]

    # ------------------------------------------------------------------
    # Strategy: NESTED_BLOCKS
    # ------------------------------------------------------------------

    def _gen_memory(
        self,
        ctx: SignalContext,
        expr_gen: ExpressionGenerator,
        stmt_gen: StatementGenerator,
    ) -> None:
        """Memories written on posedge clk and read by continuous assigns.
        Dimensions start anywhere (negative too) in either direction, some
        are 2-D, words may be wider than 64 bits or have an offset packed
        range; indices are input slices (in and out of range), signed
        slices, or constants. (The fuzzer had no memories at all: words of
        `m [1:4]` were misaddressed and `m[i][j]` read a bit on every
        engine, unnoticed.)"""
        rng = self._rng
        ctx.add_clock()
        for _ in range(rng.randint(2, 4)):
            ctx.add_input(rng, width=rng.randint(4, 12))
        writes: list[Statement] = []
        reads: list[Expression] = []
        mems: list[tuple[str, int, int, list[tuple[int, int]], Callable[..., Expression]]] = []
        for k in range(rng.randint(1, 2)):
            width = rng.choice((1, 3, 8, 13, 64, 65, 100))
            lsb = rng.choice((0, 0, 0, 4, -2))
            # Signed words, and 2-state integer-atom arrays (`byte m [..]`,
            # signed unless `unsigned`): a signed memory's words read as
            # unsigned on every engine, unnoticed (fixed 2026-10-13).
            atom = rng.choice(("byte", "shortint", "int")) if rng.random() < 0.2 else None
            if atom is not None:
                width, lsb = INTEGER_ATOM_WIDTHS[atom], 0
            signed = rng.random() < (0.8 if atom else 0.35)
            dims: list[tuple[int, int]] = []
            n_dims = rng.choice((1, 1, 2))
            for _ in range(n_dims):
                # (2-D: no negative bounds -- Icarus mishandles an unsigned
                # 32-bit index like `i + -3`, address -3 to Icarus's own 1-D
                # arrays and to Verilator, on a multi-dimensional array.)
                low, size = rng.randint(-3 if n_dims == 1 else 0, 5), rng.randint(2, 5)
                dims.append((low, size))
            name = f"mem{k}"
            ranges = []
            for low, size in dims:
                bounds = (low, low + size - 1) if rng.random() < 0.6 else (low + size - 1, low)
                ranges.append(Range(_int_literal(bounds[0]), _int_literal(bounds[1])))
            packed = Range(_int_literal(lsb + width - 1), _int_literal(lsb)) if width > 1 or lsb else None
            if atom is not None:
                ctx.memories.append(Variable(name, VariableKind(atom), signed=signed, dimensions=ranges))
            else:
                ctx.memories.append(Variable(name, VariableKind.REG, width=packed, signed=signed, dimensions=ranges))

            def element(dims=dims, name=name, *, selected: bool = False, wide_ok: bool = True) -> Expression:
                # Icarus quirks avoided (known_issues.md): with several
                # dimensions it range-checks only the flattened address, so
                # a variable index out of one dimension's range aliases
                # another row -- variable indices stay in range there; and
                # a constant out-of-range word followed by a bit/part select
                # reads the whole element as x -- no such constants then.
                node: Expression = Identifier(name)
                multi = len(dims) > 1
                for low, size in dims:
                    node = BitSelect(
                        node,
                        self._memory_index(ctx, low, size, in_range=multi, const_ok=not selected, wide_ok=wide_ok),
                    )
                return node

            mems.append((name, width, lsb, dims, element))
            for _ in range(rng.randint(2, 4)):
                rhs = expr_gen.expr(rng, depth=rng.randint(1, 3))
                bit = width > 1 and rng.random() < 0.3
                target = element()
                if bit:
                    target = BitSelect(target, _int_literal(lsb + rng.randrange(width)))
                writes.append(NonblockingAssign(target, rhs))
            for _ in range(rng.randint(1, 3)):
                # (Not of a signed word: Icarus makes the select signed --
                # known_issues.md.)
                part = width > 2 and not signed and rng.random() < 0.3
                # (No wide index in a continuous-assign read: Icarus doesn't
                # truncate one there, unlike procedurally and Verilator.)
                read = element(selected=part, wide_ok=False)
                if part:
                    hi = lsb + rng.randrange(1, width)
                    read = RangeSelect(read, _int_literal(hi), _int_literal(rng.randint(lsb, hi)))
                reads.append(read)
        clocked_reads: list[Expression] = []
        self._memory_extras(ctx, expr_gen, mems, writes, reads, clocked_reads)
        for read in clocked_reads:
            # (A function call: read at the clock edge -- a continuous assign
            # re-runs only when the call's arguments change, not the memory.)
            out = ctx.add_output(rng, width=rng.choice((8, 16, 64)))
            writes.append(NonblockingAssign(out.as_identifier(), read))
        ab = AlwaysBlock(
            stmt_gen._seq_block_from_stmts(rng, writes),
            sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            sensitivity_type=SensitivityType.SEQUENTIAL,
        )
        ctx.always_blocks = [ab]
        for read in reads:
            out = ctx.add_output(rng, width=rng.choice((8, 16, 64, 100)))
            if rng.random() < 0.6:
                rhs = read
            else:
                other = expr_gen.expr(rng, depth=1, exclude=out.name)  # (no self-reference)
                rhs = BinaryOp(rng.choice(("+", "^", "&")), read, other)
            ctx.continuous_assigns.append(ContinuousAssign(out.as_identifier(), rhs))

    def _memory_extras(  # noqa: PLR0913
        self,
        ctx: SignalContext,
        expr_gen: ExpressionGenerator,
        mems: list,
        writes: list[Statement],
        reads: list[Expression],
        clocked_reads: list[Expression],
    ) -> None:
        """Memory copies, a task writing a word, a function reading one, and
        a net array driven by a generate loop (none of which the fuzzer
        produced; a function's scalar `input j;` turned out never to bind)."""
        rng = self._rng
        one_d = [m for m in mems if len(m[3]) == 1 and m[1] <= 64]  # (compiled: function/task ports <= 64 bits)
        if len(mems) >= 2 and rng.random() < 0.4:
            (_n0, _w0, _l0, _d0, src), (_n1, _w1, _l1, _d1, dst) = rng.sample(mems, 2)
            writes.append(NonblockingAssign(dst(), src()))
        if one_d and rng.random() < 0.3:
            name, width, _lsb, ((low, size),), _element = rng.choice(one_d)
            bits = max(1, (size - 1).bit_length() if not self._memory_in_range else size.bit_length() - 1)
            idx = Port("i", PortDirection.INPUT, width=Range(Literal(bits - 1), Literal(0)) if bits > 1 else None)
            data = Port("d", PortDirection.INPUT, width=Range(Literal(width - 1), Literal(0)) if width > 1 else None)
            index: Expression = Identifier("i")
            if low:
                index = BinaryOp("+", index, _int_literal(low))
            body = BlockingAssign(BitSelect(Identifier(name), index), Identifier("d"))
            ctx.tasks.append(TaskDecl("tmw", ports=[idx, data], body=SeqBlock([body])))
            source = rng.choice([s for s in ctx.inputs if s.name != "clk"])
            arg = RangeSelect(source.as_identifier(), _int_literal(bits - 1), _int_literal(0))
            writes.insert(rng.randint(0, len(writes)), TaskEnable("tmw", [arg, expr_gen.expr(rng, depth=1)]))
        if one_d and rng.random() < 0.3:
            name, width, _lsb, ((low, size),), _element = rng.choice(one_d)
            bits = max(1, (size - 1).bit_length() if not self._memory_in_range else size.bit_length() - 1)
            idx = Port("i", PortDirection.INPUT, width=Range(Literal(bits - 1), Literal(0)) if bits > 1 else None)
            index = Identifier("i") if not low else BinaryOp("+", Identifier("i"), _int_literal(low))
            # (Sometimes no return range: a 1-bit `function f;` returned 32 bits.)
            ret = Range(Literal(width - 1), Literal(0)) if width > 1 or rng.random() < 0.5 else None
            body = BlockingAssign(Identifier("fmr"), BitSelect(Identifier(name), index))
            ctx.functions.append(FunctionDecl("fmr", return_range=ret, ports=[idx], body=SeqBlock([body])))
            source = rng.choice([s for s in ctx.inputs if s.name != "clk"])
            arg = RangeSelect(source.as_identifier(), _int_literal(bits - 1), _int_literal(0))
            clocked_reads.append(FunctionCall("fmr", [arg]))
        if rng.random() < 0.35:
            width = rng.choice((1, 8, 13, 70))
            # (Non-negative: Icarus sign-extends a negative genvar in an
            # unsigned context, IEEE and Verilator zero-extend it -- see
            # test_functions_indices_genvars.py for negative genvars.)
            low, size = rng.randint(0, 4), rng.randint(2, 4)
            high = low + size - 1
            bounds = (low, high) if rng.random() < 0.6 else (high, low)
            packed = Range(Literal(width - 1), Literal(0)) if width > 1 else None
            ctx.memories.append(
                Net(
                    "nmem",
                    NetKind.WIRE,
                    width=packed,
                    signed=rng.random() < 0.3,
                    dimensions=[Range(_int_literal(bounds[0]), _int_literal(bounds[1]))],
                )
            )
            value = BinaryOp(rng.choice(("^", "+", "&")), expr_gen.expr(rng, depth=1), Identifier("g"))
            items: list = [ContinuousAssign(BitSelect(Identifier("nmem"), Identifier("g")), value)]
            if rng.random() < 0.5:
                # Through a block-local wire of the element's width (the same
                # value: the RHS is truncated to it either way).
                items = [
                    Net("gt", NetKind.WIRE, width=copy.deepcopy(packed)),
                    ContinuousAssign(Identifier("gt"), value),
                    ContinuousAssign(BitSelect(Identifier("nmem"), Identifier("g")), Identifier("gt")),
                ]
            loop = GenerateFor(
                "g",
                _int_literal(low),
                BinaryOp("<=", Identifier("g"), _int_literal(high)),
                BinaryOp("+", Identifier("g"), _int_literal(1)),
                GenerateBlock(name="gnm", items=items),
                genvar_local=True,
            )
            ctx.generate_blocks.append(loop)
            for _ in range(rng.randint(1, 2)):
                reads.append(BitSelect(Identifier("nmem"), self._memory_index(ctx, low, size, wide_ok=False)))

    def _memory_index(  # noqa: PLR0913
        self,
        ctx: SignalContext,
        low: int,
        size: int,
        *,
        in_range: bool = False,
        const_ok: bool = True,
        wide_ok: bool = True,  # (procedural context: wide and signed indices allowed)
    ) -> Expression:
        rng = self._rng
        kind = rng.random()
        in_range = in_range or self._memory_in_range
        if kind < 0.25:
            if in_range or not const_ok:
                return _int_literal(rng.randint(low, low + size - 1))
            return _int_literal(rng.randint(low - 1, low + size))  # sometimes out of range
        source = rng.choice([s for s in ctx.inputs if s.name != "clk"])
        if in_range:
            # An unsigned slice no wider than the dimension, offset to its low end.
            bits = max(1, (size).bit_length() - 1)
            msb = rng.randint(bits - 1, source.width - 1)
            sliced: Expression = RangeSelect(source.as_identifier(), _int_literal(msb), _int_literal(msb - bits + 1))
            if self._memory_in_range and kind > 0.85 and wide_ok:
                top = rng.randrange(1, 32)
                high = Literal(top, width=5, base="d", original_text=f"5'd{top}")
                sliced = Concatenation([high, Literal(0, width=31, base="d", original_text="31'd0"), sliced])
            return sliced if low == 0 else BinaryOp("+", sliced, _int_literal(low))
        bits = rng.randint(1, 3)
        msb = rng.randint(bits - 1, source.width - 1)
        part: Expression = RangeSelect(source.as_identifier(), _int_literal(msb), _int_literal(msb - bits + 1))
        if kind > 0.85 and wide_ok:
            # Wider than 32 bits: only the low 32 count (Icarus, Verilator).
            # (A constant high part: with an x above bit 31, Icarus treats the
            # whole index as x rather than truncating it away.)
            top = rng.randrange(1, 32)
            high = Literal(top, width=5, base="d", original_text=f"5'd{top}")
            zero = Literal(0, width=31, base="d", original_text="31'd0")
            part = Concatenation([high, zero, part])
        # (No signed slices: a negative narrow signed index is out of range
        # per IEEE, and to Icarus in an initial block, but Icarus elsewhere
        # and Verilator read it as unsigned -- an unusable oracle; see
        # test_memory_base_and_multidim.py for signed indices.)
        offset = low + rng.choice((0, 0, -1, 1))
        if offset == 0 and low >= 0:
            # (A bare narrow slice indexing a dimension with negative
            # indices: Icarus reads `3'd7` as -1 there -- known_issues.md.
            # `+ 0` makes it a 32-bit index.)
            return part
        return BinaryOp("+", part, _int_literal(offset))

    def _gen_with_functions(
        self,
        ctx: SignalContext,
        expr_gen: ExpressionGenerator,
        stmt_gen: StatementGenerator,
    ) -> None:
        """1-3 automatic functions (signed/unsigned ports, `integer`/signed
        locals, signed or unsigned returns; later ones may call earlier
        ones), called from the outputs' expressions. Added 2026-10-09: no
        function was ever generated, and signed ports/locals, `integer`
        locals and wider-context use of a call were all broken somewhere."""
        rng = self._rng
        specs: list[_FuncSpec] = []
        for k in range(rng.randint(1, 3)):
            func, spec = self._gen_function(f"f{k + 1}", specs)
            ctx.functions.append(func)
            specs.append(spec)

        for _ in range(rng.randint(2, 5)):
            ctx.add_input(rng)
        for _ in range(rng.randint(1, 3)):
            ctx.add_output(rng, width=rng.choice(_FUNC_WIDTHS))
        ctx.continuous_assigns = [
            ContinuousAssign(
                out.as_identifier(),
                expr_gen.expr(rng, depth=rng.randint(2, 4), callables=specs, exclude=out.name),
            )
            for out in ctx.outputs
        ]

    def _gen_with_tasks(
        self,
        ctx: SignalContext,
        expr_gen: ExpressionGenerator,
        stmt_gen: StatementGenerator,
    ) -> None:
        """``always @(posedge clk)`` calling 1-2 automatic tasks (input and
        output ports, `integer`/signed locals), whose outputs land in
        dedicated regs, plus an impure function that bumps a module-level
        `int` counter. Added 2026-10-10: tasks and side effects were never
        generated (task locals were even dropped by the parser).

        The impure call is a top-level RHS operand, or (since short-circuit
        evaluation was fixed, 2026-10-10) a `?:` branch or the right side of
        `&&`/`||`, where it must run only when that operand is evaluated."""
        rng = self._rng
        ctx.add_clock()
        for _ in range(rng.randint(2, 4)):
            ctx.add_input(rng)
        for _ in range(rng.randint(1, 3)):
            ctx.add_output(rng, width=rng.choice(_FUNC_WIDTHS))

        stmts: list[Statement] = []
        for k in range(rng.randint(1, 2)):
            task, in_widths, out_width = self._gen_task(f"tk{k + 1}")
            ctx.tasks.append(task)
            target = ctx.add_reg(rng, width=out_width, signed=rng.choice([True, False]))
            args = [expr_gen.expr(rng, depth=rng.randint(1, 3)) for _ in in_widths]
            stmts.append(TaskEnable(task.name, [*args, target.as_identifier()]))

        # Impure function: `cnt = cnt + a1; f = <expr>` with an `int` counter
        # (2-state, so it starts at 0 and its value is meaningful).
        counter = Signal(ctx._next_name("r"), 32, True, "reg", var_kind="int")
        ctx._regs.append(counter)
        func, spec = self._gen_function("fimp", [])
        side_effect = BlockingAssign(
            counter.as_identifier(),
            BinaryOp("+", counter.as_identifier(), Identifier(func.ports[0].name)),
        )
        assert isinstance(func.body, SeqBlock)
        func.body.statements.insert(0, side_effect)
        ctx.functions.append(func)

        # The counter isn't read in these statements: the order of the call's
        # side effect against another operand's read is unspecified (IEEE
        # 1364 5.1.2; compiled hoists calls, Icarus/reference go left to
        # right). Later statements (the $display) still read it.
        ctx._regs.remove(counter)
        for out in ctx.outputs:
            rhs = expr_gen.expr(rng, depth=rng.randint(1, 3))
            if rng.random() < 0.5:
                call = FunctionCall("fimp", [expr_gen.expr(rng, depth=1) for _ in spec.arg_widths])
                shape = rng.choice(["op", "op", "ternary", "logical"])
                if shape == "ternary":
                    cond = expr_gen.expr(rng, depth=rng.randint(0, 2))
                    rhs = TernaryOp(cond, call, rhs) if rng.random() < 0.5 else TernaryOp(cond, rhs, call)
                elif shape == "logical":
                    cond = expr_gen.expr(rng, depth=rng.randint(0, 2))
                    rhs = BinaryOp("^", BinaryOp(rng.choice(["&&", "||"]), cond, call), rhs)
                else:
                    rhs = BinaryOp(rng.choice(["+", "^", "-"]), call, rhs)
            stmts.append(NonblockingAssign(out.as_identifier(), rhs))
        ctx._regs.append(counter)
        stmts.extend(self._maybe_display(expr_gen))
        ab = AlwaysBlock(
            stmt_gen._seq_block_from_stmts(rng, stmts),
            sensitivity_list=[SensitivityEdge("posedge", Identifier("clk"))],
            sensitivity_type=SensitivityType.SEQUENTIAL,
        )
        ctx.always_blocks = [ab]

    def _maybe_display(self, expr_gen: ExpressionGenerator) -> list[Statement]:
        """Sometimes a `$display("#D ...")` of random expressions with random
        format specifiers, compared line-for-line by the runner (display
        formatting was never fuzzed before 2026-10-10)."""
        rng = self._rng
        if rng.random() >= _DISPLAY_PROBABILITY:
            return []
        pieces = [DISPLAY_MARK.strip()]
        args: list[Expression] = []
        for _ in range(rng.randint(1, 3)):
            spec = rng.choice(_DISPLAY_SPECS)
            pieces.append(spec)
            if spec in ("%e", "%g", "%0.2f"):
                # A plain signal of <= 53 bits: Icarus formats a signed
                # *expression* as unsigned under the real formats (but a
                # signed signal as signed), and rounds a wider value to a
                # double differently (off by one ulp) -- Icarus quirks, not
                # worth fuzzing.
                narrow = [sig for sig in expr_gen._ctx.all_readable() if sig.width <= 53]
                if not narrow:
                    pieces[-1] = "%d"
                    args.append(expr_gen.expr(rng, depth=rng.randint(0, 2)))
                    continue
                args.append(rng.choice(narrow).as_identifier())
            elif spec != "%m":
                args.append(expr_gen.expr(rng, depth=rng.randint(0, 2)))
        return [SystemTaskCall("$display", [StringLiteral(" ".join(pieces)), *args])]

    def _gen_task(self, name: str) -> tuple[TaskDecl, list[int], int]:
        rng = self._rng
        tctx = SignalContext()
        ports: list[Port] = []
        for k in range(rng.randint(1, 3)):
            sig = Signal(f"a{k + 1}", rng.choice(_FUNC_WIDTHS), rng.choice([True, False]), "input")
            tctx._inputs.append(sig)
            ports.append(sig.as_port(PortDirection.INPUT))
        out_width = rng.choice(_FUNC_WIDTHS)
        out_sig = Signal("q", out_width, rng.choice([True, False]), "output")
        out_port = out_sig.as_port(PortDirection.OUTPUT)
        out_port.data_type = None  # `output [w] q`, not `output reg`
        ports.append(out_port)
        local_sigs: list[Signal] = []
        for k in range(rng.randint(0, 2)):
            if rng.random() < 0.5:
                local_sigs.append(Signal(f"l{k + 1}", 32, True, "reg", var_kind="integer"))
            else:
                local_sigs.append(Signal(f"l{k + 1}", rng.choice(_FUNC_WIDTHS), rng.choice([True, False]), "reg"))
        texpr = ExpressionGenerator(tctx, x_literals=self._x_literals)
        body: list[Statement] = []
        for sig in local_sigs:
            body.append(BlockingAssign(sig.as_identifier(), texpr.expr(rng, depth=rng.randint(1, 3))))
            tctx._regs.append(sig)
        body.append(BlockingAssign(out_sig.as_identifier(), texpr.expr(rng, depth=rng.randint(1, 3))))
        task = TaskDecl(
            name,
            is_automatic=True,
            ports=ports,
            local_vars=[sig.as_variable() for sig in local_sigs],
            body=SeqBlock(body),
        )
        return task, [sig.width for sig in tctx._inputs], out_width

    def _gen_function(self, name: str, earlier: list[_FuncSpec]) -> tuple[FunctionDecl, _FuncSpec]:
        rng = self._rng
        fctx = SignalContext()
        ports: list[Port] = []
        for k in range(rng.randint(1, 3)):
            sig = Signal(f"a{k + 1}", rng.choice(_FUNC_WIDTHS), rng.choice([True, False]), "input")
            fctx._inputs.append(sig)
            ports.append(sig.as_port(PortDirection.INPUT))
        local_sigs: list[Signal] = []
        for k in range(rng.randint(0, 2)):
            if rng.random() < 0.5:
                sig = Signal(f"l{k + 1}", 32, True, "reg", var_kind="integer")
            else:
                sig = Signal(f"l{k + 1}", rng.choice(_FUNC_WIDTHS), rng.choice([True, False]), "reg")
            local_sigs.append(sig)
        fexpr = ExpressionGenerator(fctx, x_literals=self._x_literals)
        body: list[Statement] = []
        for sig in local_sigs:
            body.append(
                BlockingAssign(sig.as_identifier(), fexpr.expr(rng, depth=rng.randint(1, 3), callables=earlier))
            )
            fctx._regs.append(sig)  # readable from the next statement on
        body.append(BlockingAssign(Identifier(name), fexpr.expr(rng, depth=rng.randint(1, 3), callables=earlier)))
        ret_width = rng.choice(_FUNC_WIDTHS)
        func = FunctionDecl(
            name,
            return_range=Range(Literal(ret_width - 1), Literal(0)),
            signed=rng.choice([True, False]),
            is_automatic=True,
            ports=ports,
            local_vars=[sig.as_variable() for sig in local_sigs],
            body=SeqBlock(body),
        )
        return func, _FuncSpec(name, [sig.width for sig in fctx._inputs])

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
        `notes/developer/roadmap.md`): some fraction of the child's input port
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
            elif port.name == "clk":
                # The child's clock comes from a real parent clock the runner
                # toggles. An arbitrary (often constant) expression made a
                # t=0 x->1 "edge" whose firing is a race (Icarus itself
                # fires it in some designs and not others).
                if not any(sig.name == "clk" for sig in ctx.inputs):
                    ctx.add_clock()
                actual = Identifier("clk")
            else:
                # Not reading clk: a port connection is a continuous assign,
                # so a child's clocked block reading one that depends on clk
                # races the clock edge (Icarus runs the block first, veriforge
                # the assign) -- a legitimate IEEE ambiguity, not a bug.
                clocks = [sig for sig in ctx._inputs if sig.name == "clk"]
                ctx._inputs = [sig for sig in ctx._inputs if sig.name != "clk"]
                depth = self._rng.randint(1, 3)
                roll = self._rng.random()
                if roll < 0.30:
                    actual = expr_gen._concat(self._rng, depth, ())
                elif roll < 0.45:
                    actual = expr_gen._streaming_concat(self._rng, depth, ())
                else:
                    actual = expr_gen.expr(self._rng, depth)
                ctx._inputs.extend(clocks)
                # Sized to the port: an input port connection is evaluated in
                # the port's width (IEEE 1364 12.3.9, as Verilator does), but
                # Icarus evaluates it self-determined and pads -- a cast
                # means the same in both.
                port_w, _signed = _port_width_signed(port)
                actual = WidthCast(Literal(port_w, width=32), actual)
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
                    # `exclude=out.name` -- see the identical fix/comment
                    # on the plain-expression branch below (same class of
                    # bug: `out` was already added to the readable pool by
                    # `add_output` above, so without this it could pick
                    # itself, generating `assign o9 = {w7, o9};`).
                    expr_gen._in_concat += 1  # a concat operand: no unsized literal
                    try:
                        parts.append(expr_gen.leaf(self._rng, exclude=out.name))
                    finally:
                        expr_gen._in_concat -= 1
                rhs: Expression = StreamingConcatenation(parts) if self._rng.random() < 0.3 else Concatenation(parts)
            else:
                # `exclude=out.name` matches every other strategy's own
                # write-target exclusion (see `pick_readable`'s docstring)
                # -- without it, `out` (already added to the pool by
                # `add_output` just above) could pick ITSELF as a leaf
                # operand, generating a degenerate `assign o9 = o9;` with
                # no other logic driving it at all. Confirmed via fuzzing:
                # Verilator (correctly) rejects this as unresolvable
                # circular combinational logic ("Wire inputs its own
                # output"), which the fuzzer was then miscounting as a
                # Verilator cross-check mismatch rather than a fuzzer-side
                # generation bug.
                rhs = expr_gen.expr(self._rng, depth=self._rng.randint(1, 3), exclude=out.name)
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


def _ensure_comb_blocks_read_a_signal(ctx: SignalContext) -> None:
    """Make every ``always @(*)`` block read an input. An `@*` block waits
    for its first change (IEEE 1800 9.2.2.2.2; only `always_comb` runs at
    time 0), and whether a continuous assign's time-0 value counts as one is
    a race: Icarus wakes the block on it, veriforge doesn't (known_issues.md,
    "Start-up activation"). So a block whose only changing reads are wires
    that settle at time 0 -- a constant `assign w = 2'b10;`, or a wire whose
    value the vector-0 drive doesn't change -- would differ by design. An
    input always changes at vector 0 (from x), waking it everywhere. The
    patch `rhs | (input & 1'b0)` leaves the value unchanged."""
    inputs = [s for s in ctx.inputs if s.name != "clk"]
    if not inputs:
        return
    live = {s.name for s in inputs}
    for ab in ctx.always_blocks:
        if ab.sensitivity_type != SensitivityType.COMBINATIONAL:
            continue
        written = _collect_assigned_signals(ab.body)
        read = {n.name for n in ab.body.walk() if isinstance(n, Identifier)} - written
        if read & live:
            continue
        for node in ab.body.walk():
            if isinstance(node, BlockingAssign):
                node.rhs = BinaryOp(
                    "|",
                    node.rhs,
                    BinaryOp("&", inputs[0].as_identifier(), Literal(0, width=1, base="b", original_text="1'b0")),
                )
                break
