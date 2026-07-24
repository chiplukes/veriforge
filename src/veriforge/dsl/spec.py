"""Declarative module specification layer on top of the imperative DSL builder.

Ports, parameters, and internal signals are declared as class attributes;
the ``__set_name__`` descriptor protocol captures each attribute's name, so
names are never repeated as strings::

    from veriforge.dsl import ModuleSpec, In, OutReg, Param

    class Counter(ModuleSpec):
        WIDTH = Param(8)
        clk = In()
        rst = In()
        count = OutReg("WIDTH")

        def body(self, m):
            with m.seq(self.clk, rst=self.rst, rst_vals={self.count: 0}):
                self.count.next = self.count + 1

    module = Counter().build()           # model Module, name "Counter"
    module16 = Counter(WIDTH=16).build() # parameter default overridden

Notes:

- The emitted module name is the class name; override with a
  ``module_name = "..."`` class attribute.
- A width given as a string (``OutReg("WIDTH")``) refers to a ``Param``
  declared on the same class.
- Declaration order is class-body order (parameters are emitted first).
- ``body(self, m)`` receives the live :class:`~veriforge.dsl.builder.Module`
  builder; ``self.<attr>`` resolves to the declared :class:`Signal` proxies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .builder import Module, Signal

if TYPE_CHECKING:
    from ..model.design import Module as ModelModule
    from .builder import Expr


class _SpecItem:
    """Base descriptor for declarative port/signal/parameter declarations."""

    __slots__ = ("name",)

    def __init__(self) -> None:
        self.name: str = ""  # set by __set_name__ at class creation

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, instance: object, owner: type | None = None):
        if instance is None:
            return self
        signals = getattr(instance, "_spec_signals", None)
        if not signals or self.name not in signals:
            raise RuntimeError(
                f"'{self.name}' is only accessible during build() — declare signals as class "
                "attributes and use them inside body(self, m)."
            )
        return signals[self.name]

    def _declare(self, spec: ModuleSpec, m: Module) -> Signal:
        raise NotImplementedError


class Param(_SpecItem):
    """Verilog ``parameter`` declaration: ``WIDTH = Param(8)``."""

    __slots__ = ("default", "signed", "width")

    def __init__(self, default: int = 0, *, width: int | None = None, signed: bool = False):
        super().__init__()
        self.default = default
        self.width = width
        self.signed = signed

    def _declare(self, spec: ModuleSpec, m: Module) -> Signal:
        default = spec._param_overrides.get(self.name, self.default)
        return m.parameter(self.name, default, width=self.width, signed=self.signed)


class _PortItem(_SpecItem):
    """Base for port/signal descriptors carrying width/signed/init."""

    __slots__ = ("init", "signed", "width")

    def __init__(
        self,
        width: int | str = 1,
        *,
        signed: bool = False,
        init: int | None = None,
    ):
        super().__init__()
        self.width = width
        self.signed = signed
        self.init = init

    def _resolved_width(self, spec: ModuleSpec) -> int | Expr:
        """Resolve a string width to the Signal of the named Param."""
        if isinstance(self.width, str):
            try:
                return spec._spec_signals[self.width]
            except KeyError:
                raise ValueError(
                    f"Width {self.width!r} of '{self.name}' does not name a Param on {type(spec).__name__}"
                ) from None
        return self.width


class In(_PortItem):
    """Input port: ``clk = In()``, ``data = In(8)``, ``addr = In("AW")``."""

    __slots__ = ()

    def _declare(self, spec: ModuleSpec, m: Module) -> Signal:
        return m.input(self.name, self._resolved_width(spec), signed=self.signed, init=self.init)


class Out(_PortItem):
    """Output (wire) port: ``ready = Out()``, ``data = Out(8)``."""

    __slots__ = ()

    def _declare(self, spec: ModuleSpec, m: Module) -> Signal:
        return m.output(self.name, self._resolved_width(spec), signed=self.signed, init=self.init)


class OutReg(_PortItem):
    """Output reg port: ``count = OutReg(8)``, ``count = OutReg(8, init=0)``."""

    __slots__ = ()

    def _declare(self, spec: ModuleSpec, m: Module) -> Signal:
        return m.output_reg(self.name, self._resolved_width(spec), signed=self.signed, init=self.init)


class Inout(_PortItem):
    """Inout port: ``sda = Inout()``."""

    __slots__ = ()

    def _declare(self, spec: ModuleSpec, m: Module) -> Signal:
        return m.inout(self.name, self._resolved_width(spec), signed=self.signed, init=self.init)


class Wire(_PortItem):
    """Internal wire: ``sum_w = Wire(9)``."""

    __slots__ = ("depth",)

    def __init__(
        self,
        width: int | str = 1,
        *,
        signed: bool = False,
        init: int | None = None,
        depth: int | None = None,
    ):
        super().__init__(width, signed=signed, init=init)
        self.depth = depth

    def _declare(self, spec: ModuleSpec, m: Module) -> Signal:
        return m.wire(self.name, self._resolved_width(spec), signed=self.signed, init=self.init, depth=self.depth)


class Reg(_PortItem):
    """Internal reg: ``state = Reg(3)``, ``mem = Reg(8, depth=256)``."""

    __slots__ = ("depth",)

    def __init__(
        self,
        width: int | str = 1,
        *,
        signed: bool = False,
        init: int | None = None,
        depth: int | None = None,
    ):
        super().__init__(width, signed=signed, init=init)
        self.depth = depth

    def _declare(self, spec: ModuleSpec, m: Module) -> Signal:
        return m.reg(self.name, self._resolved_width(spec), signed=self.signed, init=self.init, depth=self.depth)


class ModuleSpec:
    """Base class for declarative module definitions — see module docstring."""

    module_name: str | None = None

    def __init__(self, **param_overrides: int):
        items = self._spec_items()
        param_names = {name for name, item in items.items() if isinstance(item, Param)}
        unknown = set(param_overrides) - param_names
        if unknown:
            raise TypeError(f"{type(self).__name__}() got overrides for undeclared parameters: {sorted(unknown)}")
        self._param_overrides = param_overrides
        self._spec_signals: dict[str, Signal] = {}

    @classmethod
    def _spec_items(cls) -> dict[str, _SpecItem]:
        """Collect descriptors in class-body order (base classes first)."""
        items: dict[str, _SpecItem] = {}
        for klass in reversed(cls.__mro__):
            for name, value in vars(klass).items():
                if isinstance(value, _SpecItem):
                    items[name] = value
        return items

    def build(self) -> ModelModule:
        """Declare everything, run :meth:`body`, and return the model Module."""
        items = self._spec_items()
        self._spec_signals = {}
        with Module(self.module_name or type(self).__name__) as m:
            # Parameters first so string widths can refer to them regardless
            # of class-body ordering; then everything else in declared order.
            for name, item in items.items():
                if isinstance(item, Param):
                    self._spec_signals[name] = item._declare(self, m)
            for name, item in items.items():
                if not isinstance(item, Param):
                    self._spec_signals[name] = item._declare(self, m)
            self.body(m)
        return m.build()

    def body(self, m: Module) -> None:
        """Override with the module's behavior, using the builder *m* and ``self.<signals>``."""
        raise NotImplementedError(f"{type(self).__name__} must define body(self, m)")
