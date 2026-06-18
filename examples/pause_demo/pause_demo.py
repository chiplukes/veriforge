"""Comprehensive PauseGenerator demo — AXI-Stream, AXI-Lite, AXI4.

This file demonstrates the :class:`~veriforge.sim.endpoints.PauseGenerator`
backpressure feature across all supported interface types, and contrasts the
Python-stepped :class:`~veriforge.sim.bench.Testbench` (flexible, supports
live pause) with the compiled-engine :func:`~veriforge.sim.bench.compile_native`
fast path (no per-cycle Python overhead, no pause).

-------------------------------------------------------------------------------
How this scaffold was created
-------------------------------------------------------------------------------

For the AXI-Stream loopback (AXIS source + sink):

    uv run veriforge generate-python-testbench \\
        --enhanced --style bench --engine native \\
        -f <(python -c "print(LOOPBACK_VERILOG)") \\
        --module axis_loopback -o axis_loopback_stub.py

For the AXI-Lite slave register file:

    uv run veriforge generate-python-testbench \\
        --enhanced --style bench \\
        -f <(python -c "print(AXIL_REGS_VERILOG)") \\
        --module axil_regfile -o axil_regs_stub.py

For the AXI-Lite master writer (DUT drives the bus, bench is responder):

    uv run veriforge generate-python-testbench \\
        --enhanced --style bench \\
        -f <(python -c "print(AXIL_WRITER_VERILOG)") \\
        --module axil_writer -o axil_writer_stub.py

For the AXI4 memory slave:

    uv run veriforge generate-python-testbench \\
        --enhanced --style bench \\
        -f <(python -c "print(AXI4_RAM_VERILOG)") \\
        --module axi4_ram -o axi4_ram_stub.py

All four stubs were merged here and their placeholder stubs replaced with the
real exercise functions you see below.

-------------------------------------------------------------------------------
Demo structure
-------------------------------------------------------------------------------

  demo_axis_source_pause()  — bench ← (AXI-Stream) ← DUT; gate tvalid
  demo_axis_sink_pause()    — bench → (AXI-Stream) → DUT; gate tready
  demo_axil_slave_write_read() — bench drives AXI-Lite writes/reads to DUT slave
  demo_axil_responder_pause() — DUT is AXI-Lite master; bench gates awready/wready
  demo_axi4_write_read()    — bench drives AXI4 bursts to DUT slave
  demo_compile_native()     — same AXIS loopback, compiled engine, no Python overhead

Run:
    uv run python examples/pause_demo/pause_demo.py [--vcd DIR]
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# DUT Verilog sources (inline strings — no external .v file required)
# ---------------------------------------------------------------------------

# 1. AXI-Stream wire-through (combinational loopback + heartbeat tick reg).
#    Interface: m_axis_* (DUT slave — bench sends) + s_axis_* (DUT master — bench receives).
LOOPBACK_VERILOG = """
module axis_loopback (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        m_axis_tvalid,
    output wire        m_axis_tready,
    input  wire [7:0]  m_axis_tdata,
    input  wire        m_axis_tlast,
    output wire        s_axis_tvalid,
    input  wire        s_axis_tready,
    output wire [7:0]  s_axis_tdata,
    output wire        s_axis_tlast
);
    assign s_axis_tvalid = m_axis_tvalid;
    assign s_axis_tdata  = m_axis_tdata;
    assign s_axis_tlast  = m_axis_tlast;
    assign m_axis_tready = s_axis_tready;

    // Heartbeat register — gives the design an always block so clock/reset
    // detection is unambiguous (active-low asynchronous reset).
    reg [7:0] tick;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) tick <= 8'h00; else tick <= tick + 8'd1;
