"""AXI-Stream source endpoint."""

from __future__ import annotations

from collections import deque

from veriforge.sim.step_harness import step_drive

from .frame import AXIStreamFrame
from .helpers import resolve_signal_name


class AXIStreamSource:  # cm:8b7f1d
    """Pure-Python AXI-Stream source endpoint.

    The endpoint drives a source interface such as ``s_axis_*`` or ``m_axis_*``
    through the existing Simulator API.
    """

    def __init__(self, sim, prefix: str):
        self.sim = sim
        self.prefix = prefix
        self.pause = False
        self._paused_this_cycle: bool = False
        self.queue: deque[AXIStreamFrame] = deque()
        self._beat_queue: deque[tuple[int, int, int, int, int, int]] = deque()
        self._current_beat: tuple[int, int, int, int, int, int] | None = None
        self._sampled_handshake = False

        self.tvalid = self._required_signal("tvalid")
        self.tready = self._required_signal("tready")
        self.tdata = self._required_signal("tdata")
        self.tlast = self._required_signal("tlast")
        self.tkeep = self._optional_signal("tkeep")
        self.tdest = self._optional_signal("tdest")
        self.tid = self._optional_signal("tid")
        self.tuser = self._optional_signal("tuser")

        self.elements_per_beat, self.element_size_bits = self._infer_layout()
        self.endian: str = "little"
        self._drive_idle()

    def _required_signal(self, suffix: str):
        resolved = resolve_signal_name(self.sim, self.prefix, suffix)
        if resolved is None:
            raise KeyError(f"AXIStream source: required signal {self.prefix}_{suffix} not found")
        return self.sim.signal(resolved)

    def _optional_signal(self, suffix: str):
        resolved = resolve_signal_name(self.sim, self.prefix, suffix)
        if resolved is None:
            return None
        return self.sim.signal(resolved)

    def _infer_layout(self) -> tuple[int, int]:
        if self.tkeep is not None:
            elements_per_beat = self.tkeep.width
            element_size_bits = self.tdata.width // elements_per_beat
        else:
            elements_per_beat = 1
            element_size_bits = self.tdata.width
        return elements_per_beat, element_size_bits

    def _drive_idle(self) -> None:
        step_drive(self.sim, self.sim._engine, self.tvalid.name, 0)
        step_drive(self.sim, self.sim._engine, self.tdata.name, 0)
        step_drive(self.sim, self.sim._engine, self.tlast.name, 0)
        if self.tkeep is not None:
            step_drive(self.sim, self.sim._engine, self.tkeep.name, 0)
        if self.tdest is not None:
            step_drive(self.sim, self.sim._engine, self.tdest.name, 0)
        if self.tid is not None:
            step_drive(self.sim, self.sim._engine, self.tid.name, 0)
        if self.tuser is not None:
            step_drive(self.sim, self.sim._engine, self.tuser.name, 0)

    def _load_next_frame(self) -> None:
        if self._beat_queue or not self.queue:
            return
        frame = self.queue.popleft()
        frame = AXIStreamFrame(
            frame,
            elements_per_beat=self.elements_per_beat,
            element_size_bits=self.element_size_bits,
            endian=self.endian,
        )
        tdata, tkeep, tdest, tuser, tid, tlast = frame.to_beats()
        self._beat_queue.extend(zip(tdata, tkeep, tdest, tuser, tid, tlast, strict=True))

    def send(self, frame: AXIStreamFrame | bytes | bytearray | list[int]) -> None:
        if isinstance(frame, AXIStreamFrame):
            self.queue.append(AXIStreamFrame(frame))
        else:
            self.queue.append(
                AXIStreamFrame(
                    frame,
                    elements_per_beat=self.elements_per_beat,
                    element_size_bits=self.element_size_bits,
                    endian=self.endian,
                )
            )

    def write(self, data: bytes | bytearray | list[int]) -> None:
        self.send(data)

    def count(self) -> int:
        return len(self.queue) + (1 if self._current_beat is not None else 0) + len(self._beat_queue)

    def empty(self) -> bool:
        return self.count() == 0

    def tick_pre(self) -> None:
        self._paused_this_cycle = self.pause() if callable(self.pause) else bool(self.pause)
        self._load_next_frame()
        if self._current_beat is None and self._beat_queue:
            self._current_beat = self._beat_queue.popleft()

        if self._paused_this_cycle or self._current_beat is None:
            self._drive_idle()
            return

        tdata, tkeep, tdest, tuser, tid, tlast = self._current_beat
        step_drive(self.sim, self.sim._engine, self.tvalid.name, 1)
        step_drive(self.sim, self.sim._engine, self.tdata.name, tdata)
        step_drive(self.sim, self.sim._engine, self.tlast.name, tlast)
        if self.tkeep is not None:
            step_drive(self.sim, self.sim._engine, self.tkeep.name, tkeep)
        if self.tdest is not None:
            step_drive(self.sim, self.sim._engine, self.tdest.name, tdest)
        if self.tid is not None:
            step_drive(self.sim, self.sim._engine, self.tid.name, tid)
        if self.tuser is not None:
            step_drive(self.sim, self.sim._engine, self.tuser.name, tuser)

    def sample_pre(self) -> None:
        self._sampled_handshake = False
        if self._paused_this_cycle or self._current_beat is None:
            return
        self._sampled_handshake = int(self.tready.value) == 1

    def tick_post(self) -> None:
        if self._paused_this_cycle or self._current_beat is None:
            return
        if self._sampled_handshake:
            self._current_beat = None
