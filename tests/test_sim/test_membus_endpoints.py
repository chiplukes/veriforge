"""Tests for MemBus endpoints: MemBusMaster, MemBusResponder, MemBusProxy, and detection.

Layout:
  * Section 1 – detect_membus_interfaces on DSL modules
  * Section 2 – MemBusMaster + MemBusResponder directly (stub module, no DUT logic)
  * Section 3 – MemBusMaster against a real SRAM DUT (Verilog module)
  * Section 4 – MemBusProxy via Testbench.iface() (SRAM DUT, role='slave')
  * Section 5 – MemBusProxy via Testbench.iface() (DUT-master scenario, role='master')
"""

from __future__ import annotations

import pytest

from veriforge.dsl import Module
from veriforge.sim.endpoints import MemBusMaster, MemBusResponder
from veriforge.sim.endpoints.detect import detect_interfaces, detect_membus_interfaces
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


ENGINES = ["reference", "vm"]

# Canonical signal map for the stub / SRAM DUT (prefix "mem").
_SIGNALS = {
    "addr": "mem_addr",
    "wdata": "mem_wdata",
    "rdata": "mem_rdata",
    "wen": "mem_wen",
    "ren": "mem_ren",
    "rvalid": "mem_rvalid",
}
# Same map without rvalid — master samples rdata immediately after the request posedge.
_SIGNALS_NO_RVALID = {k: v for k, v in _SIGNALS.items() if k != "rvalid"}


# ---------------------------------------------------------------------------
# DSL / Verilog module helpers
# ---------------------------------------------------------------------------


def _membus_stub_module():
    """Bare-port module with no logic. Both master and responder live on the bench."""
    module = Module("membus_stub")
    module.input("clk")
    module.input("rst")
    module.input("mem_addr", width=2)
    module.input("mem_wdata", width=32)
    module.output("mem_rdata", width=32)
    module.input("mem_wen")
    module.input("mem_ren")
    module.output("mem_rvalid")
    return module.build()


# Simple 4×32-bit SRAM implemented in Verilog.
_SRAM_VERILOG = """
module membus_sram_dut (
    input  wire        clk,
    input  wire        rst,
    input  wire [1:0]  mem_addr,
    input  wire [31:0] mem_wdata,
    output reg  [31:0] mem_rdata,
    input  wire        mem_wen,
    input  wire        mem_ren,
    output reg         mem_rvalid
);
    reg [31:0] reg0, reg1, reg2, reg3;
    always @(posedge clk) begin
        if (rst) begin
            reg0 <= 32'd0; reg1 <= 32'd0; reg2 <= 32'd0; reg3 <= 32'd0;
            mem_rdata <= 32'd0; mem_rvalid <= 1'b0;
        end else begin
            mem_rvalid <= 1'b0;
            if (mem_wen) begin
                case (mem_addr)
                    2'h0: reg0 <= mem_wdata;
                    2'h1: reg1 <= mem_wdata;
                    2'h2: reg2 <= mem_wdata;
                    2'h3: reg3 <= mem_wdata;
                endcase
            end
            if (mem_ren) begin
                mem_rvalid <= 1'b1;
                case (mem_addr)
                    2'h0: mem_rdata <= reg0;
                    2'h1: mem_rdata <= reg1;
                    2'h2: mem_rdata <= reg2;
                    2'h3: mem_rdata <= reg3;
                endcase
            end
        end
    end
endmodule
"""