endmodule
"""

# 2. AXI-Lite slave register file — 4 × 32-bit registers.
#    Interface: s_axi_* (DUT slave — bench master sends reads/writes).
AXIL_REGS_VERILOG = """
module axil_regfile (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [3:0]  s_axi_awaddr,
    input  wire [2:0]  s_axi_awprot,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,

    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,

    output wire [1:0]  s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,

    input  wire [3:0]  s_axi_araddr,
    input  wire [2:0]  s_axi_arprot,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,

    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready
);
    reg [31:0] regs0, regs1, regs2, regs3;
    reg        aw_seen, w_seen, b_pending, r_pending;
    reg [3:0]  aw_addr_q;
    reg [31:0] w_data_q;
    reg [3:0]  w_strb_q;
    reg [31:0] rdata_q;

    assign s_axi_awready = ~aw_seen & ~b_pending;
    assign s_axi_wready  = ~w_seen  & ~b_pending;
    assign s_axi_bvalid  = b_pending;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_arready = ~r_pending;
    assign s_axi_rvalid  = r_pending;
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rdata   = rdata_q;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            regs0 <= 0; regs1 <= 0; regs2 <= 0; regs3 <= 0;
            aw_seen <= 0; w_seen <= 0; b_pending <= 0; r_pending <= 0;
            aw_addr_q <= 0; w_data_q <= 0; w_strb_q <= 0; rdata_q <= 0;
        end else begin
            if (s_axi_awvalid && s_axi_awready) begin aw_addr_q <= s_axi_awaddr; aw_seen <= 1; end
            if (s_axi_wvalid  && s_axi_wready)  begin w_data_q  <= s_axi_wdata;
                                                       w_strb_q  <= s_axi_wstrb; w_seen <= 1; end
            if (aw_seen && w_seen && !b_pending) begin
                case (aw_addr_q[3:2])
                    2'd0: begin
                        if (w_strb_q[0]) regs0[ 7: 0] <= w_data_q[ 7: 0];
                        if (w_strb_q[1]) regs0[15: 8] <= w_data_q[15: 8];
                        if (w_strb_q[2]) regs0[23:16] <= w_data_q[23:16];
                        if (w_strb_q[3]) regs0[31:24] <= w_data_q[31:24];
                    end
                    2'd1: begin
                        if (w_strb_q[0]) regs1[ 7: 0] <= w_data_q[ 7: 0];
                        if (w_strb_q[1]) regs1[15: 8] <= w_data_q[15: 8];
                        if (w_strb_q[2]) regs1[23:16] <= w_data_q[23:16];
                        if (w_strb_q[3]) regs1[31:24] <= w_data_q[31:24];
                    end
                    2'd2: begin
                        if (w_strb_q[0]) regs2[ 7: 0] <= w_data_q[ 7: 0];
                        if (w_strb_q[1]) regs2[15: 8] <= w_data_q[15: 8];
                        if (w_strb_q[2]) regs2[23:16] <= w_data_q[23:16];
                        if (w_strb_q[3]) regs2[31:24] <= w_data_q[31:24];
                    end
                    default: begin
                        if (w_strb_q[0]) regs3[ 7: 0] <= w_data_q[ 7: 0];
                        if (w_strb_q[1]) regs3[15: 8] <= w_data_q[15: 8];
                        if (w_strb_q[2]) regs3[23:16] <= w_data_q[23:16];
                        if (w_strb_q[3]) regs3[31:24] <= w_data_q[31:24];
                    end
                endcase
                aw_seen <= 0; w_seen <= 0; b_pending <= 1;
            end
            if (s_axi_bvalid && s_axi_bready) b_pending <= 0;
            if (s_axi_arvalid && s_axi_arready) begin
                r_pending <= 1;
                case (s_axi_araddr[3:2])
                    2'd0: rdata_q <= regs0;
                    2'd1: rdata_q <= regs1;
                    2'd2: rdata_q <= regs2;
                    default: rdata_q <= regs3;
                endcase
            end
            if (s_axi_rvalid && s_axi_rready) r_pending <= 0;
        end
    end
