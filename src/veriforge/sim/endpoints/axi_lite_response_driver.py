"""AXI-Lite response driver for timing-sensitive tests."""

from __future__ import annotations

from .axi_lite_common import _AXILiteSignals


class AXILiteResponseDriver(_AXILiteSignals):
    """Drive AXI-Lite ready and response channels explicitly.

    This complements AXILiteRequestDriver for tests that need precise control over
    when a downstream target accepts requests or returns responses.
    """

    def __init__(self, sim, prefix: str, *, clock_name: str = "clk") -> None:
        super().__init__(sim, prefix, clock_name=clock_name)
        self.awready = self._resolve_signal("awready")
        self.wready = self._resolve_signal("wready")
        self.bresp = self._resolve_signal("bresp")
        self.bvalid = self._resolve_signal("bvalid")
        self.arready = self._resolve_signal("arready")
        self.rdata = self._resolve_signal("rdata")
        self.rresp = self._resolve_signal("rresp")
        self.rvalid = self._resolve_signal("rvalid")
        self.idle()

    def idle(self) -> None:
        self._drive(self.awready, 0)
        self._drive(self.wready, 0)
        self._drive(self.bresp, 0)
        self._drive(self.bvalid, 0)
        self._drive(self.arready, 0)
        self._drive(self.rdata, 0)
        self._drive(self.rresp, 0)
        self._drive(self.rvalid, 0)

    def set_write_ready(self, asserted: bool) -> None:
        value = int(asserted)
        self._drive(self.awready, value)
        self._drive(self.wready, value)

    def set_read_ready(self, asserted: bool) -> None:
        self._drive(self.arready, int(asserted))

    def begin_write_response(self, resp: int = 0) -> None:
        self._drive(self.bresp, resp)
        self._drive(self.bvalid, 1)

    def end_write_response(self) -> None:
        self._drive(self.bvalid, 0)

    def begin_read_response(self, data: int, *, resp: int = 0) -> None:
        self._drive(self.rdata, data)
        self._drive(self.rresp, resp)
        self._drive(self.rvalid, 1)

    def end_read_response(self) -> None:
        self._drive(self.rvalid, 0)
