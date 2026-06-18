"""Non-blocking (stage) narrow signal operation Cython helpers."""

from __future__ import annotations

from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _gen_narrow_stage_code() -> list[str]:
    """Return list of Cython source lines for _whole_assign_signal and all _whole_stage_* helpers."""
    return (_TEMPLATE_DIR / "narrow_stage.pxi").read_text().splitlines()