endmodule
"""

# 3. AXI-Lite master writer — DUT drives the bus; bench acts as AXI-Lite responder.
#    After reset, the DUT sequentially writes 4 values (0xA0..0xA3) to
#    addresses 0x0, 0x4, 0x8, 0xC, then asserts done=1.
#    The bench's awready/wready can be gated by PauseGenerator to backpressure
#    the DUT and observe how long the writes take under load.
AXIL_WRITER_VERILOG = """
module axil_writer (
    input  wire        clk,
    input  wire        rst_n,
    output wire        done,            // pulses high (level) when all 4 writes finish

    // AXI-Lite master outputs (driven by DUT)
    output wire [3:0]  m_axi_awaddr,
    output wire [2:0]  m_axi_awprot,
    output wire        m_axi_awvalid,
    input  wire        m_axi_awready,

    output wire [31:0] m_axi_wdata,
    output wire [3:0]  m_axi_wstrb,
    output wire        m_axi_wvalid,
    input  wire        m_axi_wready,

    input  wire [1:0]  m_axi_bresp,
    input  wire        m_axi_bvalid,
    output wire        m_axi_bready,

    // Read channel tied off (unused in this demo)
    output wire [3:0]  m_axi_araddr,
    output wire [2:0]  m_axi_arprot,
    output wire        m_axi_arvalid,
    input  wire        m_axi_arready,
    input  wire [31:0] m_axi_rdata,
    input  wire [1:0]  m_axi_rresp,
    input  wire        m_axi_rvalid,
    output wire        m_axi_rready
);
    // State encoding.
    // The AXI-Lite responder fires write_fire only when awvalid, wvalid,
    // awready, and wready are ALL high simultaneously.  The DUT therefore
    // presents awvalid=1 AND wvalid=1 together from the start of S_TXFR,
    // and holds both until the bench accepts the transaction in one shot
    // (awready and wready are driven together by the same pause generator).
    localparam S_IDLE = 3'd0;
    localparam S_TXFR = 3'd1;  // present AW+W simultaneously; wait for awready & wready
    localparam S_B    = 3'd2;  // assert bready; wait for bvalid
    localparam S_DONE = 3'd3;  // all 4 writes sent

    reg [2:0]  state;
    reg [2:0]  idx;              // write index 0..3
    reg [3:0]  r_awaddr;
    reg        r_awvalid;
    reg [31:0] r_wdata;
    reg [3:0]  r_wstrb;
    reg        r_wvalid;
    reg        r_bready;
    reg        r_done;

    assign m_axi_awaddr  = r_awaddr;
    assign m_axi_awvalid = r_awvalid;
    assign m_axi_wdata   = r_wdata;
    assign m_axi_wstrb   = r_wstrb;
    assign m_axi_wvalid  = r_wvalid;
    assign m_axi_bready  = r_bready;
    assign done          = r_done;

    // Read channel permanently idle
    assign m_axi_araddr  = 4'h0;
    assign m_axi_arprot  = 3'h0;
    assign m_axi_arvalid = 1'b0;
    assign m_axi_rready  = 1'b1;
    assign m_axi_awprot  = 3'h0;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state    <= S_IDLE;
            idx      <= 3'd0;
            r_awaddr <= 0; r_awvalid <= 0;
            r_wdata  <= 0; r_wstrb   <= 0; r_wvalid <= 0;
            r_bready <= 0; r_done    <= 0;
        end else begin
            case (state)
                S_IDLE: begin
                    if (idx < 4) begin
                        // Present AW and W channels simultaneously so the bench
                        // responder sees all four handshake signals in one cycle.
                        r_awaddr  <= idx * 4;
                        r_awvalid <= 1'b1;
                        r_wdata   <= 32'hA0 + idx;
                        r_wstrb   <= 4'hF;
                        r_wvalid  <= 1'b1;
                        r_bready  <= 1'b0;  // deasserted until S_B
                        state     <= S_TXFR;
                    end else begin
                        r_done <= 1'b1;
                        state  <= S_DONE;
                    end
                end
                S_TXFR: begin
                    // awready and wready are driven together by the pause generator
                    // on the bench side.  Wait until both fire in the same cycle.
                    if (m_axi_awready && m_axi_wready) begin
                        r_awvalid <= 1'b0;
                        r_wvalid  <= 1'b0;
                        r_bready  <= 1'b1;  // assert bready to accept the response
                        state     <= S_B;
                    end
                end
                S_B: begin
                    // Bench drives bvalid after write_fire; bready was asserted
                    // when entering this state so the handshake completes as soon
                    // as bvalid appears.
                    if (m_axi_bvalid && r_bready) begin
                        r_bready <= 1'b0;
                        idx      <= idx + 1;
                        state    <= S_IDLE;
                    end
                end
                default: ; // S_DONE — hold done=1 indefinitely
            endcase
        end
    end
