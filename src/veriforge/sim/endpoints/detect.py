"""Port-name based interface detection for simulation helpers."""

from __future__ import annotations

from dataclasses import dataclass

from veriforge.model.design import Module
from veriforge.model.ports import Port, PortDirection


class InterfaceDetectionError(ValueError):
    """Raised when a candidate bundle is incomplete or directionally inconsistent."""


AXIS_REQUIRED_SIGNALS = ("tvalid", "tready", "tdata", "tlast")
AXIS_OPTIONAL_SIGNALS = ("tkeep", "tdest", "tid", "tuser")
AXI_LITE_REQUIRED_SIGNALS = (
    "awaddr",
    "awprot",
    "awvalid",
    "awready",
    "wdata",
    "wstrb",
    "wvalid",
    "wready",
    "bresp",
    "bvalid",
    "bready",
    "araddr",
    "arprot",
    "arvalid",
    "arready",
    "rdata",
    "rresp",
    "rvalid",
    "rready",
)
AXI4_REQUIRED_SIGNALS = (
    "awaddr",
    "awlen",
    "awsize",
    "awburst",
    "awvalid",
    "awready",
    "wdata",
    "wstrb",
    "wlast",
    "wvalid",
    "wready",
    "bresp",
    "bvalid",
    "bready",
    "araddr",
    "arlen",
    "arsize",
    "arburst",
    "arvalid",
    "arready",
    "rdata",
    "rresp",
    "rlast",
    "rvalid",
    "rready",
)
AXI4_OPTIONAL_SIGNALS = (
    "awid",
    "awlock",
    "awcache",
    "awprot",
    "awqos",
    "awregion",
    "awuser",
    "wuser",
    "bid",
    "buser",
    "arid",
    "arlock",
    "arcache",
    "arprot",
    "arqos",
    "arregion",
    "aruser",
    "rid",
    "ruser",
)

_AXIS_MASTER_OUTPUTS = {"tvalid", "tdata", "tlast", "tkeep", "tdest", "tid", "tuser"}
_AXIS_SLAVE_OUTPUTS = {"tready"}
_AXI_LITE_MASTER_OUTPUTS = {
    "awaddr",
    "awprot",
    "awvalid",
    "wdata",
    "wstrb",
    "wvalid",
    "bready",
    "araddr",
    "arprot",
    "arvalid",
    "rready",
}
_AXI_LITE_SLAVE_OUTPUTS = set(AXI_LITE_REQUIRED_SIGNALS) - _AXI_LITE_MASTER_OUTPUTS
_AXI4_MASTER_OUTPUTS = {
    # AW
    "awid",
    "awaddr",
    "awlen",
    "awsize",
    "awburst",
    "awlock",
    "awcache",
    "awprot",
    "awqos",
    "awregion",
    "awuser",
    "awvalid",
    # W
    "wdata",
    "wstrb",
    "wlast",
    "wuser",
    "wvalid",
    # B
    "bready",
    # AR
    "arid",
    "araddr",
    "arlen",
    "arsize",
    "arburst",
    "arlock",
    "arcache",
    "arprot",
    "arqos",
    "arregion",
    "aruser",
    "arvalid",
    # R
    "rready",
}
_AXI4_SLAVE_OUTPUTS = (set(AXI4_REQUIRED_SIGNALS) | set(AXI4_OPTIONAL_SIGNALS)) - _AXI4_MASTER_OUTPUTS


# ---------------------------------------------------------------------------
# Memory-bus constants
# ---------------------------------------------------------------------------

# Canonical write-enable aliases (normalised to "wen" in signal maps).
_MEMBUS_WEN_SUFFIXES = frozenset({"wen", "we", "wren", "write_en", "write_enable", "wena"})
# Canonical read-enable aliases (normalised to "ren" in signal maps).
_MEMBUS_REN_SUFFIXES = frozenset({"ren", "re", "rden", "read_en", "read_enable", "rena"})
# Byte-enable aliases (normalised to "be").
_MEMBUS_BE_SUFFIXES = frozenset({"be", "wstrb", "strb", "ben", "byte_en"})
# Read-valid aliases (normalised to "rvalid").
_MEMBUS_RVALID_SUFFIXES = frozenset({"rvalid", "read_valid", "dout_valid"})

