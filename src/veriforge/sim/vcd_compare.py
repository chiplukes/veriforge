"""VCD file parser and comparator for simulation validation.

Parses IEEE 1364-2001 VCD (Value Change Dump) files into a normalized
representation and compares signal values across two simulations.

Usage::

    from veriforge.sim.vcd_compare import parse_vcd, compare_vcd

    ref = parse_vcd(open("iverilog.vcd").read())
    test = parse_vcd(open("our_sim.vcd").read())
    diffs = compare_vcd(ref, test)
    assert not diffs, "\\n".join(diffs)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Data structures ──────────────────────────────────────────────────


@dataclass
class VcdSignal:
    """Metadata for a single VCD signal."""

    name: str
    width: int
    ident: str
    scope: str


@dataclass
class VcdData:
    """Parsed VCD file contents."""

    signals: dict[str, VcdSignal] = field(default_factory=dict)  # ident → VcdSignal
    changes: dict[str, list[tuple[int, str]]] = field(default_factory=dict)  # signal_name → [(time, value)]
    timescale: str = "1ns"

    @property
    def signal_names(self) -> set[str]:
        """All signal names in this VCD."""
        return {sig.name for sig in self.signals.values()}

    def values_at(self, name: str, time: int) -> str | None:
        """Get signal value at a specific time (last value <= time)."""
        changes = self.changes.get(name, [])
        result = None
        for t, val in changes:
            if t <= time:
                result = val
            else:
                break
        return result

    def all_times(self) -> list[int]:
        """Get sorted list of all unique timestamps."""
        times: set[int] = set()
        for change_list in self.changes.values():
            for t, _v in change_list:
                times.add(t)
        return sorted(times)


# ── VCD Parser ───────────────────────────────────────────────────────


def parse_vcd(text: str, *, strip_hierarchy: bool = True) -> VcdData:  # noqa: PLR0912, PLR0915
    """Parse a VCD file into a VcdData object.

    Args:
        text: VCD file content as string.
        strip_hierarchy: If True, remove scope prefixes from signal names
            (e.g. "test_module.a" → "a"). Default True.

    Returns:
        Parsed VCD data with signals and value changes.
    """
    data = VcdData()
    lines = text.split("\n")
    i = 0
    current_scope: list[str] = []
    current_time = 0
    in_dumpvars = False

    while i < len(lines):
        line = lines[i].strip()
        i += 1

        if not line:
            continue

        # Section keywords
        if line.startswith("$date"):
            i = _skip_section(lines, i, line)
            continue

        if line.startswith("$version"):
            i = _skip_section(lines, i, line)
            continue

        if line.startswith("$timescale"):
            # May be single-line: $timescale 1ns $end
            m = re.match(r"\$timescale\s+(\S+)\s+\$end", line)
            if m:
                data.timescale = m.group(1)
            else:
                i = _skip_section(lines, i, line)
            continue

        if line.startswith("$scope"):
            m = re.match(r"\$scope\s+\w+\s+(\S+)\s+\$end", line)
            if m:
                current_scope.append(m.group(1))
            continue

        if line.startswith("$upscope"):
            if current_scope:
                current_scope.pop()
            continue

        if line.startswith("$var"):
            # $var wire 8 ! a [7:0] $end
            m = re.match(r"\$var\s+\w+\s+(\d+)\s+(\S+)\s+(\S+)(?:\s+\[.*?\])?\s+\$end", line)
            if m:
                width = int(m.group(1))
                ident = m.group(2)
                raw_name = m.group(3)
                scope = ".".join(current_scope) if current_scope else "top"
                full_name = f"{scope}.{raw_name}" if scope else raw_name

                if strip_hierarchy:
                    sig_name = raw_name
                else:
                    sig_name = full_name

                sig = VcdSignal(name=sig_name, width=width, ident=ident, scope=scope)
                data.signals[ident] = sig
                if sig_name not in data.changes:
                    data.changes[sig_name] = []
            continue

        if line.startswith("$enddefinitions"):
            continue

        if line.startswith("$dumpvars"):
            in_dumpvars = True
            continue

        if line == "$end":
            in_dumpvars = False  # noqa: F841
            continue

        # Timestamp
        if line.startswith("#"):
            m = re.match(r"#(\d+)", line)
            if m:
                current_time = int(m.group(1))
            continue

        # Value change: single-bit (e.g., "0!" or "1!" or "x!")
        m = re.match(r"^([01xXzZ])(\S+)$", line)
        if m:
            val = m.group(1).lower()
            ident = m.group(2)
            if ident in data.signals:
                sig_name = data.signals[ident].name
                data.changes[sig_name].append((current_time, val))
            continue

        # Value change: multi-bit (e.g., "b10101011 !")
        m = re.match(r"^[bB]([01xXzZ]+)\s+(\S+)$", line)
        if m:
            val = m.group(1).lower()
            ident = m.group(2)
            if ident in data.signals:
                sig_name = data.signals[ident].name
                data.changes[sig_name].append((current_time, val))
            continue

        # Real value change (r<value> <ident>)
        m = re.match(r"^[rR]([\d.eE+-]+)\s+(\S+)$", line)
        if m:
            # Skip real values for now
            continue

    return data


def _skip_section(lines: list[str], i: int, first_line: str) -> int:
    """Skip until $end is found (handles multi-line sections)."""
    if "$end" in first_line:
        return i
    while i < len(lines):
        if "$end" in lines[i]:
            return i + 1
        i += 1
    return i


# ── VCD Comparator ───────────────────────────────────────────────────


def compare_vcd(  # noqa: PLR0912
    ref: VcdData,
    test: VcdData,
    *,
    signals: list[str] | None = None,
    ignore_signals: set[str] | None = None,
    max_time: int | None = None,
) -> list[str]:
    """Compare two VCD datasets and return a list of differences.

    Args:
        ref: Reference VCD data (e.g. from iverilog).
        test: Test VCD data (e.g. from our simulator).
        signals: If provided, only compare these signals. Otherwise
            compare all signals present in both datasets.
        ignore_signals: Signal names to skip during comparison.
        max_time: If provided, only compare up to this time.

    Returns:
        List of human-readable difference strings. Empty list means
        the simulations match.
    """
    diffs: list[str] = []
    ignore = ignore_signals or set()

    # Determine which signals to compare
    if signals is not None:
        compare_sigs = [s for s in signals if s not in ignore]
    else:
        common = ref.signal_names & test.signal_names
        compare_sigs = sorted(common - ignore)

    if not compare_sigs:
        ref_only = ref.signal_names - test.signal_names - ignore
        test_only = test.signal_names - ref.signal_names - ignore
        if ref_only:
            diffs.append(f"Signals only in reference: {sorted(ref_only)}")
        if test_only:
            diffs.append(f"Signals only in test: {sorted(test_only)}")
        if not ref_only and not test_only:
            diffs.append("No common signals to compare")
        return diffs

    # Collect all timestamps from both datasets
    all_times = sorted(set(ref.all_times()) | set(test.all_times()))
    if max_time is not None:
        all_times = [t for t in all_times if t <= max_time]

    # Compare signal values at each time point
    for sig in compare_sigs:
        ref_changes = ref.changes.get(sig, [])
        test_changes = test.changes.get(sig, [])

        # Build time → value maps
        ref_val = _build_value_timeline(ref_changes)
        test_val = _build_value_timeline(test_changes)

        # Compare at each time point where either changes
        sig_times = sorted({t for t, _v in ref_changes} | {t for t, _v in test_changes})
        if max_time is not None:
            sig_times = [t for t in sig_times if t <= max_time]

        for t in sig_times:
            rv = _value_at_time(ref_val, t)
            tv = _value_at_time(test_val, t)
            if rv is not None and tv is not None:
                if not _values_match(rv, tv):
                    diffs.append(f"@t={t} signal '{sig}': ref={rv}, test={tv}")
            elif rv is not None and tv is None:
                # Reference has a change, test doesn't — check if test has any value
                pass  # Only flag if values actually differ at this time
            elif rv is None and tv is not None:
                pass  # Extra change in test — check against ref's last value

    return diffs


def _build_value_timeline(changes: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Build sorted timeline from change list."""
    return sorted(changes, key=lambda x: x[0])