endmodule
"""

# 4. AXI4 single-beat slave memory — 8 × 32-bit words, no burst (AWLEN must be 0).
#    Interface: s_axi_* (DUT slave — bench AXI4Master sends reads/writes).
#    Supports WSTRB byte-enables.
AXI4_RAM_VERILOG = """
module axi4_ram (
    input  wire        clk,
    input  wire        rst_n,

    // Write address channel
    input  wire [3:0]  s_axi_awid,
    input  wire [4:0]  s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,
    input  wire [2:0]  s_axi_awsize,
    input  wire [1:0]  s_axi_awburst,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,

    // Write data channel
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,

    // Write response channel
    output wire [3:0]  s_axi_bid,
    output wire [1:0]  s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,

    // Read address channel
    input  wire [3:0]  s_axi_arid,
    input  wire [4:0]  s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,
    input  wire [2:0]  s_axi_arsize,
    input  wire [1:0]  s_axi_arburst,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,

    // Read data channel
    output wire [3:0]  s_axi_rid,
    output wire [31:0] s_axi_rdata,
    output wire [1:0]  s_axi_rresp,
    output wire        s_axi_rlast,
    output wire        s_axi_rvalid,
    input  wire        s_axi_rready
);
    reg [31:0] mem [0:7];
    reg        aw_pending, w_pending, b_pending, r_pending;
    reg [4:0]  aw_addr_q;
    reg [3:0]  aw_id_q, ar_id_q;
    reg [31:0] rdata_q, wdata_q;
    reg [3:0]  wstrb_q;
    reg [4:0]  ar_addr_q;

    assign s_axi_awready = ~aw_pending & ~b_pending;
    assign s_axi_wready  = ~w_pending  & ~b_pending;
    assign s_axi_bvalid  = b_pending;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_bid     = aw_id_q;
    assign s_axi_arready = ~r_pending;
    assign s_axi_rvalid  = r_pending;
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rlast   = r_pending;   // single-beat: last always with data
    assign s_axi_rid     = ar_id_q;
    assign s_axi_rdata   = rdata_q;

    // Note: mem[] is uninitialised at power-on; that is intentional for a
    // simulation demo (writes always precede reads in this bench).
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            aw_pending <= 0; w_pending <= 0; b_pending <= 0; r_pending <= 0;
            aw_addr_q  <= 0; aw_id_q   <= 0; ar_id_q   <= 0;
            rdata_q    <= 0; ar_addr_q <= 0; wdata_q   <= 0; wstrb_q  <= 0;
        end else begin
            if (s_axi_awvalid && s_axi_awready) begin
                aw_addr_q  <= s_axi_awaddr;
                aw_id_q    <= s_axi_awid;
                aw_pending <= 1;
            end
            // Latch wdata/wstrb at W-channel handshake so data is stable
            // when the write is committed one cycle later.
            if (s_axi_wvalid && s_axi_wready) begin
                wdata_q   <= s_axi_wdata;
                wstrb_q   <= s_axi_wstrb;
                w_pending <= 1;
            end
            if (aw_pending && w_pending && !b_pending) begin
                if (wstrb_q[0]) mem[aw_addr_q[4:2]][ 7: 0] <= wdata_q[ 7: 0];
                if (wstrb_q[1]) mem[aw_addr_q[4:2]][15: 8] <= wdata_q[15: 8];
                if (wstrb_q[2]) mem[aw_addr_q[4:2]][23:16] <= wdata_q[23:16];
                if (wstrb_q[3]) mem[aw_addr_q[4:2]][31:24] <= wdata_q[31:24];
                aw_pending <= 0; w_pending <= 0; b_pending <= 1;
            end
            if (s_axi_bvalid && s_axi_bready) b_pending <= 0;
            if (s_axi_arvalid && s_axi_arready) begin
                ar_addr_q <= s_axi_araddr;
                ar_id_q   <= s_axi_arid;
                rdata_q   <= mem[s_axi_araddr[4:2]];
                r_pending <= 1;
            end
            if (s_axi_rvalid && s_axi_rready) r_pending <= 0;
        end
    end
endmodule
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_verilog(src: str):
    """Parse a Verilog source string and return (design, first_module)."""
    from veriforge.transforms.tree_to_model import tree_to_design
    from veriforge.verilog_parser import verilog_parser

    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src.strip())
    design = tree_to_design(tree)
    return design, design.modules[0]


def _banner(title: str) -> None:
    width = 72
    print("\n" + "=" * width)
    print(f"  {title}")
    print("=" * width)


# ---------------------------------------------------------------------------
# Demo 1 — AXI-Stream: source-side backpressure (gate tvalid)
# ---------------------------------------------------------------------------