# All suffixes that can be part of a memory-bus bundle.
_MEMBUS_ALL_SUFFIXES = (
    {"addr", "wdata", "rdata"}
    | _MEMBUS_WEN_SUFFIXES
    | _MEMBUS_REN_SUFFIXES
    | _MEMBUS_BE_SUFFIXES
    | _MEMBUS_RVALID_SUFFIXES
)

# For a DUT-slave bus: master (bench) drives addr/wdata/wen/ren/be; DUT outputs rdata/rvalid.
_MEMBUS_SLAVE_INPUTS = frozenset({"addr", "wdata"} | _MEMBUS_WEN_SUFFIXES | _MEMBUS_REN_SUFFIXES | _MEMBUS_BE_SUFFIXES)


@dataclass(frozen=True, slots=True)
class DetectedInterface:  # cm:5a4c7e
    """Detected flat-port interface bundle."""

    prefix: str
    protocol: str
    role: str
    signals: dict[str, Port]

    def signal_names(self) -> dict[str, str]:
        return {name: port.name for name, port in self.signals.items()}

    def make_axis_source(self, sim):
        if self.protocol != "axi_stream" or self.role != "slave":
            raise ValueError("AXI-Stream source requires a detected slave-side DUT bundle")
        from .axis_source import AXIStreamSource

        return AXIStreamSource(sim, self.prefix)

    def make_axis_sink(self, sim):
        if self.protocol != "axi_stream" or self.role != "master":
            raise ValueError("AXI-Stream sink requires a detected master-side DUT bundle")
        from .axis_sink import AXIStreamSink

        return AXIStreamSink(sim, self.prefix)

    def make_axi_lite_master(self, sim, **kwargs):
        if self.protocol != "axi_lite" or self.role != "slave":
            raise ValueError("AXI-Lite master requires a detected slave-side DUT bundle")
        from .axi_lite_master import AXILiteMaster

        return AXILiteMaster(sim, self.prefix, **kwargs)

    def make_membus_master(self, sim, **kwargs):
        if self.protocol != "membus" or self.role != "slave":
            raise ValueError("MemBus master requires a detected slave-side DUT bundle")
        from .membus_master import MemBusMaster

        return MemBusMaster(sim, self.signal_names(), **kwargs)

    def make_membus_responder(self, sim, **kwargs):
        if self.protocol != "membus" or self.role != "master":
            raise ValueError("MemBus responder requires a detected master-side DUT bundle")
        from .membus_responder import MemBusResponder

        return MemBusResponder(sim, self.signal_names(), **kwargs)

    def make_axi4_master(self, sim, **kwargs):
        if self.protocol != "axi4" or self.role != "slave":
            raise ValueError("AXI4 master requires a detected slave-side DUT bundle")
        from .axi4_master import AXI4Master

        return AXI4Master(sim, self.prefix, **kwargs)

    def make_stream_source(self, sim):
        if self.protocol != "stream" or self.role != "slave":
            raise ValueError("Stream source requires a detected slave-side DUT bundle")
        from .stream_source import StreamSource

        return StreamSource(sim, self.signal_names())

    def make_stream_sink(self, sim):
        if self.protocol != "stream" or self.role != "master":
            raise ValueError("Stream sink requires a detected master-side DUT bundle")
        from .stream_sink import StreamSink

        return StreamSink(sim, self.signal_names())


@dataclass(frozen=True, slots=True)
class NearMissInterface:
    """A prefix group that nearly matched a known protocol but was missing some signals.

    Attributes:
        prefix:   Port-name prefix (e.g. ``"slv"``).
        protocol: Protocol that was almost matched (``"axi_stream"``, ``"axi_lite"``,
                  ``"axi4"``).
        matched:  Required signals that were present.
        missing:  Required signals that were absent.

    Example::

        NearMissInterface(prefix="slv", protocol="axi_lite",
                          matched=("awaddr", "awvalid", ...),
                          missing=("awprot", "wstrb"))
        # → "slv: ports match AXI-Lite except missing: awprot, wstrb"
    """

    prefix: str
    protocol: str
    matched: tuple[str, ...]
    missing: tuple[str, ...]

    def explain(self) -> str:
        """Return a one-line human-readable description of the near-miss."""
        protocol_label = {
            "axi_stream": "AXI-Stream",
            "axi_lite": "AXI-Lite",
            "axi4": "AXI4",
        }.get(self.protocol, self.protocol)
        return f"'{self.prefix}': ports match {protocol_label} except missing: {', '.join(sorted(self.missing))}"


