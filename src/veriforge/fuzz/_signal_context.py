"""Signal context — tracks available signals during module generation."""

from __future__ import annotations

import random
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field

from ..model.expressions import Identifier, Literal, Range
from ..model.nets import Net, NetKind
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
        return Variable(self.name, kind=VariableKind.REG, width=self.as_range(), signed=self.signed)


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
        # Populated by ModuleGenerator strategies after assembly
        self.always_blocks: list = []
        self.continuous_assigns: list = []

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
        s = Signal(name=name, width=width, signed=signed, kind="parameter")
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
        """Every signal that can appear on the LHS of an assignment."""
        return [*self._wires, *self._regs, *self._outputs, *self.locals]

    # ------------------------------------------------------------------
    # Random selection
    # ------------------------------------------------------------------

    def pick_readable(self, rng: random.Random) -> Signal:
        """Pick a random readable signal for use as an expression leaf."""
        pool = self.all_readable()
        if not pool:
            pool = self._inputs  # fallback
        if not pool:
            return self.add_input(rng)  # create one as last resort
        return rng.choice(pool)

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
