"""Narrow signal accessor Cython helpers: wmask, _sig_word_val, etc."""

from __future__ import annotations

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _gen_narrow_accessor_code() -> list[str]:
    """Return list of Cython source lines for low-level signal-word read helpers."""
    return (_TEMPLATE_DIR / "narrow_accessors.pxi").read_text().splitlines()