def _group_ports_by_prefix(module: Module) -> dict[str, dict[str, Port]]:
    grouped: dict[str, dict[str, Port]] = {}
    suffixes = (
        set(AXIS_REQUIRED_SIGNALS)
        | set(AXIS_OPTIONAL_SIGNALS)
        | set(AXI_LITE_REQUIRED_SIGNALS)
        | set(AXI4_REQUIRED_SIGNALS)
        | set(AXI4_OPTIONAL_SIGNALS)
    )
    # Channel prefixes used to detect underscore-separated AXI naming
    # (e.g. ``slv_aw_valid`` ↔ canonical ``slv_awvalid``). The flat
    # detector keys on canonical (no-underscore) suffixes; many
    # real-world testbenches (pulp) and tools emit the underscore form.
    _AXI_CHANNELS = ("aw", "ar", "w", "b", "r")
    _AXIS_CHANNELS = ("t",)
    for port in module.ports:
        name = port.name
        prefix, _, suffix = name.rpartition("_")
        # Try canonical form first.
        if prefix and suffix in suffixes:
            grouped.setdefault(prefix, {})[suffix] = port
            continue
        # Try underscore-separated form: ``<prefix>_<chan>_<field>``
        # → canonical ``<prefix>_<chan><field>``.
        outer_prefix, _, last = name.rpartition("_")
        if not outer_prefix:
            continue
        inner_prefix, _, channel = outer_prefix.rpartition("_")
        if not inner_prefix or not channel:
            continue
        if channel in _AXI_CHANNELS or channel in _AXIS_CHANNELS:
            canonical = f"{channel}{last}"
            if canonical in suffixes:
                grouped.setdefault(inner_prefix, {})[canonical] = port
    return grouped


def _infer_role(signals: dict[str, Port], *, master_outputs: set[str], slave_outputs: set[str]) -> str:
    master_matches = all(
        ((name in master_outputs) and port.direction == PortDirection.OUTPUT)
        or ((name not in master_outputs) and port.direction == PortDirection.INPUT)
        for name, port in signals.items()
    )
    slave_matches = all(
        ((name in slave_outputs) and port.direction == PortDirection.OUTPUT)
        or ((name not in slave_outputs) and port.direction == PortDirection.INPUT)
        for name, port in signals.items()
    )
    if master_matches and not slave_matches:
        return "master"
    if slave_matches and not master_matches:
        return "slave"
    raise InterfaceDetectionError("bundle directions do not match a valid interface role")


def _detect_protocol(prefix: str, signals: dict[str, Port]) -> DetectedInterface | None:
    signal_names = set(signals)
    # AXI4 is a strict superset of AXI-Lite (has wlast/rlast/awlen/etc.),
    # so it MUST be checked first; otherwise an AXI4 bundle would be
    # misclassified as AXI-Lite.
    if set(AXI4_REQUIRED_SIGNALS).issubset(signal_names):
        present = [name for name in (*AXI4_REQUIRED_SIGNALS, *AXI4_OPTIONAL_SIGNALS) if name in signals]
        role = _infer_role(
            {name: signals[name] for name in present},
            master_outputs=_AXI4_MASTER_OUTPUTS,
            slave_outputs=_AXI4_SLAVE_OUTPUTS,
        )
        return DetectedInterface(
            prefix=prefix,
            protocol="axi4",
            role=role,
            signals={name: signals[name] for name in present},
        )

    if set(AXI_LITE_REQUIRED_SIGNALS).issubset(signal_names):
        role = _infer_role(
            {name: signals[name] for name in AXI_LITE_REQUIRED_SIGNALS},
            master_outputs=_AXI_LITE_MASTER_OUTPUTS,
            slave_outputs=_AXI_LITE_SLAVE_OUTPUTS,
        )
        return DetectedInterface(
            prefix=prefix,
            protocol="axi_lite",
            role=role,
            signals={name: signals[name] for name in AXI_LITE_REQUIRED_SIGNALS},
        )

    if set(AXIS_REQUIRED_SIGNALS).issubset(signal_names):
        axis_names = [*AXIS_REQUIRED_SIGNALS, *[name for name in AXIS_OPTIONAL_SIGNALS if name in signals]]
        role = _infer_role(
            {name: signals[name] for name in axis_names},
            master_outputs=_AXIS_MASTER_OUTPUTS,
            slave_outputs=_AXIS_SLAVE_OUTPUTS,
        )
        return DetectedInterface(
            prefix=prefix,
            protocol="axi_stream",
            role=role,
            signals={name: signals[name] for name in axis_names},
        )

    return None


