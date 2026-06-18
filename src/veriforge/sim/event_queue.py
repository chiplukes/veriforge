"""Shared event queue and coroutine primitives used by all simulation engines."""

from __future__ import annotations

import heapq

from .executor import StopExecution
from .value import Value

__all__ = ["CoroutineMixin", "EventQueueMixin", "SignalDictBase", "TimedEvent"]


class TimedEvent:  # cm:5a3c7e
    """An event scheduled for a specific simulation time.

    Ordering: (time, seq) ensures FIFO within the same time step.
    The ``payload`` field is untyped so all engines can reuse this.
    """

    __slots__ = ("payload", "seq", "time")

    def __init__(self, time: int, payload: object, seq: int) -> None:
        self.time = time
        self.payload = payload
        self.seq = seq

    def __lt__(self, other: TimedEvent) -> bool:
        if self.time != other.time:
            return self.time < other.time
        return self.seq < other.seq


class EventQueueMixin:  # cm:8b6f1d
    """Mixin providing heapq-based event scheduling.

    Subclasses must initialise ``_event_queue: list[TimedEvent]`` and
    ``_event_seq: int`` (typically in ``__init__``).
    """

    _event_queue: list[TimedEvent]
    _event_seq: int

    def _schedule_event(self, time: int, payload: object) -> None:
        """Schedule an event at the given time."""
        self._event_seq += 1
        heapq.heappush(self._event_queue, TimedEvent(time, payload, self._event_seq))

    def _pop_events_at(self, time: int) -> list[object]:
        """Pop all events at exactly the given time, return payloads."""
        result: list[object] = []
        while self._event_queue and self._event_queue[0].time == time:
            result.append(heapq.heappop(self._event_queue).payload)
        return result


