"""Generic ready/valid stream source endpoint.

Drives a Pulp-style ``valid_i``/``ready_o``/``data_i`` (or prefixed
variant) bundle. Unlike :class:`AXIStreamSource`, this endpoint has no
notion of frame boundaries (no ``tlast``), no per-element packing, and
no AXIS sideband (``tkeep``/``tdest``/``tuser``/``tid``). One queued
"item" maps to exactly one accepted handshake on the bus.

Optional same-direction sideband signals (anything besides ``valid``,
``ready``, and the primary data port) can be driven by passing a
``sideband`` mapping at :meth:`send` time.
"""

from __future__ import annotations

from collections import deque

from veriforge.sim.step_harness import step_drive


class StreamSource:
    """Pure-Python ready/valid stream source endpoint.

    Args:
        sim: Owning :class:`Simulator`.
        signals: Map of role -> DUT port name. Required keys:
            ``valid`` (output of testbench, input of DUT),
            ``ready`` (input of testbench, output of DUT). Optional:
            ``data`` (testbench output) plus any number of additional
            same-direction sideband signal names.
    """

    def __init__(self, sim, signals: dict[str, str]):
        if "valid" not in signals or "ready" not in signals:
            raise ValueError("StreamSource requires at least 'valid' and 'ready' signal names")
        self.sim = sim
        self.signals = dict(signals)
        self.pause = False
        self._paused_this_cycle: bool = False
        self.queue: deque[tuple[int, dict[str, int]]] = deque()
        self._current: tuple[int, dict[str, int]] | None = None
        self._sampled_handshake = False

        self.valid = sim.signal(signals["valid"])
        self.ready = sim.signal(signals["ready"])
        self.data = sim.signal(signals["data"]) if "data" in signals else None
        # All other entries are sideband output signals.
        self._sideband = {
            role: sim.signal(name) for role, name in signals.items() if role not in {"valid", "ready", "data"}
        }
        self._drive_idle()

    def _drive_idle(self) -> None:
        step_drive(self.sim, self.sim._engine, self.valid.name, 0)
        if self.data is not None:
            step_drive(self.sim, self.sim._engine, self.data.name, 0)
        for sig in self._sideband.values():
            step_drive(self.sim, self.sim._engine, sig.name, 0)

    def send(self, data: int = 0, *, sideband: dict[str, int] | None = None) -> None:
        """Queue a single beat for transmission."""
        self.queue.append((int(data), dict(sideband) if sideband else {}))

    def write(self, items) -> None:
        """Queue many beats at once. Accepts an iterable of ``int`` or
        ``(int, sideband_dict)`` tuples."""
        for item in items:
            if isinstance(item, tuple):
                self.send(item[0], sideband=item[1])
            else:
                self.send(item)

    def count(self) -> int:
        return len(self.queue) + (1 if self._current is not None else 0)

    def empty(self) -> bool:
        return self.count() == 0

    def tick_pre(self) -> None:
        self._paused_this_cycle = self.pause() if callable(self.pause) else bool(self.pause)
        if self._current is None and self.queue:
            self._current = self.queue.popleft()

        if self._paused_this_cycle or self._current is None:
            self._drive_idle()
            return

        data, sideband = self._current
        step_drive(self.sim, self.sim._engine, self.valid.name, 1)
        if self.data is not None:
            step_drive(self.sim, self.sim._engine, self.data.name, data)
        for role, sig in self._sideband.items():
            step_drive(self.sim, self.sim._engine, sig.name, int(sideband.get(role, 0)))

    def sample_pre(self) -> None:
        self._sampled_handshake = False
        if self._paused_this_cycle or self._current is None:
            return
        self._sampled_handshake = int(self.ready.value) == 1

    def tick_post(self) -> None:
        if self._paused_this_cycle or self._current is None:
            return
        if self._sampled_handshake or int(self.ready.value) == 1:
            self._current = None
