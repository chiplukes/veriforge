"""AXI4 (full) downstream responder/model.

Pure-Python AXI4 slave-side responder that mirrors :class:`AXILiteResponder`
but supports INCR bursts, ID echo, and WLAST/RLAST sequencing. Behaves as
``always_ready`` by default and auto-ticks via
:func:`register_time_step_callback`.

Only INCR (``burst == 1``) bursts are fully modeled. Single-beat FIXED
bursts work too (since walking the same address is correct), but WRAP is
not implemented (the responder treats addresses as INCR even if the master
sends WRAP).

Supports a lightweight DDR/HBM-style latency/bandwidth model: the first
read (write) beat returned to an idle pipeline pays a randomized
``rd_latency_cycles`` (``wr_latency_cycles``) delay, while subsequent
back-to-back bursts queued behind it are throttled to sustain
``max_bw_percent`` (``wr_max_bw_percent``) of the theoretical peak instead
of re-paying that latency. Also supports per-channel PAUSE (``.pause_aw``,
``.pause_w``, ``.pause_ar``, ``.pause_b``, ``.pause_r``) in addition to the
legacy combined ``.pause``, and can be constructed against a DUT that only
exposes a write channel (AW/W/B) or only a read channel (AR/R) — the
missing side is simply left inert.
"""

from __future__ import annotations

