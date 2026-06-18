"""AXI4 (full) downstream responder/model.

Pure-Python AXI4 slave-side responder that mirrors :class:`AXILiteResponder`
but supports INCR bursts, ID echo, and WLAST/RLAST sequencing. Behaves as
``always_ready`` by default and auto-ticks via
:func:`register_time_step_callback`.

Only INCR (``burst == 1``) bursts are fully modeled. Single-beat FIXED
bursts work too (since walking the same address is correct), but WRAP is
not implemented (the responder treats addresses as INCR even if the master
sends WRAP).
"""

from __future__ import annotations

from .helpers import resolve_signal_name
from ..step_harness import step_drive
from ..trace import register_time_step_callback


def _apply_write_strobes(current_value: int, data_value: int, strobe_value: int, byte_count: int) -> int:
    updated = current_value
    for byte_index in range(byte_count):
        if strobe_value & (1 << byte_index):
            mask = 0xFF << (byte_index * 8)
            updated = (updated & ~mask) | (((data_value >> (byte_index * 8)) & 0xFF) << (byte_index * 8))
    return updated & ((1 << (byte_count * 8)) - 1)


class AXI4ProtocolError(RuntimeError):
    """Raised in strict mode when the DUT violates the AXI4 specification."""


class AXI4Responder:  # cm:7e9b5d
    """Respond to AXI4 INCR-burst transactions on a flat signal prefix.

    Keeps a simple word-aligned ``dict[int, int]`` memory keyed by the byte
    address of each beat. Writes honour WSTRB. Reads return ``default_read_value``
    for unwritten addresses. Both write and read paths echo the transaction ID
    (``AWID``/``ARID``) on the response channels (``BID``/``RID``).
    """

    def __init__(  # noqa: PLR0913, PLR0915
        self,
        sim,
        prefix: str,
        *,
        clock_name: str = "clk",
        initial_memory: dict[int, int] | None = None,
        default_read_value: int = 0,
        default_write_resp: int = 0,
        default_read_resp: int = 0,
        always_ready: bool = True,
        store_writes: bool = True,
        strict: bool = False,
    ) -> None:
        self.sim = sim
        self.prefix = prefix
        self.strict = strict
        self.clock = sim.signal(clock_name)

        # AW (master-driven, observed)
        self.awaddr = self._sig("awaddr")
        self.awlen = self._sig("awlen", required=False)
        self.awsize = self._sig("awsize", required=False)
        self.awburst = self._sig("awburst", required=False)
        self.awvalid = self._sig("awvalid")
        self.awready = self._sig("awready")
        self.awid = self._sig("awid", required=False)

        # W (master-driven, observed)
        self.wdata = self._sig("wdata")
        self.wstrb = self._sig("wstrb", required=False)
        self.wlast = self._sig("wlast", required=False)
        self.wvalid = self._sig("wvalid")
        self.wready = self._sig("wready")

        # B (slave-driven)
        self.bresp = self._sig("bresp")
        self.bvalid = self._sig("bvalid")
        self.bready = self._sig("bready")
        self.bid = self._sig("bid", required=False)

        # AR (master-driven, observed)
        self.araddr = self._sig("araddr")
        self.arlen = self._sig("arlen", required=False)
        self.arsize = self._sig("arsize", required=False)
        self.arburst = self._sig("arburst", required=False)
        self.arvalid = self._sig("arvalid")
        self.arready = self._sig("arready")
        self.arid = self._sig("arid", required=False)

        # R (slave-driven)
        self.rdata = self._sig("rdata")
        self.rresp = self._sig("rresp")
        self.rlast = self._sig("rlast", required=False)
        self.rvalid = self._sig("rvalid")
        self.rready = self._sig("rready")
        self.rid = self._sig("rid", required=False)

        self.memory = dict(initial_memory or {})
        self.default_read_value = default_read_value
        self.default_write_resp = default_write_resp
        self.default_read_resp = default_read_resp
        self.always_ready = always_ready
        self.store_writes = store_writes
        self.pause = False
        self.data_bytes = self.wdata.width // 8

        # Logs: writes are appended per beat as (addr, data, strb).
        self.write_log: list[tuple[int, int, int]] = []
        self.read_log: list[int] = []
        # Burst-level logs.
        self.write_burst_log: list[tuple[int, int, int]] = []  # (addr, beats, txn_id)
        self.read_burst_log: list[tuple[int, int, int]] = []  # (addr, beats, txn_id)

        # Pending burst state.
        self._aw_pending: dict | None = None  # accepted AW awaiting W beats
        self._w_beats_done = 0
        self._b_active = False
        self._b_id = 0
        self._pending_b: list[tuple[int, int]] = []  # (resp, id)
        # When True, retire B at the *next* posedge instead of the current one.
        # This gives any downstream pipeline register (e.g. a crossbar S-side
        # B register that only latches after the B arbiter asserts b_grant_valid)
        # one extra posedge to capture bvalid=1 before we de-assert it.
        self._b_retire_pending: bool = False

        self._ar_pending: list[dict] = []
        self._r_active = False
        self._r_beats: list[tuple[int, int, int]] = []  # (data, resp, id), with last = rlast
        # Same one-extra-posedge hold for the R channel.
        self._r_retire_pending: bool = False

        # Edge tracking.
        self._prev_clk = self._read_known(self.clock) or 0
        self._aw_seen = False
        self._w_seen = False
        self._ar_seen = False

        if self.always_ready:
            self._drive(self.awready, 1)
            self._drive(self.wready, 1)
            self._drive(self.arready, 1)
        self._drive(self.bresp, 0)
        self._drive(self.bvalid, 0)
        self._drive(self.bid, 0)
        self._drive(self.rdata, 0)
        self._drive(self.rresp, 0)
        self._drive(self.rvalid, 0)
        self._drive(self.rlast, 0)
        self._drive(self.rid, 0)

        self._callback_handle = register_time_step_callback(self.sim._sched, self._on_time_step)

    # ------------------------------------------------------------------ helpers

    def _sig(self, suffix: str, *, required: bool = True):
        resolved = resolve_signal_name(self.sim, self.prefix, suffix)
        if resolved is not None:
            return self.sim.signal(resolved)
        if required:
            raise ValueError(f"AXI4 responder: required signal {self.prefix}_{suffix} not found")
        return None

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
        self._callback_handle.close()

    def __enter__(self) -> AXI4Responder:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def queue_write_response(self, resp: int) -> None:
        """No-op accepted for AXI-Lite parity (responses currently come from default_write_resp)."""
        # Could be extended; for now provide for API symmetry.
        self._next_write_resp_override = resp  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ tick

    def _on_time_step(self, _sched) -> None:  # noqa: PLR0912, PLR0915
        current_clk = self._read_known(self.clock)
        if current_clk is None:
            return
        is_posedge = self._prev_clk == 0 and current_clk == 1

        if current_clk == 0:
            # Accept AW.
            if self._aw_pending is None and self._is_high(self.awvalid) and self._is_high(self.awready):
                if not self._aw_seen:
                    addr = self._read_known(self.awaddr)
                    awlen = self._read_known(self.awlen) or 0
                    awsize = self._read_known(self.awsize)
                    if awsize is None:
                        awsize = (self.data_bytes.bit_length() - 1) if self.data_bytes else 0
                    awid = self._read_known(self.awid) or 0
                    if addr is not None:
                        beats = awlen + 1
                        self._aw_pending = {
                            "addr": addr,
                            "beats": beats,
                            "size": awsize,
                            "id": awid,
                            "next_addr": addr,
                        }
                        self._w_beats_done = 0
                        self.write_burst_log.append((addr, beats, awid))
                    self._aw_seen = True
            elif not (self._is_high(self.awvalid) and self._is_high(self.awready)):
                self._aw_seen = False

            # Accept W beats once an AW is in flight.
            if self._aw_pending is not None and self._is_high(self.wvalid) and self._is_high(self.wready):
                if not self._w_seen:
                    data = self._read_known(self.wdata)
                    if self.wstrb is not None:
                        strb = self._read_known(self.wstrb)
                        if strb is None:
                            strb = (1 << self.data_bytes) - 1
                    else:
                        strb = (1 << self.data_bytes) - 1
                    if data is not None:
                        beat_addr = self._aw_pending["next_addr"]
                        self.write_log.append((beat_addr, data, strb))
                        if self.store_writes:
                            current = self.memory.get(beat_addr, 0)
                            self.memory[beat_addr] = _apply_write_strobes(current, data, strb, self.data_bytes)
                        self._w_beats_done += 1
                        expected_beats = self._aw_pending["beats"]
                        # INCR by beat-byte size.
                        beat_bytes = 1 << self._aw_pending["size"]
                        self._aw_pending["next_addr"] = beat_addr + beat_bytes
                        # Strict mode: verify WLAST alignment.
                        if self.strict and self.wlast is not None:
                            wlast_now = self._is_high(self.wlast)
                            is_last_beat = self._w_beats_done >= expected_beats
                            if wlast_now and not is_last_beat:
                                raise AXI4ProtocolError(
                                    f"AXI4 protocol violation on {self.prefix!r}: "
                                    f"WLAST asserted on beat {self._w_beats_done} "
                                    f"but AWLEN+1={expected_beats} beats were expected"
                                )
                            if is_last_beat and not wlast_now:
                                raise AXI4ProtocolError(
                                    f"AXI4 protocol violation on {self.prefix!r}: "
                                    f"WLAST not asserted on final beat "
                                    f"(beat {self._w_beats_done} of {expected_beats})"
                                )
                        if self._w_beats_done >= expected_beats:
                            # Burst complete; queue B response.
                            override = getattr(self, "_next_write_resp_override", None)
                            resp = override if override is not None else self.default_write_resp
                            if override is not None:
                                self._next_write_resp_override = None  # type: ignore[attr-defined, assignment]
                            self._pending_b.append((resp, self._aw_pending["id"]))
                            self._aw_pending = None
                            self._w_beats_done = 0
                    self._w_seen = True
            elif not (self._is_high(self.wvalid) and self._is_high(self.wready)):
                self._w_seen = False

            # Accept AR.
            if self._is_high(self.arvalid) and self._is_high(self.arready):
                if not self._ar_seen:
                    addr = self._read_known(self.araddr)
                    arlen = self._read_known(self.arlen) or 0
                    arsize = self._read_known(self.arsize)
                    if arsize is None:
                        arsize = (self.data_bytes.bit_length() - 1) if self.data_bytes else 0
                    arid = self._read_known(self.arid) or 0
                    if addr is not None:
                        beats = arlen + 1
                        self._ar_pending.append({"addr": addr, "beats": beats, "size": arsize, "id": arid})
                        self.read_burst_log.append((addr, beats, arid))
                    self._ar_seen = True
            elif not (self._is_high(self.arvalid) and self._is_high(self.arready)):
                self._ar_seen = False

            # Drive B.
            if not self._b_active and self._pending_b:
                resp, bid = self._pending_b.pop(0)
                self._drive(self.bresp, resp)
                self._drive(self.bid, bid)
                self._drive(self.bvalid, 1)
                self._b_active = True

            # Drive R: prepare beat list when starting a new burst.
            if not self._r_active and self._ar_pending:
                burst = self._ar_pending.pop(0)
                addr = burst["addr"]
                beat_bytes = 1 << burst["size"]
                read_mask = (1 << (self.data_bytes * 8)) - 1
                for i in range(burst["beats"]):
                    beat_addr = addr + i * beat_bytes
                    data = self.memory.get(beat_addr, self.default_read_value) & read_mask
                    self.read_log.append(beat_addr)
                    self._r_beats.append((data, self.default_read_resp, burst["id"]))
                self._r_active = True
                # Drive first beat now.
                data, resp, rid = self._r_beats[0]
                self._drive(self.rdata, data)
                self._drive(self.rresp, resp)
                self._drive(self.rid, rid)
                self._drive(self.rlast, 1 if len(self._r_beats) == 1 else 0)
                self._drive(self.rvalid, 1)

        if is_posedge:
            if self.always_ready:
                _paused = self.pause() if callable(self.pause) else bool(self.pause)
                self._drive(self.awready, 0 if _paused else 1)
                self._drive(self.wready, 0 if _paused else 1)
                self._drive(self.arready, 0 if _paused else 1)
            # Retire B after handshake.  We hold bvalid=1 for one extra
            # posedge before de-asserting so that any downstream registered
            # stage (e.g. a crossbar S-side B register gated by b_grant_valid)
            # has time to latch bvalid=1 AFTER the B arbiter first asserts
            # b_grant_valid.  Without the extra hold the responder would
            # de-assert bvalid in the same posedge that b_grant_valid rises,
            # which is one cycle too early for the S-side register.
            if self._b_active:
                if self._b_retire_pending:
                    self._drive(self.bvalid, 0)
                    self._b_active = False
                    self._b_retire_pending = False
                elif self._is_high(self.bready):
                    self._b_retire_pending = True

            # Retire one R beat per accepted handshake.  Same one-extra-posedge
            # hold for the same reason as the B channel above.
            if self._r_active:
                if self._r_retire_pending:
                    self._r_beats.pop(0)
                    if self._r_beats:
                        data, resp, rid = self._r_beats[0]
                        self._drive(self.rdata, data)
                        self._drive(self.rresp, resp)
                        self._drive(self.rid, rid)
                        self._drive(self.rlast, 1 if len(self._r_beats) == 1 else 0)
                        self._drive(self.rvalid, 1)
                    else:
                        self._drive(self.rvalid, 0)
                        self._drive(self.rlast, 0)
                        self._r_active = False
                    self._r_retire_pending = False
                elif self._is_high(self.rready):
                    self._r_retire_pending = True

        self._prev_clk = current_clk
