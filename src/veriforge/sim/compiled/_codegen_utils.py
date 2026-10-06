"""Shared constants and helpers used by codegen modules."""

from __future__ import annotations

import re

from veriforge._env import get_env
from veriforge.semantics import const_int as _const_int

_WORD_BITS = 64

_PROCESS_LOOP_LIMIT = 100_000
_I32_MAX = 0x7FFFFFFF
_I32_MIN = -0x80000000

DELTA_ENGINE_MODES = ("scan", "queue")


def delta_engine_mode() -> str:
    """Which ``delta_loop`` implementation codegen emits
    (``VERIFORGE_DELTA_ENGINE``).

    ``"scan"`` (default): each delta iteration scans all signals and checks
    every continuous-assign/combinational process -- per-iteration cost
    scales with total design size. ``"queue"`` (opt-in): per-iteration cost
    proportional to real activity (see
    ``notes/plans/work_queue_delta_engine.md``) -- 3-5x faster on large,
    low-activity designs, but slower than ``scan`` when most of a design is
    active every cycle. Both run the identical set of processes in the
    identical order each iteration, so results and delta-iteration counts
    are identical; only speed differs.
    """
    mode = (get_env("DELTA_ENGINE", "scan") or "scan").strip().lower()
    if mode not in DELTA_ENGINE_MODES:
        raise ValueError(f"VERIFORGE_DELTA_ENGINE must be one of {DELTA_ENGINE_MODES}, got {mode!r}")
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
