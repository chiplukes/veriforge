"""AXI-Stream sink endpoint."""

from __future__ import annotations

from collections import deque

from veriforge.sim.step_harness import step_drive

from .frame import AXIStreamFrame
from .helpers import resolve_signal_name


class AXIStreamProtocolError(RuntimeError):
    """Raised in strict mode when the DUT violates the AXI-Stream specification."""


class AXIStreamSink:  # cm:f2d5b9
    """Pure-Python AXI-Stream sink endpoint.

    Args:
        sim: Owning :class:`Simulator`.
        prefix: Common port-name prefix (e.g. ``"m_axis"``).
        strict: When *True*, raises :class:`AXIStreamProtocolError` if the DUT
            de-asserts ``TVALID`` or changes ``TDATA``/``TLAST`` while
            ``TVALID`` is asserted but no handshake has occurred
            (AXI spec section 2.2.1).

    ``tready`` is optional: some DUTs (fixed-latency pipelines with no
    internal buffering) expose ``tvalid``/``tdata``/``tlast`` with no flow
    control at all. When ``tready`` is absent, the sink drives nothing and
    captures every beat the DUT presents unconditionally (handshake
    degrades to ``tvalid`` alone -- there's no other signal that could
    withhold it). Because there is then nothing to gate, setting ``pause``
    to anything but its default ``False`` raises :class:`ValueError`.

    ``tlast`` is also optional. Presence of ``tlast`` is what defines a
    "frame" for :attr:`queue` -- when it's absent, there is no wire that
    could ever signal a frame boundary, so **every accepted beat is treated
    as a complete one-beat frame**: each beat is captured, immediately
    pushed onto :attr:`queue` on its own, and read back out as its own
    single-element :class:`~veriforge.sim.endpoints.frame.AXIStreamFrame`.
    Callers of the high-level ``get()``/``expect()`` API on a ``tlast``-less
    interface should expect one call per beat, not per multi-beat frame.
    """

    def __init__(self, sim, prefix: str, *, strict: bool = False):
        self.sim = sim
        self.prefix = prefix
        self.strict = strict
        self._pause = False
        self._paused_this_cycle: bool = False
        self.queue: deque[AXIStreamFrame] = deque()
        self.read_queue: deque[int] = deque()
        self._sampled_handshake = False
        self._sampled_tdata = 0
        self._sampled_tkeep = 0
        self._sampled_tdest = 0
        self._sampled_tuser = 0
        self._sampled_tid = 0
        self._sampled_tlast = 0

        # Strict-mode monitor state.
        self._mon_prev_valid: bool = False
        self._mon_prev_handshake: bool = False
        self._mon_prev_data: int = 0
        self._mon_prev_tlast: int = 0
        self._mon_prev_tkeep: int = 0

        self.tvalid = self._required_signal("tvalid")
        self.tready = self._optional_signal("tready")
        self.tdata = self._required_signal("tdata")
        self.tlast = self._optional_signal("tlast")
        self.tkeep = self._optional_signal("tkeep")
        self.tdest = self._optional_signal("tdest")
        self.tid = self._optional_signal("tid")
        self.tuser = self._optional_signal("tuser")

        self.elements_per_beat, self.element_size_bits = self._infer_layout()
        self.endian: str = "little"
        self._pending_tdata: list[int] = []
        self._pending_tkeep: list[int] = []
        self._pending_tdest: list[int] = []
        self._pending_tuser: list[int] = []
        self._pending_tid: list[int] = []
        self._pending_tlast: list[int] = []
        if self.tready is not None:
            step_drive(self.sim, self.sim._engine, self.tready.name, 0)

    @property
    def pause(self):
        """Per-cycle pause setting; see :class:`AXIStreamSink`'s docstring for the tready-less case."""
        return self._pause

    @pause.setter
    def pause(self, value) -> None:
        if self.tready is None and value is not False:
            raise ValueError(
                f"AXIStreamSink({self.prefix!r}) has no tready signal, so there is nothing "
                "for `pause` to gate -- this is a fixed-latency stream with no flow control."
            )
        self._pause = value

    def _required_signal(self, suffix: str):
        resolved = resolve_signal_name(self.sim, self.prefix, suffix)
        if resolved is None:
            raise KeyError(f"AXIStream sink: required signal {self.prefix}_{suffix} not found")
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

    def count(self) -> int:
        return len(self.queue)

    def empty(self) -> bool:
        return len(self.queue) == 0

    def recv(self) -> AXIStreamFrame | None:
        if self.queue:
            return self.queue.popleft()
        return None

    def read(self, count: int = -1) -> list[int]:
        while self.queue:
            frame = self.queue.popleft()
            self.read_queue.extend(frame.data)
        if count < 0:
            count = len(self.read_queue)
        data = list(self.read_queue)[:count]
        for _ in range(len(data)):
            self.read_queue.popleft()
        return data

    def tick_pre(self) -> None:
        self._paused_this_cycle = self.pause() if callable(self.pause) else bool(self.pause)
        if self.tready is not None:
            step_drive(self.sim, self.sim._engine, self.tready.name, 0 if self._paused_this_cycle else 1)

    def sample_pre(self) -> None:
        tvalid_now = int(self.tvalid.value)
        # No tready at all: nothing can withhold acceptance, so the sink is
        # always ready (pause is disallowed in this mode -- see the pause
        # setter -- so _paused_this_cycle is always False here too).
        tready_now = 1 if self.tready is None else (0 if self._paused_this_cycle else int(self.tready.value))

        # Strict-mode VALID-stability and data-stability check.
        if self.strict and self._mon_prev_valid and not self._mon_prev_handshake:
            if not tvalid_now:
                raise AXIStreamProtocolError(
                    f"AXI-Stream protocol violation on {self.prefix!r}: "
                    "TVALID de-asserted before a completed handshake "
                    "(AXI spec §2.2.1)"
                )
            tdata_now = int(self.tdata.value)
            if tdata_now != self._mon_prev_data:
                raise AXIStreamProtocolError(
                    f"AXI-Stream protocol violation on {self.prefix!r}: "
                    f"TDATA changed while TVALID was asserted without handshake "
                    f"(0x{self._mon_prev_data:x} → 0x{tdata_now:x})"
                )
            tlast_now = 1 if self.tlast is None else int(self.tlast.value)
            if tlast_now != self._mon_prev_tlast:
                raise AXIStreamProtocolError(
                    f"AXI-Stream protocol violation on {self.prefix!r}: "
                    f"TLAST changed while TVALID was asserted without handshake "
                    f"({self._mon_prev_tlast} → {tlast_now})"
                )
            if self.tkeep is not None:
                tkeep_now = int(self.tkeep.value)
                if tkeep_now != self._mon_prev_tkeep:
                    raise AXIStreamProtocolError(
                        f"AXI-Stream protocol violation on {self.prefix!r}: "
                        f"TKEEP changed while TVALID was asserted without handshake "
                        f"(0x{self._mon_prev_tkeep:x} → 0x{tkeep_now:x})"
                    )

        handshake = bool(tvalid_now and tready_now)
        self._sampled_handshake = False
        if not self._paused_this_cycle and handshake:
            self._sampled_handshake = True
            self._sampled_tdata = int(self.tdata.value)
            self._sampled_tkeep = int(self.tkeep.value) if self.tkeep is not None else (1 << self.elements_per_beat) - 1
            self._sampled_tdest = int(self.tdest.value) if self.tdest is not None else 0
            self._sampled_tuser = int(self.tuser.value) if self.tuser is not None else 0
            self._sampled_tid = int(self.tid.value) if self.tid is not None else 0
            # No tlast: nothing could ever signal a frame boundary, so every
            # accepted beat completes its own one-beat frame (see docstring).
            self._sampled_tlast = 1 if self.tlast is None else int(self.tlast.value)

        # Update monitor state for next cycle.
        self._mon_prev_valid = bool(tvalid_now)
        self._mon_prev_handshake = handshake
        if tvalid_now:
            self._mon_prev_data = int(self.tdata.value)
            self._mon_prev_tlast = 1 if self.tlast is None else int(self.tlast.value)
            if self.tkeep is not None:
                self._mon_prev_tkeep = int(self.tkeep.value)

    def tick_post(self) -> None:
        if self._paused_this_cycle or not self._sampled_handshake:
            return
        self._pending_tdata.append(self._sampled_tdata)
        self._pending_tkeep.append(self._sampled_tkeep)
        self._pending_tdest.append(self._sampled_tdest)
        self._pending_tuser.append(self._sampled_tuser)
        self._pending_tid.append(self._sampled_tid)
        self._pending_tlast.append(self._sampled_tlast)

        if self._sampled_tlast:
            frame = AXIStreamFrame(
                elements_per_beat=self.elements_per_beat,
                element_size_bits=self.element_size_bits,
                endian=self.endian,
            )
            frame.from_beats(
                tdata=list(self._pending_tdata),
                tkeep=list(self._pending_tkeep),
                tdest=list(self._pending_tdest),
                tuser=list(self._pending_tuser),
                tid=list(self._pending_tid),
                tlast=list(self._pending_tlast),
            )
            self.queue.append(frame)
            self._pending_tdata.clear()
            self._pending_tkeep.clear()
            self._pending_tdest.clear()
            self._pending_tuser.clear()
            self._pending_tid.clear()
            self._pending_tlast.clear()