# DUT that acts as a memory master — writes 0xCAFEBABE to address 0, then reads it back.
_MASTER_DUT_VERILOG = """
module membus_master_dut (
    input  wire        clk,
    input  wire        rst,
    output reg  [1:0]  bus_addr,
    output reg  [31:0] bus_wdata,
    input  wire [31:0] bus_rdata,
    output reg         bus_wen,
    output reg         bus_ren,
    input  wire        bus_rvalid
);
    reg [2:0] state;
    reg [31:0] captured;
    localparam S_IDLE = 3'd0, S_WRITE = 3'd1, S_READ = 3'd2, S_WAIT = 3'd3, S_DONE = 3'd4;
    always @(posedge clk) begin
        if (rst) begin
            state    <= S_IDLE;
            bus_addr <= 0; bus_wdata <= 0; bus_wen <= 0; bus_ren <= 0;
            captured <= 0;
        end else begin
            bus_wen <= 0;
            bus_ren <= 0;
            case (state)
                S_IDLE: state <= S_WRITE;
                S_WRITE: begin
                    bus_addr  <= 2'd0;
                    bus_wdata <= 32'hCAFEBABE;
                    bus_wen   <= 1'b1;
                    state     <= S_READ;
                end
                S_READ: begin
                    bus_addr <= 2'd0;
                    bus_ren  <= 1'b1;
                    state    <= S_WAIT;
                end
                S_WAIT: begin
                    if (bus_rvalid) begin
                        captured <= bus_rdata;
                        state    <= S_DONE;
                    end
                end
                default: ;
            endcase
        end
    end
endmodule
"""


def _parse_verilog(src: str):
    parser = verilog_parser(start="source_text")
    tree = parser.build_tree(src)
    design = tree_to_design(tree)
    return design.modules[0]


def _parse_sram():
    return _parse_verilog(_SRAM_VERILOG)


def _parse_master_dut():
    return _parse_verilog(_MASTER_DUT_VERILOG)


# ---------------------------------------------------------------------------
# Sim construction helpers
# ---------------------------------------------------------------------------


def _settle(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=sim.time)
    else:
        step_eval_now(sim)


def _make_stub_sim(engine: str) -> Simulator:
    """Sim from stub module. Drives all input ports to 0; also zeros the outputs."""
    sim = Simulator(_membus_stub_module(), engine=engine)
    sim.run(max_time=0)
    for sig in ["clk", "rst", "mem_addr", "mem_wdata", "mem_wen", "mem_ren"]:
        step_drive(sim, engine, sig, 0)
    # Explicitly zero-drive the unconnected output ports so they are
    # known (not X) when no responder is present.
    for sig in ["mem_rdata", "mem_rvalid"]:
        step_drive(sim, engine, sig, 0)
    _settle(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 2000)
    _settle(sim, engine)
    step_run_until(sim, 12)
    return sim


def _make_sram_sim(engine: str) -> Simulator:
    """Sim from SRAM DUT module with reset applied."""
    sim = Simulator(_parse_sram(), engine=engine)
    sim.run(max_time=0)
    for sig in ["clk", "rst", "mem_addr", "mem_wdata", "mem_wen", "mem_ren"]:
        step_drive(sim, engine, sig, 0)
    _settle(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 2000)
    _settle(sim, engine)
    step_run_until(sim, 12)
    step_drive(sim, engine, "rst", 1)
    _settle(sim, engine)
    step_run_until(sim, 22)
    step_drive(sim, engine, "rst", 0)
    _settle(sim, engine)
    return sim


# ===========================================================================
# Section 1 – Interface detection
# ===========================================================================


def test_detect_membus_slave_basic() -> None:
    module = Module("dut")
    module.input("clk")
    module.input("rst")
    module.input("mem_addr", width=8)
    module.input("mem_wdata", width=32)
    module.output("mem_rdata", width=32)
    module.input("mem_wen")

    bundles = detect_membus_interfaces(module.build())

    assert len(bundles) == 1
    assert bundles[0].prefix == "mem"
    assert bundles[0].role == "slave"
    assert {"addr", "wdata", "rdata", "wen"}.issubset(bundles[0].signal_names())


def test_detect_membus_with_optional_signals() -> None:
    module = Module("dut")
    module.input("mem_addr", width=8)
    module.input("mem_wdata", width=32)
    module.output("mem_rdata", width=32)
    module.input("mem_wen")
    module.input("mem_ren")
    module.output("mem_rvalid")

    bundles = detect_membus_interfaces(module.build())

    assert len(bundles) == 1
    sn = bundles[0].signal_names()
    assert sn["ren"] == "mem_ren"
    assert sn["rvalid"] == "mem_rvalid"


