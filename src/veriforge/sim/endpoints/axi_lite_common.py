"""Shared AXI-Lite signal helpers."""

from __future__ import annotations

from veriforge.sim.step_harness import step_drive


AXI_LITE_SIGNAL_SUFFIX_ALIASES = {
    "awaddr": ("awaddr", "aw_addr", "awaddr_int", "aw_addr_int"),
    "awprot": ("awprot", "aw_prot", "awprot_int", "aw_prot_int"),
    "awvalid": ("awvalid", "aw_valid", "awvalid_int", "aw_valid_int"),
    "awready": ("awready", "aw_ready", "awready_int", "aw_ready_int"),
    "wdata": ("wdata", "w_data", "wdata_int", "w_data_int"),
    "wstrb": ("wstrb", "w_strb", "wstrb_int", "w_strb_int"),
    "wvalid": ("wvalid", "w_valid", "wvalid_int", "w_valid_int"),
    "wready": ("wready", "w_ready", "wready_int", "w_ready_int"),
    "bresp": ("bresp", "b_resp", "bresp_int", "b_resp_int"),
    "bvalid": ("bvalid", "b_valid", "bvalid_int", "b_valid_int"),
    "bready": ("bready", "b_ready", "bready_int", "b_ready_int"),
    "araddr": ("araddr", "ar_addr", "araddr_int", "ar_addr_int"),
    "arprot": ("arprot", "ar_prot", "arprot_int", "ar_prot_int"),
    "arvalid": ("arvalid", "ar_valid", "arvalid_int", "ar_valid_int"),
    "arready": ("arready", "ar_ready", "arready_int", "ar_ready_int"),
    "rdata": ("rdata", "r_data", "rdata_int", "r_data_int"),
    "rresp": ("rresp", "r_resp", "rresp_int", "r_resp_int"),
    "rvalid": ("rvalid", "r_valid", "rvalid_int", "r_valid_int"),
    "rready": ("rready", "r_ready", "rready_int", "r_ready_int"),
}


class _AXILiteSignals:
    def __init__(self, sim, prefix: str, *, clock_name: str = "clk") -> None:
        self.sim = sim
        self.prefix = prefix
        self.clock = sim.signal(clock_name)

    def _resolve_signal(self, logical_name: str, *, required: bool = True):
        candidate_suffixes = AXI_LITE_SIGNAL_SUFFIX_ALIASES[logical_name]
        for suffix in candidate_suffixes:
            try:
                return self.sim.signal(f"{self.prefix}_{suffix}")
            except Exception:  # noqa: S112 - signal lookup intentionally probes multiple aliases
                continue

        if not required:
            return None

        candidates = ", ".join(f"{self.prefix}_{suffix}" for suffix in candidate_suffixes)
        raise ValueError(f"AXI-Lite signal not found for {logical_name}: tried {candidates}")

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
