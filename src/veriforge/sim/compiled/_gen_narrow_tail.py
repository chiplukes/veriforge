"""Tail Cython helpers: slice extraction, sign extension, and $display output functions."""

from __future__ import annotations

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _gen_narrow_tail_code() -> list[str]:
    """Return list of Cython source lines for slice/const helpers and display output functions."""
    return (_TEMPLATE_DIR / "narrow_tail.pxi").read_text().splitlines()
