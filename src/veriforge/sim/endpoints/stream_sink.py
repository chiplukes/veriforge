"""Generic ready/valid stream sink endpoint.

Companion to :class:`StreamSource`. Asserts ``ready`` (subject to the
``pause`` flag), samples ``data`` and any additional same-direction
sideband signals on each accepted handshake, and queues the captured
records for the testbench to consume.
"""

from __future__ import annotations

from collections import deque

from veriforge.sim.step_harness import step_drive


class StreamSink:
    """Pure-Python ready/valid stream sink endpoint.

    Args:
        sim: Owning :class:`Simulator`.
        signals: Map of role -> DUT port name. Required keys:
            ``valid`` (input of testbench, output of DUT),
            ``ready`` (output of testbench, input of DUT). Optional:
            ``data`` (testbench input) plus any number of additional
            same-direction sideband signal names.
    """

    def __init__(self, sim, signals: dict[str, str]):
        if "valid" not in signals or "ready" not in signals:
            raise ValueError("StreamSink requires at least 'valid' and 'ready' signal names")
        self.sim = sim
        self.signals = dict(signals)
        self.pause = False
        self._paused_this_cycle: bool = False
        # Records: (data, sideband_dict)
        self.queue: deque[tuple[int, dict[str, int]]] = deque()
        self._sampled_handshake = False
        self._sampled_data = 0
        self._sampled_sideband: dict[str, int] = {}

        self.valid = sim.signal(signals["valid"])
        self.ready = sim.signal(signals["ready"])
        self.data = sim.signal(signals["data"]) if "data" in signals else None
        self._sideband = {
            role: sim.signal(name) for role, name in signals.items() if role not in {"valid", "ready", "data"}
        }
        step_drive(self.sim, self.sim._engine, self.ready.name, 0)

    def count(self) -> int:
        return len(self.queue)

    def empty(self) -> bool:
        return len(self.queue) == 0

    def recv(self) -> tuple[int, dict[str, int]] | None:
        if self.queue:
            return self.queue.popleft()
        return None

    def read(self, count: int = -1) -> list[int]:
        """Return up to ``count`` queued data values (-1 for all)."""
        if count < 0 or count > len(self.queue):
            count = len(self.queue)
        out = []
        for _ in range(count):
            data, _ = self.queue.popleft()
            out.append(data)
        return out

    def tick_pre(self) -> None:
        self._paused_this_cycle = self.pause() if callable(self.pause) else bool(self.pause)
        step_drive(self.sim, self.sim._engine, self.ready.name, 0 if self._paused_this_cycle else 1)

    def sample_pre(self) -> None:
        self._sampled_handshake = False
        if self._paused_this_cycle:
            return
        if int(self.valid.value) and int(self.ready.value):
            self._sampled_handshake = True
            self._sampled_data = int(self.data.value) if self.data is not None else 0
            self._sampled_sideband = {role: int(sig.value) for role, sig in self._sideband.items()}

    def tick_post(self) -> None:
        if self._paused_this_cycle or not self._sampled_handshake:
            return
        self.queue.append((self._sampled_data, dict(self._sampled_sideband)))