def test_detect_membus_master_role() -> None:
    """DUT outputs addr/wdata/wen → detected role is 'master'."""
    module = Module("dut")
    module.output("mem_addr", width=8)
    module.output("mem_wdata", width=32)
    module.input("mem_rdata", width=32)
    module.output("mem_wen")

    bundles = detect_membus_interfaces(module.build())

    assert len(bundles) == 1
    assert bundles[0].role == "master"


def test_detect_membus_we_suffix_alias() -> None:
    """Suffix 'we' normalises to canonical role 'wen'."""
    module = Module("dut")
    module.input("bus_addr", width=8)
    module.input("bus_wdata", width=32)
    module.output("bus_rdata", width=32)
    module.input("bus_we")

    bundles = detect_membus_interfaces(module.build())

    assert len(bundles) == 1
    assert "wen" in bundles[0].signal_names()
    assert bundles[0].prefix == "bus"


def test_detect_membus_signal_names_map_to_port_names() -> None:
    module = Module("dut")
    module.input("regs_addr", width=4)
    module.input("regs_wdata", width=32)
    module.output("regs_rdata", width=32)
    module.input("regs_wen")

    bundle = detect_membus_interfaces(module.build())[0]

    assert bundle.signal_names() == {
        "addr": "regs_addr",
        "wdata": "regs_wdata",
        "rdata": "regs_rdata",
        "wen": "regs_wen",
    }


def test_detect_membus_missing_required_not_detected() -> None:
    """Bundle missing rdata must not be detected."""
    module = Module("dut")
    module.input("mem_addr", width=8)
    module.input("mem_wdata", width=32)
    module.input("mem_wen")

    assert detect_membus_interfaces(module.build()) == []


def test_detect_membus_does_not_overlap_with_axi() -> None:
    """MemBus detection must not claim signals already taken by AXI-Lite."""
    from veriforge.dsl.lib import axi4_lite  # noqa: PLC0415

    module = Module("dut")
    module.interface("s_axi", axi4_lite(data_width=32, addr_width=8), role="slave")
    # Additional standalone membus ports that share the prefix "mem".
    module.input("mem_addr", width=8)
    module.input("mem_wdata", width=32)
    module.output("mem_rdata", width=32)
    module.input("mem_wen")

    detected = detect_interfaces(module.build())
    protocols = {b.protocol for b in detected}

    assert "axi_lite" in protocols
    # AXI-Lite ports must not pollute the membus bundle.
    membus = [b for b in detected if b.protocol == "membus"]
    assert len(membus) == 1
    assert membus[0].prefix == "mem"


# ===========================================================================
# Section 2 – Direct MemBusMaster + MemBusResponder (stub module, no DUT)
# ===========================================================================


@pytest.mark.parametrize("engine", ENGINES)
def test_membus_write_read_roundtrip(engine: str) -> None:
    """Master writes two locations; responder reflects them on read."""
    sim = _make_stub_sim(engine)
    master = MemBusMaster(sim, _SIGNALS)
    responder = MemBusResponder(sim, _SIGNALS)

    master.write(0, 0xDEADBEEF)
    master.write(1, 0x12345678)

    assert master.read(0) == 0xDEADBEEF
    assert master.read(1) == 0x12345678
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_membus_write_log_records_transaction(engine: str) -> None:
    sim = _make_stub_sim(engine)
    master = MemBusMaster(sim, _SIGNALS)
    responder = MemBusResponder(sim, _SIGNALS)

    master.write(3, 0xABCDEF01)

    assert len(responder.write_log) == 1
    addr, data, _strb = responder.write_log[0]
    assert addr == 3
    assert data == 0xABCDEF01
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_membus_read_log_records_address(engine: str) -> None:
    sim = _make_stub_sim(engine)
    master = MemBusMaster(sim, _SIGNALS)
    responder = MemBusResponder(sim, _SIGNALS, initial_memory={3: 0x99887766})

    val = master.read(3)

    assert val == 0x99887766
    assert 3 in responder.read_log
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_membus_initial_memory_preloaded(engine: str) -> None:
    sim = _make_stub_sim(engine)
    master = MemBusMaster(sim, _SIGNALS)
    responder = MemBusResponder(sim, _SIGNALS, initial_memory={0: 0x11223344, 1: 0x55667788})

    assert master.read(0) == 0x11223344
    assert master.read(1) == 0x55667788
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_membus_default_read_value(engine: str) -> None:
    sim = _make_stub_sim(engine)
    master = MemBusMaster(sim, _SIGNALS)
    responder = MemBusResponder(sim, _SIGNALS, default_read_value=0xCAFECAFE)

    val = master.read(42)

    assert val == 0xCAFECAFE
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_membus_read_timeout_raises(engine: str) -> None:
    """Without a responder, rvalid stays 0 and read() must raise TimeoutError."""
    sim = _make_stub_sim(engine)
    # No responder — mem_rvalid stays at 0 (driven in _make_stub_sim).
    master = MemBusMaster(sim, _SIGNALS, default_timeout_cycles=3)

    with pytest.raises(TimeoutError):
        master.read(0)


