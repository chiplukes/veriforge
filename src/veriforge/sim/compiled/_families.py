"""Share one implementation among process functions that differ only in constants.

A generate loop elaborates into one process per iteration, and the generated
functions differ only in integer constants: signal ids, the loop index, a bit
position. Emitted one function each, the generated code grows with the number
of iterations -- a 65536-iteration loop over a flip-flop became 1.3M lines of
Cython, at about 13 GB of Cython memory per million lines (an ibex reducer
candidate). Grouped, a family of at least ``VERIFORGE_COMPILE_FAMILY_MIN``
members (default 64; 0 disables) gets one shared body; each member keeps its
own two-line wrapper with the original signature (process tables, the scan
delta loop and settle code call members by name) that passes the member's row
of a constant table.

Only decimal literals that differ between members become table reads; equal
ones stay literal, so the C compiler still folds them. Literals in
declarations (``cdef`` lines: array sizes), strings and comments, and
non-canonical or large literals are left alone and must match exactly.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from veriforge._env import get_env

# One alternative per token class; only `num` is a candidate for a parameter.
_TOKEN = re.compile(
    r'"""[\s\S]*?"""'
    r"|^[ \t]*cdef [^\n]*"  # (declarations: array sizes must stay constant)
    r"|'(?:\\.|[^'\\\n])*'"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|\#[^\n]*"
    r"|[A-Za-z_]\w*"
    r"|\d+\.\d*|\d+[eE][-+]?\d+|\d+[A-Za-z_]\w*"  # (floats, suffixed: 1LL)
    r"|(?<![\w.])(?P<num>0|[1-9]\d*)(?![\w.])",
    re.M,
)
_MAX_PARAM = (1 << 31) - 1
# Expression temporaries (`_et12_v`), numbered by a counter that runs across
# processes, so siblings declare different names. They are function locals:
# numbered by first appearance in the template, and restored for an unshared
# function (its text then is exactly what was generated).
_TEMP = re.compile(r"\b_et(\d+)_([vm])\b")
# `1 << k` with both literals is folded by Cython at full precision; with k a
# table read the left operand would be a C int and the shift overflow.
_SHIFT_LEFT = re.compile(r"(?<![\w.\])])(\d+)(\s*<<\s*\(*\s*)(?=\x00)")


def family_min() -> int:
    raw = (get_env("COMPILE_FAMILY_MIN", "64") or "64").strip()
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("VERIFORGE_COMPILE_FAMILY_MIN must be a nonnegative integer (0 disables)") from None
    if value < 0:
        raise ValueError("VERIFORGE_COMPILE_FAMILY_MIN must be a nonnegative integer (0 disables)")
    return value


def split_constants(body: str) -> tuple[tuple[str, ...], tuple[int, ...]]:
    """*body* as literal segments around its parameterizable constants, and their values."""
    segments: list[str] = []
    values: list[int] = []
    cursor = 0
    for match in _TOKEN.finditer(body):
        num = match["num"]
        if num is None or int(num) > _MAX_PARAM:
            continue
        segments.append(body[cursor : match.start()])
        values.append(int(num))
        cursor = match.end()
    segments.append(body[cursor:])
    return tuple(segments), tuple(values)


def join_constants(segments: tuple[str, ...], values: tuple[int, ...]) -> str:
    parts = [segments[0]]
    for seg, value in zip(segments[1:], values, strict=True):
        parts.extend((str(value), seg))
    return "".join(parts)


def canonical_temps(body: str) -> tuple[str, tuple[int, ...]]:
    """*body* with its temporaries renumbered from 0, and their original numbers in that order."""
    order: dict[int, int] = {}

    def rename(match: re.Match[str]) -> str:
        number = order.setdefault(int(match[1]), len(order))
        return f"_et{number}_{match[2]}"

    return _TEMP.sub(rename, body), tuple(order)


def restore_temps(body: str, temps: tuple[int, ...]) -> str:
    if not temps:
        return body
    return _TEMP.sub(lambda m: f"_et{temps[int(m[1])]}_{m[2]}", body)


class _Member:
    __slots__ = ("header", "template", "temps", "values")

    def __init__(self, header: str, template: int, values: tuple[int, ...], temps: tuple[int, ...]):
        self.header = header
        self.template = template
        self.values = values
        self.temps = temps


# cm:f3b8d1
class ProcessFamilies:
    """Collect process functions, then emit them with families shared.

    ``add`` takes a function's lines (signature first) in output order;
    ``emit`` writes every function in that same order -- an unshared one
    exactly as given, a family member as a wrapper, its family's shared
    body just before the first member. Templates are stored once, so
    holding a design's functions costs about its distinct text.
    """

    def __init__(self, minimum: int):
        self.minimum = minimum
        self._templates: dict[tuple[str, tuple[str, ...]], int] = {}
        self._keys: list[tuple[str, tuple[str, ...]]] = []
        self._members: list[tuple[str, bool, _Member]] = []

    def add(self, func_parts: list[str], prefix: str, use_sv: bool) -> None:
        body, temps = canonical_temps("\n".join(func_parts[1:]))
        segments, values = split_constants(body)
        key = (prefix, segments)
        template = self._templates.setdefault(key, len(self._keys))
        if template == len(self._keys):
            self._keys.append(key)
        self._members.append((prefix, use_sv, _Member(func_parts[0], template, values, temps)))

    def emit(self, write: Callable[[str], None]) -> int:
        """Write every function via *write* (one chunk each); return the number of families."""
        members_of: dict[int, list[_Member]] = {}
        for _prefix, _sv, member in self._members:
            members_of.setdefault(member.template, []).append(member)
        varying = {t: _varying(ms) for t, ms in members_of.items() if self.minimum and len(ms) >= self.minimum}
        names: dict[int, int] = {}
        rows: dict[int, int] = {}
        for _prefix, use_sv, member in self._members:
            segments = self._keys[member.template][1]
            if member.template not in varying:
                body = restore_temps(join_constants(segments, member.values), member.temps)
                write("\n".join((member.header, body)))
                continue
            slots = varying[member.template]
            if member.template not in names:
                names[member.template] = len(names)
                write(_shared_body(names[member.template], segments, members_of[member.template], slots, use_sv))
            fam = names[member.template]
            row = rows[member.template] = rows.get(member.template, -1) + 1
            table = f"&VF_FAM_{fam}[{row}][0]" if slots else "NULL"
            args = "c, sv, sm" if use_sv else "c"
            write("\n".join((member.header, f"    _vf_fam_{fam}({args}, {table})", "")))
        return len(names)


def _shared_body(fam: int, segments: tuple[str, ...], group: list[_Member], slots: list[int], use_sv: bool) -> str:
    slot = {position: j for j, position in enumerate(slots)}
    first = group[0].values
    parts = [segments[0]]
    for position, seg in enumerate(segments[1:]):
        parts.extend((f"\x00{slot[position]}\x00" if position in slot else str(first[position]), seg))
    body = _SHIFT_LEFT.sub(r"(<long long>\1)\2", "".join(parts))
    body = re.sub(r"\x00(\d+)\x00", r"_vf_k[\1]", body)
    lines = []
    if slots:
        n, p = len(group), len(slots)
        rows = ",\n".join("{" + ", ".join(str(m.values[i]) for i in slots) + "}" for m in group)
        lines += [
            "cdef extern from *:",
            '    """',
            f"    static const long long VF_FAM_{fam}[{n}][{p}] = {{",
            rows,
            "    };",
            '    """',
            f"    const long long VF_FAM_{fam}[{n}][{p}]",
            "",
        ]
    sv = "long long *sv, long long *sm, " if use_sv else ""
    lines += [f"cdef void _vf_fam_{fam}(SimCtx *c, {sv}const long long *_vf_k) noexcept nogil:", body]
    return "\n".join(lines)


def _varying(group: list[_Member]) -> list[int]:
    first = group[0].values
    return [i for i in range(len(first)) if any(m.values[i] != first[i] for m in group)]
