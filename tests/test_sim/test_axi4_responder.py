"""Conformance tests for the pure-Python AXI4 endpoints (AXI4Master + AXI4Responder).

``AXI4Master`` is tested against a real hand-written multi-beat AXI4 RAM DUT
(``AXI4_RAM_MULTIBEAT_SRC``, independent of ``AXI4SlaveLowering``/the engine-
native lowering path in ``test_bench_native.py``) — this is how it's used in
practice, driving real RTL.

``AXI4Responder`` is tested via raw signal poking on a bare stub, mirroring
``test_axi_lite_master.py``'s strict-mode section.

Pure-Python ``AXI4Master`` and ``AXI4Responder`` *can* be paired directly
against each other — see the passthrough tests at the bottom of this file,
which pair them through both a purely combinational (``assign``-only) and a
registered slave-to-master DUT, both successfully. The one combination that
does **not** work reliably is pairing them with **no module at all** between
them (both endpoints attached to a bare, logic-free stub's own top-level
ports) — confirmed pre-existing, independent of the changes in this session,
and not a realistic scenario in practice (every real test has a DUT). Build
your simulator the way `Testbench`/`bench.run()` do — via
`sim._schedule_clock_events(Clock(...), n)`, not `sim.fork(Clock(...))` — to
match this file's convention and avoid surprises.
"""

from __future__ import annotations

import pytest

from veriforge.dsl import Module
from veriforge.sim.endpoints import (
    AXI4Master,
    AXI4ProtocolError,
    AXI4Responder,
    AXI4ResponseError,
)
from veriforge.sim.step_harness import step_drive, step_eval_now, step_run_until
from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser

from .engines import ENGINES

_ID_W = 4


def _parse(src: str):
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


# ---------------------------------------------------------------------------
# Real multi-beat AXI4 RAM DUT (independent of AXI4SlaveLowering)
# ---------------------------------------------------------------------------

AXI4_RAM_MULTIBEAT_SRC = """
module axi4_ram_multibeat (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [3:0]  s_axi_awid,
    input  wire [7:0]  s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,
    input  wire [2:0]  s_axi_awsize,
    input  wire [1:0]  s_axi_awburst,
    input  wire        s_axi_awvalid,
    output reg          s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,
    input  wire        s_axi_wvalid,
    output reg          s_axi_wready,
    output reg  [3:0]   s_axi_bid,
    output reg  [1:0]   s_axi_bresp,
    output reg           s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [3:0]  s_axi_arid,
    input  wire [7:0]  s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,
    input  wire [2:0]  s_axi_arsize,
    input  wire [1:0]  s_axi_arburst,
    input  wire        s_axi_arvalid,
    output reg           s_axi_arready,
    output reg  [3:0]    s_axi_rid,
    output reg  [31:0]   s_axi_rdata,
    output reg  [1:0]    s_axi_rresp,
    output reg            s_axi_rlast,
    output reg             s_axi_rvalid,
    input  wire        s_axi_rready
);
    reg [31:0] mem [0:31];
    reg [2:0]  wstate;   // 0=idle 1=w_burst 2=b_resp
    reg [2:0]  rstate;   // 0=idle 1=r_burst
    reg [7:0]  waddr, raddr;
    reg [7:0]  wremain, rremain;
    reg [3:0]  bid_q, rid_q;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            wstate <= 0; rstate <= 0;
            s_axi_awready <= 0; s_axi_wready <= 0; s_axi_bvalid <= 0; s_axi_bresp <= 0;
            s_axi_arready <= 0; s_axi_rvalid <= 0; s_axi_rlast <= 0; s_axi_rresp <= 0;
            waddr <= 0; raddr <= 0; wremain <= 0; rremain <= 0;
            bid_q <= 0; rid_q <= 0;
        end else begin
            s_axi_awready <= (wstate == 0);
            s_axi_arready <= (rstate == 0);

            if (wstate == 0 && s_axi_awvalid && s_axi_awready) begin
                waddr   <= s_axi_awaddr;
                wremain <= s_axi_awlen;
                bid_q   <= s_axi_awid;
                wstate  <= 1;
            end
            s_axi_wready <= (wstate == 1);
            if (wstate == 1 && s_axi_wvalid && s_axi_wready) begin
                if (s_axi_wstrb[0]) mem[waddr[6:2]][ 7: 0] <= s_axi_wdata[ 7: 0];
                if (s_axi_wstrb[1]) mem[waddr[6:2]][15: 8] <= s_axi_wdata[15: 8];
                if (s_axi_wstrb[2]) mem[waddr[6:2]][23:16] <= s_axi_wdata[23:16];
                if (s_axi_wstrb[3]) mem[waddr[6:2]][31:24] <= s_axi_wdata[31:24];
                waddr <= waddr + 4;
                if (s_axi_wlast) begin
                    wstate <= 2;
                    s_axi_bvalid <= 1;
                    s_axi_bresp <= 0;
                    s_axi_bid <= bid_q;
                end else begin
                    wremain <= wremain - 1;
                end
            end
            if (wstate == 2 && s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 0;
                wstate <= 0;
            end

            if (rstate == 0 && s_axi_arvalid && s_axi_arready) begin
                raddr    <= s_axi_araddr;
                rremain  <= s_axi_arlen;
                rid_q    <= s_axi_arid;
                s_axi_rdata <= mem[s_axi_araddr[6:2]];
                s_axi_rresp <= 0;
                s_axi_rlast <= (s_axi_arlen == 0);
                s_axi_rvalid <= 1;
                s_axi_rid <= s_axi_arid;
                rstate   <= 1;
            end
            if (rstate == 1 && s_axi_rvalid && s_axi_rready) begin
                if (s_axi_rlast) begin
                    s_axi_rvalid <= 0;
                    s_axi_rlast <= 0;
                    rstate <= 0;
                end else begin
                    raddr <= raddr + 4;
                    rremain <= rremain - 1;
                    s_axi_rdata <= mem[(raddr + 4) >> 2];
                    s_axi_rlast <= (rremain == 1);
                end
            end
        end
    end
endmodule
"""


