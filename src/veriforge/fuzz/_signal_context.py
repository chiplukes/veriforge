"""Signal context — tracks available signals during module generation."""

from __future__ import annotations

import random
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..model.expressions import Identifier, Literal, Range
from ..model.nets import Net, NetKind
from ..model.parameters import Parameter
from ..model.ports import Port, PortDirection
from ..model.variables import Variable, VariableKind

# Widths biased towards edge cases for better bug finding.
_EDGE_WIDTHS = [1, 2, 3, 7, 8, 15, 16, 31, 32, 63, 64, 65, 80, 127, 128]
_NAME_POOL = [chr(ord("a") + i) for i in range(26)]  # a-z


@dataclass
class Signal:
    """A named signal with width and signedness metadata."""

    name: str
    width: int
    signed: bool
    kind: str  # "input", "output", "wire", "reg", "local", "parameter"
    value: int | None = None  # constant value, "parameter" kind only

    def as_identifier(self) -> Identifier:
        return Identifier(self.name)

    def as_range(self) -> Range:
        return Range(Literal(self.width - 1), Literal(0))

    def as_port(self, direction: PortDirection) -> Port:
        return Port(
            self.name,
            direction,
            width=self.as_range(),
            signed=self.signed,
            net_type=None,
            data_type="reg" if direction == PortDirection.OUTPUT else None,
        )

    def as_net(self) -> Net:
        return Net(self.name, kind=NetKind.WIRE, width=self.as_range(), signed=self.signed)

    def as_variable(self) -> Variable:
        kind = VariableKind.REG if self.kind in ("reg", "local") else VariableKind.LOGIC
        return Variable(self.name, kind=kind, width=self.as_range(), signed=self.signed)

    def as_parameter(self) -> Parameter:
        return Parameter(
            self.name,
            width=self.as_range(),
            signed=self.signed,
            default_value=Literal(self.value or 0, width=self.width, base="d", signed=self.signed),
        )


