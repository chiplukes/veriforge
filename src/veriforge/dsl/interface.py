"""Interface / bus abstraction for the Verilog DSL.

Groups related signals (like an AXI stream bus) into a reusable template
and binds them to a module as prefixed ports or internal wires.

Conceptually similar to SystemVerilog ``interface`` with ``modport``
declarations, but emits Verilog-2005-compatible flat ports with a naming
prefix.

Example::

    from veriforge.dsl import Interface, Module, posedge

    # Define a reusable bus template
    axi_stream = (Interface("axi_stream")
        .signal("tvalid", src="master")
        .signal("tready", src="slave")
        .signal("tdata", width=8, src="master")
        .signal("tlast", src="master"))

    # Bind to a module as master ports
    m = Module("producer")
    clk = m.input("clk")
    m_axis = m.interface("m_axis", axi_stream, role="master")
    # Creates:  output m_axis_tvalid, input m_axis_tready,
    #           output [7:0] m_axis_tdata, output m_axis_tlast

    # Access individual signals
    with m.always(posedge(clk)):
        m_axis.tvalid <<= 1
        m_axis.tdata  <<= count

    # Internal wires (no role — all become wires)
    top = Module("top")
    axis = top.wire_interface("axis", axi_stream)
    top.instance("producer", "i_prod", ports={
        "clk": clk,
        **axis.port_map("m_axis"),   # {"m_axis_tvalid": axis_tvalid, ...}
    })
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..model.expressions import Literal, Range
from ..model.interface import Interface as ModelInterface
from ..model.interface import Modport, ModportPort
from ..model.nets import Net, NetKind
from ..model.ports import PortDirection

if TYPE_CHECKING:
    from .builder import Signal


# ---------------------------------------------------------------------------
# Interface signal definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InterfaceSignal:
    """Definition of a single signal within an interface template.

    Attributes:
        name:   Signal name (prefixed when bound to a module).
        width:  Bit width (1 = scalar).
        src:    Which role *drives* this signal — ``"master"`` or ``"slave"``.
        signed: Whether the signal is signed.
    """

    name: str
    width: int
    src: str
    signed: bool


# ---------------------------------------------------------------------------
# Interface — reusable bus template
# ---------------------------------------------------------------------------


class Interface:
    """Reusable interface template defining a group of signals with role-based
    directions.

    Each signal declares which *role* is its source (driver).  When bound to a
    module via :meth:`Module.interface`, the source role gets ``output`` ports
    and the other role gets ``input`` ports — mirroring SystemVerilog
    ``modport`` semantics.

    Supports method chaining::

        wishbone = (Interface("wishbone")
            .signal("cyc", src="master")
            .signal("stb", src="master")
            .signal("we",  src="master")
            .signal("adr", width=32, src="master")
            .signal("dat_w", width=32, src="master")
            .signal("dat_r", width=32, src="slave")
            .signal("ack", src="slave"))
    """

    __slots__ = ("_signals", "name")

    def __init__(self, name: str) -> None:
        self.name = name
        self._signals: list[InterfaceSignal] = []

    def signal(
        self,
        name: str,
        *,
        width: int = 1,
        src: str = "master",
        signed: bool = False,
    ) -> Interface:
        """Add a signal to the interface.

        Args:
            name:   Signal name (will be prefixed when bound to a module).
            width:  Bit width (default 1).
            src:    Which role drives this signal — ``"master"`` or ``"slave"``.
            signed: Whether the signal is signed.

        Returns:
            ``self``, for chaining.

        Raises:
            ValueError: If *src* is not ``"master"`` or ``"slave"``.
        """
        if src not in ("master", "slave"):
            raise ValueError(f"src must be 'master' or 'slave', got {src!r}")
        # M33: duplicate signal name in interface
        for existing in self._signals:
            if existing.name == name:
                raise ValueError(f"Signal '{name}' already declared in interface '{self.name}'")
        self._signals.append(InterfaceSignal(name, width, src, signed))
        return self

    def __repr__(self) -> str:
        sigs = ", ".join(s.name for s in self._signals)
        return f"Interface({self.name!r}, [{sigs}])"

    def to_model(self) -> ModelInterface:
        """Convert this DSL interface template to a model-layer Interface.

        Generates ``logic`` net declarations for each signal and two
        ``modport`` declarations (``master`` and ``slave``) with direction
        derived from each signal's ``src`` attribute.

        Returns:
            A :class:`~veriforge.model.interface.Interface` ready for
            emission via :func:`~veriforge.codegen.emit_interface`.

        Example::

            axi = (Interface("axi_stream")
                .signal("tvalid", src="master")
                .signal("tready", src="slave")
                .signal("tdata", width=8, src="master"))

            model_intf = axi.to_model()

            from veriforge.codegen import emit_interface
            print(emit_interface(model_intf))
            # interface axi_stream;
            #     logic tvalid;
            #     logic tready;
            #     logic [7:0] tdata;
            #     modport master(output tvalid, input tready, output tdata);
            #     modport slave(input tvalid, output tready, input tdata);
            # endinterface
        """
        if not self._signals:
            raise ValueError(f"Interface '{self.name}' has no signals — add signals before converting to model")

        # Build net declarations
        nets: list[Net] = []
        for sig in self._signals:
            width_range = None
            if sig.width > 1:
                width_range = Range(
                    Literal(sig.width - 1, original_text=str(sig.width - 1)),
                    Literal(0, original_text="0"),
                )
            net = Net(sig.name, NetKind.WIRE, width=width_range, signed=sig.signed)
            nets.append(net)

        # Build master and slave modports
        master_ports: list[ModportPort] = []
        slave_ports: list[ModportPort] = []
        for sig in self._signals:
            if sig.src == "master":
                master_ports.append(ModportPort(sig.name, PortDirection.OUTPUT))
                slave_ports.append(ModportPort(sig.name, PortDirection.INPUT))
            else:
                master_ports.append(ModportPort(sig.name, PortDirection.INPUT))
                slave_ports.append(ModportPort(sig.name, PortDirection.OUTPUT))

        modports = [
            Modport("master", master_ports),
            Modport("slave", slave_ports),
        ]

        return ModelInterface(self.name, nets=nets, modports=modports)


# ---------------------------------------------------------------------------
# BoundInterface — interface bound to a module with a prefix and role
# ---------------------------------------------------------------------------


class BoundInterface:
    """An interface bound to a module with a prefix and role.

    Provides attribute access to individual :class:`Signal` objects and a
    :meth:`port_map` method for convenient instance connections.

    Created by :meth:`Module.interface` or :meth:`Module.wire_interface`;
    not instantiated directly.
    """

    __slots__ = ("_interface", "_prefix", "_role", "_signals")

    def __init__(
        self,
        prefix: str,
        interface: Interface,
        role: str | None,
        signals: dict[str, Signal],
    ) -> None:
        self._prefix = prefix
        self._interface = interface
        self._role = role
        self._signals: dict[str, Signal] = signals

    def __getattr__(self, name: str) -> Signal:
        # __slots__ names are handled by the default descriptor protocol;
        # this is only called for names *not* in __slots__.
        try:
            return self._signals[name]
        except KeyError:
            raise AttributeError(f"Interface '{self._interface.name}' has no signal '{name}'") from None

    def __setattr__(self, name: str, value: object) -> None:
        # Slot attributes (_prefix, _interface, _role, _signals) are set
        # normally via the descriptor protocol.
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        # Augmented assignment (e.g. ``m_axis.tvalid <<= 1``) desugars to
        #   m_axis.tvalid = m_axis.tvalid.__ilshift__(1)
        # The statement is already recorded by __ilshift__; absorb the
        # write-back silently when the name is a known signal.
        if name in self._signals:
            return
        raise AttributeError(f"Interface '{self._interface.name}' has no signal '{name}'")

    def port_map(self, prefix: str | None = None) -> dict[str, Signal]:
        """Return a mapping of port names to :class:`Signal` objects.

        Useful with ``**`` expansion in :meth:`Module.instance` connections.

        Args:
            prefix: Port-name prefix for the dict keys.  Defaults to this
                    interface's own prefix.  Override when the target instance
                    uses a different prefix (e.g. ``"s_axis"`` vs ``"m_axis"``).

        Returns:
            ``{"prefix_signame": Signal, ...}``

        Example::

            top.instance("consumer", "i_cons", ports={
                "clk": clk,
                **axis.port_map("s_axis"),
                # => {"s_axis_tvalid": ..., "s_axis_tready": ..., ...}
            })
        """
        p = prefix if prefix is not None else self._prefix
        return {f"{p}_{name}": sig for name, sig in self._signals.items()}

    @property
    def signals(self) -> dict[str, Signal]:
        """Read-only view of the signal dict ``{name: Signal}``."""
        return dict(self._signals)

    def __repr__(self) -> str:
        role = self._role or "wire"
        sigs = ", ".join(self._signals)
        return f"BoundInterface({self._prefix!r}, role={role!r}, [{sigs}])"
