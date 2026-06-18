"""AXI-Lite master helper."""

from __future__ import annotations

from .axi_lite_common import _AXILiteSignals
from .helpers import _settle_current_time


class AXILiteResponseError(RuntimeError):
    """Raised when an AXI-Lite transaction completes with an unexpected response."""


class AXILiteMaster(_AXILiteSignals):  # cm:6c5a9f
    """Pure-Python AXI-Lite master helper for single-beat transactions."""

    def __init__(self, sim, prefix: str, *, clock_name: str = "clk", default_timeout_cycles: int = 50):
        super().__init__(sim, prefix, clock_name=clock_name)
        self.default_timeout_cycles = default_timeout_cycles

        self.awaddr = self._resolve_signal("awaddr")
        self.awprot = self._resolve_signal("awprot", required=False)
        self.awvalid = self._resolve_signal("awvalid")
        self.awready = self._resolve_signal("awready")

        self.wdata = self._resolve_signal("wdata")
        self.wstrb = self._resolve_signal("wstrb")
        self.wvalid = self._resolve_signal("wvalid")
        self.wready = self._resolve_signal("wready")

        self.bresp = self._resolve_signal("bresp")
        self.bvalid = self._resolve_signal("bvalid")
        self.bready = self._resolve_signal("bready")

        self.araddr = self._resolve_signal("araddr")
        self.arprot = self._resolve_signal("arprot", required=False)
        self.arvalid = self._resolve_signal("arvalid")
        self.arready = self._resolve_signal("arready")

        self.rdata = self._resolve_signal("rdata")
        self.rresp = self._resolve_signal("rresp")
        self.rvalid = self._resolve_signal("rvalid")
        self.rready = self._resolve_signal("rready")

        self._drive_idle()

    def _drive_idle(self) -> None:
        self._drive(self.awaddr, 0)
        self._drive(self.awprot, 0)
        self._drive(self.awvalid, 0)
        self._drive(self.wdata, 0)
        self._drive(self.wstrb, 0)
        self._drive(self.wvalid, 0)
        self._drive(self.bready, 0)
        self._drive(self.araddr, 0)
        self._drive(self.arprot, 0)
        self._drive(self.arvalid, 0)
        self._drive(self.rready, 0)

    def _step_cycle(self) -> None:
        _settle_current_time(self.sim, self.clock.name)
        while True:
            previous_clock = int(self.clock.value)
            if not self.sim.run_step():
                raise RuntimeError("simulation stopped before AXI-Lite transaction completed")
            current_clock = int(self.clock.value)
            if previous_clock == 0 and current_clock == 1:
                return

    def write(  # noqa: PLR0913
        self,
        addr: int,
        data: int,
        *,
        strb: int | None = None,
        prot: int = 0,
        expected_resp: int = 0,
        timeout_cycles: int | None = None,
    ) -> int:
        timeout = self.default_timeout_cycles if timeout_cycles is None else timeout_cycles
        if strb is None:
            strb = (1 << self.wstrb.width) - 1

        self._drive(self.awaddr, addr)
        self._drive(self.awprot, prot)
        self._drive(self.awvalid, 1)
        self._drive(self.wdata, data)
        self._drive(self.wstrb, strb)
        self._drive(self.wvalid, 1)

        aw_done = False
        w_done = False
        for _ in range(timeout):
            _settle_current_time(self.sim, self.clock.name)
            aw_accepting = not aw_done and self._is_high(self.awready)
            w_accepting = not w_done and self._is_high(self.wready)
            self._step_cycle()
            if aw_accepting or (not aw_done and self._is_high(self.awready)):
                aw_done = True
                self._drive(self.awvalid, 0)
            if w_accepting or (not w_done and self._is_high(self.wready)):
                w_done = True
                self._drive(self.wvalid, 0)
            if aw_done and w_done:
                break
        else:
            self._drive_idle()
            raise TimeoutError("AXI-Lite write address/data handshake timed out")

        self._drive(self.bready, 1)
        for _ in range(timeout):
            if self._is_high(self.bvalid):
                bresp = int(self.bresp.value)
                self._step_cycle()
                self._drive(self.bready, 0)
                if bresp != expected_resp:
                    self._drive_idle()
                    raise AXILiteResponseError(
                        f"AXI-Lite write response mismatch: expected {expected_resp:#x}, got {bresp:#x}"
                    )
                self._drive_idle()
                return bresp
            self._step_cycle()

        self._drive_idle()
        raise TimeoutError("AXI-Lite write response timed out")

    def read(
        self,
        addr: int,
        *,
        prot: int = 0,
        expected_resp: int = 0,
        timeout_cycles: int | None = None,
    ) -> int:
        timeout = self.default_timeout_cycles if timeout_cycles is None else timeout_cycles

        self._drive(self.araddr, addr)
        self._drive(self.arprot, prot)
        self._drive(self.arvalid, 1)

        for _ in range(timeout):
            _settle_current_time(self.sim, self.clock.name)
            ar_accepting = self._is_high(self.arready)
            self._step_cycle()
            if ar_accepting or self._is_high(self.arready):
                self._drive(self.arvalid, 0)
                break
        else:
            self._drive_idle()
            raise TimeoutError("AXI-Lite read address handshake timed out")

        self._drive(self.rready, 1)
        for _ in range(timeout):
            if self._is_high(self.rvalid):
                rresp = int(self.rresp.value)
                rdata = int(self.rdata.value)
                self._step_cycle()
                self._drive(self.rready, 0)
                if rresp != expected_resp:
                    self._drive_idle()
                    raise AXILiteResponseError(
                        f"AXI-Lite read response mismatch: expected {expected_resp:#x}, got {rresp:#x}"
                    )
                self._drive_idle()
                return rdata
            self._step_cycle()

        self._drive_idle()
        raise TimeoutError("AXI-Lite read response timed out")