import random

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
        memory_depth: int | None = None,
        default_read_value: int = 0,
        default_write_resp: int = 0,
        default_read_resp: int = 0,
        always_ready: bool = True,
        store_writes: bool = True,
        strict: bool = False,
        rd_latency_cycles: int = 1,
        wr_latency_cycles: int = 1,
        max_bw_percent: int = 100,
        wr_max_bw_percent: int | None = None,
        latency_seed: int | None = None,
    ) -> None:
        self.sim = sim
        self.prefix = prefix
        self.strict = strict
        self.clock = sim.signal(clock_name)

        # A DUT may expose only a write channel (AW/W/B) or only a read
        # channel (AR/R) — e.g. a read-only DMA engine. Detect whole-group
        # presence up front so the missing side's signals stay ``None``
        # instead of raising at construction; the tick logic below already
        # treats ``None`` signals as permanently low/absent.
        self._has_write_channel = resolve_signal_name(sim, prefix, "awvalid") is not None
        self._has_read_channel = resolve_signal_name(sim, prefix, "arvalid") is not None
        if not self._has_write_channel and not self._has_read_channel:
            raise ValueError(
                f"AXI4 responder: neither a write (AW/W/B) nor a read (AR/R) channel was found on prefix {prefix!r}"
            )

        # AW (master-driven, observed)
        self.awaddr = self._sig("awaddr", required=self._has_write_channel)
        self.awlen = self._sig("awlen", required=False)
        self.awsize = self._sig("awsize", required=False)
        self.awburst = self._sig("awburst", required=False)
        self.awvalid = self._sig("awvalid", required=self._has_write_channel)
        self.awready = self._sig("awready", required=self._has_write_channel)
        self.awid = self._sig("awid", required=False)

        # W (master-driven, observed)
        self.wdata = self._sig("wdata", required=self._has_write_channel)
        self.wstrb = self._sig("wstrb", required=False)
        self.wlast = self._sig("wlast", required=False)
        self.wvalid = self._sig("wvalid", required=self._has_write_channel)
        self.wready = self._sig("wready", required=self._has_write_channel)

        # B (slave-driven)
        self.bresp = self._sig("bresp", required=self._has_write_channel)
        self.bvalid = self._sig("bvalid", required=self._has_write_channel)
        self.bready = self._sig("bready", required=self._has_write_channel)
        self.bid = self._sig("bid", required=False)

        # AR (master-driven, observed)
        self.araddr = self._sig("araddr", required=self._has_read_channel)
        self.arlen = self._sig("arlen", required=False)
        self.arsize = self._sig("arsize", required=False)
        self.arburst = self._sig("arburst", required=False)
        self.arvalid = self._sig("arvalid", required=self._has_read_channel)
        self.arready = self._sig("arready", required=self._has_read_channel)
        self.arid = self._sig("arid", required=False)

        # R (slave-driven)
        self.rdata = self._sig("rdata", required=self._has_read_channel)
        self.rresp = self._sig("rresp", required=self._has_read_channel)
        self.rlast = self._sig("rlast", required=False)
        self.rvalid = self._sig("rvalid", required=self._has_read_channel)
        self.rready = self._sig("rready", required=self._has_read_channel)
        self.rid = self._sig("rid", required=False)

        self.memory = dict(initial_memory or {})
        self.memory_depth = memory_depth
        self.default_read_value = default_read_value
        self.default_write_resp = default_write_resp
        self.default_read_resp = default_read_resp
        self.always_ready = always_ready
        self.store_writes = store_writes

        # Legacy combined pause — still gates AW/W/AR together.
        self.pause = False
        # Per-channel pause (new). Each accepts False/True, a zero-arg
        # callable, or a PauseGenerator, same as the legacy `.pause`.
        # `.pause_b`/`.pause_r` gate *starting* a new response only (they
        # don't interrupt a burst already in flight, since AXI4 forbids
        # dropping VALID once asserted without a handshake).
        self.pause_aw = False
        self.pause_w = False
        self.pause_ar = False
        self.pause_b = False
        self.pause_r = False

        self.data_bytes = (self.wdata.width if self.wdata is not None else self.rdata.width) // 8

        # Latency/bandwidth model (DDR/HBM-style: random-ish latency to the
        # first beat of an idle pipeline, sustained `max_bw_percent`
        # throughput for bursts queued up behind it).
        self.rd_latency_cycles = rd_latency_cycles
        self.wr_latency_cycles = wr_latency_cycles
        self.max_bw_percent = max_bw_percent
        self.wr_max_bw_percent = max_bw_percent if wr_max_bw_percent is None else wr_max_bw_percent
        self._rng = random.Random(latency_seed)  # noqa: S311 — not for cryptography
        self._cycle_count = 0
        self._rd_primed = False
        self._wr_primed = False
        # "Primed" tracks *request* cadence, not internal FSM occupancy: a
        # single-beat burst can dispatch and retire within one cycle, so
        # sampling "is the pipeline empty right now" at posedges can't tell
        # a genuinely idle gap from a fully back-to-back stream (both look
        # empty between beats). Instead, track the cycle each AR was
        # accepted (each write burst completed); a new arrival more than
        # one cycle after the previous one is a real gap and resets primed
        # — anything tighter is treated as sustained.
        self._r_last_arrival_cycle: int | None = None
        self._wr_last_arrival_cycle: int | None = None
        self._r_waiting: dict | None = None  # burst popped off _ar_pending, waiting out latency/bw
        self._r_wait_target_cycle = 0
        # Set at the retiring posedge, consumed on the *following* low
        # phase (see `_on_time_step`'s handling of it) -- see the docstring
        # on that handling for why presenting the next beat can't happen
        # synchronously at the retiring edge itself.
        self._r_needs_present = False
        self._b_waiting: tuple[int, int] | None = None  # (resp, id) waiting out latency/bw
        self._b_wait_target_cycle = 0

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

        self._ar_pending: list[dict] = []
        self._r_active = False
        self._r_beats: list[tuple[int, int, int]] = []  # (data, resp, id), with last = rlast

        # Edge tracking.
        self._prev_clk = self._read_known(self.clock) or 0
        self._aw_seen = False
        self._w_seen = False
        self._ar_seen = False
        # BREADY/RREADY as sampled during the *low phase* -- i.e. the value
        # they held stably throughout the cycle leading up to the next
        # rising edge. See the `is_posedge` retire block for why the
        # retire check must use these rather than a live read.
        self._bready_sampled = False
        self._rready_sampled = False

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

    @staticmethod
    def _is_paused(pause_attr) -> bool:
        return pause_attr() if callable(pause_attr) else bool(pause_attr)

    def _draw_latency_cycles(self, mean_cycles: int) -> int:
        """Extra wait cycles before the first beat of a freshly-primed pipeline.

        ``mean_cycles <= 1`` means "respond as soon as possible" (0 extra
        wait cycles — this is what reproduces pre-latency-model behaviour at
        the default of 1). Larger means draw a mildly jittered value
        centered on ``mean_cycles``.
        """
        if mean_cycles <= 1:
            return 0
        half = max(1, mean_cycles // 2)
        lo = max(1, mean_cycles - half)
        hi = mean_cycles + half
        return self._rng.randint(lo, hi) - 1

    def _draw_bw_stall_cycles(self, bw_percent: int) -> int:
        """Extra stall cycles so the *average* start rate approaches bw_percent%.

        Draws a geometric number of failed Bernoulli(1 - bw_percent/100)
        trials, which is equivalent to (and cheaper than) rolling a fresh
        per-cycle pause gate every cycle until it succeeds.
        """
        if bw_percent >= 100:
            return 0
        pause_prob = max(0.0, min(1.0, 1 - bw_percent / 100))
        stall = 0
        while pause_prob > 0 and stall < 100_000 and self._rng.random() < pause_prob:
            stall += 1
        return stall

    def _check_addr_in_range(self, addr: int) -> None:
        if self.memory_depth is None:
            return
        limit = self.memory_depth * self.data_bytes
        if not (0 <= addr < limit):
            raise ValueError(
                f"AXI4 responder {self.prefix!r}: address {addr:#x} is out of range "
                f"for memory_depth={self.memory_depth} (limit={limit:#x})"
            )

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
        is_negedge = self._prev_clk == 1 and current_clk == 0

        if current_clk == 0:
            # Sample BREADY/RREADY here, during the settled low phase, for
            # the *next* posedge's retire check to use (see that block).
            # Sampling here rather than reading live at the posedge matters
            # specifically when a master asserts READY in the very same
            # cycle it also completes the AW/AR handshake that started this
            # burst: that NBA update is not "real" (in NBA terms) until the
            # following cycle, but by the time this Python callback runs at
            # that same posedge, the master's own always-block has already
            # committed it -- reading it live there would retire the beat
            # one full edge earlier than a properly-scheduled synchronous
            # reader ever could, before the master's own logic has even had
            # a chance to look at RVALID/RDATA. Sampling it here, one full
            # settled low phase before the retiring edge, reflects only
            # what was stably true for the *entire preceding* cycle.
            self._rready_sampled = self._is_high(self.rready)
            self._bready_sampled = self._is_high(self.bready)

            # Present the *next* R beat (or clear RVALID once the burst is
            # done) here, one phase after the retiring posedge, rather than
            # synchronously within that same edge's processing. A real
            # registered slave's RDATA/RVALID only becomes the *next*
            # beat's value starting the cycle *after* the edge that
            # retired the previous one -- any other synchronous logic
            # sampling RVALID/RDATA at that same retiring edge must still
            # see the beat that edge just accepted, not the one after it.
            # Because this responder is a plain Python callback rather than
            # itself simulated via proper NBA scheduling, driving the next
            # beat's data immediately (in the same call as the retiring
            # posedge, as this used to do) raced against a real RTL DUT's
            # own posedge-triggered capture of that same beat: depending on
            # event-scheduling order, the DUT could see the *next* beat's
            # data instead of the one actually being retired at that edge,
            # silently losing beat 0 of every burst and shifting every
            # later beat's captured value by one position -- reproduced
            # via a small RTL read-master FSM (pure-Python `AXI4Master`
            # doesn't hit this, since it polls settled signal values rather
            # than sampling synchronously like a real DUT does). Deferring
            # the update to here fixes it without reintroducing the
            # earlier double-accept bug (see the posedge block below):
            # the retire itself still happens at the same edge as before,
            # only the *visible new value* is delayed by one phase.
            if self._r_needs_present:
                self._r_needs_present = False
                if self._r_beats:
                    next_data, next_resp, next_rid = self._r_beats[0]
                    self._drive(self.rdata, next_data)
                    self._drive(self.rresp, next_resp)
                    self._drive(self.rid, next_rid)
                    self._drive(self.rlast, 1 if len(self._r_beats) == 1 else 0)
                    self._drive(self.rvalid, 1)
                else:
                    self._drive(self.rvalid, 0)
                    self._drive(self.rlast, 0)
                    self._r_active = False

            # Entering a fresh low phase: clear the accept-guards so a
            # master that keeps VALID asserted continuously (streaming a
            # new beat every cycle without ever deasserting) can have each
            # cycle's handshake accepted, not just the very first one. The
            # valid&ready-deassert-based reset below still guards against
            # this callback firing multiple times *within* one low phase.
            if is_negedge:
                self._aw_seen = False
                self._w_seen = False
                self._ar_seen = False

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
                        self._check_addr_in_range(beat_addr)
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
                            # Burst complete; queue B response. A gap of
                            # more than one cycle since the *previous*
                            # write burst completed means this is a fresh,
                            # isolated write, not part of a sustained
                            # stream — pay full latency again.
                            if (
                                self._wr_last_arrival_cycle is not None
                                and self._cycle_count - self._wr_last_arrival_cycle > 1
                            ):
                                self._wr_primed = False
                            self._wr_last_arrival_cycle = self._cycle_count
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
                        # A gap of more than one cycle since the previous AR
                        # arrived means this is a fresh, isolated request,
                        # not part of a sustained stream — pay full latency
                        # again rather than treating it as already primed.
                        if (
                            self._r_last_arrival_cycle is not None
                            and self._cycle_count - self._r_last_arrival_cycle > 1
                        ):
                            self._rd_primed = False
                        self._r_last_arrival_cycle = self._cycle_count
                    self._ar_seen = True
            elif not (self._is_high(self.arvalid) and self._is_high(self.arready)):
                self._ar_seen = False

            # Drive B: pop a completed write burst off the queue and let it
            # wait out its latency/bandwidth target before asserting BVALID.
            if not self._b_active and self._b_waiting is None and self._pending_b:
                self._b_waiting = self._pending_b.pop(0)
                if not self._wr_primed:
                    wait = self._draw_latency_cycles(self.wr_latency_cycles)
                    self._wr_primed = True
                else:
                    wait = self._draw_bw_stall_cycles(self.wr_max_bw_percent)
                self._b_wait_target_cycle = self._cycle_count + wait
            if (
                not self._b_active
                and self._b_waiting is not None
                and self._cycle_count >= self._b_wait_target_cycle
                and not self._is_paused(self.pause_b)
            ):
                resp, bid = self._b_waiting
                self._b_waiting = None
                self._drive(self.bresp, resp)
                self._drive(self.bid, bid)
                self._drive(self.bvalid, 1)
                self._b_active = True

            # Drive R: pop the next queued read burst and let it wait out its
            # latency/bandwidth target before asserting RVALID. `rd_latency_cycles`
            # only applies to the first burst after an idle pipeline; once
            # primed, further bursts are throttled to `max_bw_percent` instead.
            if not self._r_active and self._r_waiting is None and self._ar_pending:
                self._r_waiting = self._ar_pending.pop(0)
                if not self._rd_primed:
                    wait = self._draw_latency_cycles(self.rd_latency_cycles)
                    self._rd_primed = True
                else:
                    wait = self._draw_bw_stall_cycles(self.max_bw_percent)
                self._r_wait_target_cycle = self._cycle_count + wait
            if (
                not self._r_active
                and self._r_waiting is not None
                and self._cycle_count >= self._r_wait_target_cycle
                and not self._is_paused(self.pause_r)
            ):
                burst = self._r_waiting
                self._r_waiting = None
                addr = burst["addr"]
                beat_bytes = 1 << burst["size"]
                read_mask = (1 << (self.data_bytes * 8)) - 1
                for i in range(burst["beats"]):
                    beat_addr = addr + i * beat_bytes
                    self._check_addr_in_range(beat_addr)
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
            self._cycle_count += 1
            if self.always_ready:
                _paused = self._is_paused(self.pause)
                self._drive(self.awready, 0 if (_paused or self._is_paused(self.pause_aw)) else 1)
                self._drive(self.wready, 0 if (_paused or self._is_paused(self.pause_w)) else 1)
                self._drive(self.arready, 0 if (_paused or self._is_paused(self.pause_ar)) else 1)
            # Retire B/R after handshake. AXI4 completes a transfer at the
            # rising edge where VALID and READY are both high, so the
            # transfer must retire at that same edge — not one edge later.
            # A one-extra-cycle "hold" here was tried previously to
            # accommodate a specific downstream arbiter's registered grant
            # timing, but it breaks the far more common case of a master
            # holding BREADY/RREADY asserted continuously across a burst:
            # BVALID/RVALID+RDATA would stay unchanged across two
            # consecutive rising edges, so the master observes (and
            # accepts) the same beat twice. If a downstream stage genuinely
            # needs an extra registered cycle, that belongs in the
            # DUT/interconnect, not baked into every responder.
            #
            # The check below uses `_bready_sampled`/`_rready_sampled`
            # (captured during the *preceding* low phase, above) rather
            # than a live `_is_high(self.bready/.rready)` read. This
            # matters when a master's own always-block asserts READY in
            # the very same edge it also completes the AW/AR handshake
            # that started this burst: from that always-block's own
            # perspective, its nonblocking `bready/rready <= 1` isn't
            # "real" until the *following* edge, but this Python callback
            # runs after the master's block has already committed it for
            # this same posedge -- a live read here would retire (and,
            # were it not deferred below, present wrong/skipped beat data
            # for) the transfer one full edge earlier than any
            # properly-scheduled synchronous reader could ever observe it,
            # before the master's own logic has had a single chance to
            # look at BVALID/RVALID. Reproduced via a small RTL read-master
            # FSM: every beat's captured value came out shifted by one
            # position, with beat 0 silently lost and the final beat never
            # written (pure-Python `AXI4Master` never hit this, since it
            # polls settled signal values rather than sampling
            # synchronously like a real DUT does).
            if self._b_active and self._bready_sampled:
                self._drive(self.bvalid, 0)
                self._b_active = False

            # Retire one R beat per accepted handshake, same edge as above.
            # The *next* beat's data (or RVALID's deassertion) is presented
            # one phase later, in the `current_clk == 0` handling of
            # `_r_needs_present` above -- see that comment for why.
            if self._r_active and self._rready_sampled:
                self._r_beats.pop(0)
                self._r_needs_present = True

        self._prev_clk = current_clk