def demo_axis_source_pause(vcd_dir: Path | None = None) -> None:
    """Demonstrate PauseGenerator on the AXI-Stream source.

    The bench is the *source* (DUT slave = ``m_axis_*``). Pausing the source
    holds ``tvalid`` low, simulating upstream backpressure — e.g. a slow
    upstream producer or an intentional bandwidth-limit on a test vector.

    Two runs compare:
      * No pause   — source fires on every ready cycle (100% bandwidth)
      * 1-in-3 pause — source idles ~33% of cycles (~67% bandwidth)
    """
    _banner("Demo 1 — AXI-Stream source pause  (gate tvalid on m_axis)")
    from veriforge.sim.bench import Testbench
    from veriforge.sim.endpoints import PauseGenerator

    design, dut = _parse_verilog(LOOPBACK_VERILOG)
    payload = list(range(0x10, 0x30))  # 32 bytes

    for label, pause_val in [
        ("no pause   (100% BW)", False),
        ("1/3 pause  ( 67% BW)", PauseGenerator(1, 3, seed=42)),
        ("1/2 pause  ( 50% BW)", PauseGenerator(1, 2, seed=99)),
    ]:
        vcd = vcd_dir / f"axis_src_pause_{label.split()[0]}.vcd" if vcd_dir else None
        bench = Testbench(dut, design=design, engine="reference")
        with bench.run(vcd=vcd):
            bench.reset_all()
            src = bench.iface("m_axis")  # bench is source
            snk = bench.iface("s_axis")  # bench is sink
            # Apply pause to the source only
            src.pause = pause_val
            t0 = time.perf_counter()
            src.put(payload)
            src.wait_drain()
            received = snk.get()
            elapsed_ms = (time.perf_counter() - t0) * 1000
        assert list(received.data) == payload, f"payload mismatch under '{label}'"
        print(f"  {label}: {elapsed_ms:>6.1f}ms for {len(payload)} beats" + (f"  → VCD: {vcd}" if vcd else ""))


# ---------------------------------------------------------------------------
# Demo 2 — AXI-Stream: sink-side backpressure (gate tready)
# ---------------------------------------------------------------------------


def demo_axis_sink_pause(vcd_dir: Path | None = None) -> None:
    """Demonstrate PauseGenerator on the AXI-Stream sink.

    The bench is the *sink* (DUT master = ``s_axis_*``). Pausing the sink
    holds ``tready`` low, simulating a slow downstream consumer — e.g. a
    rate-limited FIFO or a congested interconnect.

    Two pause rates are compared against the no-pause baseline.
    """
    _banner("Demo 2 — AXI-Stream sink pause  (gate tready on s_axis)")
    from veriforge.sim.bench import Testbench
    from veriforge.sim.endpoints import PauseGenerator

    design, dut = _parse_verilog(LOOPBACK_VERILOG)
    payload = list(range(0x40, 0x60))  # 32 bytes

    for label, pause_val in [
        ("no pause   (100% BW)", False),
        ("1/4 pause  ( 75% BW)", PauseGenerator(1, 4, seed=7)),
        ("1/2 pause  ( 50% BW)", PauseGenerator(1, 2, seed=13)),
    ]:
        vcd = vcd_dir / f"axis_snk_pause_{label.split()[0]}.vcd" if vcd_dir else None
        bench = Testbench(dut, design=design, engine="reference")
        with bench.run(vcd=vcd):
            bench.reset_all()
            src = bench.iface("m_axis")
            snk = bench.iface("s_axis")
            # Apply pause to the sink only
            snk.pause = pause_val
            t0 = time.perf_counter()
            src.put(payload)
            src.wait_drain()
            received = snk.get()
            elapsed_ms = (time.perf_counter() - t0) * 1000
        assert list(received.data) == payload, f"payload mismatch under '{label}'"
        print(f"  {label}: {elapsed_ms:>6.1f}ms for {len(payload)} beats" + (f"  → VCD: {vcd}" if vcd else ""))


# ---------------------------------------------------------------------------
# Demo 3 — AXI-Lite: bench drives writes/reads to DUT slave register file
# ---------------------------------------------------------------------------