def _settle_drives(sim: Simulator, engine: str) -> None:
    if engine == "reference":
        sim.run(max_time=sim.time)
    else:
        step_eval_now(sim)


def _make_ram_sim(engine: str) -> Simulator:
    sim = Simulator(_parse(AXI4_RAM_MULTIBEAT_SRC), engine=engine)
    sim.run(max_time=0)
    for signal_name in [
        "clk",
        "rst_n",
        "s_axi_awid",
        "s_axi_awaddr",
        "s_axi_awlen",
        "s_axi_awsize",
        "s_axi_awburst",
        "s_axi_awvalid",
        "s_axi_wdata",
        "s_axi_wstrb",
        "s_axi_wlast",
        "s_axi_wvalid",
        "s_axi_bready",
        "s_axi_arid",
        "s_axi_araddr",
        "s_axi_arlen",
        "s_axi_arsize",
        "s_axi_arburst",
        "s_axi_arvalid",
        "s_axi_rready",
    ]:
        step_drive(sim, engine, signal_name, 0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 4000)
    _settle_drives(sim, engine)
    step_run_until(sim, 12)
    step_drive(sim, engine, "rst_n", 0)
    _settle_drives(sim, engine)
    step_run_until(sim, 32)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    return sim


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_master_burst_write_then_read(engine: str) -> None:
    sim = _make_ram_sim(engine)
    master = AXI4Master(sim, "s_axi", default_timeout_cycles=100)

    values = [0x11110000 | i for i in range(4)]
    resp = master.write(0x0, values)
    assert resp == 0
    beats = master.read(0x0, length=4)
    assert beats == values


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_master_single_beat_write_then_read(engine: str) -> None:
    sim = _make_ram_sim(engine)
    master = AXI4Master(sim, "s_axi", default_timeout_cycles=100)

    master.write(0x10, [0xCAFEF00D])
    assert master.read(0x10, length=1) == [0xCAFEF00D]


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_master_wstrb_partial_write(engine: str) -> None:
    sim = _make_ram_sim(engine)
    master = AXI4Master(sim, "s_axi", default_timeout_cycles=100)

    master.write(0x0, [0xAABBCCDD])
    master.write(0x0, [0x00000000], strb=0x1)  # only byte 0
    assert master.read(0x0, length=1) == [0xAABBCC00]


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_master_id_echoed(engine: str) -> None:
    sim = _make_ram_sim(engine)
    master = AXI4Master(sim, "s_axi", default_timeout_cycles=100)

    resp = master.write(0x0, [0x1], txn_id=0xA)
    assert resp == 0
    beats = master.read(0x0, length=1, txn_id=0x5)
    assert beats == [0x1]


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_master_write_timeout_no_dut_response(engine: str) -> None:
    sim = _make_ram_sim(engine)
    # Hold reset asserted forever so the RAM never accepts anything.
    step_drive(sim, engine, "rst_n", 0)
    _settle_drives(sim, engine)
    master = AXI4Master(sim, "s_axi", default_timeout_cycles=5)

    with pytest.raises(TimeoutError):
        master.write(0x0, [0xDEADBEEF])


# ---------------------------------------------------------------------------
# AXI4Responder — bare stub, raw signal poking (mirrors test_axi_lite_master.py's
# strict-mode section). This gives precise control over handshake timing and
# avoids pairing AXI4Master directly with AXI4Responder (see module docstring).
# ---------------------------------------------------------------------------