class SignalContext:
    """Manages the pool of available signals during module generation.

    Provides scoped access (``push_scope``/``pop_scope``) for local variables
    within ``begin...end`` blocks, and helpers to pick readable/writable
    signals from the current context.
    """

    def __init__(self) -> None:
        self._inputs: list[Signal] = []
        self._outputs: list[Signal] = []
        self._wires: list[Signal] = []
        self._regs: list[Signal] = []
        self._params: list[Signal] = []
        self._scopes: list[list[Signal]] = [[]]  # stack of scoped locals
        self._counter = 0
        # Names temporarily excluded from all_writable()/pick_writable() --
        # e.g. a for/while loop's own control variable while generating its
        # body, so the randomly generated body can't clobber the counter and
        # turn the loop unbounded.
        self._reserved: set[str] = set()
        # Direct combinational dependency edges: target -> signals directly
        # read while computing target's value, across EVERY continuous
        # assign / always @(*) block in the module (module-wide, not
        # per-block -- two separate always @(*) blocks each driving a
        # different signal can still form a cycle between them). Populated
        # incrementally by `pick_readable` whenever it's called with
        # `exclude` set (which every combinational call site does -- see
        # `record_comb_dep`'s docstring). Deliberately NOT populated for
        # `always @(posedge clk)` reads, where self/mutual reference via
        # nonblocking assignment (`r <= r + 1`, `a <= b; b <= a;`) is
        # legitimate, well-defined Verilog, not a race.
        self._comb_adj: dict[str, set[str]] = {}
        # Populated by ModuleGenerator strategies after assembly
        self.always_blocks: list = []
        self.continuous_assigns: list = []
        self.instances: list = []

    # ------------------------------------------------------------------
    # Signal creation
    # ------------------------------------------------------------------

    def _next_name(self, prefix: str = "s") -> str:
        self._counter += 1
        return f"{prefix}{self._counter}"

    @staticmethod
    def pick_width(rng: random.Random) -> int:
        """Return a random width biased toward edge cases."""
        if rng.random() < 0.6:
            return rng.choice(_EDGE_WIDTHS)
        return rng.randint(1, 128)

    def add_input(self, rng: random.Random, *, width: int | None = None, signed: bool | None = None) -> Signal:
        s = Signal(
            name=self._next_name("i"),
            width=width if width is not None else self.pick_width(rng),
            signed=signed if signed is not None else rng.choice([True, False]),
            kind="input",
        )
        self._inputs.append(s)
        return s

    def add_output(self, rng: random.Random, *, width: int | None = None, signed: bool | None = None) -> Signal:
        s = Signal(
            name=self._next_name("o"),
            width=width if width is not None else self.pick_width(rng),
            signed=signed if signed is not None else rng.choice([True, False]),
            kind="output",
        )
        self._outputs.append(s)
        return s

    def add_wire(self, rng: random.Random, *, width: int | None = None, signed: bool | None = None) -> Signal:
        s = Signal(
            name=self._next_name("w"),
            width=width if width is not None else self.pick_width(rng),
            signed=signed if signed is not None else rng.choice([True, False]),
            kind="wire",
        )
        self._wires.append(s)
        return s

    def add_reg(self, rng: random.Random, *, width: int | None = None, signed: bool | None = None) -> Signal:
        s = Signal(
            name=self._next_name("r"),
            width=width if width is not None else self.pick_width(rng),
            signed=signed if signed is not None else rng.choice([True, False]),
            kind="reg",
        )
        self._regs.append(s)
        return s

    def add_local(self, rng: random.Random, *, width: int | None = None, signed: bool | None = None) -> Signal:
        s = Signal(
            name=self._next_name("t"),
            width=width if width is not None else self.pick_width(rng),
            signed=signed if signed is not None else rng.choice([True, False]),
            kind="local",
        )
        self._scopes[-1].append(s)
        return s

    def add_param(self, name: str, width: int, signed: bool, value: int) -> Signal:
        s = Signal(name=name, width=width, signed=signed, kind="parameter", value=value)
        self._params.append(s)
        return s

    def add_clock(self) -> Signal:
        s = Signal(name="clk", width=1, signed=False, kind="input")
        self._inputs.append(s)
        return s

    def add_reset(self) -> Signal:
        s = Signal(name="rst_n", width=1, signed=False, kind="input")
        self._inputs.append(s)
        return s

    # ------------------------------------------------------------------
    # Scope management
    # ------------------------------------------------------------------

    @contextmanager
    def scope(self) -> Generator[SignalContext, None, None]:
        self._scopes.append([])
        try:
            yield self
        finally:
            self._scopes.pop()

    @contextmanager
    def reserve(self, *names: str) -> Generator[SignalContext, None, None]:
        """Temporarily exclude *names* from all_writable()/pick_writable().

        Used while generating a for/while loop's own body so the randomly
        generated body can't pick the loop's own control variable as an
        assignment target -- clobbering it and turning a bounded loop into
        an unbounded one.
        """
        self._reserved.update(names)
        try:
            yield self
        finally:
            self._reserved.difference_update(names)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def inputs(self) -> list[Signal]:
        return list(self._inputs)

    @property
    def outputs(self) -> list[Signal]:
        return list(self._outputs)

    @property
    def wires(self) -> list[Signal]:
        return list(self._wires)

    @property
    def regs(self) -> list[Signal]:
        return list(self._regs)

    @property
    def locals(self) -> list[Signal]:
        result: list[Signal] = []
        for scope in self._scopes:
            result.extend(scope)
        return result

    @property
    def parameters(self) -> list[Signal]:
        return list(self._params)

    def all_readable(self) -> list[Signal]:
        """Every signal that can appear in an expression RHS."""
        return [
            *self._inputs,
            *self._wires,
            *self._regs,
            *self._outputs,
            *self.locals,
            *self._params,
        ]

    def all_writable(self) -> list[Signal]:
        """Every signal that can appear on the LHS of an assignment.

        Excludes anything currently `reserve()`d (e.g. an enclosing loop's
        own control variable).
        """
        return [s for s in (*self._wires, *self._regs, *self._outputs, *self.locals) if s.name not in self._reserved]

    # ------------------------------------------------------------------
    # Random selection
    # ------------------------------------------------------------------

    def pick_readable(self, rng: random.Random, *, exclude: str | None = None) -> Signal:
        """Pick a random readable signal for use as an expression leaf.

        *exclude*, when set, is the signal name of the combinational
        assignment (continuous assign or `always @(*)`) currently being
        built for -- every current call site passes its own write target
        here. Two things happen:

        1. That one signal is dropped from the pool -- a same-statement
           self-reference (`o <= f(o);`) forms a combinational loop with
           simulator-implementation-defined behavior, not a well-defined
           result.
        2. Any OTHER signal that would close a longer combinational cycle
           back to *exclude* (directly or transitively, and possibly
           through an entirely different always @(*) block -- e.g. block A
           driving `x` from `y` while block B drives `y` from `x`) is also
           dropped. Confirmed as a real, not just theoretical, cause of
           Icarus (and occasionally our own engines') "unbounded
           simulation loop" hangs from this fuzzer.

        The chosen signal's dependency edge is then recorded via
        `record_comb_dep` so later picks (in this expression, this
        statement, or any later statement/block in the module) see it.

        Pass ``exclude=None`` (the default) for a genuinely sequential read
        (`always @(posedge clk)`), where self/mutual reference via
        nonblocking assignment is legitimate and must NOT be restricted or
        recorded into the combinational dependency graph.
        """
        if exclude:
            pool = [s for s in self.all_readable() if s.name != exclude and not self.comb_reaches(s.name, exclude)]
        else:
            pool = self.all_readable()
        if not pool:
            pool = [s for s in self._inputs if s.name != exclude] if exclude else self._inputs  # fallback
        picked = self.add_input(rng) if not pool else rng.choice(pool)  # create one as last resort
        if exclude:
            self.record_comb_dep(exclude, picked.name)
        return picked

    # ------------------------------------------------------------------
    # Combinational dependency graph (cycle avoidance)
    # ------------------------------------------------------------------

    def record_comb_dep(self, target: str, read_name: str) -> None:
        """Record that *target*'s combinational value directly reads *read_name*."""
        self._comb_adj.setdefault(target, set()).add(read_name)

    def comb_reaches(self, start: str, goal: str) -> bool:
        """True if *goal* is reachable from *start* via recorded direct
        combinational dependency edges, i.e. *start* already (directly or
        transitively) depends on *goal*'s value.

        Module-sized graphs here are tiny (a handful of signals), so a
        fresh traversal per query is simpler and plenty fast -- no need
        for incremental transitive-closure bookkeeping.
        """
        seen: set[str] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node == goal:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(self._comb_adj.get(node, ()))
        return False

    def pick_writable(self, rng: random.Random) -> Signal:
        """Pick a random writable signal for use as an assignment target."""
        pool = self.all_writable()
        if not pool:
            return self.add_wire(rng)
        return rng.choice(pool)

    # ------------------------------------------------------------------
    # Model object emission
    # ------------------------------------------------------------------

    def emit_ports(self, has_clock: bool = False) -> list[Port]:
        result: list[Port] = [s.as_port(PortDirection.INPUT) for s in self._inputs]
        if has_clock:
            for s in self._outputs:
                result.append(s.as_port(PortDirection.OUTPUT))
        else:
            result.extend(s.as_port(PortDirection.OUTPUT) for s in self._outputs)
        return result

    def emit_nets(self) -> list[Net]:
        return [s.as_net() for s in self._wires]

    def emit_variables(self) -> list[Variable]:
        return [s.as_variable() for s in self._regs]

    def emit_parameters(self) -> list[Parameter]:
        return [s.as_parameter() for s in self._params]
