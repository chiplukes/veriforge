"""AXI-Lite request driver for stimulus-oriented tests."""

from __future__ import annotations

from .axi_lite_common import _AXILiteSignals


class AXILiteRequestDriver(_AXILiteSignals):
    """Drive AXI-Lite request channels without waiting for a full transaction.

    This is useful for converter and protocol tests that need to inject a write or
    read request while still inspecting intermediate channel behavior.
    """

    def __init__(self, sim, prefix: str, *, clock_name: str = "clk") -> None:
        super().__init__(sim, prefix, clock_name=clock_name)
        self.awaddr = self._resolve_signal("awaddr")
        self.awprot = self._resolve_signal("awprot", required=False)
        self.awvalid = self._resolve_signal("awvalid")
        self.wdata = self._resolve_signal("wdata")
        self.wstrb = self._resolve_signal("wstrb")
        self.wvalid = self._resolve_signal("wvalid")
        self.bready = self._resolve_signal("bready")
        self.araddr = self._resolve_signal("araddr")
        self.arprot = self._resolve_signal("arprot", required=False)
        self.arvalid = self._resolve_signal("arvalid")
        self.rready = self._resolve_signal("rready")
        self.idle()

    def idle(self) -> None:
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

    def begin_write(self, addr: int, data: int, *, strb: int | None = None, prot: int = 0) -> None:
        if strb is None:
            strb = (1 << self.wstrb.width) - 1
        self._drive(self.awaddr, addr)
        self._drive(self.awprot, prot)
        self._drive(self.awvalid, 1)
        self._drive(self.wdata, data)
        self._drive(self.wstrb, strb)
        self._drive(self.wvalid, 1)

    def end_write(self) -> None:
        self._drive(self.awvalid, 0)
        self._drive(self.wvalid, 0)

    def begin_read(self, addr: int, *, prot: int = 0) -> None:
        self._drive(self.araddr, addr)
        self._drive(self.arprot, prot)
        self._drive(self.arvalid, 1)

    def end_read(self) -> None:
        self._drive(self.arvalid, 0)

    def set_bready(self, asserted: bool) -> None:
        self._drive(self.bready, int(asserted))

    def set_rready(self, asserted: bool) -> None:
        self._drive(self.rready, int(asserted))