def _axi4_stub_module(*, id_width: int = 0, write: bool = True, read: bool = True):
    module = Module("axi4_stub_tb")
    module.input("clk")
    if write:
        module.input("m_axi_awaddr", width=8)
        module.input("m_axi_awlen", width=8)
        module.input("m_axi_awsize", width=3)
        module.input("m_axi_awburst", width=2)
        module.input("m_axi_awvalid")
        module.output("m_axi_awready")
        module.input("m_axi_wdata", width=32)
        module.input("m_axi_wstrb", width=4)
        module.input("m_axi_wlast")
        module.input("m_axi_wvalid")
        module.output("m_axi_wready")
        module.output("m_axi_bresp", width=2)
        module.output("m_axi_bvalid")
        module.input("m_axi_bready")
        if id_width > 0:
            module.input("m_axi_awid", width=id_width)
            module.output("m_axi_bid", width=id_width)
    if read:
        module.input("m_axi_araddr", width=8)
        module.input("m_axi_arlen", width=8)
        module.input("m_axi_arsize", width=3)
        module.input("m_axi_arburst", width=2)
        module.input("m_axi_arvalid")
        module.output("m_axi_arready")
        module.output("m_axi_rdata", width=32)
        module.output("m_axi_rresp", width=2)
        module.output("m_axi_rlast")
        module.output("m_axi_rvalid")
        module.input("m_axi_rready")
        if id_width > 0:
            module.input("m_axi_arid", width=id_width)
            module.output("m_axi_rid", width=id_width)
    return module.build()


def _make_stub_sim(engine: str, *, id_width: int = 0, write: bool = True, read: bool = True) -> Simulator:
    sim = Simulator(_axi4_stub_module(id_width=id_width, write=write, read=read), engine=engine)
    sim.run(max_time=0)
    _settle_drives(sim, engine)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 4000)
    _settle_drives(sim, engine)
    step_run_until(sim, 12)
    return sim


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_responder_accepts_write_and_read_via_raw_signals(engine: str) -> None:
    sim = _make_stub_sim(engine)
    responder = AXI4Responder(sim, "m_axi", initial_memory={0x4: 0xCAFEBABE})

    step_drive(sim, engine, "m_axi_awvalid", 1)
    step_drive(sim, engine, "m_axi_awaddr", 0x0)
    step_drive(sim, engine, "m_axi_awlen", 0)
    step_drive(sim, engine, "m_axi_awsize", 2)
    step_drive(sim, engine, "m_axi_awburst", 1)
    step_drive(sim, engine, "m_axi_wvalid", 1)
    step_drive(sim, engine, "m_axi_wdata", 0x11223344)
    step_drive(sim, engine, "m_axi_wstrb", 0xF)
    step_drive(sim, engine, "m_axi_wlast", 1)
    for target in range(15, 200, 5):  # poll in small increments; drop VALID the instant it's accepted
        step_run_until(sim, target)
        if responder.write_log:
            break
    step_drive(sim, engine, "m_axi_awvalid", 0)
    step_drive(sim, engine, "m_axi_wvalid", 0)
    step_run_until(sim, sim.time + 20)
    assert responder.write_log == [(0x0, 0x11223344, 0xF)]

    step_drive(sim, engine, "m_axi_arvalid", 1)
    step_drive(sim, engine, "m_axi_araddr", 0x4)
    step_drive(sim, engine, "m_axi_arlen", 0)
    step_drive(sim, engine, "m_axi_arsize", 2)
    step_drive(sim, engine, "m_axi_arburst", 1)
    step_drive(sim, engine, "m_axi_rready", 1)
    step_run_until(sim, 100)
    assert int(sim.signal("m_axi_rdata").value) == 0xCAFEBABE
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_responder_read_only_channel(engine: str) -> None:
    sim = _make_stub_sim(engine, write=False, read=True)
    responder = AXI4Responder(sim, "m_axi", initial_memory={0x4: 0xCAFEBABE})
    assert responder._has_read_channel and not responder._has_write_channel
    assert responder.awaddr is None and responder.wdata is None and responder.bresp is None
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_responder_write_only_channel(engine: str) -> None:
    sim = _make_stub_sim(engine, write=True, read=False)
    responder = AXI4Responder(sim, "m_axi")
    assert responder._has_write_channel and not responder._has_read_channel
    assert responder.araddr is None and responder.rdata is None
    responder.close()


