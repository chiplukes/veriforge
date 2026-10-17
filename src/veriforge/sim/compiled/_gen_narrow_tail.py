"""Tail Cython helpers: slice extraction, sign extension, and $display output functions."""

from __future__ import annotations

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _gen_narrow_tail_code() -> list[str]:
    """Return list of Cython source lines for slice/const helpers and display output functions."""
    # (real_ops.pxi: `$__vf_real`, sim/reals.py -- shared with vm-fast)
    return [
        *(_TEMPLATE_DIR / "narrow_tail.pxi").read_text().splitlines(),
        *(_TEMPLATE_DIR / "real_ops.pxi").read_text().splitlines(),
    ]
