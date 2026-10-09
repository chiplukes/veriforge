"""``$info`` / ``$warning`` / ``$error`` / ``$fatal`` (IEEE 1800 20.10).

Every engine already implements ``$display`` (with ``$time``) and
``$finish``, so each one lowers a severity task to those: a ``$display``
whose format starts with a marker, the severity letter and ``$time`` (then
``file:line:`` and the user's format), followed by ``$finish`` for
``$fatal``. The scheduler's ``display_output`` is a ``DisplayLog``, which
turns a marked line into the printed ``ERROR: file:line: message`` form and
records ``(time, severity, message)`` -- the uniform "simulation error"
event capture sessions trigger on (see ``trace.CaptureSession``).

Before this, reference and compiled ignored all four tasks and vm printed
``$error``/``$warning``/``$info`` like ``$display`` (no severity) and
ignored ``$fatal``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable

from veriforge.model.expressions import FunctionCall, Literal, StringLiteral
from veriforge.model.statements import SystemTaskCall

SEVERITIES = {"$info": "INFO", "$warning": "WARNING", "$error": "ERROR", "$fatal": "FATAL"}
_MARK = "\x1e"
_SEP = "\x1f"
_BY_LETTER = {name[0]: name for name in SEVERITIES.values()}


def severity_display(task: SystemTaskCall) -> tuple[SystemTaskCall, bool] | None:
    """For a severity task, ``(equivalent $display, is_fatal)``; else None.
    The caller emits ``$finish`` after the display when *is_fatal*."""
    name = task.task_name.lower()
    severity = SEVERITIES.get(name)
    if severity is None:
        return None
    args = list(task.arguments)
    fatal = name == "$fatal"
    if fatal and args and isinstance(args[0], Literal) and not isinstance(args[0], StringLiteral):
        args = args[1:]  # finish_number (0/1/2): only affects $finish's own report
    loc = task.loc
    where = f"{os.path.basename(loc.file)}:{loc.line}: " if loc is not None and loc.file and loc.line else ""
    if args and isinstance(args[0], StringLiteral):
        fmt, rest = args[0].value, args[1:]
    else:
        fmt, rest = " ".join(["%0d"] * len(args)), args
    marked = StringLiteral(value=f"{_MARK}{severity[0]}%0d{_SEP}{where}{fmt}")
    display = SystemTaskCall(
        task_name="$display", arguments=[marked, FunctionCall(name="$time", arguments=[]), *rest], loc=loc
    )
    return display, fatal


class DisplayLog(list):
    """A scheduler's ``display_output``: plain display lines, except that a
    severity line (``severity_display``) is rewritten to its printed form
    and recorded in ``events`` as ``(time, severity, message)``; each
    listener is called with that tuple."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[tuple[int, str, str]] = []
        self.listeners: list[Callable[[int, str, str], None]] = []

    def append(self, text: str) -> None:  # type: ignore[override]
        super().append(self._take(text))

    def extend(self, texts: Iterable[str]) -> None:  # type: ignore[override]
        for text in texts:
            self.append(text)

    def _take(self, text: str) -> str:
        i = text.find(_MARK)
        if i < 0 or i + 1 >= len(text):
            return text
        severity = _BY_LETTER.get(text[i + 1])
        time_str, sep, message = text[i + 2 :].partition(_SEP)
        if severity is None or not sep:
            return text
        try:
            time = int(time_str)
        except ValueError:
            return text
        event = (time, severity, message)
        self.events.append(event)
        for listener in list(self.listeners):
            listener(*event)
        return f"{text[:i]}{severity}: {message}"