def test_axi4_responder_neither_channel_raises() -> None:
    sim = _make_stub_sim("reference", write=False, read=False)
    with pytest.raises(ValueError, match=r"neither a write.*nor a read"):
        AXI4Responder(sim, "m_axi")


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_responder_memory_depth_out_of_range_raises(engine: str) -> None:
    sim = _make_stub_sim(engine)
    responder = AXI4Responder(sim, "m_axi", memory_depth=4)  # 4 words = 16 bytes

    step_drive(sim, engine, "m_axi_awvalid", 1)
    step_drive(sim, engine, "m_axi_awaddr", 0x40)  # out of range (limit=16), fits the 8-bit port
    step_drive(sim, engine, "m_axi_awlen", 0)
    step_drive(sim, engine, "m_axi_awsize", 2)
    step_drive(sim, engine, "m_axi_awburst", 1)
    step_drive(sim, engine, "m_axi_wvalid", 1)
    step_drive(sim, engine, "m_axi_wdata", 0xDEADBEEF)
    step_drive(sim, engine, "m_axi_wstrb", 0xF)
    step_drive(sim, engine, "m_axi_wlast", 1)
    step_drive(sim, engine, "m_axi_bready", 1)
    with pytest.raises(ValueError, match="out of range"):
        step_run_until(sim, 100)
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_responder_pause_ar_holds_arready_low(engine: str) -> None:
    sim = _make_stub_sim(engine)
    responder = AXI4Responder(sim, "m_axi")
    responder.pause_ar = True

    step_run_until(sim, 30)
    assert int(sim.signal("m_axi_arready").value) == 0
    assert int(sim.signal("m_axi_awready").value) == 1  # AW unaffected by ar_pause
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_responder_queue_write_response_overrides_bresp(engine: str) -> None:
    sim = _make_stub_sim(engine)
    responder = AXI4Responder(sim, "m_axi")
    responder.queue_write_response(0x2)  # SLVERR

    step_drive(sim, engine, "m_axi_awvalid", 1)
    step_drive(sim, engine, "m_axi_awaddr", 0x0)
    step_drive(sim, engine, "m_axi_awlen", 0)
    step_drive(sim, engine, "m_axi_awsize", 2)
    step_drive(sim, engine, "m_axi_awburst", 1)
    step_drive(sim, engine, "m_axi_wvalid", 1)
    step_drive(sim, engine, "m_axi_wdata", 0xDEADBEEF)
    step_drive(sim, engine, "m_axi_wstrb", 0xF)
    step_drive(sim, engine, "m_axi_wlast", 1)
    step_drive(sim, engine, "m_axi_bready", 1)
    for target in range(15, 200, 5):  # poll; drop VALID the instant the beat is accepted
        step_run_until(sim, target)
        if responder.write_log:
            break
    step_drive(sim, engine, "m_axi_awvalid", 0)
    step_drive(sim, engine, "m_axi_wvalid", 0)
    step_run_until(sim, sim.time + 20)
    assert int(sim.signal("m_axi_bresp").value) == 0x2
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_responder_max_bw_percent_throttles_sustained_reads(engine: str) -> None:
    """A throttled responder takes measurably longer to retire the same
    N back-to-back single-beat reads than an unthrottled one."""

    def _elapsed_for_n_reads(max_bw_percent: int, seed: int, n: int) -> int:
        sim = _make_stub_sim(engine)
        responder = AXI4Responder(sim, "m_axi", max_bw_percent=max_bw_percent, latency_seed=seed)
        step_drive(sim, engine, "m_axi_arsize", 2)
        step_drive(sim, engine, "m_axi_arburst", 1)
        step_drive(sim, engine, "m_axi_rready", 1)
        # Queue all N single-beat ARs back-to-back (ARREADY is always high,
        # so each cycle with ARVALID=1 enqueues one burst) — reads start
        # retiring immediately, overlapping with enqueue, so start the
        # clock *before* this loop, not after it.
        t0 = sim.time
        step_drive(sim, engine, "m_axi_arvalid", 1)
        for i in range(n):
            step_drive(sim, engine, "m_axi_araddr", i * 4)
            step_run_until(sim, sim.time + 10)
        step_drive(sim, engine, "m_axi_arvalid", 0)
        for _ in range(2000):  # generous cap; only guards against a true hang
            if len(responder.read_log) >= n:
                break
            step_run_until(sim, sim.time + 10)
        assert len(responder.read_log) >= n, f"only {len(responder.read_log)}/{n} reads completed"
        elapsed = sim.time - t0
        responder.close()
        return elapsed

    fast_elapsed = _elapsed_for_n_reads(100, 0, 40)
    slow_elapsed = _elapsed_for_n_reads(20, 0, 40)
    assert slow_elapsed > fast_elapsed


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_responder_rd_latency_delays_first_read(engine: str) -> None:
    # RREADY is left low throughout: once RVALID goes high it must stay high
    # (no handshake can retire it), so a late-time check is race-free.
    sim = _make_stub_sim(engine)
    responder = AXI4Responder(sim, "m_axi", rd_latency_cycles=10, latency_seed=1)

    step_drive(sim, engine, "m_axi_arvalid", 1)
    step_drive(sim, engine, "m_axi_araddr", 0x0)
    step_drive(sim, engine, "m_axi_arlen", 0)
    step_drive(sim, engine, "m_axi_arsize", 2)
    step_drive(sim, engine, "m_axi_arburst", 1)
    step_run_until(sim, 22)  # a couple of cycles after AR accept — should NOT be valid yet
    assert int(sim.signal("m_axi_rvalid").value) == 0
    step_run_until(sim, 200)
    assert int(sim.signal("m_axi_rvalid").value) == 1
    responder.close()


