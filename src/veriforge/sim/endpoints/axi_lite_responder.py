"""AXI-Lite downstream responder/model."""

from __future__ import annotations

from .axi_lite_common import _AXILiteSignals
from ..trace import register_time_step_callback


class AXILiteProtocolError(RuntimeError):
    """Raised in strict mode when the DUT violates the AXI-Lite specification.

    AXI4-Lite rules enforced:

    * Once ``AWVALID`` is asserted it must not deassert before ``AWREADY``.
      ``AWADDR`` must be stable for the same window.
    * Once ``WVALID`` is asserted it must not deassert before ``WREADY``.
      ``WDATA`` and ``WSTRB`` must be stable for the same window.
    * Once ``ARVALID`` is asserted it must not deassert before ``ARREADY``.
      ``ARADDR`` must be stable for the same window.
    """


def _apply_write_strobes(current_value: int, data_value: int, strobe_value: int, byte_count: int) -> int:
    updated = current_value
    for byte_index in range(byte_count):
        if strobe_value & (1 << byte_index):
            mask = 0xFF << (byte_index * 8)
            updated = (updated & ~mask) | (((data_value >> (byte_index * 8)) & 0xFF) << (byte_index * 8))
    return updated & ((1 << (byte_count * 8)) - 1)


class AXILiteResponder(_AXILiteSignals):  # cm:1f5c6a
    """Respond to AXI-Lite transactions on a flat signal prefix.

    The responder defaults to always-ready behavior, logs observed transactions,
    can maintain a small backing store, and can queue explicit responses when a
    test needs to model downstream behavior cycle by cycle.
    """

    def __init__(  # noqa: PLR0913, PLR0915
        self,
        sim,
        prefix: str,
        *,
        clock_name: str = "clk",
        observe_prefix: str | None = None,
        drive_prefix: str | None = None,
        initial_memory: dict[int, int] | None = None,
        default_read_value: int = 0,
        default_write_resp: int = 0,
        default_read_resp: int = 0,
        write_hold_cycles: int = 0,
        read_hold_cycles: int = 0,
        wait_for_write_ready: bool = False,
        wait_for_read_ready: bool = False,
        always_ready: bool = True,
        store_writes: bool = True,
        strict: bool = False,
    ) -> None:
        super().__init__(sim, prefix, clock_name=clock_name)
        observe_prefix = observe_prefix or prefix
        drive_prefix = drive_prefix or prefix

        self.awaddr = self._resolve_prefixed_signal(observe_prefix, "awaddr")
        self.awvalid = self._resolve_prefixed_signal(observe_prefix, "awvalid")
        self.awready = self._resolve_prefixed_signal(drive_prefix, "awready")
        self.wdata = self._resolve_prefixed_signal(observe_prefix, "wdata")
        self.wstrb = self._resolve_prefixed_signal(observe_prefix, "wstrb")
        self.wvalid = self._resolve_prefixed_signal(observe_prefix, "wvalid")
        self.wready = self._resolve_prefixed_signal(drive_prefix, "wready")
        self.bresp = self._resolve_prefixed_signal(drive_prefix, "bresp")
        self.bvalid = self._resolve_prefixed_signal(drive_prefix, "bvalid")
        self.bready = self._resolve_prefixed_signal(observe_prefix, "bready")
        self.araddr = self._resolve_prefixed_signal(observe_prefix, "araddr")
        self.arvalid = self._resolve_prefixed_signal(observe_prefix, "arvalid")
        self.arready = self._resolve_prefixed_signal(drive_prefix, "arready")
        self.rdata = self._resolve_prefixed_signal(drive_prefix, "rdata")
        self.rresp = self._resolve_prefixed_signal(drive_prefix, "rresp")
        self.rvalid = self._resolve_prefixed_signal(drive_prefix, "rvalid")
        self.rready = self._resolve_prefixed_signal(observe_prefix, "rready")

        self.memory = dict(initial_memory or {})
        self.default_read_value = default_read_value
        self.default_write_resp = default_write_resp
        self.default_read_resp = default_read_resp
        self.write_hold_cycles = write_hold_cycles
        self.read_hold_cycles = read_hold_cycles
        self.wait_for_write_ready = wait_for_write_ready
        self.wait_for_read_ready = wait_for_read_ready
        self.always_ready = always_ready
        self.store_writes = store_writes
        self.strict = strict
        self.pause = False
        self.data_bytes = self.wdata.width // 8
        self.write_log: list[tuple[int, int, int]] = []
        self.read_log: list[int] = []
        self._queued_write_responses: list[int] = []
        self._queued_read_responses: list[tuple[int, int]] = []
        self._pending_write_responses: list[int] = []
        self._pending_read_responses: list[tuple[int, int]] = []
        self._b_active = False
        self._r_active = False
        self._clear_b_after_edges: int | None = None
        self._clear_r_after_edges: int | None = None
        self._write_seen = False
        self._read_seen = False
        self._prev_clk = self._read_known(self.clock) or 0

        # Strict-mode channel-stability tracking state.
        # Each "_unacked" flag means: VALID was seen at the previous posedge
        # without the corresponding READY, so the signal must stay stable.
        self._strict_awvalid_unacked: bool = False
        self._strict_awaddr_snapshot: int = 0
        self._strict_wvalid_unacked: bool = False
        self._strict_wdata_snapshot: int = 0
        self._strict_wstrb_snapshot: int = 0
        self._strict_arvalid_unacked: bool = False
        self._strict_araddr_snapshot: int = 0

        if self.always_ready:
            self._drive(self.awready, 1)
            self._drive(self.wready, 1)
            self._drive(self.arready, 1)
        self._drive(self.bresp, 0)
        self._drive(self.bvalid, 0)
        self._drive(self.rdata, 0)
        self._drive(self.rresp, 0)
        self._drive(self.rvalid, 0)
        self._callback_handle = register_time_step_callback(self.sim._sched, self._on_time_step)

    def _resolve_prefixed_signal(self, prefix: str, logical_name: str):
        original_prefix = self.prefix
        try:
            self.prefix = prefix
            return self._resolve_signal(logical_name)
        finally:
            self.prefix = original_prefix

    def close(self) -> None:
        self._callback_handle.close()

    def __enter__(self) -> AXILiteResponder:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def queue_write_response(self, resp: int) -> None:
        self._queued_write_responses.append(resp)

    def queue_read_response(self, data: int, *, resp: int = 0) -> None:
        self._queued_read_responses.append((data, resp))

    def _next_write_resp(self) -> int:
        if self._queued_write_responses:
            return self._queued_write_responses.pop(0)
        return self.default_write_resp

    def _next_read_response(self, addr: int) -> tuple[int, int]:
        if self._queued_read_responses:
            return self._queued_read_responses.pop(0)
        read_mask = (1 << (self.data_bytes * 8)) - 1
        return self.memory.get(addr, self.default_read_value) & read_mask, self.default_read_resp

    def _check_channel_stability(self) -> None:
        """Raise AXILiteProtocolError if a channel signal violated stability rules."""
        if self._strict_awvalid_unacked:
            cur_awvalid = self._read_known(self.awvalid)
            if not cur_awvalid:
                raise AXILiteProtocolError(
                    f"AWVALID deasserted before AWREADY on prefix {self.prefix!r} "
                    f"(AWADDR was 0x{self._strict_awaddr_snapshot:x})"
                )
            cur_awaddr = self._read_known(self.awaddr) or 0
            if cur_awaddr != self._strict_awaddr_snapshot:
                raise AXILiteProtocolError(
                    f"AWADDR changed while AWVALID asserted without AWREADY "
                    f"on prefix {self.prefix!r}: "
                    f"0x{self._strict_awaddr_snapshot:x} → 0x{cur_awaddr:x}"
                )
        if self._strict_wvalid_unacked:
            cur_wvalid = self._read_known(self.wvalid)
            if not cur_wvalid:
                raise AXILiteProtocolError(f"WVALID deasserted before WREADY on prefix {self.prefix!r}")
            cur_wdata = self._read_known(self.wdata) or 0
            cur_wstrb = self._read_known(self.wstrb) or 0
            if cur_wdata != self._strict_wdata_snapshot:
                raise AXILiteProtocolError(
                    f"WDATA changed while WVALID asserted without WREADY "
                    f"on prefix {self.prefix!r}: "
                    f"0x{self._strict_wdata_snapshot:x} → 0x{cur_wdata:x}"
                )
            if cur_wstrb != self._strict_wstrb_snapshot:
                raise AXILiteProtocolError(
                    f"WSTRB changed while WVALID asserted without WREADY "
                    f"on prefix {self.prefix!r}: "
                    f"0x{self._strict_wstrb_snapshot:x} → 0x{cur_wstrb:x}"
                )
        if self._strict_arvalid_unacked:
            cur_arvalid = self._read_known(self.arvalid)
            if not cur_arvalid:
                raise AXILiteProtocolError(
                    f"ARVALID deasserted before ARREADY on prefix {self.prefix!r} "
                    f"(ARADDR was 0x{self._strict_araddr_snapshot:x})"
                )
            cur_araddr = self._read_known(self.araddr) or 0
            if cur_araddr != self._strict_araddr_snapshot:
                raise AXILiteProtocolError(
                    f"ARADDR changed while ARVALID asserted without ARREADY "
                    f"on prefix {self.prefix!r}: "
                    f"0x{self._strict_araddr_snapshot:x} → 0x{cur_araddr:x}"
                )

    def _update_strict_state(self) -> None:
        """Snapshot current channel state for next-posedge stability checks."""
        awvalid = bool(self._read_known(self.awvalid))
        awready = bool(self._read_known(self.awready))
        self._strict_awvalid_unacked = awvalid and not awready
        if self._strict_awvalid_unacked:
            self._strict_awaddr_snapshot = self._read_known(self.awaddr) or 0

        wvalid = bool(self._read_known(self.wvalid))
        wready = bool(self._read_known(self.wready))
        self._strict_wvalid_unacked = wvalid and not wready
        if self._strict_wvalid_unacked:
            self._strict_wdata_snapshot = self._read_known(self.wdata) or 0
            self._strict_wstrb_snapshot = self._read_known(self.wstrb) or 0

        arvalid = bool(self._read_known(self.arvalid))
        arready = bool(self._read_known(self.arready))
        self._strict_arvalid_unacked = arvalid and not arready
        if self._strict_arvalid_unacked:
            self._strict_araddr_snapshot = self._read_known(self.araddr) or 0

    def _on_time_step(self, _sched) -> None:  # noqa: PLR0912, PLR0915
        current_clk = self._read_known(self.clock)
        if current_clk is None:
            return
        is_posedge = self._prev_clk == 0 and current_clk == 1

        if current_clk == 0:
            write_fire = all(
                (
                    self._is_high(self.awvalid),
                    self._is_high(self.wvalid),
                    self._is_high(self.awready),
                    self._is_high(self.wready),
                )
            )
            if write_fire and not self._write_seen:
                addr = self._read_known(self.awaddr)
                data = self._read_known(self.wdata)
                strb = self._read_known(self.wstrb)
                if addr is not None and data is not None and strb is not None:
                    self.write_log.append((addr, data, strb))
                    if self.store_writes:
                        current_value = self.memory.get(addr, 0)
                        self.memory[addr] = _apply_write_strobes(current_value, data, strb, self.data_bytes)
                    self._pending_write_responses.append(self._next_write_resp())
                    self._write_seen = True
            elif not write_fire:
                self._write_seen = False

            read_fire = self._is_high(self.arvalid) and self._is_high(self.arready)
            if read_fire and not self._read_seen:
                addr = self._read_known(self.araddr)
                if addr is not None:
                    self.read_log.append(addr)
                    self._pending_read_responses.append(self._next_read_response(addr))
                    self._read_seen = True
            elif not read_fire:
                self._read_seen = False

            if (
                not self._b_active
                and self._pending_write_responses
                and (not self.wait_for_write_ready or self._is_high(self.bready))
            ):
                self._drive(self.bresp, self._pending_write_responses.pop(0))
                self._drive(self.bvalid, 1)
                self._b_active = True
                self._clear_b_after_edges = None

            if (
                not self._r_active
                and self._pending_read_responses
                and (not self.wait_for_read_ready or self._is_high(self.rready))
            ):
                read_data, read_resp = self._pending_read_responses.pop(0)
                self._drive(self.rdata, read_data)
                self._drive(self.rresp, read_resp)
                self._drive(self.rvalid, 1)
                self._r_active = True
                self._clear_r_after_edges = None

            if self._b_active and self._is_high(self.bready) and self._clear_b_after_edges is None:
                self._clear_b_after_edges = self.write_hold_cycles + 1
            if self._r_active and self._is_high(self.rready) and self._clear_r_after_edges is None:
                self._clear_r_after_edges = self.read_hold_cycles + 1

        if is_posedge:
            if self.strict:
                self._check_channel_stability()
                self._update_strict_state()
            if self.always_ready:
                _paused = self.pause() if callable(self.pause) else bool(self.pause)
                self._drive(self.awready, 0 if _paused else 1)
                self._drive(self.wready, 0 if _paused else 1)
                self._drive(self.arready, 0 if _paused else 1)
            if self._clear_b_after_edges is not None:
                self._clear_b_after_edges -= 1
                if self._clear_b_after_edges == 0:
                    self._drive(self.bvalid, 0)
                    self._b_active = False
                    self._clear_b_after_edges = None
            if self._clear_r_after_edges is not None:
                self._clear_r_after_edges -= 1
                if self._clear_r_after_edges == 0:
                    self._drive(self.rvalid, 0)
                    self._r_active = False
                    self._clear_r_after_edges = None

        self._prev_clk = current_clk