@pytest.mark.parametrize("engine", ENGINES)
def test_membus_read_without_rvalid_signal_immediate(engine: str) -> None:
    """When 'rvalid' is absent from the signals map, rdata is sampled immediately."""
    sim = _make_stub_sim(engine)
    master = MemBusMaster(sim, _SIGNALS_NO_RVALID)
    responder = MemBusResponder(sim, _SIGNALS_NO_RVALID, initial_memory={0: 0xFEEDFACE})

    # With no rvalid gate, read() returns immediately — no TimeoutError.
    val = master.read(0)
    assert val == 0xFEEDFACE
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_membus_multiple_writes_and_reads(engine: str) -> None:
    sim = _make_stub_sim(engine)
    master = MemBusMaster(sim, _SIGNALS)
    responder = MemBusResponder(sim, _SIGNALS)

    data = {0: 0xAABBCCDD, 1: 0x11223344, 2: 0xDEADC0DE, 3: 0x0}
    for addr, val in data.items():
        master.write(addr, val)
    for addr, expected in data.items():
        assert master.read(addr) == expected

    assert len(responder.write_log) == 4
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_membus_responder_context_manager(engine: str) -> None:
    sim = _make_stub_sim(engine)
    master = MemBusMaster(sim, _SIGNALS)

    with MemBusResponder(sim, _SIGNALS, initial_memory={0: 0x55AA55AA}) as resp:
        val = master.read(0)
        assert val == 0x55AA55AA
    # After close, further sim steps are fine; responder just won't react.
    _ = resp


# ===========================================================================
# Section 3 – MemBusMaster against the SRAM DUT
# ===========================================================================


@pytest.mark.parametrize("engine", ENGINES)
def test_sram_dut_write_read_roundtrip(engine: str) -> None:
    sim = _make_sram_sim(engine)
    master = MemBusMaster(sim, _SIGNALS)

    master.write(0, 0xDEADBEEF)
    master.write(1, 0x12345678)

    assert master.read(0) == 0xDEADBEEF
    assert master.read(1) == 0x12345678


@pytest.mark.parametrize("engine", ENGINES)
def test_sram_dut_all_four_addresses(engine: str) -> None:
    sim = _make_sram_sim(engine)
    master = MemBusMaster(sim, _SIGNALS)

    values = [0xAABBCCDD, 0x11223344, 0xDEADC0DE, 0xCAFEBABE]
    for i, v in enumerate(values):
        master.write(i, v)
    for i, expected in enumerate(values):
        assert master.read(i) == expected


@pytest.mark.parametrize("engine", ENGINES)
def test_sram_dut_overwrites_previous_value(engine: str) -> None:
    sim = _make_sram_sim(engine)
    master = MemBusMaster(sim, _SIGNALS)

    master.write(0, 0xAAAAAAAA)
    master.write(0, 0xBBBBBBBB)

    assert master.read(0) == 0xBBBBBBBB


@pytest.mark.parametrize("engine", ENGINES)
def test_sram_dut_initial_value_zero_after_reset(engine: str) -> None:
    sim = _make_sram_sim(engine)
    master = MemBusMaster(sim, _SIGNALS)

    assert master.read(0) == 0
    assert master.read(3) == 0