AXI4_READ_MASTER_FSM_SRC = """
module axi4_read_master_fsm (
    input  wire        clk,
    input  wire        rst_n,
    output reg  [7:0]  m_axi_araddr,
    output reg  [7:0]  m_axi_arlen,
    output reg  [2:0]  m_axi_arsize,
    output reg  [1:0]  m_axi_arburst,
    output reg         m_axi_arvalid,
    input  wire        m_axi_arready,
    input  wire [31:0] m_axi_rdata,
    input  wire [1:0]  m_axi_rresp,
    input  wire        m_axi_rvalid,
    input  wire        m_axi_rlast,
    output reg         m_axi_rready,
    output reg  [31:0] beat0,
    output reg  [31:0] beat1,
    output reg  [31:0] beat2,
    output reg  [31:0] beat3,
    output reg  [2:0]  beat_count,
    output reg         done
);
    localparam IDLE = 0, ISSUE = 1, WAIT = 2, DONE_ST = 3;
    reg [1:0] state;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= IDLE;
            m_axi_arvalid <= 0;
            m_axi_rready <= 0;
            beat_count <= 0;
            done <= 0;
        end else begin
            case (state)
                IDLE: begin
                    m_axi_araddr <= 0;
                    m_axi_arlen <= 3;
                    m_axi_arsize <= 2;
                    m_axi_arburst <= 1;
                    m_axi_arvalid <= 1;
                    state <= ISSUE;
                end
                ISSUE: begin
                    if (m_axi_arvalid && m_axi_arready) begin
                        m_axi_arvalid <= 0;
                        m_axi_rready <= 1;
                        state <= WAIT;
                    end
                end
                WAIT: begin
                    if (m_axi_rvalid) begin
                        case (beat_count)
                            0: beat0 <= m_axi_rdata;
                            1: beat1 <= m_axi_rdata;
                            2: beat2 <= m_axi_rdata;
                            default: beat3 <= m_axi_rdata;
                        endcase
                        beat_count <= beat_count + 1;
                        if (m_axi_rlast) begin
                            m_axi_rready <= 0;
                            done <= 1;
                            state <= DONE_ST;
                        end
                    end
                end
                default: begin
                end
            endcase
        end
    end
endmodule
"""


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_responder_rd_latency_one_no_dropped_or_shifted_beat(engine: str) -> None:
    """Regression: with the default `rd_latency_cycles=1` ("respond ASAP"),
    a real RTL read-master FSM against `AXI4Responder` (not `AXI4Master` —
    see below) must see every beat of a multi-beat burst exactly once, in
    the right order.

    Previously, the R-channel retire check read RREADY *live* at the same
    posedge such a master's own clocked process asserts it for the first
    time (as part of the same state transition that recognizes its AR was
    accepted). Recognizing a nonblocking assignment takes a full edge, so
    a live read here observes a value one edge "ahead" of what any
    properly-scheduled synchronous reader could ever see — retiring beat 0
    before the master had any chance to look at RVALID at all, silently
    losing it and shifting every subsequent beat's captured value back by
    one position, with the final beat never presented. Not reproducible
    via pure-Python `AXI4Master` (see the module docstring) — it polls
    settled signal values in an ordinary Python loop rather than sampling
    synchronously the way a real clocked RTL process does, so it never
    exercised this; this test uses a small real RTL FSM instead, closing
    exactly the gap the module docstring calls out as genuinely untested.
    """
    values = [0xA0000000 | i for i in range(4)]
    initial_memory = {i * 4: values[i] for i in range(4)}
    sim = Simulator(_parse(AXI4_READ_MASTER_FSM_SRC), engine=engine)
    sim.run(max_time=0)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 4000)
    step_run_until(sim, 12)
    step_drive(sim, engine, "rst_n", 0)
    step_run_until(sim, 32)
    step_drive(sim, engine, "rst_n", 1)

    responder = AXI4Responder(sim, "m_axi", initial_memory=initial_memory, rd_latency_cycles=1, latency_seed=1)

    step_run_until(sim, sim.time + 2000)
    assert int(sim.signal("done").value) == 1, "read master FSM never completed"
    beats = [int(sim.signal(f"beat{i}").value) for i in range(4)]
    assert beats == values, f"expected {[hex(v) for v in values]}, got {[hex(v) for v in beats]}"
    responder.close()


