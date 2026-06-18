"""Immutable data model for inferred testbench plans.

A :class:`TestbenchPlan` is the single source of truth that downstream
generators, the multi-domain runtime, and the transaction-level DSL all
read from. It deliberately holds **only inferred metadata** — names,
roles, polarities, and domain bindings — not live simulator handles or
parse-tree references. That makes it cheap to construct, copy, compare
in tests, and serialize for diagnostics.

Construction is normally done by ``planner.build_plan(...)`` (Phase 2);
the dataclasses here are usable directly in tests and for hand-built
plans.

All collections are stored as ``tuple`` so the plan is hashable and
safely shareable across threads and engines.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping


class PlanValidationError(ValueError):
    """Raised when a :class:`TestbenchPlan` is internally inconsistent.

    Examples include an interface bound to an unknown domain, two
    domains sharing a clock name, or a reset whose polarity / style is
    unset.
    """


# ---------------------------------------------------------------------------
# Clock and reset specs
# ---------------------------------------------------------------------------


_VALID_EDGES = frozenset({"posedge", "negedge"})
_VALID_RESET_STYLES = frozenset({"async", "sync"})


@dataclass(frozen=True, slots=True)
class ClockSpec:  # cm:9d6c3f
    """A single clock signal in the design under test.

    Attributes:
        name: Port or net name of the clock signal.
        edge: Active edge: ``"posedge"`` or ``"negedge"``.
        period_hint: Optional clock period in simulator time units. Used
            by the generator and runtime when scheduling clock toggles;
            when ``None`` the caller must supply a period.
    """

    name: str
    edge: str = "posedge"
    period_hint: int | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise PlanValidationError("ClockSpec.name must be non-empty")
        if self.edge not in _VALID_EDGES:
            raise PlanValidationError(f"ClockSpec.edge must be one of {sorted(_VALID_EDGES)}, got {self.edge!r}")
        if self.period_hint is not None and self.period_hint <= 0:
            raise PlanValidationError(f"ClockSpec.period_hint must be positive, got {self.period_hint}")

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "edge": self.edge, "period_hint": self.period_hint}


@dataclass(frozen=True, slots=True)
class ResetSpec:  # cm:2a8b7e
    """A single reset signal associated with one or more clock domains.

    Attributes:
        name: Port or net name of the reset signal.
        active_low: ``True`` if the reset asserts low (e.g. ``rst_n``).
        style: ``"async"`` (sensitivity-list reset) or ``"sync"``.
        edge: Sensitivity edge for async resets, or ``None`` for sync.
    """

    name: str
    active_low: bool
    style: str = "sync"
    edge: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise PlanValidationError("ResetSpec.name must be non-empty")
        if self.style not in _VALID_RESET_STYLES:
            raise PlanValidationError(
                f"ResetSpec.style must be one of {sorted(_VALID_RESET_STYLES)}, got {self.style!r}"
            )
        if self.style == "async":
            if self.edge not in _VALID_EDGES:
                raise PlanValidationError("ResetSpec.edge must be 'posedge' or 'negedge' for async resets")
        elif self.edge is not None:
            raise PlanValidationError("ResetSpec.edge must be None for sync resets")

    @property
    def assert_level(self) -> int:
        """Logic level that *asserts* this reset (0 for active-low, 1 otherwise)."""
        return 0 if self.active_low else 1

    @property
    def release_level(self) -> int:
        """Logic level that *releases* this reset."""
        return 1 - self.assert_level

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "active_low": self.active_low,
            "style": self.style,
            "edge": self.edge,
        }


# ---------------------------------------------------------------------------
# Domain and interface binding
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClockDomain:  # cm:7e9f8c
    """A clock + optional reset grouping.

    A domain is the unit the multi-domain runtime steps. Every interface
    in the plan is bound to exactly one domain; every domain owns
    exactly one clock and, optionally, one reset.

    Attributes:
        name: Unique domain name. By convention the clock signal name,
            but planners may rename (e.g. ``"axi"``) when multiple
            clocks share semantic meaning.
        clock: Clock that drives this domain.
        reset: Reset associated with the clock, or ``None`` if the
            design has no reset on this domain.
    """

    name: str
    clock: ClockSpec
    reset: ResetSpec | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise PlanValidationError("ClockDomain.name must be non-empty")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "clock": self.clock.to_dict(),
            "reset": self.reset.to_dict() if self.reset is not None else None,
        }


@dataclass(frozen=True, slots=True)
class InterfaceBinding:  # cm:c4f5d1
    """A protocol bundle bound to a single clock domain.

    Attributes:
        prefix: Common port-name prefix that identifies the bundle
            (e.g. ``"m_axis"``).
        protocol: Protocol identifier (``"axi_stream"``, ``"axi_lite"``).
        role: DUT-side role (``"master"`` or ``"slave"``).
        domain_name: Name of the :class:`ClockDomain` this bundle uses.
            Must match a domain present in the owning plan.
        signals: Suffix-to-port-name mapping (e.g.
            ``{"tvalid": "m_axis_tvalid", ...}``).
        confidence: Inference confidence: ``"structural"`` (proved by
            connectivity), ``"naming"`` (matched a naming convention),
            ``"sole-domain"`` (only one domain existed), or
            ``"override"`` (forced by the user).
    """

    prefix: str
    protocol: str
    role: str
    domain_name: str
    signals: Mapping[str, str] = field(default_factory=dict)
    confidence: str = "naming"

    def __post_init__(self) -> None:
        if not self.prefix:
            raise PlanValidationError("InterfaceBinding.prefix must be non-empty")
        if not self.protocol:
            raise PlanValidationError("InterfaceBinding.protocol must be non-empty")
        if self.role not in {"master", "slave"}:
            raise PlanValidationError(f"InterfaceBinding.role must be 'master' or 'slave', got {self.role!r}")
        if not self.domain_name:
            raise PlanValidationError("InterfaceBinding.domain_name must be non-empty")
        if self.confidence not in {"structural", "naming", "sole-domain", "override"}:
            raise PlanValidationError(f"InterfaceBinding.confidence invalid: {self.confidence!r}")
        # Freeze signals so the binding stays hashable / immutable.
        if not isinstance(self.signals, _FrozenSignals):
            object.__setattr__(self, "signals", _FrozenSignals(self.signals))

    def to_dict(self) -> dict[str, object]:
        return {
            "prefix": self.prefix,
            "protocol": self.protocol,
            "role": self.role,
            "domain_name": self.domain_name,
            "signals": dict(self.signals),
            "confidence": self.confidence,
        }


class _FrozenSignals(Mapping[str, str]):
    """Immutable, hashable mapping wrapper used inside :class:`InterfaceBinding`."""

    _items: tuple[tuple[str, str], ...]
    __slots__ = ("_items",)

    def __init__(self, source: Mapping[str, str] | Iterable[tuple[str, str]]) -> None:
        items = tuple(sorted(dict(source).items()))
        object.__setattr__(self, "_items", items)

    def __getitem__(self, key: str) -> str:
        for k, v in self._items:
            if k == key:
                return v
        raise KeyError(key)

    def __iter__(self):
        return (k for k, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __hash__(self) -> int:
        return hash(self._items)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, _FrozenSignals):
            return self._items == other._items
        if isinstance(other, Mapping):
            return dict(self._items) == dict(other)
        return NotImplemented

    def __repr__(self) -> str:
        return f"_FrozenSignals({dict(self._items)!r})"


# ---------------------------------------------------------------------------
# Top-level plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TestbenchPlan:  # cm:5b4a9e
    """Inferred testbench plan for one DUT module.

    Attributes:
        top: Name of the top-level module the plan describes.
        domains: All clock domains, in deterministic order (typically
            the order the planner discovered them).
        interfaces: All bound interfaces.
        warnings: Human-readable diagnostics produced during inference
            (e.g. "interface 's_axi' had two plausible clocks; picked
            'aclk' under non-strict mode").
        overrides_applied: Snapshot of override keys that actually
            changed an inference decision. Useful for both debugging
            and the generated bench's leading docstring.
    """

    # Tell pytest this is not a test class despite the "Test" prefix.
    __test__ = False

    top: str
    domains: tuple[ClockDomain, ...] = ()
    interfaces: tuple[InterfaceBinding, ...] = ()
    warnings: tuple[str, ...] = ()
    overrides_applied: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.top:
            raise PlanValidationError("TestbenchPlan.top must be non-empty")
        # Coerce iterables to tuples so callers may pass lists.
        object.__setattr__(self, "domains", tuple(self.domains))
        object.__setattr__(self, "interfaces", tuple(self.interfaces))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "overrides_applied", tuple(self.overrides_applied))

        # Domain names must be unique.
        seen: set[str] = set()
        for d in self.domains:
            if d.name in seen:
                raise PlanValidationError(f"duplicate domain name: {d.name!r}")
            seen.add(d.name)

        # Clock names must be unique across domains (a given clock signal
        # cannot drive two distinct domains).
        clock_seen: set[str] = set()
        for d in self.domains:
            if d.clock.name in clock_seen:
                raise PlanValidationError(f"clock {d.clock.name!r} appears in more than one domain")
            clock_seen.add(d.clock.name)

        # Interface bindings must reference a known domain.
        domain_names = {d.name for d in self.domains}
        for iface in self.interfaces:
            if iface.domain_name not in domain_names:
                raise PlanValidationError(f"interface {iface.prefix!r} bound to unknown domain {iface.domain_name!r}")

        # Interface prefixes must be unique.
        prefix_seen: set[str] = set()
        for iface in self.interfaces:
            if iface.prefix in prefix_seen:
                raise PlanValidationError(f"duplicate interface prefix: {iface.prefix!r}")
            prefix_seen.add(iface.prefix)

    # ---- Convenience accessors --------------------------------------------------

    def domain(self, name: str) -> ClockDomain:
        """Return the named domain or raise :class:`KeyError`."""
        for d in self.domains:
            if d.name == name:
                return d
        raise KeyError(f"no domain named {name!r}")

    def interface(self, prefix: str) -> InterfaceBinding:
        """Return the interface binding for ``prefix`` or raise :class:`KeyError`."""
        for iface in self.interfaces:
            if iface.prefix == prefix:
                return iface
        raise KeyError(f"no interface with prefix {prefix!r}")

    def interfaces_for_domain(self, domain_name: str) -> tuple[InterfaceBinding, ...]:
        """Return all interfaces bound to ``domain_name`` (in order)."""
        return tuple(i for i in self.interfaces if i.domain_name == domain_name)

    def has_warnings(self) -> bool:
        return bool(self.warnings)

    # ---- Diagnostics ------------------------------------------------------------

    def to_dict(self) -> dict[str, object]:
        """Serialize to a plain ``dict`` for diagnostics or JSON dumps."""
        return {
            "top": self.top,
            "domains": [d.to_dict() for d in self.domains],
            "interfaces": [i.to_dict() for i in self.interfaces],
            "warnings": list(self.warnings),
            "overrides_applied": list(self.overrides_applied),
        }

    def summary(self) -> str:
        """Return a multi-line human-readable summary for docstrings / CLI."""
        lines: list[str] = [f"TestbenchPlan(top={self.top!r})"]
        if self.domains:
            lines.append("  domains:")
            for d in self.domains:
                clk = d.clock
                rst = d.reset
                period = f"period={clk.period_hint}" if clk.period_hint is not None else "period=?"
                if rst is None:
                    rst_desc = "reset=<none>"
                else:
                    polarity = "active-low" if rst.active_low else "active-high"
                    rst_desc = f"reset={rst.name} ({polarity}, {rst.style})"
                lines.append(f"    - {d.name}: clock={clk.name} ({clk.edge}, {period}); {rst_desc}")
        else:
            lines.append("  domains: <none>")

        if self.interfaces:
            lines.append("  interfaces:")
            for iface in self.interfaces:
                lines.append(
                    f"    - {iface.prefix} ({iface.protocol}, role={iface.role}) "
                    f"-> domain={iface.domain_name} [{iface.confidence}]"
                )
        else:
            lines.append("  interfaces: <none>")

        if self.overrides_applied:
            lines.append("  overrides_applied: " + ", ".join(self.overrides_applied))
        if self.warnings:
            lines.append("  warnings:")
            for w in self.warnings:
                lines.append(f"    ! {w}")
        return "\n".join(lines)
