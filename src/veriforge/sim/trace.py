"""Reusable simulation tracing helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .elaborate import is_synthesized_local_name
from .vcd import VcdWriter

if TYPE_CHECKING:
    import io
    from collections.abc import Iterable

    from .testbench import Simulator


def _split_hierarchy(name: str) -> tuple[str, str]:
    """Split a hierarchical signal name into (scope, leaf_name)."""
    if "." in name:
        parts = name.rsplit(".", 1)
        return parts[0], parts[1]
    return "top", name


class _TimeStepCallbackChain:
    __slots__ = ("callbacks",)

    def __init__(self, callbacks) -> None:
        self.callbacks = list(callbacks)

    def __call__(self, sched) -> None:
        for callback in tuple(self.callbacks):
            callback(sched)


class TimeStepCallbackHandle:
    """Disposable registration for scheduler time-step callbacks."""

    __slots__ = ("_callback", "_closed", "_sched")

    def __init__(self, sched, callback) -> None:
        self._sched = sched
        self._callback = callback
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        current = self._sched._on_time_step
        if current is self._callback:
            self._sched._on_time_step = None
        elif isinstance(current, _TimeStepCallbackChain):
            current.callbacks = [callback for callback in current.callbacks if callback is not self._callback]
            if not current.callbacks:
                self._sched._on_time_step = None
            elif len(current.callbacks) == 1:
                self._sched._on_time_step = current.callbacks[0]
        self._closed = True

    def __enter__(self) -> TimeStepCallbackHandle:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def register_time_step_callback(sched, callback) -> TimeStepCallbackHandle:
    """Register a scheduler callback without clobbering existing listeners."""

    current = sched._on_time_step
    if current is None:
        sched._on_time_step = callback
    elif isinstance(current, _TimeStepCallbackChain):
        current.callbacks.append(callback)
    else:
        sched._on_time_step = _TimeStepCallbackChain([current, callback])
    return TimeStepCallbackHandle(sched, callback)


class VcdTraceSession:
    """Context manager that records simulator signal changes to a VCD sink."""

    def __init__(
        self,
        sim: Simulator,
        output: str | Path | io.TextIOBase,
        *,
        timescale: str = "1ns",
        signal_names: Iterable[str] | None = None,
    ) -> None:
        self._sim = sim
        self._sched = sim._sched
        self._writer = VcdWriter(str(output) if isinstance(output, Path) else output, timescale=timescale)
        all_names = signal_names if signal_names is not None else self._sched.signal_names()
        self._signal_names = sorted(n for n in all_names if not is_synthesized_local_name(n))

        for signal_name in self._signal_names:
            scope, leaf = _split_hierarchy(signal_name)
            self._writer.add_signal(signal_name, width=sim.read(signal_name).width, scope=scope, vcd_name=leaf)

        self._writer.write_header(scope_modules=sim.hierarchy())
        self._writer.write_initial({signal_name: sim.read(signal_name) for signal_name in self._signal_names})

        def _record_callback(sched) -> None:
            self._writer.set_time(sched.time)
            for signal_name in self._signal_names:
                self._writer.change(signal_name, sched.read_signal(signal_name))

        self._callback_handle = register_time_step_callback(self._sched, _record_callback)
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._callback_handle.close()
        self._writer.finalize()
        self._closed = True

    def __enter__(self) -> VcdTraceSession:
        return self

    def __exit__(self, *_args) -> None:
        self.close()


def attach_vcd(  # cm:5b5a9e
    sim: Simulator,
    output: str | Path | io.TextIOBase,
    *,
    timescale: str = "1ns",
    signal_names: Iterable[str] | None = None,
) -> VcdTraceSession:
    """Attach VCD tracing to an existing simulator.

    The returned session records initial values immediately, then appends value
    changes after each completed time step until the session is closed.
    """

    return VcdTraceSession(sim, output, timescale=timescale, signal_names=signal_names)