# ---------------------------------------------------------------------------
# AXI4Responder strict mode — WLAST alignment (AXI4ProtocolError)
# ---------------------------------------------------------------------------
# Timing reference (clock period=10, _make_stub_sim ends at t=12):
#   posedge N is at t=15, N+1 at t=25, ...


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_strict_wlast_early_raises(engine: str) -> None:
    """WLAST asserted before AWLEN+1 beats have been sent raises AXI4ProtocolError."""
    sim = _make_stub_sim(engine)
    responder = AXI4Responder(sim, "m_axi", strict=True)

    step_drive(sim, engine, "m_axi_awvalid", 1)
    step_drive(sim, engine, "m_axi_awaddr", 0x0)
    step_drive(sim, engine, "m_axi_awlen", 1)  # 2-beat burst
    step_drive(sim, engine, "m_axi_awsize", 2)
    step_drive(sim, engine, "m_axi_awburst", 1)
    step_drive(sim, engine, "m_axi_wvalid", 1)
    step_drive(sim, engine, "m_axi_wdata", 0x11111111)
    step_drive(sim, engine, "m_axi_wstrb", 0xF)
    step_drive(sim, engine, "m_axi_wlast", 1)  # violation: WLAST on beat 1 of 2
    with pytest.raises(AXI4ProtocolError, match="WLAST asserted"):
        step_run_until(sim, 40)

    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_strict_wlast_missing_raises(engine: str) -> None:
    """WLAST not asserted on the final (only) beat raises AXI4ProtocolError."""
    sim = _make_stub_sim(engine)
    responder = AXI4Responder(sim, "m_axi", strict=True)

    step_drive(sim, engine, "m_axi_awvalid", 1)
    step_drive(sim, engine, "m_axi_awaddr", 0x0)
    step_drive(sim, engine, "m_axi_awlen", 0)  # 1-beat burst
    step_drive(sim, engine, "m_axi_awsize", 2)
    step_drive(sim, engine, "m_axi_awburst", 1)
    step_drive(sim, engine, "m_axi_wvalid", 1)
    step_drive(sim, engine, "m_axi_wdata", 0x22222222)
    step_drive(sim, engine, "m_axi_wstrb", 0xF)
    step_drive(sim, engine, "m_axi_wlast", 0)  # violation: no WLAST on the only (=final) beat
    with pytest.raises(AXI4ProtocolError, match="WLAST not asserted"):
        step_run_until(sim, 40)

    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_strict_off_by_default_no_raise(engine: str) -> None:
    sim = _make_stub_sim(engine)
    responder = AXI4Responder(sim, "m_axi", strict=False)

    step_drive(sim, engine, "m_axi_awvalid", 1)
    step_drive(sim, engine, "m_axi_awaddr", 0x0)
    step_drive(sim, engine, "m_axi_awlen", 1)
    step_drive(sim, engine, "m_axi_awsize", 2)
    step_drive(sim, engine, "m_axi_awburst", 1)
    step_drive(sim, engine, "m_axi_wvalid", 1)
    step_drive(sim, engine, "m_axi_wdata", 0x33333333)
    step_drive(sim, engine, "m_axi_wstrb", 0xF)
    step_drive(sim, engine, "m_axi_wlast", 1)  # would violate strict mode
    step_run_until(sim, 40)  # no error raised

    responder.close()


# ---------------------------------------------------------------------------
# AXI4Master <-> AXI4Responder paired through a slave-to-master passthrough,
# characterizing exactly when direct pairing works (see the module docstring).
# ---------------------------------------------------------------------------

AXI4_COMBINATIONAL_PASSTHRU_SRC = """
module axi4_combinational_passthru (
    input wire clk,
    input wire rst_n,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [7:0]  s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,
    input  wire [2:0]  s_axi_awsize,
    input  wire [1:0]  s_axi_awburst,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    output wire [1:0]  s_axi_bresp,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
    input  wire [7:0]  s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,
    input  wire [2:0]  s_axi_arsize,
    input  wire [1:0]  s_axi_arburst,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rlast,
    output wire        m_axi_awvalid,
    input  wire        m_axi_awready,
    output wire [7:0]  m_axi_awaddr,
    output wire [7:0]  m_axi_awlen,
    output wire [2:0]  m_axi_awsize,
    output wire [1:0]  m_axi_awburst,
    output wire        m_axi_wvalid,
    input  wire        m_axi_wready,
    output wire [31:0] m_axi_wdata,
    output wire [3:0]  m_axi_wstrb,
    output wire        m_axi_wlast,
    input  wire        m_axi_bvalid,
    output wire        m_axi_bready,
    input  wire [1:0]  m_axi_bresp,
    output wire        m_axi_arvalid,
    input  wire        m_axi_arready,
    output wire [7:0]  m_axi_araddr,
    output wire [7:0]  m_axi_arlen,
    output wire [2:0]  m_axi_arsize,
    output wire [1:0]  m_axi_arburst,
    input  wire        m_axi_rvalid,
    output wire        m_axi_rready,
    input  wire [31:0] m_axi_rdata,
    input  wire [1:0]  m_axi_rresp,
    input  wire        m_axi_rlast
);
    assign m_axi_awvalid = s_axi_awvalid;
    assign s_axi_awready = m_axi_awready;
    assign m_axi_awaddr  = s_axi_awaddr;
    assign m_axi_awlen   = s_axi_awlen;
    assign m_axi_awsize  = s_axi_awsize;
    assign m_axi_awburst = s_axi_awburst;
    assign m_axi_wvalid = s_axi_wvalid;
    assign s_axi_wready = m_axi_wready;
    assign m_axi_wdata  = s_axi_wdata;
    assign m_axi_wstrb  = s_axi_wstrb;
    assign m_axi_wlast  = s_axi_wlast;
    assign s_axi_bvalid = m_axi_bvalid;
    assign m_axi_bready = s_axi_bready;
    assign s_axi_bresp  = m_axi_bresp;
    assign m_axi_arvalid = s_axi_arvalid;
    assign s_axi_arready = m_axi_arready;
    assign m_axi_araddr  = s_axi_araddr;
    assign m_axi_arlen   = s_axi_arlen;
    assign m_axi_arsize  = s_axi_arsize;
    assign m_axi_arburst = s_axi_arburst;
    assign s_axi_rvalid = m_axi_rvalid;
    assign m_axi_rready = s_axi_rready;
    assign s_axi_rdata  = m_axi_rdata;
    assign s_axi_rresp  = m_axi_rresp;
    assign s_axi_rlast  = m_axi_rlast;
endmodule
"""