class CoroutineMixin:  # cm:f1d5b8
    """Mixin for coroutine-based timing-control fallback in initial/always blocks.

    Both ``VMScheduler`` and ``CompiledScheduler`` use the reference
    ``StatementExecutor`` to handle ``#delay`` and ``@(event)`` controls.
    The lifecycle is identical: create generator, iterate with ``next()``,
    schedule resume events, and loop on ``StopIteration`` for always blocks.

    Subclass must provide the following attributes (set during ``__init__``):

    - ``_initial_coroutines: dict[int, Generator]``
    - ``_always_timing_coroutines: dict[int, tuple[Generator | None, object]]``
    - ``_ref_executor``: reference ``StatementExecutor``
    - ``_ref_ctx``: reference ``EvalContext``
    - ``_event_queue``: from ``EventQueueMixin``
    - ``_schedule_event(time, payload)``: from ``EventQueueMixin``

    Subclass must implement the following hooks:

    - ``_coro_sync_in(names=None)``: sync engine state → reference context
    - ``_coro_sync_out(names=None)``: sync reference context → engine state
    - ``time`` (property or slot): current simulation time
    """

    def _coro_post_resume(self) -> None:
        """Hook called after successful coroutine resume (e.g. VCD wiring). Default no-op."""

    def _coro_flush_nba(self) -> None:
        """Commit pending fallback NBAs before syncing state back to the engine."""
        if self._ref_executor.nba_queue:
            self._ref_executor.apply_nba(self._ref_ctx)

    def _coro_get_sync_names(self, proc_id: int) -> set[str] | None:
        """Return signal names for targeted sync, or None for full sync. Default: None."""
        return None

    def _coro_get_initial_sync_names(self, proc_id: int) -> set[str] | None:
        """Return signal names for targeted sync of initial blocks, or None for full sync. Default: None."""
        return None

    def _run_initial_coro(self, body: object, proc_id: int) -> bool:
        """Start an initial block as a coroutine. Returns True if $finish."""
        names = self._coro_get_initial_sync_names(proc_id)
        self._coro_sync_in(names)
        coro = self._ref_executor.execute_coroutine(body, self._ref_ctx)
        try:
            suspend = next(coro)
            self._coro_flush_nba()
            self._coro_sync_out(names)
            if suspend.delay is not None and suspend.delay >= 0:
                self._initial_coroutines[proc_id] = coro
                self._schedule_event(self.time + suspend.delay, ("initial_coro", proc_id))
            else:
                self._initial_coroutines[proc_id] = coro
        except StopIteration:
            self._coro_flush_nba()
            self._coro_sync_out(names)
            self._coro_post_resume()
        except StopExecution:
            self._coro_flush_nba()
            self._coro_sync_out(names)
            self._coro_post_resume()
            self._event_queue.clear()
            return True
        return False

    def _resume_initial_coro(self, proc_id: int) -> bool:
        """Resume a suspended initial block coroutine. Returns True if $finish."""
        coro = self._initial_coroutines.get(proc_id)
        if coro is None:
            return False

        names = self._coro_get_initial_sync_names(proc_id)
        self._coro_sync_in(names)
        try:
            suspend = next(coro)
            self._coro_flush_nba()
            self._coro_sync_out(names)
            self._coro_post_resume()
            if suspend.delay is not None and suspend.delay >= 0:
                self._schedule_event(self.time + suspend.delay, ("initial_coro", proc_id))
        except StopIteration:
            self._coro_flush_nba()
            self._coro_sync_out(names)
            self._coro_post_resume()
            del self._initial_coroutines[proc_id]
        except StopExecution:
            self._coro_flush_nba()
            self._coro_sync_out(names)
            self._coro_post_resume()
            del self._initial_coroutines[proc_id]
            self._event_queue.clear()
            return True
        return False

    def _start_always_coro(self, body: object, proc_id: int) -> None:
        """Start an always block with timing as a coroutine."""
        names = self._coro_get_sync_names(proc_id)
        self._coro_sync_in(names)
        coro = self._ref_executor.execute_coroutine(body, self._ref_ctx)
        try:
            suspend = next(coro)
            self._coro_flush_nba()
            self._coro_sync_out(names)
            self._always_timing_coroutines[proc_id] = (coro, body)
            if suspend.delay is not None and suspend.delay >= 0:
                self._schedule_event(self.time + suspend.delay, ("always_coro", proc_id))
        except StopIteration:
            self._coro_flush_nba()
            self._coro_sync_out(names)
            self._coro_post_resume()
            self._always_timing_coroutines[proc_id] = (None, body)
            self._schedule_event(self.time, ("always_coro", proc_id))
        except StopExecution:
            self._coro_flush_nba()
            self._coro_sync_out(names)
            self._coro_post_resume()

    def _resume_always_coro(self, proc_id: int) -> bool:
        """Resume a suspended always block coroutine. Returns True if $finish.

        If the coroutine completes (StopIteration), a fresh one is created
        because always blocks loop forever.
        """
        entry = self._always_timing_coroutines.get(proc_id)
        if entry is None:
            return False

        coro, body = entry
        names = self._coro_get_sync_names(proc_id)

        if coro is None:
            self._coro_sync_in(names)
            coro = self._ref_executor.execute_coroutine(body, self._ref_ctx)
        else:
            self._coro_sync_in(names)

        try:
            suspend = next(coro)
            self._coro_flush_nba()
            self._coro_sync_out(names)
            self._always_timing_coroutines[proc_id] = (coro, body)
            if suspend.delay is not None and suspend.delay >= 0:
                self._schedule_event(self.time + suspend.delay, ("always_coro", proc_id))
        except StopIteration:
            self._coro_flush_nba()
            self._coro_sync_out(names)
            self._coro_post_resume()
            self._always_timing_coroutines[proc_id] = (None, body)
            self._schedule_event(self.time, ("always_coro", proc_id))
        except StopExecution:
            self._coro_flush_nba()
            self._coro_sync_out(names)
            self._coro_post_resume()
            del self._always_timing_coroutines[proc_id]
            self._event_queue.clear()
            return True
        return False


class SignalDictBase:  # cm:6c4a9f
    """Base class for dict-like wrappers over engine signal storage.

    Subclasses implement three hooks:

    - ``_sig_map() -> dict[str, int]``: return name → signal-id mapping
    - ``_read_sid(sid) -> tuple[int, int, int]``: return ``(val, mask, width)``
    - ``_write_sid(sid, val, mask)``: write a signal by id
    """

    def _sig_map(self) -> dict[str, int]:
        raise NotImplementedError

    def _read_sid(self, sid: int) -> tuple[int, int, int]:
        raise NotImplementedError

    def _write_sid(self, sid: int, val: int, mask: int) -> None:
        raise NotImplementedError

    def get(self, name: str, default=None) -> Value | None:
        sid = self._sig_map().get(name)
        if sid is None:
            return default
        v, m, w = self._read_sid(sid)
        return Value(v, width=w, mask=m)

    def __getitem__(self, name: str) -> Value:
        v = self.get(name)
        if v is None:
            raise KeyError(name)
        return v

    def __setitem__(self, name: str, value: Value) -> None:
        sid = self._sig_map().get(name)
        if sid is not None:
            self._write_sid(sid, value.val, value.mask)

    def __contains__(self, name: str) -> bool:
        return name in self._sig_map()

    def __iter__(self):
        return iter(self._sig_map())

    def items(self):
        """Yield (name, Value) pairs for all signals."""
        for name, sid in self._sig_map().items():
            v, m, w = self._read_sid(sid)
            yield name, Value(v, width=w, mask=m)

    def keys(self):
        return self._sig_map().keys()
