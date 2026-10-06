"""Shared constants and helpers used by codegen modules."""

from __future__ import annotations

import re

from veriforge._env import get_env
from veriforge.semantics import const_int as _const_int

_WORD_BITS = 64

_PROCESS_LOOP_LIMIT = 100_000
_I32_MAX = 0x7FFFFFFF
_I32_MIN = -0x80000000

DELTA_ENGINE_MODES = ("auto", "scan", "queue")

# `auto` picks the queue engine for designs with more than this many
# continuous-assign + combinational processes. Measured crossover (see
# notes/benchmarks_work_queue.md): queue loses ~25% on a 9-process design and
# ~10% at ~40 processes, and wins 1.5x+ at ~320 processes unless nearly all of
# the design is active every cycle.
AUTO_QUEUE_MIN_PROCESSES = 64


def delta_engine_mode() -> str:
    """The requested ``delta_loop`` implementation (``VERIFORGE_DELTA_ENGINE``).

    ``"scan"``: each delta iteration scans all signals and checks every
    continuous-assign/combinational process -- per-iteration cost scales with
    total design size; cheapest per process actually run. ``"queue"``:
    per-iteration cost proportional to real activity, and redundant reruns of
    pure processes skipped (see ``notes/plans/work_queue_delta_engine.md``) --
    1.5-5x faster on larger designs at low-to-moderate activity, slower on
    tiny designs or when nearly everything is active every cycle. ``"auto"``
    (default): ``queue`` above ``AUTO_QUEUE_MIN_PROCESSES`` processes, else
    ``scan`` -- resolved per design by ``resolve_delta_engine``. Results and
    delta-iteration counts are identical either way; only speed differs.
    """
    mode = (get_env("DELTA_ENGINE", "auto") or "auto").strip().lower()
    if mode not in DELTA_ENGINE_MODES:
        raise ValueError(f"VERIFORGE_DELTA_ENGINE must be one of {DELTA_ENGINE_MODES}, got {mode!r}")
    return mode


def resolve_delta_engine(n_processes: int) -> str:
    """The engine actually emitted for a design with *n_processes*
    continuous-assign + combinational processes: ``"scan"`` or ``"queue"``."""
    mode = delta_engine_mode()
    if mode == "auto":
        return "queue" if n_processes > AUTO_QUEUE_MIN_PROCESSES else "scan"
    return mode


# Verilog operator to Cython infix/prefix mapping.
_BINARY_VALUE_OP: dict[str, tuple[str, bool]] = {
    "+": ("+", True),
    "-": ("-", True),
    "*": ("*", True),
    "/": ("/", False),
    "%": ("%", False),
    "**": ("**", True),
    "&": ("&", False),
    "|": ("|", False),
    "^": ("^", False),
    "~^": ("^", False),
    "^~": ("^", False),
    "<<": ("<<", True),
    ">>": (">>", False),
    "<<<": ("<<", True),
    ">>>": (">>_ARITH", False),
    "==": ("==", False),
    "!=": ("!=", False),
    "===": ("==", False),
    "!==": ("!=", False),
    "<": ("<", False),
    "<=": ("<=", False),
    ">": (">", False),
    ">=": (">=", False),
    "&&": ("and", False),
    "||": ("or", False),
}

_COMPARISON_OPS = frozenset({"==", "!=", "===", "!==", "<", "<=", ">", ">=", "&&", "||"})

# Bitwise ops must evaluate operands at their natural width (not the surrounding
# context width) so every bit participates in the operation.  Without this, an
# if-condition like `(a+b) & c` would mask (a+b) to 1 bit before the &.
_NATURAL_WIDTH_OPS = _COMPARISON_OPS | frozenset({"&", "|", "^", "~^", "^~"})

_UNARY_PREFIX: dict[str, str] = {
    "~": "~",
    "!": "not ",
    "-": "-",
    "+": "+",
}

_REDUCTION_OPS = frozenset({"&", "|", "^", "~&", "~|", "~^", "^~"})


def _cy_lit(val: int) -> str:
    """Format val as a Cython integer literal safe for nogil blocks."""
    if _I32_MIN <= val <= _I32_MAX:
        return str(val)
    if 0 < val < (1 << 63):
        vp1 = val + 1
        if (vp1 & (vp1 - 1)) == 0:
            return f"wmask({vp1.bit_length() - 1})"
        if val < (1 << 32):
            hi16 = val >> 16
            lo16 = val & 0xFFFF
            return f"((wmask(16) + 1) * {hi16} + {lo16})"
        hi = val >> 32
        lo = val & 0xFFFFFFFF
        hi_str = _cy_lit(hi)
        lo_str = _cy_lit(lo) if lo > _I32_MAX else str(lo)
        return f"({hi_str} * (wmask(16) + 1) * (wmask(16) + 1) + {lo_str})"
    if val < 0:
        pos = -val
        return f"(-{_cy_lit(pos)})"
    return f"(<long long>{val})"


def _cy_hex(val: int) -> str:
    """Like _cy_lit but emits hex notation for small values."""
    if val > _I32_MAX or val < _I32_MIN:
        return _cy_lit(val)
    return hex(val)


def _safe_const_name(name: str) -> str:
    """Sanitize a signal name for use as a Cython DEF constant."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name).upper()


def _safe_ident(name: str) -> str:
    """Sanitize a name for use as a Cython identifier."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _cy_u64_hex(val: int) -> str:
    """Format a 64-bit chunk as an unsigned C literal."""
    if val == 0:
        return "0"
    return f"(<unsigned long long>0x{val:x})"