def demo_axil_slave_write_read(vcd_dir: Path | None = None) -> None:
    """Bench-as-master: write and read back four 32-bit registers.

    The DUT is an AXI-Lite slave (``s_axi_*``). The bench drives the bus
    using :class:`~veriforge.sim.bench.interfaces.AXILiteProxy` with
    ``role="slave"`` (DUT is slave; bench is master).

    Note: *Pause on the bench master side is not applicable here* — the
    bench controls its own transaction rate. The DUT's awready/wready are
    driven by the DUT's internal logic, and the bench simply waits for them.
    To demonstrate gating those ready signals from the bench side, see
    :func:`demo_axil_responder_pause` where the *DUT* is the AXI-Lite master.
    """
    _banner("Demo 3 — AXI-Lite slave  (bench as master; write + read sweep)")
    from veriforge.sim.bench import Testbench

    design, dut = _parse_verilog(AXIL_REGS_VERILOG)
    vcd = vcd_dir / "axil_slave.vcd" if vcd_dir else None
    bench = Testbench(dut, design=design, engine="reference")
    with bench.run(vcd=vcd):
        bench.reset_all()
        iface = bench.iface("s_axi")  # AXILiteProxy(role="slave")

        # Write four registers
        payload = [0xDEAD_BEEF, 0xCAFE_F00D, 0x1234_5678, 0xA5A5_5A5A]
        addrs = [0x0, 0x4, 0x8, 0xC]
        for addr, val in zip(addrs, payload):
            iface.write(addr, val)

        # Read back and verify
        for addr, expected in zip(addrs, payload):
            got = iface.read(addr)
            assert got == expected, f"reg[0x{addr:x}] mismatch: {got:#x} != {expected:#x}"

        # Byte-strobe partial write: update only the low byte of reg0
        iface.write(0x0, 0x0000_00FF, strb=0b0001)
        got = iface.read(0x0)
        expected_after = (payload[0] & 0xFFFF_FF00) | 0xFF
        assert got == expected_after, f"strb write failed: {got:#x} != {expected_after:#x}"

        if vcd:
            print(f"  VCD: {vcd}")
    print("  4-register write/read sweep + WSTRB partial write: PASSED")


# ---------------------------------------------------------------------------
# Demo 4 — AXI-Lite: responder pause (DUT is master, bench gates ready)
# ---------------------------------------------------------------------------


def demo_axil_responder_pause(vcd_dir: Path | None = None) -> None:
    """Demonstrate PauseGenerator on the AXI-Lite responder.

    The DUT (``axil_writer``) is an AXI-Lite *master* — after reset it
    issues 4 sequential writes.  The bench acts as the AXI-Lite *responder*
    (slave), which means the bench drives ``awready``, ``wready``, and
    ``arready``.

    ``PauseGenerator`` on the responder holds those ready signals low on
    selected cycles, forcing the DUT master to wait — exactly like a slow
    downstream slave or congested interconnect.

    The cycle count until DUT ``done=1`` is compared for three scenarios:
      * always_ready, no pause    — fastest path
      * 1-in-4 ready pause        — ~25% stall rate, modest slowdown
      * 1-in-2 ready pause        — ~50% stall rate, roughly 2× slower

    After each run the bench's responder ``.memory`` dict is checked for the
    correct payload written by the DUT.

    Implementation detail
    ---------------------
    ``PauseGenerator`` is sampled once per rising clock edge in the
    responder's ``_on_time_step`` callback.  It is guaranteed to advance the
    RNG exactly once per cycle regardless of how many internal checks the
    responder performs per step.
    """
    _banner("Demo 4 — AXI-Lite responder pause  (DUT master; bench gates awready/wready)")
    from veriforge.sim.bench import Testbench
    from veriforge.sim.endpoints import PauseGenerator

    design, dut = _parse_verilog(AXIL_WRITER_VERILOG)

    # Expected memory after all 4 writes: keyed by byte address.
    # DUT writes: addr=i*4, data=0xA0+i (32-bit, all strobes enabled).
    expected_memory = {i * 4: 0xA0 + i for i in range(4)}

    for label, pause_val in [
        ("no pause      (100% ready)", False),
        ("1/4 pause     ( 75% ready)", PauseGenerator(1, 4, seed=17)),
        ("1/2 pause     ( 50% ready)", PauseGenerator(1, 2, seed=31)),
    ]:
        vcd = vcd_dir / f"axil_resp_pause_{label.split()[0]}.vcd" if vcd_dir else None
        bench = Testbench(dut, design=design, engine="reference")
        with bench.run(vcd=vcd):
            bench.reset_all()
            resp = bench.iface("m_axi")  # AXILiteProxy(role="master") — bench is responder
            # write_hold_cycles=2 keeps bvalid high for 3 rising edges after bready
            # is first seen — necessary for a registered (hardware) DUT master where
            # bready appears one cycle after AW+W acceptance.
            resp._responder.write_hold_cycles = 2
            # Gate bench awready/wready with PauseGenerator
            resp.pause = pause_val

            # Wait for the DUT to assert 'done' (all 4 writes completed).
            # We poll up to 500 cycles.
            elapsed = 0
            for _ in range(500):
                bench.domain("clk").step(1)
                elapsed += 1
                if bench.sim.read("done") == 1:
                    break
            else:
                raise AssertionError(f"DUT 'done' never asserted under '{label}'")

        # Verify that the correct values landed in the responder's memory
        # (memory is keyed by byte address: 0, 4, 8, 12).
        for byte_addr, expected_val in expected_memory.items():
            actual = resp.memory.get(byte_addr, 0)
            assert actual == expected_val, (
                f"[{label}] addr 0x{byte_addr:x}: expected 0x{expected_val:02x}, got 0x{actual:08x}"
            )
        print(f"  {label}: {elapsed:>4} cycles until done" + (f"  → VCD: {vcd}" if vcd else ""))


