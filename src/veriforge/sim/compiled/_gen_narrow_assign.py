"""Blocking (assign) narrow signal operation Cython helpers."""

from __future__ import annotations

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _gen_narrow_assign_code() -> list[str]:
    """Return list of Cython source lines for all _whole_assign_* helpers (excluding mem-element)."""
    return (_TEMPLATE_DIR / "narrow_assign.pxi").read_text().splitlines()
