"""Simple synchronous memory-bus master endpoint (SRAM/BRAM style)."""

from __future__ import annotations

from veriforge.sim.step_harness import step_drive

from .helpers import _settle_current_time


class MemBusMaster:  # cm:a6c9f4
    """Pure-Python synchronous memory-bus master (SRAM/BRAM style).

    Drives ``addr``/``wdata``/``wen`` (and optionally ``ren``/``be``) for
    single-cycle transactions and reads ``rdata`` one posedge after the
    request.  If an ``rvalid`` signal is present, :meth:`read` waits for it
    to be asserted before returning.

    Args:
        sim: Owning :class:`Simulator`.
        signals: Mapping of canonical role → actual DUT port name.
            Required keys: ``"addr"``, ``"wdata"``, ``"rdata"``, ``"wen"``.
            Optional keys: ``"ren"``, ``"be"``, ``"rvalid"``.
        clock_name: Name of the clock signal in the simulator.
        default_timeout_cycles: Max posedges to wait when polling ``rvalid``.
    """

    def __init__(
        self,
        sim,
        signals: dict[str, str],
        *,
        clock_name: str = "clk",
        default_timeout_cycles: int = 50,
    ) -> None:
        for req in ("addr", "wdata", "rdata", "wen"):
            if req not in signals:
                raise ValueError(f"MemBusMaster: required signal role {req!r} missing from signals map")
        self.sim = sim
        self.clock = sim.signal(clock_name)
        self.default_timeout_cycles = default_timeout_cycles

        self.addr = sim.signal(signals["addr"])
        self.wdata = sim.signal(signals["wdata"])
        self.rdata = sim.signal(signals["rdata"])
        self.wen = sim.signal(signals["wen"])
        self.ren = sim.signal(signals["ren"]) if "ren" in signals else None
        self.be = sim.signal(signals["be"]) if "be" in signals else None
        self.rvalid = sim.signal(signals["rvalid"]) if "rvalid" in signals else None

        self._drive_idle()

    # ------------------------------------------------------------------ helpers

    def _drive(self, signal, value: int) -> None:
        if signal is None:
            return
        step_drive(self.sim, self.sim._engine, signal.name, value)

    def _drive_idle(self) -> None:
        self._drive(self.addr, 0)
        self._drive(self.wdata, 0)
        self._drive(self.wen, 0)
        self._drive(self.ren, 0)
        if self.be is not None:
            self._drive(self.be, 0)

    def _step_cycle(self) -> None:
        """Advance the simulation exactly one rising clock edge."""
        _settle_current_time(self.sim, self.clock.name)
        while True:
            prev = int(self.clock.value)
            if not self.sim.run_step():
                raise RuntimeError("simulation stopped before MemBus transaction completed")
            curr = int(self.clock.value)
            if prev == 0 and curr == 1:
                return

    # ------------------------------------------------------------------ transactions

    def write(self, addr: int, data: int, *, be: int | None = None) -> None:
        """Drive a single synchronous write.

        Asserts ``addr``/``wdata``/``wen`` (and ``be`` when supplied) for one
        clock cycle, then returns the bus to idle.  The byte-enable defaults
        to all bytes enabled when the signal is present but ``be`` is not given.
        """
        self._drive(self.addr, addr)
        self._drive(self.wdata, data)
        self._drive(self.wen, 1)
        if be is not None:
            self._drive(self.be, be)
        elif self.be is not None:
            self._drive(self.be, (1 << self.be.width) - 1)
        self._step_cycle()
        self._drive_idle()

    def read(self, addr: int, *, timeout_cycles: int | None = None) -> int:
        """Drive a synchronous read and return the rdata value.

        Asserts ``addr`` (and ``ren`` when present) for one clock cycle.
        When an ``rvalid`` signal exists, waits up to *timeout_cycles* posedges
        for it before sampling ``rdata``.  Without ``rvalid``, ``rdata`` is
        sampled immediately after the request posedge (combinatorial or one-
        cycle-latency responder).
        """
        timeout = self.default_timeout_cycles if timeout_cycles is None else timeout_cycles
        self._drive(self.addr, addr)
        self._drive(self.ren, 1)
        self._step_cycle()
        self._drive(self.ren, 0)
        # Settle so the responder's rdata drive propagates before we sample.
        _settle_current_time(self.sim, self.clock.name)
        if self.rvalid is not None:
            if int(self.rvalid.value) == 1:
                return int(self.rdata.value)
            for _ in range(timeout):
                self._step_cycle()
                _settle_current_time(self.sim, self.clock.name)
                if int(self.rvalid.value) == 1:
                    return int(self.rdata.value)
            raise TimeoutError(f"MemBusMaster.read: rvalid did not assert within {timeout} cycles")
        return int(self.rdata.value)