def detect_interfaces(module: Module) -> list[DetectedInterface]:  # cm:9f5b2d
    """Detect AXI-Stream, AXI-Lite, AXI4, Stream, and MemBus bundles from flat module ports."""
    detected: list[DetectedInterface] = []
    for prefix, signals in _group_ports_by_prefix(module).items():
        bundle = _detect_protocol(prefix, signals)
        if bundle is not None:
            detected.append(bundle)
    # Stream detection is orthogonal: it looks at *_i / *_o direction
    # suffixes rather than AXIS-style payload-name suffixes. Run it on
    # all ports that have not already been claimed by an AXIS or AXI-Lite
    # bundle so users don't get duplicate (overlapping) bindings.
    claimed = {port.name for bundle in detected for port in bundle.signals.values()}
    detected.extend(_detect_stream_interfaces(module, claimed))
    # MemBus detection: look for addr/wdata/rdata/wen patterns not already claimed.
    claimed.update(port.name for bundle in detected for port in bundle.signals.values())
    detected.extend(_detect_membus_interfaces(module, claimed))
    return sorted(detected, key=lambda bundle: (bundle.protocol, bundle.prefix))


# Near-miss thresholds: minimum required signals that must be present before
# we report a near-miss.  Set conservatively to avoid false positives from
# short prefix groups that happen to share a couple of common names.
_NEAR_MISS_MIN: dict[str, int] = {
    "axi_stream": 2,  # 4 required total — report if ≥2 present
    "axi_lite": 8,  # 18 required total — report if ≥8 present
    "axi4": 12,  # 26 required total — report if ≥12 present
}
# Maximum number of missing required signals to still call it a near-miss.
# Keeps the report actionable — "missing 1-6 signals" is useful; "missing 20"
# is just noise.
_NEAR_MISS_MAX_MISSING: int = 6


def detect_near_misses(module: Module) -> list[NearMissInterface]:
    """Return near-miss candidates: prefix groups that almost matched a protocol.

    A prefix group is a near-miss for a protocol when it contains at least
    ``_NEAR_MISS_MIN[protocol]`` of that protocol's required signals but is
    missing at least one required signal.  Only the *best* match per prefix
    (fewest missing signals) is returned.

    Args:
        module: The module to inspect.

    Returns:
        List of :class:`NearMissInterface` sorted by (protocol, prefix).
    """
    grouped = _group_ports_by_prefix(module)
    # Collect fully-detected prefixes to avoid double-reporting.
    full_prefixes: set[str] = {bundle.prefix for bundle in detect_interfaces(module)}

    _CANDIDATES: list[tuple[str, tuple[str, ...]]] = [
        ("axi4", AXI4_REQUIRED_SIGNALS),
        ("axi_lite", AXI_LITE_REQUIRED_SIGNALS),
        ("axi_stream", AXIS_REQUIRED_SIGNALS),
    ]

    near_misses: list[NearMissInterface] = []
    for prefix, signals in grouped.items():
        if prefix in full_prefixes:
            continue
        signal_names = set(signals)
        best: NearMissInterface | None = None
        for protocol, required in _CANDIDATES:
            matched = tuple(s for s in required if s in signal_names)
            missing = tuple(s for s in required if s not in signal_names)
            n_matched = len(matched)
            n_missing = len(missing)
            if n_matched < _NEAR_MISS_MIN[protocol]:
                continue
            if n_missing == 0:
                continue  # full match — handled by detect_interfaces()
            if n_missing > _NEAR_MISS_MAX_MISSING:
                continue
            candidate = NearMissInterface(
                prefix=prefix,
                protocol=protocol,
                matched=matched,
                missing=missing,
            )
            if best is None or len(candidate.missing) < len(best.missing):
                best = candidate
        if best is not None:
            near_misses.append(best)

    return sorted(near_misses, key=lambda nm: (nm.protocol, nm.prefix))