# ===========================================================================
# Section 4 – MemBusProxy via Testbench.iface() — role='slave' (DUT is slave)
# ===========================================================================


def test_testbench_membus_proxy_slave_detected() -> None:
    """Testbench auto-detects the membus bundle and returns a MemBusProxy."""
    from veriforge.sim.bench import Testbench  # noqa: PLC0415
    from veriforge.sim.bench.interfaces import MemBusProxy  # noqa: PLC0415

    bench = Testbench(_parse_sram())
    proxy = bench.iface("mem")
    assert isinstance(proxy, MemBusProxy)
    assert proxy.role == "slave"


def test_testbench_membus_proxy_is_cached() -> None:
    from veriforge.sim.bench import Testbench  # noqa: PLC0415

    bench = Testbench(_parse_sram())
    first = bench.iface("mem")
    second = bench.iface("mem")
    assert first is second


@pytest.mark.parametrize("engine", ENGINES)
def test_testbench_membus_proxy_slave_write_read(engine: str) -> None:
    """Full integration: proxy writes + reads via the SRAM DUT using Testbench.run()."""
    from veriforge.sim.bench import Testbench  # noqa: PLC0415

    bench = Testbench(_parse_sram(), engine=engine)
    with bench.run():
        bench.reset_all()
        proxy = bench.iface("mem")

        proxy.write(0, 0xDEADBEEF)
        proxy.write(1, 0x12345678)

        assert proxy.read(0) == 0xDEADBEEF
        assert proxy.read(1) == 0x12345678


@pytest.mark.parametrize("engine", ENGINES)
def test_testbench_membus_proxy_slave_multiple_addresses(engine: str) -> None:
    from veriforge.sim.bench import Testbench  # noqa: PLC0415

    bench = Testbench(_parse_sram(), engine=engine)
    with bench.run():
        bench.reset_all()
        proxy = bench.iface("mem")

        for addr, val in enumerate([0xAABBCCDD, 0x11223344, 0xDEADC0DE, 0xCAFEBABE]):
            proxy.write(addr, val)
        for addr, expected in enumerate([0xAABBCCDD, 0x11223344, 0xDEADC0DE, 0xCAFEBABE]):
            assert proxy.read(addr) == expected


# ===========================================================================
# Section 5 – MemBusProxy via Testbench.iface() — role='master' (DUT is master)
# ===========================================================================


def test_testbench_membus_proxy_master_detected() -> None:
    """Testbench detects a DUT-master bus and returns a MemBusProxy with role='master'."""
    from veriforge.sim.bench import Testbench  # noqa: PLC0415
    from veriforge.sim.bench.interfaces import MemBusProxy  # noqa: PLC0415

    bench = Testbench(_parse_master_dut())
    proxy = bench.iface("bus")
    assert isinstance(proxy, MemBusProxy)
    assert proxy.role == "master"


@pytest.mark.parametrize("engine", ENGINES)
def test_testbench_membus_proxy_master_responder_captures_write(engine: str) -> None:
    """DUT writes 0xCAFEBABE to address 0; bench proxy captures it via MemBusResponder."""
    from veriforge.sim.bench import BenchTimeoutError, Testbench  # noqa: PLC0415

    bench = Testbench(_parse_master_dut(), engine=engine)
    with bench.run():
        # Create proxy BEFORE reset so the responder is active during
        # the settle cycles when the DUT starts issuing transactions.
        proxy = bench.iface("bus")
        bench.reset_all()

        # DUT may have already written during the settle cycles.
        # Give it a few more cycles to complete if needed.
        if not proxy.write_log:
            domain = bench.domain("clk")
            for _ in range(20):
                domain.step()
                if proxy.write_log:
                    break
            else:
                raise BenchTimeoutError("DUT write not captured within 20 cycles")

        assert len(proxy.write_log) >= 1
        addr, data, _strb = proxy.write_log[0]
        assert addr == 0
        assert data == 0xCAFEBABE
