"""Simple synchronous memory-bus responder endpoint (SRAM/BRAM style)."""

from __future__ import annotations

from veriforge.sim.step_harness import step_drive

from ..trace import register_time_step_callback


def _apply_write_strobes(current_value: int, data_value: int, strobe_value: int, byte_count: int) -> int:
    updated = current_value
    for byte_index in range(byte_count):
        if strobe_value & (1 << byte_index):
            mask = 0xFF << (byte_index * 8)
            updated = (updated & ~mask) | (((data_value >> (byte_index * 8)) & 0xFF) << (byte_index * 8))
    return updated & ((1 << (byte_count * 8)) - 1)


class MemBusResponder:  # cm:c3f9a2
    """Respond to synchronous memory-bus transactions (SRAM/BRAM style).

    Registers with the simulator time-step callback to auto-tick.  On each
    rising edge:

    * If ``wen`` is asserted: stores ``wdata`` (applying ``be`` strobes when
      present) into :attr:`memory` and appends ``(addr, data, strobe)`` to
      :attr:`write_log`.
    * If ``ren`` is asserted (or the port is absent): drives ``rdata`` from
      :attr:`memory` (falling back to *default_read_value*), and asserts
      ``rvalid`` when that signal exists.

    The responder is compatible with :class:`MemBusMaster` and can be used
    as the bench-side model when the DUT is the memory-bus master.

    Args:
        sim: Owning :class:`Simulator`.
        signals: Mapping of canonical role → actual DUT port name.
            Required keys: ``"addr"``, ``"wdata"``, ``"rdata"``, ``"wen"``.
            Optional keys: ``"ren"``, ``"be"``, ``"rvalid"``.
        clock_name: Name of the clock signal.
        initial_memory: Optional pre-populated backing store ``{addr: value}``.
        default_read_value: Value returned for addresses not in ``memory``.
        store_writes: When *True* (default), write transactions update
            :attr:`memory`.  Set to *False* to log but not store writes.
    """

    def __init__(
        self,
        sim,
        signals: dict[str, str],
        *,
        clock_name: str = "clk",
        initial_memory: dict[int, int] | None = None,
        default_read_value: int = 0,
        store_writes: bool = True,
    ) -> None:
        for req in ("addr", "wdata", "rdata", "wen"):
            if req not in signals:
                raise ValueError(f"MemBusResponder: required signal role {req!r} missing from signals map")
        self.sim = sim
        self.clock = sim.signal(clock_name)
        self.memory: dict[int, int] = dict(initial_memory or {})
        self.default_read_value = default_read_value
        self.store_writes = store_writes
        self.write_log: list[tuple[int, int, int]] = []
        self.read_log: list[int] = []

        self.addr = sim.signal(signals["addr"])
        self.wdata = sim.signal(signals["wdata"])
        self.rdata = sim.signal(signals["rdata"])
        self.wen = sim.signal(signals["wen"])
        self.ren = sim.signal(signals["ren"]) if "ren" in signals else None
        self.be = sim.signal(signals["be"]) if "be" in signals else None
        self.rvalid = sim.signal(signals["rvalid"]) if "rvalid" in signals else None

        self.data_bytes = max(1, self.wdata.width // 8)
        self._prev_clk = self._read_known(self.clock) or 0

        self._drive(self.rdata, 0)
        if self.rvalid is not None:
            self._drive(self.rvalid, 0)

        self._callback_handle = register_time_step_callback(sim._sched, self._on_time_step)

    # ------------------------------------------------------------------ helpers

    def _drive(self, signal, value: int) -> None:
        if signal is None:
            return
        step_drive(self.sim, self.sim._engine, signal.name, value)

    def _read_known(self, signal) -> int | None:
        if signal is None:
            return None
        current = signal.value
        if current.mask != 0:
            return None
        return int(current)

    def _is_high(self, signal) -> bool:
        return self._read_known(signal) == 1

    def close(self) -> None:
        """Detach the auto-tick callback."""
        self._callback_handle.close()

    def __enter__(self) -> MemBusResponder:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    # ------------------------------------------------------------------ tick

    def _on_time_step(self, _sched) -> None:
        current_clk = self._read_known(self.clock)
        if current_clk is None:
            return
        is_posedge = self._prev_clk == 0 and current_clk == 1
        self._prev_clk = current_clk
        if not is_posedge:
            return

        addr = self._read_known(self.addr)
        if addr is None:
            return

        # Write: store wdata on wen.
        if self._is_high(self.wen):
            data = self._read_known(self.wdata)
            if data is not None:
                if self.be is not None:
                    strb = self._read_known(self.be) or ((1 << self.data_bytes) - 1)
                else:
                    strb = (1 << self.data_bytes) - 1
                if self.store_writes:
                    current = self.memory.get(addr, 0)
                    self.memory[addr] = _apply_write_strobes(current, data, strb, self.data_bytes)
                self.write_log.append((addr, data, strb))

        # Read: drive rdata when ren is asserted (or no ren port).
        do_read = self.ren is None or self._is_high(self.ren)
        if do_read:
            data_mask = (1 << (self.data_bytes * 8)) - 1
            rdata = self.memory.get(addr, self.default_read_value) & data_mask
            self._drive(self.rdata, rdata)
            self.read_log.append(addr)
            if self.rvalid is not None:
                self._drive(self.rvalid, 1)
        elif self.rvalid is not None:
            self._drive(self.rvalid, 0)