# Protocol → (required_signals, master_outputs, slave_outputs, optional_signals) mapping
# used by detect_relaxed_interfaces() to promote near-misses to full interfaces.
_PROTOCOL_SPECS: dict[str, tuple[tuple[str, ...], set[str], set[str], tuple[str, ...]]] = {
    "axi_stream": (AXIS_REQUIRED_SIGNALS, _AXIS_MASTER_OUTPUTS, _AXIS_SLAVE_OUTPUTS, AXIS_OPTIONAL_SIGNALS),
    "axi_lite": (
        AXI_LITE_REQUIRED_SIGNALS,
        _AXI_LITE_MASTER_OUTPUTS,
        _AXI_LITE_SLAVE_OUTPUTS,
        (),
    ),
    "axi4": (AXI4_REQUIRED_SIGNALS, _AXI4_MASTER_OUTPUTS, _AXI4_SLAVE_OUTPUTS, AXI4_OPTIONAL_SIGNALS),
}


def detect_relaxed_interfaces(
    module: Module,
    *,
    relaxed_signals: dict[str, list[str]] | None = None,
) -> list[DetectedInterface]:
    """Detect interfaces with relaxed required-signal constraints.

    When a bundle matches all but a few required signals and those missing
    signals are listed in *relaxed_signals* for the protocol, the bundle is
    promoted to a full `DetectedInterface`.  This handles ARM-spec-legal
    variants such as tlast-less AXIS (unframed streams) or awprot-less
    AXI-Lite (simple register-files).

    Args:
        module: The flat-ported module to scan.
        relaxed_signals: ``{protocol: [signal_name, ...]}`` — which required
            signals may be absent without blocking detection.  E.g.
            ``{"axi_stream": ["tlast"], "axi_lite": ["awprot"]}``.

    Returns:
        List of `DetectedInterface` for bundles that match with relaxed
        constraints.  Bundles already detected by `detect_interfaces()`
        are excluded.
    """
    if not relaxed_signals:
        return []

    promoted: list[DetectedInterface] = []
    already_detected = {bundle.prefix for bundle in detect_interfaces(module)}

    for prefix, signals in _group_ports_by_prefix(module).items():
        if prefix in already_detected:
            continue
        signal_names = set(signals)

        for protocol, (required, master_outputs, slave_outputs, optional) in _PROTOCOL_SPECS.items():
            relaxed = set(relaxed_signals.get(protocol, ()))
            if not relaxed:
                continue
            relaxed_required = set(required) - relaxed
            if not relaxed_required.issubset(signal_names):
                continue
            present = [name for name in (*relaxed_required, *optional, *relaxed) if name in signals]
            try:
                role = _infer_role(
                    {name: signals[name] for name in present},
                    master_outputs=master_outputs,
                    slave_outputs=slave_outputs,
                )
            except InterfaceDetectionError:
                continue
            promoted.append(
                DetectedInterface(
                    prefix=prefix,
                    protocol=protocol,
                    role=role,
                    signals={name: signals[name] for name in present},
                )
            )
            break  # best protocol match wins

    return sorted(promoted, key=lambda b: (b.protocol, b.prefix))


def detect_axi_stream_interfaces(module: Module) -> list[DetectedInterface]:
    """Return detected AXI-Stream bundles from a module."""
    return [bundle for bundle in detect_interfaces(module) if bundle.protocol == "axi_stream"]