# One-deep register stage per channel: READY only asserts when the stage is
# empty (so throughput is capped at one beat every two cycles), but it fully
# decouples timing between the two sides with a genuine clocked stage.
AXI4_REGSLICE_PASSTHRU_SRC = """
module axi4_regslice_passthru (
    input wire clk,
    input wire rst_n,
    input  wire        s_axi_awvalid,
    output reg          s_axi_awready,
    input  wire [7:0]  s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,
    input  wire [2:0]  s_axi_awsize,
    input  wire [1:0]  s_axi_awburst,
    input  wire        s_axi_wvalid,
    output reg          s_axi_wready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,
    output reg           s_axi_bvalid,
    input  wire        s_axi_bready,
    output reg  [1:0]    s_axi_bresp,
    input  wire        s_axi_arvalid,
    output reg          s_axi_arready,
    input  wire [7:0]  s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,
    input  wire [2:0]  s_axi_arsize,
    input  wire [1:0]  s_axi_arburst,
    output reg           s_axi_rvalid,
    input  wire        s_axi_rready,
    output reg  [31:0]   s_axi_rdata,
    output reg  [1:0]    s_axi_rresp,
    output reg            s_axi_rlast,
    output reg          m_axi_awvalid,
    input  wire        m_axi_awready,
    output reg  [7:0]   m_axi_awaddr,
    output reg  [7:0]   m_axi_awlen,
    output reg  [2:0]   m_axi_awsize,
    output reg  [1:0]   m_axi_awburst,
    output reg          m_axi_wvalid,
    input  wire        m_axi_wready,
    output reg  [31:0]  m_axi_wdata,
    output reg  [3:0]   m_axi_wstrb,
    output reg          m_axi_wlast,
    input  wire        m_axi_bvalid,
    output reg          m_axi_bready,
    input  wire [1:0]  m_axi_bresp,
    output reg          m_axi_arvalid,
    input  wire        m_axi_arready,
    output reg  [7:0]   m_axi_araddr,
    output reg  [7:0]   m_axi_arlen,
    output reg  [2:0]   m_axi_arsize,
    output reg  [1:0]   m_axi_arburst,
    input  wire        m_axi_rvalid,
    output reg          m_axi_rready,
    input  wire [31:0] m_axi_rdata,
    input  wire [1:0]  m_axi_rresp,
    input  wire        m_axi_rlast
);
    reg aw_full, w_full, b_full, ar_full, r_full;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aw_full <= 0; w_full <= 0; b_full <= 0; ar_full <= 0; r_full <= 0;
            m_axi_awvalid <= 0; m_axi_wvalid <= 0; s_axi_bvalid <= 0;
            m_axi_arvalid <= 0; s_axi_rvalid <= 0;
            s_axi_awready <= 0; s_axi_wready <= 0; s_axi_arready <= 0;
            m_axi_bready <= 0; m_axi_rready <= 0;
        end else begin
            s_axi_awready <= ~aw_full;
            if (s_axi_awvalid && s_axi_awready) begin
                aw_full <= 1;
                m_axi_awvalid <= 1;
                m_axi_awaddr <= s_axi_awaddr; m_axi_awlen <= s_axi_awlen;
                m_axi_awsize <= s_axi_awsize; m_axi_awburst <= s_axi_awburst;
            end else if (m_axi_awvalid && m_axi_awready) begin
                aw_full <= 0; m_axi_awvalid <= 0;
            end
            s_axi_wready <= ~w_full;
            if (s_axi_wvalid && s_axi_wready) begin
                w_full <= 1;
                m_axi_wvalid <= 1;
                m_axi_wdata <= s_axi_wdata; m_axi_wstrb <= s_axi_wstrb; m_axi_wlast <= s_axi_wlast;
            end else if (m_axi_wvalid && m_axi_wready) begin
                w_full <= 0; m_axi_wvalid <= 0;
            end
            m_axi_bready <= ~b_full;
            if (m_axi_bvalid && m_axi_bready) begin
                b_full <= 1;
                s_axi_bvalid <= 1;
                s_axi_bresp <= m_axi_bresp;
            end else if (s_axi_bvalid && s_axi_bready) begin
                b_full <= 0; s_axi_bvalid <= 0;
            end
            s_axi_arready <= ~ar_full;
            if (s_axi_arvalid && s_axi_arready) begin
                ar_full <= 1;
                m_axi_arvalid <= 1;
                m_axi_araddr <= s_axi_araddr; m_axi_arlen <= s_axi_arlen;
                m_axi_arsize <= s_axi_arsize; m_axi_arburst <= s_axi_arburst;
            end else if (m_axi_arvalid && m_axi_arready) begin
                ar_full <= 0; m_axi_arvalid <= 0;
            end
            m_axi_rready <= ~r_full;
            if (m_axi_rvalid && m_axi_rready) begin
                r_full <= 1;
                s_axi_rvalid <= 1;
                s_axi_rdata <= m_axi_rdata; s_axi_rresp <= m_axi_rresp; s_axi_rlast <= m_axi_rlast;
            end else if (s_axi_rvalid && s_axi_rready) begin
                r_full <= 0; s_axi_rvalid <= 0;
            end
        end
    end
endmodule
"""