# ---------------------------------------------------------------------------
# Demo 5 — AXI4: bench drives write + burst-read to DUT slave memory
# ---------------------------------------------------------------------------


def demo_axi4_write_read(vcd_dir: Path | None = None) -> None:
    """Bench-as-master: AXI4 single-beat writes and reads against the DUT RAM.

    The DUT is an AXI4 slave (``s_axi_*``).  The bench wraps an
    :class:`~veriforge.sim.endpoints.AXI4Master` via
    :class:`~veriforge.sim.bench.interfaces.AXI4Proxy` (``role="slave"``).

    AXI4 *responder* pause (bench gates ``awready``/``wready``/``arready`` for a
    DUT that is an AXI4 *master*) works identically to the AXI-Lite responder
    demo above.  See :class:`~veriforge.sim.bench.interfaces.AXI4Proxy`
    (``role="master"``) to enable it.
    """
    _banner("Demo 5 — AXI4 slave  (bench as master; single-beat write + read sweep)")
    from veriforge.sim.bench import Testbench

    design, dut = _parse_verilog(AXI4_RAM_VERILOG)
    vcd = vcd_dir / "axi4_ram.vcd" if vcd_dir else None
    bench = Testbench(dut, design=design, engine="reference")
    with bench.run(vcd=vcd):
        bench.reset_all()
        iface = bench.iface("s_axi")  # AXI4Proxy(role="slave")

        # Single-beat write sweep across all 8 words
        payload = [0xDEAD_0000 | i for i in range(8)]
        for i, val in enumerate(payload):
            iface.write(i * 4, val)

        # Read back single-beat
        for i, expected in enumerate(payload):
            got = iface.read(i * 4, length=1)
            assert got[0] == expected, f"word {i} mismatch: {got[0]:#010x} != {expected:#010x}"

        # WSTRB partial-byte write: update only the low byte of word 0
        iface.write(0x0, 0x0000_00AB, strb=0b0001)
        got_low = iface.read(0x0, length=1)
        expected_low = (payload[0] & 0xFFFF_FF00) | 0xAB
        assert got_low[0] == expected_low, f"WSTRB mismatch: {got_low[0]:#010x} != {expected_low:#010x}"

        if vcd:
            print(f"  VCD: {vcd}")
    print("  AXI4 8-word sweep + WSTRB partial-byte write: PASSED")


# ---------------------------------------------------------------------------
# Demo 6 — compile_native fast path (AXIS loopback, compiled engine)
# ---------------------------------------------------------------------------


