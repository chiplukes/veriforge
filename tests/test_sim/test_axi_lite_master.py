from __future__ import annotations

import shutil

import pytest

from veriforge.dsl import Module, posedge
from veriforge.dsl.lib import axi4_lite
from veriforge.sim.endpoints import (
    AXILiteMaster,
    AXILiteProtocolError,
    AXILiteRequestDriver,
    AXILiteResponder,
    AXILiteResponseDriver,
    AXILiteResponseError,
)
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator


_has_compiler = shutil.which("gcc") or shutil.which("cl") or shutil.which("cc")


def _engines() -> list[str]:
    engines = ["reference", "vm", "vm-fast"]
    if _has_compiler:
        engines.append("compiled")
    return engines


ENGINES = _engines()


def _axi_lite_regs_module():
    module = Module("axi_lite_regs_tb")
    clk = module.input("clk")
    rst = module.input("rst")
    s_axi = module.interface("s_axi", axi4_lite(data_width=32, addr_width=4), role="slave", reg=True)

    reg0 = module.reg("reg0", width=32)
    reg1 = module.reg("reg1", width=32)

    with module.always(posedge(clk)):
        with module.if_(rst):
            s_axi.awready <<= 0
            s_axi.wready <<= 0
            s_axi.bvalid <<= 0
            s_axi.bresp <<= 0
            s_axi.arready <<= 0
            s_axi.rvalid <<= 0
            s_axi.rdata <<= 0
            s_axi.rresp <<= 0
            reg0 <<= 0x44332211
            reg1 <<= 0x88776655
        with module.else_():
            s_axi.awready <<= 0
            s_axi.wready <<= 0
            s_axi.arready <<= 0

            with module.if_(s_axi.bvalid & s_axi.bready):
                s_axi.bvalid <<= 0
            with module.if_(s_axi.rvalid & s_axi.rready):
                s_axi.rvalid <<= 0

            with module.if_(s_axi.awvalid & s_axi.wvalid & ~s_axi.bvalid & (s_axi.awaddr != 0xC)):
                s_axi.awready <<= 1
                s_axi.wready <<= 1
                s_axi.bvalid <<= 1
                s_axi.bresp <<= 0
                with module.case(s_axi.awaddr[3:2]) as c:
                    with c.when(0):
                        with module.if_(s_axi.wstrb[0]):
                            reg0[7:0] <<= s_axi.wdata[7:0]
                        with module.if_(s_axi.wstrb[1]):
                            reg0[15:8] <<= s_axi.wdata[15:8]
                        with module.if_(s_axi.wstrb[2]):
                            reg0[23:16] <<= s_axi.wdata[23:16]
                        with module.if_(s_axi.wstrb[3]):
                            reg0[31:24] <<= s_axi.wdata[31:24]
                    with c.when(1):
                        with module.if_(s_axi.wstrb[0]):
                            reg1[7:0] <<= s_axi.wdata[7:0]
                        with module.if_(s_axi.wstrb[1]):
                            reg1[15:8] <<= s_axi.wdata[15:8]
                        with module.if_(s_axi.wstrb[2]):
                            reg1[23:16] <<= s_axi.wdata[23:16]
                        with module.if_(s_axi.wstrb[3]):
                            reg1[31:24] <<= s_axi.wdata[31:24]

            with module.if_(s_axi.arvalid & ~s_axi.rvalid & (s_axi.araddr != 0xC)):
                s_axi.arready <<= 1
                s_axi.rvalid <<= 1
                with module.if_(s_axi.araddr == 0x8):
                    s_axi.rresp <<= 0x2
                    s_axi.rdata <<= 0
                with module.else_():
                    s_axi.rresp <<= 0
                    with module.case(s_axi.araddr[3:2]) as c:
                        with c.when(0):
                            s_axi.rdata <<= reg0
                        with c.when(1):
                            s_axi.rdata <<= reg1
                        with c.default():
                            s_axi.rdata <<= 0

    return module.build()