def detect_axi_lite_interfaces(module: Module) -> list[DetectedInterface]:
    """Return detected AXI-Lite bundles from a module."""
    return [bundle for bundle in detect_interfaces(module) if bundle.protocol == "axi_lite"]


def detect_axi4_interfaces(module: Module) -> list[DetectedInterface]:
    """Return detected AXI4 (full) bundles from a module."""
    return [bundle for bundle in detect_interfaces(module) if bundle.protocol == "axi4"]


def detect_stream_interfaces(module: Module) -> list[DetectedInterface]:
    """Return detected ready/valid stream bundles from a module."""
    return [bundle for bundle in detect_interfaces(module) if bundle.protocol == "stream"]


def detect_membus_interfaces(module: Module) -> list[DetectedInterface]:
    """Return detected simple memory-bus bundles from a module."""
    return [bundle for bundle in detect_interfaces(module) if bundle.protocol == "membus"]


# ---------------------------------------------------------------------------
# Simple memory-bus detection (SRAM/BRAM style: addr/wdata/rdata/wen)
# ---------------------------------------------------------------------------


def _group_membus_ports_by_prefix(module: Module, claimed: set[str]) -> dict[str, dict[str, Port]]:
    """Group unclaimed ports into candidate memory-bus bundles by prefix.

    The canonical suffix for each role is normalised (e.g. ``"we"`` → ``"wen"``
    so callers can always key on ``"wen"``).  Returns a mapping of
    ``prefix → {canonical_role: Port}``.
    """
    groups: dict[str, dict[str, Port]] = {}
    for port in module.ports:
        if port.name in claimed:
            continue
        prefix, _, raw_suffix = port.name.rpartition("_")
        if not prefix:
            continue
        if raw_suffix == "addr":
            groups.setdefault(prefix, {})["addr"] = port
        elif raw_suffix == "wdata":
            groups.setdefault(prefix, {})["wdata"] = port
        elif raw_suffix == "rdata":
            groups.setdefault(prefix, {})["rdata"] = port
        elif raw_suffix in _MEMBUS_WEN_SUFFIXES:
            groups.setdefault(prefix, {}).setdefault("wen", port)
        elif raw_suffix in _MEMBUS_REN_SUFFIXES:
            groups.setdefault(prefix, {}).setdefault("ren", port)
        elif raw_suffix in _MEMBUS_BE_SUFFIXES:
            groups.setdefault(prefix, {}).setdefault("be", port)
        elif raw_suffix in _MEMBUS_RVALID_SUFFIXES:
            groups.setdefault(prefix, {}).setdefault("rvalid", port)
    return groups


def _infer_membus_role(signals: dict[str, Port]) -> str:
    """Infer DUT role for a memory-bus candidate.

    For a DUT-slave: addr/wdata/wen/ren/be are DUT inputs; rdata/rvalid are outputs.
    For a DUT-master: addr/wdata/wen/ren/be are DUT outputs; rdata/rvalid are inputs.
    """
    slave_input_count = sum(
        1 for role, port in signals.items() if role in _MEMBUS_SLAVE_INPUTS and port.direction == PortDirection.INPUT
    )
    slave_output_count = sum(
        1
        for role, port in signals.items()
        if role not in _MEMBUS_SLAVE_INPUTS and port.direction == PortDirection.OUTPUT
    )
    master_input_count = sum(
        1
        for role, port in signals.items()
        if role not in _MEMBUS_SLAVE_INPUTS and port.direction == PortDirection.INPUT
    )
    master_output_count = sum(
        1 for role, port in signals.items() if role in _MEMBUS_SLAVE_INPUTS and port.direction == PortDirection.OUTPUT
    )
    slave_score = slave_input_count + slave_output_count
    master_score = master_input_count + master_output_count
    return "slave" if slave_score >= master_score else "master"


