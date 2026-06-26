"""VCD (Value Change Dump) waveform output.

Generates IEEE Std 1364-2001 compliant VCD files that can be opened
in GTKWave or any standard waveform viewer.

Usage:
    writer = VcdWriter("output.vcd", timescale="1ns")
    writer.add_signal("clk", width=1)
    writer.add_signal("count", width=8)
    writer.write_header()
    writer.set_time(0)
    writer.change("clk", Value(0, width=1))
    writer.change("count", Value(0, width=8))
    writer.set_time(5)
    writer.change("clk", Value(1, width=1))
    writer.finalize()
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from .value import Value


class VcdWriter:
    """Write VCD formatted waveform data.

    Signals are registered with add_signal(), then time steps and
    value changes are recorded. The output conforms to IEEE 1364.
    """

    __slots__ = (
        "_bin_fmts",
        "_current_time",
        "_file",
        "_header_written",
        "_id_counter",
        "_last_values",
        "_owns_file",
        "_signals",
        "_time_written",
        "_timescale",
    )

    def __init__(
        self,
        output: str | io.TextIOBase,
        *,
        timescale: str = "1ns",
    ) -> None:
        if isinstance(output, str):
            self._file = open(output, "w")  # noqa: SIM115
            self._owns_file = True
        else:
            self._file = output  # type: ignore[assignment]
            self._owns_file = False

        self._timescale = timescale
        self._signals: dict[str, _VcdSignal] = {}
        self._id_counter = 0
        self._current_time = -1
        self._header_written = False
        self._time_written = False
        self._last_values: dict[str, str] = {}  # id → last dumped value string
        self._bin_fmts: dict[int, str] = {}  # width → format string cache

    def add_signal(self, name: str, *, width: int = 1, scope: str = "top", vcd_name: str | None = None) -> None:
        """Register a signal to be traced.

        *name* is the lookup key used by change() / dump_all().
        *vcd_name* is the name written to the VCD ``$var`` line;
        defaults to *name* when not provided.
        """
        self._id_counter += 1
        ident = _make_id(self._id_counter)
        self._signals[name] = _VcdSignal(vcd_name if vcd_name is not None else name, width, ident, scope)

    def write_header(self) -> None:
        """Write the VCD header (must be called after all add_signal calls)."""
        f = self._file
        now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        f.write(f"$date {now} $end\n")
        f.write("$version veriforge simulation $end\n")
        f.write(f"$timescale {self._timescale} $end\n")

        # Group signals by scope
        scopes: dict[str, list[_VcdSignal]] = {}
        for sig in self._signals.values():
            if sig.scope not in scopes:
                scopes[sig.scope] = []
            scopes[sig.scope].append(sig)

        _write_scopes(f, scopes)

        f.write("$enddefinitions $end\n")
        self._header_written = True

    def set_time(self, time: int) -> None:
        """Advance VCD time. Only writes timestamp if there are changes."""
        self._current_time = time
        self._time_written = False

    def change(self, name: str, value: Value) -> None:
        """Record a value change for a signal at the current time."""
        sig = self._signals.get(name)
        if sig is None:
            return

        val_str = _value_to_vcd(value, sig.width)

        # Only write if value actually changed (or first dump)
        if self._last_values.get(sig.ident) == val_str:
            return

        self._last_values[sig.ident] = val_str
        self._ensure_timestamp()

        if sig.width == 1:
            self._file.write(f"{val_str}{sig.ident}\n")
        else:
            self._file.write(f"b{val_str} {sig.ident}\n")

    def dump_all(self, time: int, signals: dict[str, Value]) -> None:
        """Dump values for all registered signals at a given time."""
        self.set_time(time)
        parts: list[str] = []
        for name, sig in self._signals.items():
            value = signals.get(name)
            if value is None:
                continue
            val_str = _value_to_vcd(value, sig.width)
            if self._last_values.get(sig.ident) == val_str:
                continue
            self._last_values[sig.ident] = val_str
            if sig.width == 1:
                parts.append(f"{val_str}{sig.ident}\n")
            else:
                parts.append(f"b{val_str} {sig.ident}\n")
        if parts:
            parts.insert(0, f"#{time}\n")
            self._time_written = True
            self._file.write("".join(parts))

    def write_initial(self, signals: dict[str, Value]) -> None:
        """Write the $dumpvars section with initial signal values."""
        self._file.write("$dumpvars\n")
        self._current_time = 0
        self._file.write(f"#{self._current_time}\n")
        for name, sig in self._signals.items():
            val = signals.get(name, Value.x(sig.width))
            val_str = _value_to_vcd(val, sig.width)
            self._last_values[sig.ident] = val_str
            if sig.width == 1:
                self._file.write(f"{val_str}{sig.ident}\n")
            else:
                self._file.write(f"b{val_str} {sig.ident}\n")
        self._file.write("$end\n")

    def finalize(self) -> None:
        """Flush and close the VCD file."""
        self._file.flush()
        if self._owns_file:
            self._file.close()

    def _ensure_timestamp(self) -> None:
        """Write a timestamp line once per time step."""
        if not self._time_written:
            self._file.write(f"#{self._current_time}\n")
            self._time_written = True

    def __enter__(self) -> VcdWriter:
        return self

    def __exit__(self, *args) -> None:
        self.finalize()


class _VcdSignal:
    """Internal signal metadata for VCD output."""

    __slots__ = ("ident", "name", "scope", "width")

    def __init__(self, name: str, width: int, ident: str, scope: str) -> None:
        self.name = name
        self.width = width
        self.ident = ident
        self.scope = scope


def _make_id(n: int) -> str:
    """Generate a unique VCD identifier from an integer.

    VCD identifiers use printable ASCII chars 33-126.
    """
    chars = []
    while True:
        chars.append(chr(33 + (n % 94)))
        n //= 94
        if n == 0:
            break
    return "".join(reversed(chars))


def _write_scopes(
    f: io.TextIOBase,
    scopes: dict[str, list[_VcdSignal]],
) -> None:
    """Emit nested ``$scope`` blocks from a flat ``{scope_path: signals}`` map."""
    root: dict[str, dict] = {}
    for scope_name, sigs in scopes.items():
        node = root
        for segment in scope_name.split("."):
            node = node.setdefault(segment, {})
        node["_sig"] = sigs  # type: ignore[assignment]

    def _emit(node: dict[str, dict]) -> None:
        for segment in sorted(k for k in node if k != "_sig"):
            child = node[segment]
            f.write(f"$scope module {segment} $end\n")
            for sig in child.get("_sig", []):
                f.write(f"$var wire {sig.width} {sig.ident} {sig.name} $end\n")
            _emit(child)
            f.write("$upscope $end\n")

    _emit(root)


def _value_to_vcd(value: Value, width: int) -> str:
    """Convert a Value to a VCD value string."""
    if width == 1:
        if value.mask & 1:
            return "x"
        return "1" if (value.val & 1) else "0"

    # Multi-bit: fast path when no x/z bits
    if value.mask == 0:
        return format(value.val, f"0{width}b")

    # Has x/z bits — per-bit conversion
    chars: list[str] = []
    for i in range(width - 1, -1, -1):
        if value.mask & (1 << i):
            chars.append("x")
        elif value.val & (1 << i):
            chars.append("1")
        else:
            chars.append("0")
    return "".join(chars)