def _make_passthru_sim(engine: str, src: str) -> Simulator:
    sim = Simulator(_parse(src), engine=engine)
    sim.run(max_time=0)
    sim._schedule_clock_events(Clock(sim.signal("clk"), period=10), 20000)
    _settle_drives(sim, engine)
    step_run_until(sim, 12)
    step_drive(sim, engine, "rst_n", 0)
    _settle_drives(sim, engine)
    step_run_until(sim, 60)
    step_drive(sim, engine, "rst_n", 1)
    _settle_drives(sim, engine)
    return sim


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_master_responder_through_regslice_passthru(engine: str) -> None:
    """A genuine (if trivial) clocked stage between AXI4Master and
    AXI4Responder is enough to make direct pairing work reliably."""
    sim = _make_passthru_sim(engine, AXI4_REGSLICE_PASSTHRU_SRC)
    master = AXI4Master(sim, "s_axi", default_timeout_cycles=200)
    responder = AXI4Responder(sim, "m_axi")

    resp = master.write(0x0, [0xAABBCCDD])
    assert resp == 0
    assert master.read(0x0, length=1) == [0xAABBCCDD]
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_master_responder_multibeat_burst_with_continuous_rready(engine: str) -> None:
    """Regression for a real bug: AXI4Master's read() holds RREADY asserted
    continuously across an entire multi-beat burst (the common, spec-legal
    pattern for a high-throughput master) — a stale one-cycle "hold" in the
    R-channel retire logic used to present the same beat's RVALID/RDATA
    across two consecutive rising edges, so a continuously-ready master
    accepted every beat but the first twice, corrupting the burst and (with
    AXI4Master specifically) raising AXI4ResponseError from the RLAST/length
    mismatch it produces. Single-beat reads (as in the tests above) never
    exercised this: AXI4Master deasserts RREADY the instant it sees RLAST,
    before a second, stale sample could occur."""
    sim = _make_passthru_sim(engine, AXI4_REGSLICE_PASSTHRU_SRC)
    master = AXI4Master(sim, "s_axi", default_timeout_cycles=200)
    responder = AXI4Responder(sim, "m_axi")

    values = [0x11110000 | i for i in range(4)]
    master.write(0x0, values)
    beats = master.read(0x0, length=4)
    assert beats == values
    responder.close()


@pytest.mark.parametrize("engine", ENGINES)
def test_axi4_master_responder_through_combinational_passthru(engine: str) -> None:
    """A purely combinational (``assign``-only, no registers) slave-to-master
    passthrough between AXI4Master and AXI4Responder works fine — pairing
    them directly on a *bare stub with no module at all* between them is the
    one combination that doesn't (see the module docstring); any real DUT,
    combinational or registered, is enough as long as the simulator is built
    with `_schedule_clock_events` like `_make_passthru_sim`/`Testbench` do."""
    sim = _make_passthru_sim(engine, AXI4_COMBINATIONAL_PASSTHRU_SRC)
    master = AXI4Master(sim, "s_axi", default_timeout_cycles=200)
    responder = AXI4Responder(sim, "m_axi")

    resp = master.write(0x0, [0xAABBCCDD])
    assert resp == 0
    assert master.read(0x0, length=1) == [0xAABBCCDD]
    responder.close()


def test_axi4_master_responder_on_bare_stub_never_completes() -> None:
    """The one pairing that doesn't work: both endpoints attached to a bare
    stub's own top-level ports, with no module logic — not even an `assign`
    — between them. Not a realistic scenario (every real test has a DUT),
    kept here as a regression/documentation anchor. Confirmed independent
    of `default_timeout_cycles` (still hangs at 2000 cycles) and of the
    other changes in this session (`git stash` reproduces it against main)."""
    engine = "reference"
    sim = _make_stub_sim(engine)
    master = AXI4Master(sim, "m_axi", default_timeout_cycles=50)
    responder = AXI4Responder(sim, "m_axi")

    with pytest.raises(TimeoutError):
        master.write(0x0, [0xAABBCCDD])
    responder.close()