def _axi_lite_stub_module():
    module = Module("axi_lite_stub_tb")
    module.input("clk")
    module.interface("axi", axi4_lite(data_width=32, addr_width=4), role="slave")
    return module.build()


def _settle_drives(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=sim.time)
    else:
        step_eval_now(sim)


def _run_until_rising_edge(sim: Simulator, signal_name: str, limit: int, message: str) -> None:
    previous = int(sim.read(signal_name))
    while sim.time < limit:
        assert sim.run_step(), f"stepped engine stopped before {message}"
        current = int(sim.read(signal_name))
        if previous == 0 and current == 1:
            return
        previous = current
    raise AssertionError(message)


def _run_until_high(sim: Simulator, signal_name: str, limit: int, message: str) -> None:
    while sim.time < limit:
        value = sim.read(signal_name)
        if value.mask == 0 and int(value) == 1:
            return
        assert sim.run_step(), f"stepped engine stopped before {message}"
    raise AssertionError(message)


def _make_sim(engine: str) -> Simulator:
    sim = Simulator(_axi_lite_regs_module(), engine=engine)
    sim.run(max_time=0)
    for signal_name in [
        "clk",
        "rst",
        "s_axi_awaddr",
        "s_axi_awprot",
        "s_axi_awvalid",
        "s_axi_wdata",
        "s_axi_wstrb",
        "s_axi_wvalid",
        "s_axi_bready",
        "s_axi_araddr",
        "s_axi_arprot",
        "s_axi_arvalid",
        "s_axi_rready",
    ]:
        step_drive(sim, engine, signal_name, 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 600)
    _settle_drives(sim, engine)
    step_run_until(sim, 12)
    step_drive(sim, engine, "rst", 1)
    _settle_drives(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst", 0)
    _settle_drives(sim, engine)
    return sim


def _make_stub_sim(engine: str) -> Simulator:
    sim = Simulator(_axi_lite_stub_module(), engine=engine)
    sim.run(max_time=0)
    for signal_name in [
        "clk",
        "axi_awaddr",
        "axi_awprot",
        "axi_awvalid",
        "axi_wdata",
        "axi_wstrb",
        "axi_wvalid",
        "axi_bready",
        "axi_araddr",
        "axi_arprot",
        "axi_arvalid",
        "axi_rready",
    ]:
        step_drive(sim, engine, signal_name, 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 400)
    _settle_drives(sim, engine)
    step_run_until(sim, 12)
    return sim


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_master_read_write_roundtrip(engine: str) -> None:
    sim = _make_sim(engine)
    master = AXILiteMaster(sim, "s_axi")

    assert master.read(0x0) == 0x44332211
    master.write(0x0, 0x00BB0000, strb=0x4)
    assert master.read(0x0) == 0x44BB2211
    master.write(0x4, 0xDEADBEEF)
    assert master.read(0x4) == 0xDEADBEEF


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_master_read_response_error(engine: str) -> None:
    sim = _make_sim(engine)
    master = AXILiteMaster(sim, "s_axi")

    with pytest.raises(AXILiteResponseError):
        master.read(0x8)


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_master_read_timeout(engine: str) -> None:
    sim = _make_sim(engine)
    master = AXILiteMaster(sim, "s_axi", default_timeout_cycles=6)

    with pytest.raises(TimeoutError):
        master.read(0xC)


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_request_driver_with_responder(engine: str) -> None:
    sim = _make_stub_sim(engine)
    driver = AXILiteRequestDriver(sim, "axi")
    responder = AXILiteResponder(sim, "axi", initial_memory={0x0: 0x11223344})

    driver.begin_write(0x4, 0xAABBCCDD)
    _settle_drives(sim, engine)
    _run_until_high(sim, "axi_bvalid", sim.time + 30, "stub write response not observed")
    assert responder.write_log == [(0x4, 0xAABBCCDD, 0xF)]
    driver.end_write()
    driver.set_bready(True)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stub write consume edge not observed")
    driver.set_bready(False)

    driver.begin_read(0x0)
    _settle_drives(sim, engine)
    _run_until_high(sim, "axi_rvalid", sim.time + 30, "stub read response not observed")
    assert responder.read_log == [0x0]
    assert int(sim.read("axi_rdata")) == 0x11223344
    driver.end_read()
    driver.set_rready(True)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stub read consume edge not observed")
    driver.set_rready(False)
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_request_and_response_drivers(engine: str) -> None:
    sim = _make_stub_sim(engine)
    request_driver = AXILiteRequestDriver(sim, "axi")
    response_driver = AXILiteResponseDriver(sim, "axi")

    response_driver.set_write_ready(True)
    request_driver.begin_write(0x4, 0xAABBCCDD)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stub write accept edge not observed")
    request_driver.end_write()
    _settle_drives(sim, engine)
    request_driver.set_bready(True)
    response_driver.begin_write_response(0x2)
    _settle_drives(sim, engine)
    _run_until_high(sim, "axi_bvalid", sim.time + 20, "stub write response valid not observed")
    assert int(sim.read("axi_bresp")) == 0x2
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stub write response consume edge not observed")
    response_driver.end_write_response()
    request_driver.set_bready(False)

    response_driver.set_read_ready(True)
    request_driver.begin_read(0x8)
    _settle_drives(sim, engine)
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stub read accept edge not observed")
    request_driver.end_read()
    _settle_drives(sim, engine)
    request_driver.set_rready(True)
    response_driver.begin_read_response(0x11223344, resp=0x0)
    _settle_drives(sim, engine)
    _run_until_high(sim, "axi_rvalid", sim.time + 20, "stub read response valid not observed")
    assert int(sim.read("axi_rdata")) == 0x11223344
    assert int(sim.read("axi_rresp")) == 0x0
    _run_until_rising_edge(sim, "clk", sim.time + 20, "stub read response consume edge not observed")
    response_driver.end_read_response()
    request_driver.set_rready(False)


# ---------------------------------------------------------------------------
# AXILiteResponder strict mode
# ---------------------------------------------------------------------------
# These tests use the stub sim (no DUT logic). The test code manually drives
# DUT-master signals (AWVALID, WVALID, ARVALID…) to inject AXI-Lite violations
# and verifies AXILiteProtocolError is raised via the responder's strict checks.
#
# Timing reference (clock period=10, _make_stub_sim ends at t=12):
#   posedge N is at t=15, N+1 at t=25, …
# Sequence for each test:
#   1. Drive VALID=1 at current time (t=12)
#   2. run to t=16  → posedge at t=15: unacked flag set
#   3. Drive VALID=0 (or change ADDR/DATA)
#   4. run to t=26  → posedge at t=25: strict check raises


def _stub_strict_responder(sim: Simulator, engine: str) -> AXILiteResponder:
    """Responder with always_ready=False so READY never fires on its own."""
    return AXILiteResponder(sim, "axi", always_ready=False, strict=True)


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_strict_awvalid_deassert_raises(engine: str) -> None:
    """AWVALID deasserted before AWREADY raises AXILiteProtocolError."""
    sim = _make_stub_sim(engine)
    responder = _stub_strict_responder(sim, engine)

    step_drive(sim, engine, "axi_awvalid", 1)
    step_drive(sim, engine, "axi_awaddr", 0x4)
    step_run_until(sim, 16)  # posedge at t=15 → sets unacked flag

    step_drive(sim, engine, "axi_awvalid", 0)  # violation: deassert before ready
    with pytest.raises(AXILiteProtocolError, match="AWVALID deasserted"):
        step_run_until(sim, 26)  # posedge at t=25 → raises

    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_strict_awaddr_change_raises(engine: str) -> None:
    """AWADDR changed while AWVALID=1 and AWREADY=0 raises AXILiteProtocolError."""
    sim = _make_stub_sim(engine)
    responder = _stub_strict_responder(sim, engine)

    step_drive(sim, engine, "axi_awvalid", 1)
    step_drive(sim, engine, "axi_awaddr", 0x4)
    step_run_until(sim, 16)  # posedge at t=15 → snapshot addr=0x4

    step_drive(sim, engine, "axi_awaddr", 0x8)  # violation: addr changed
    with pytest.raises(AXILiteProtocolError, match="AWADDR changed"):
        step_run_until(sim, 26)

    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_strict_wvalid_deassert_raises(engine: str) -> None:
    """WVALID deasserted before WREADY raises AXILiteProtocolError."""
    sim = _make_stub_sim(engine)
    responder = _stub_strict_responder(sim, engine)

    step_drive(sim, engine, "axi_wvalid", 1)
    step_drive(sim, engine, "axi_wdata", 0xDEADBEEF)
    step_run_until(sim, 16)

    step_drive(sim, engine, "axi_wvalid", 0)
    with pytest.raises(AXILiteProtocolError, match="WVALID deasserted"):
        step_run_until(sim, 26)

    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_strict_wdata_change_raises(engine: str) -> None:
    """WDATA changed while WVALID=1 and WREADY=0 raises AXILiteProtocolError."""
    sim = _make_stub_sim(engine)
    responder = _stub_strict_responder(sim, engine)

    step_drive(sim, engine, "axi_wvalid", 1)
    step_drive(sim, engine, "axi_wdata", 0x11223344)
    step_run_until(sim, 16)

    step_drive(sim, engine, "axi_wdata", 0xAABBCCDD)  # violation
    with pytest.raises(AXILiteProtocolError, match="WDATA changed"):
        step_run_until(sim, 26)

    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_strict_arvalid_deassert_raises(engine: str) -> None:
    """ARVALID deasserted before ARREADY raises AXILiteProtocolError."""
    sim = _make_stub_sim(engine)
    responder = _stub_strict_responder(sim, engine)

    step_drive(sim, engine, "axi_arvalid", 1)
    step_drive(sim, engine, "axi_araddr", 0x8)
    step_run_until(sim, 16)

    step_drive(sim, engine, "axi_arvalid", 0)
    with pytest.raises(AXILiteProtocolError, match="ARVALID deasserted"):
        step_run_until(sim, 26)

    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_strict_off_by_default_no_raise(engine: str) -> None:
    """strict=False (default) does not raise on violations."""
    sim = _make_stub_sim(engine)
    responder = AXILiteResponder(sim, "axi", always_ready=False, strict=False)

    step_drive(sim, engine, "axi_awvalid", 1)
    step_drive(sim, engine, "axi_awaddr", 0x4)
    step_run_until(sim, 16)

    step_drive(sim, engine, "axi_awvalid", 0)  # violation, but strict=False
    step_run_until(sim, 26)  # no error raised

    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi_lite_strict_clean_transaction_no_raise(engine: str) -> None:
    """A well-formed transaction with strict=True raises nothing.

    With always_ready=True the READY signal is already 1 when AWVALID
    first appears, so the handshake completes at the first posedge and
    the unacked flag is never set — deasserting AWVALID later is fine.
    """
    sim = _make_stub_sim(engine)
    responder = AXILiteResponder(sim, "axi", always_ready=True, strict=True)

    # Assert AWVALID — AWREADY is already 1 (always_ready)
    step_drive(sim, engine, "axi_awvalid", 1)
    step_drive(sim, engine, "axi_awaddr", 0x4)
    step_run_until(sim, 16)  # posedge at t=15: AWVALID=1, AWREADY=1 → unacked=False

    # Deassert AWVALID — handshake already completed, no violation.
    step_drive(sim, engine, "axi_awvalid", 0)
    step_run_until(sim, 26)  # posedge at t=25: no error raised

    responder.close()
