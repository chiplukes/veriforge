"""AXI4 (full) master helper.

Pure-Python burst-capable AXI4 master. Supports INCR bursts, single-ID
transactions, optional sideband signals (id/lock/cache/prot/qos/region/
user). FIXED and WRAP burst modes are not implemented.
"""

from __future__ import annotations

from .helpers import _settle_current_time, resolve_signal_name
from veriforge.sim.step_harness import step_drive


_AXI4_OPTIONAL_INPUTS = ("awid", "awlock", "awcache", "awprot", "awqos", "awregion", "awuser")
_AXI4_OPTIONAL_W = ("wuser",)
_AXI4_OPTIONAL_AR = ("arid", "arlock", "arcache", "arprot", "arqos", "arregion", "aruser")


class AXI4ResponseError(RuntimeError):
    """Raised when an AXI4 transaction completes with an unexpected response."""


class AXI4Master:  # cm:2e8d3b
    """Pure-Python AXI4 master helper for INCR bursts on a single ID stream."""

    def __init__(self, sim, prefix: str, *, clock_name: str = "clk", default_timeout_cycles: int = 200):
        self.sim = sim
        self.prefix = prefix
        self.default_timeout_cycles = default_timeout_cycles
        self.clock = sim.signal(clock_name)

        # AW
        self.awaddr = self._sig("awaddr")
        self.awlen = self._sig("awlen")
        self.awsize = self._sig("awsize")
        self.awburst = self._sig("awburst")
        self.awvalid = self._sig("awvalid")
        self.awready = self._sig("awready")
        self.awid = self._sig("awid", required=False)
        self.awlock = self._sig("awlock", required=False)
        self.awcache = self._sig("awcache", required=False)
        self.awprot = self._sig("awprot", required=False)
        self.awqos = self._sig("awqos", required=False)
        self.awregion = self._sig("awregion", required=False)
        self.awuser = self._sig("awuser", required=False)

        # W
        self.wdata = self._sig("wdata")
        self.wstrb = self._sig("wstrb")
        self.wlast = self._sig("wlast")
        self.wvalid = self._sig("wvalid")
        self.wready = self._sig("wready")
        self.wuser = self._sig("wuser", required=False)

        # B
        self.bresp = self._sig("bresp")
        self.bvalid = self._sig("bvalid")
        self.bready = self._sig("bready")
        self.bid = self._sig("bid", required=False)

        # AR
        self.araddr = self._sig("araddr")
        self.arlen = self._sig("arlen")
        self.arsize = self._sig("arsize")
        self.arburst = self._sig("arburst")
        self.arvalid = self._sig("arvalid")
        self.arready = self._sig("arready")
        self.arid = self._sig("arid", required=False)
        self.arlock = self._sig("arlock", required=False)
        self.arcache = self._sig("arcache", required=False)
        self.arprot = self._sig("arprot", required=False)
        self.arqos = self._sig("arqos", required=False)
        self.arregion = self._sig("arregion", required=False)
        self.aruser = self._sig("aruser", required=False)

        # R
        self.rdata = self._sig("rdata")
        self.rresp = self._sig("rresp")
        self.rlast = self._sig("rlast")
        self.rvalid = self._sig("rvalid")
        self.rready = self._sig("rready")
        self.rid = self._sig("rid", required=False)

        self._drive_idle()

    # ------------------------------------------------------------------ helpers

    def _sig(self, suffix: str, *, required: bool = True):
        resolved = resolve_signal_name(self.sim, self.prefix, suffix)
        if resolved is not None:
            return self.sim.signal(resolved)
        if required:
            raise ValueError(f"AXI4 master: required signal {self.prefix}_{suffix} not found")
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

    def _drive_idle(self) -> None:
        for s in (
            self.awaddr,
            self.awlen,
            self.awsize,
            self.awburst,
            self.awvalid,
            self.awid,
            self.awlock,
            self.awcache,
            self.awprot,
            self.awqos,
            self.awregion,
            self.awuser,
            self.wdata,
            self.wstrb,
            self.wlast,
            self.wvalid,
            self.wuser,
            self.bready,
            self.araddr,
            self.arlen,
            self.arsize,
            self.arburst,
            self.arvalid,
            self.arid,
            self.arlock,
            self.arcache,
            self.arprot,
            self.arqos,
            self.arregion,
            self.aruser,
            self.rready,
        ):
            self._drive(s, 0)

    def _step_cycle(self) -> None:
        _settle_current_time(self.sim, self.clock.name)
        while True:
            previous_clock = int(self.clock.value)
            if not self.sim.run_step():
                raise RuntimeError("simulation stopped before AXI4 transaction completed")
            current_clock = int(self.clock.value)
            if previous_clock == 0 and current_clock == 1:
                return

    def _data_width_bytes(self) -> int:
        return self.wdata.width // 8

    def _default_size(self) -> int:
        # log2(beat-bytes); minimum 0
        n = self._data_width_bytes()
        size = 0
        while (1 << size) < n:
            size += 1
        return size

    # ------------------------------------------------------------------ write

    def write(  # noqa: PLR0912, PLR0915, PLR0913
        self,
        addr: int,
        data,
        *,
        strb=None,
        size: int | None = None,
        burst: int = 1,
        txn_id: int = 0,
        prot: int = 0,
        cache: int = 0,
        lock: int = 0,
        qos: int = 0,
        region: int = 0,
        user: int = 0,
        wuser: int = 0,
        expected_resp: int = 0,
        timeout_cycles: int | None = None,
    ) -> int:
        """Issue an AXI4 INCR burst write of ``len(data)`` beats."""
        if not isinstance(data, (list, tuple)):
            data = [data]
        if not data:
            raise ValueError("AXI4 write requires at least one data beat")
        beats = list(data)
        beat_count = len(beats)
        if size is None:
            size = self._default_size()
        if strb is None:
            strb_val = (1 << self.wstrb.width) - 1
            strbs = [strb_val] * beat_count
        elif isinstance(strb, int):
            strbs = [strb] * beat_count
        else:
            strbs = list(strb)
            if len(strbs) != beat_count:
                raise ValueError("strb list length must match data length")

        timeout = self.default_timeout_cycles if timeout_cycles is None else timeout_cycles

        # AW
        self._drive(self.awaddr, addr)
        self._drive(self.awlen, beat_count - 1)
        self._drive(self.awsize, size)
        self._drive(self.awburst, burst)
        self._drive(self.awvalid, 1)
        self._drive(self.awid, txn_id)
        self._drive(self.awlock, lock)
        self._drive(self.awcache, cache)
        self._drive(self.awprot, prot)
        self._drive(self.awqos, qos)
        self._drive(self.awregion, region)
        self._drive(self.awuser, user)

        # First W beat queued in parallel.
        self._drive(self.wdata, beats[0])
        self._drive(self.wstrb, strbs[0])
        self._drive(self.wlast, 1 if beat_count == 1 else 0)
        self._drive(self.wvalid, 1)
        self._drive(self.wuser, wuser)

        aw_done = False
        beats_done = 0
        for _ in range(timeout * (beat_count + 1)):
            _settle_current_time(self.sim, self.clock.name)
            aw_take = (not aw_done) and self._is_high(self.awready)
            w_take = (beats_done < beat_count) and self._is_high(self.wready)
            self._step_cycle()
            if aw_take:
                aw_done = True
                self._drive(self.awvalid, 0)
            if w_take:
                beats_done += 1
                if beats_done < beat_count:
                    self._drive(self.wdata, beats[beats_done])
                    self._drive(self.wstrb, strbs[beats_done])
                    self._drive(self.wlast, 1 if beats_done == beat_count - 1 else 0)
                else:
                    self._drive(self.wvalid, 0)
                    self._drive(self.wlast, 0)
            if aw_done and beats_done >= beat_count:
                break
        else:
            self._drive_idle()
            raise TimeoutError("AXI4 write address/data handshake timed out")

        # B
        self._drive(self.bready, 1)
        for _ in range(timeout):
            if self._is_high(self.bvalid):
                bresp = int(self.bresp.value)
                self._step_cycle()
                self._drive(self.bready, 0)
                self._drive_idle()
                if bresp != expected_resp:
                    raise AXI4ResponseError(
                        f"AXI4 write response mismatch: expected {expected_resp:#x}, got {bresp:#x}"
                    )
                return bresp
            self._step_cycle()
        self._drive_idle()
        raise TimeoutError("AXI4 write response timed out")

    # ------------------------------------------------------------------ read

    def read(  # noqa: PLR0913, PLR0912
        self,
        addr: int,
        *,
        length: int = 1,
        size: int | None = None,
        burst: int = 1,
        txn_id: int = 0,
        prot: int = 0,
        cache: int = 0,
        lock: int = 0,
        qos: int = 0,
        region: int = 0,
        user: int = 0,
        expected_resp: int = 0,
        timeout_cycles: int | None = None,
    ) -> list[int]:
        """Issue an AXI4 INCR burst read of ``length`` beats. Returns the data list."""
        if length < 1:
            raise ValueError("read length must be >= 1")
        if size is None:
            size = self._default_size()
        timeout = self.default_timeout_cycles if timeout_cycles is None else timeout_cycles

        # AR
        self._drive(self.araddr, addr)
        self._drive(self.arlen, length - 1)
        self._drive(self.arsize, size)
        self._drive(self.arburst, burst)
        self._drive(self.arvalid, 1)
        self._drive(self.arid, txn_id)
        self._drive(self.arlock, lock)
        self._drive(self.arcache, cache)
        self._drive(self.arprot, prot)
        self._drive(self.arqos, qos)
        self._drive(self.arregion, region)
        self._drive(self.aruser, user)

        for _ in range(timeout):
            _settle_current_time(self.sim, self.clock.name)
            ar_take = self._is_high(self.arready)
            self._step_cycle()
            if ar_take:
                self._drive(self.arvalid, 0)
                break
        else:
            self._drive_idle()
            raise TimeoutError("AXI4 read address handshake timed out")

        # R
        self._drive(self.rready, 1)
        beats: list[int] = []
        for _ in range(timeout * length):
            if self._is_high(self.rvalid):
                rresp = int(self.rresp.value)
                if rresp != expected_resp:
                    self._drive_idle()
                    raise AXI4ResponseError(
                        f"AXI4 read response mismatch on beat {len(beats)}: expected {expected_resp:#x}, got {rresp:#x}"
                    )
                beats.append(int(self.rdata.value))
                last = self._is_high(self.rlast)
                self._step_cycle()
                if last:
                    self._drive(self.rready, 0)
                    self._drive_idle()
                    if len(beats) != length:
                        raise AXI4ResponseError(f"AXI4 read: rlast asserted on beat {len(beats)} but expected {length}")
                    return beats
            else:
                self._step_cycle()
        self._drive_idle()
        raise TimeoutError("AXI4 read data timed out")