def _detect_membus_interfaces(module: Module, claimed: set[str]) -> list[DetectedInterface]:
    """Detect memory-bus bundles not already claimed by higher-priority protocols."""
    bundles: list[DetectedInterface] = []
    for prefix, signals in _group_membus_ports_by_prefix(module, claimed).items():
        # Require all three of: addr, wdata, rdata, and one write-enable.
        if not {"addr", "wdata", "rdata", "wen"}.issubset(signals):
            continue
        try:
            role = _infer_membus_role(signals)
        except Exception:  # noqa: BLE001
            continue
        bundles.append(
            DetectedInterface(
                prefix=prefix,
                protocol="membus",
                role=role,
                signals=signals,
            )
        )
    return bundles


_STREAM_DIR_SUFFIXES = ("_i", "_o")
# Signals whose stem (after stripping ``_i``/``_o``) we ignore even if they
# happen to share a prefix with a stream bundle. Clocks and resets are not
# part of the bundle; ``flush``/``clr``/``testmode`` are common control
# inputs on Pulp stream cells (e.g. ``stream_fifo``) that are also not part
# of any one bundle.
_STREAM_NON_BUNDLE_STEMS = frozenset({"clk", "rst_n", "rst", "reset", "resetn", "flush", "clr", "testmode", "usage"})


def _stream_split(name: str) -> tuple[str, str, str] | None:
    """Split ``"<prefix>stem_<dir>"`` into ``(prefix, stem, dir)``.

    ``prefix`` may be empty (anonymous bundle) and otherwise ends in
    ``"_"``. Returns ``None`` if the name does not end in ``_i``/``_o``.
    """
    for suffix in _STREAM_DIR_SUFFIXES:
        if name.endswith(suffix):
            head = name[: -len(suffix)]  # everything before _i / _o
            if not head:
                return None
            # Stem is the trailing token of head; prefix is the rest.
            sep = head.rfind("_")
            if sep < 0:
                return "", head, suffix[1]
            return head[: sep + 1], head[sep + 1 :], suffix[1]
    return None


def _build_stream_bundle(
    prefix: str,
    role: str,
    signals_by_stem: dict[tuple[str, str], Port],
) -> DetectedInterface | None:
    """Try to assemble a single stream bundle for ``role`` from the group."""
    valid_dir, ready_dir, data_dir = ("i", "o", "i") if role == "slave" else ("o", "i", "o")
    valid_port = signals_by_stem.get(("valid", valid_dir))
    ready_port = signals_by_stem.get(("ready", ready_dir))
    if valid_port is None or ready_port is None:
        return None
    expected_v = PortDirection.INPUT if valid_dir == "i" else PortDirection.OUTPUT
    expected_r = PortDirection.OUTPUT if ready_dir == "o" else PortDirection.INPUT
    if valid_port.direction != expected_v or ready_port.direction != expected_r:
        return None

    bundle_signals: dict[str, Port] = {"valid": valid_port, "ready": ready_port}
    data_pdir = PortDirection.INPUT if data_dir == "i" else PortDirection.OUTPUT
    data_port = signals_by_stem.get(("data", data_dir))
    if data_port is not None and data_port.direction == data_pdir:
        bundle_signals["data"] = data_port
    # Same-direction ports at this prefix become sideband payloads.
    for (stem, dchar), port in signals_by_stem.items():
        if stem in {"valid", "ready", "data"} or dchar != data_dir:
            continue
        if port.direction == data_pdir:
            bundle_signals[stem] = port

    iface_prefix = prefix.rstrip("_") if prefix else ("in" if role == "slave" else "out")
    return DetectedInterface(
        prefix=iface_prefix,
        protocol="stream",
        role=role,
        signals=bundle_signals,
    )


def _detect_stream_interfaces(module: Module, claimed: set[str]) -> list[DetectedInterface]:
    groups: dict[str, dict[tuple[str, str], Port]] = {}
    for port in module.ports:
        if port.name in claimed:
            continue
        split = _stream_split(port.name)
        if split is None:
            continue
        prefix, stem, dchar = split
        if stem in _STREAM_NON_BUNDLE_STEMS:
            continue
        groups.setdefault(prefix, {})[(stem, dchar)] = port

    bundles: list[DetectedInterface] = []
    for prefix, signals_by_stem in groups.items():
        for role in ("slave", "master"):
            bundle = _build_stream_bundle(prefix, role, signals_by_stem)
            if bundle is not None:
                bundles.append(bundle)
    return bundles