def demo_compile_native() -> None:
    """Run the AXIS loopback using the compiled-engine native path.

    :func:`~veriforge.sim.bench.compile_native` lowers the bench
    primitives (source FSM + sink capture FSM) into Verilog DSL, wraps the
    DUT, and runs the resulting module entirely in the C-level compiled
    engine loop.

    There is **no per-cycle Python overhead** and **no PauseGenerator
    support** in this path — it is designed for fixed-vector regression
    sweeps where maximum throughput matters.  For experiments that require
    dynamic per-cycle pause logic, use the Python-stepped
    :class:`~veriforge.sim.bench.Testbench` (Demos 1 and 2).

    Speed comparison
    ----------------
    Both paths process the same 64-beat payload.  The Python-stepped path
    uses the reference engine.  The native path uses the compiled engine
    with ``batch_run()`` for maximum speed.
    """
    _banner("Demo 6 — compile_native fast path  (compiled engine, no Python overhead)")
    from veriforge.sim.bench import (
        AXIStreamSinkLowering,
        AXIStreamSourceLowering,
        Testbench,
        compile_native,
    )

    design, dut = _parse_verilog(LOOPBACK_VERILOG)
    beats = list(range(0x00, 0x40))  # 64 bytes

    # ---- Python-stepped reference (baseline) --------------------------------
    t_py_start = time.perf_counter()
    bench = Testbench(dut, design=design, engine="reference")
    with bench.run():
        bench.reset_all()
        src = bench.iface("m_axis")
        snk = bench.iface("s_axis")
        src.put(beats)
        src.wait_drain()
        received = snk.get()
    t_py = time.perf_counter() - t_py_start
    assert list(received.data) == beats

    # ---- compile_native + batch_run (fast path) -----------------------------
    t_native_start = time.perf_counter()
    bench2 = Testbench(dut, design=design, engine="reference")
    lowered = compile_native(
        bench2,
        lowerings={
            "m_axis": AXIStreamSourceLowering(beats=beats, data_width=8),
            "s_axis": AXIStreamSinkLowering(n_beats=len(beats), data_width=8),
        },
    )
    # batch_run drives clock + reset entirely in C — no Python per-cycle cost.
    results = lowered.batch_run(cycles=256, reset_cycles=4)
    t_native = time.perf_counter() - t_native_start

    assert results["s_axis_snk_done"] == 1, "Native sink never flagged done"
    captured = [results[f"s_axis_cap_{i}"] for i in range(len(beats))]
    assert captured == beats, f"Native captured mismatch: {captured} != {beats}"

    print(f"  Python-stepped (reference engine): {t_py * 1000:.1f} ms")
    print(f"  compile_native  (compiled engine): {t_native * 1000:.1f} ms")
    speedup = t_py / t_native if t_native > 0 else float("inf")
    print(f"  Speedup: {speedup:.1f}x")
    print()
    print("  Notes:")
    print("  - compile_native includes a one-time C compilation cost (~0.2-1 s).")
    print("    For short tests this outweighs the runtime gain; speedup is most")
    print("    visible in long regression sweeps (thousands of cycles).")
    print("  - PauseGenerator is NOT supported in compile_native/batch_run.")
    print("  - Use the Python-stepped Testbench (Demos 1-2) for dynamic pause.")
    print("  - batch_run uses the C-level loop; no VCD output is available.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run all six pause demos in sequence."""
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--vcd",
        type=Path,
        default=None,
        metavar="DIR",
        help=(
            "Optional directory for VCD waveform output.  One .vcd file is written"
            " per demo/variant.  The directory is created if it does not exist."
        ),
    )
    parser.add_argument(
        "--demo",
        choices=["1", "2", "3", "4", "5", "6", "all"],
        default="all",
        help="Run a single numbered demo (1–6) or all (default).",
    )
    args = parser.parse_args()

    vcd_dir: Path | None = None
    if args.vcd is not None:
        vcd_dir = args.vcd
        vcd_dir.mkdir(parents=True, exist_ok=True)

    demos = {
        "1": demo_axis_source_pause,
        "2": demo_axis_sink_pause,
        "3": demo_axil_slave_write_read,
        "4": demo_axil_responder_pause,
        "5": demo_axi4_write_read,
        "6": demo_compile_native,
    }

    selected = list(demos.keys()) if args.demo == "all" else [args.demo]
    for key in selected:
        fn = demos[key]
        if key in ("1", "2", "3", "4", "5"):
            fn(vcd_dir=vcd_dir)  # type: ignore[call-arg]
        else:
            fn()  # demo 6 has no VCD support

    print("\n" + "=" * 72)
    print("  All selected demos PASSED")
    print("=" * 72)


if __name__ == "__main__":
    main()