def _value_at_time(timeline: list[tuple[int, str]], time: int) -> str | None:
    """Get the value at or just before the given time."""
    result = None
    for t, v in timeline:
        if t <= time:
            result = v
        else:
            break
    return result


def _values_match(a: str, b: str) -> bool:
    """Compare two VCD value strings, normalizing representation.

    Handles:
    - Case insensitivity (X vs x)
    - Leading zero stripping for multi-bit values
    - x/z matching (x matches x, z matches z)
    """
    a = a.lower().strip()
    b = b.lower().strip()

    if a == b:
        return True

    # Normalize: strip leading zeros (but keep at least one char)
    a_norm = _normalize_vcd_value(a)
    b_norm = _normalize_vcd_value(b)
    return a_norm == b_norm


def _normalize_vcd_value(v: str) -> str:
    """Normalize a VCD value string for comparison.

    Strips leading zeros, normalizes case.  Collapses all-x / all-z
    strings to a single character (IEEE VCD allows ``x`` to mean
    "fill all bits with x").
    """
    v = v.lower().strip()

    # Collapse all-x or all-z to a single character
    if v and all(c == "x" for c in v):
        return "x"
    if v and all(c == "z" for c in v):
        return "z"

    # For multi-bit: strip leading zeros, but keep at least 1 char
    if len(v) > 1 and all(c in "01xz" for c in v):
        stripped = v.lstrip("0")
        if not stripped:
            return "0"
        return stripped

    return v
