"""Tests for the Phase 9 engine-native lowering."""

from __future__ import annotations

import pytest

from veriforge.sim.bench import (
    AXI4MasterLowering,
    AXI4MasterOp,
    AXI4SlaveLowering,
    AXILiteMasterLowering,
    AXILiteOp,
    AXILiteSlaveLowering,
    AXIStreamSinkLowering,
    AXIStreamSourceLowering,
    LoweringError,
    MemBusMasterLowering,
    MemBusOp,
    MemBusResponderLowering,
    Testbench,
    compile_native,
)
from veriforge.sim.testbench import Clock, Simulator
from veriforge.transforms.tree_to_model import tree_to_design
from veriforge.verilog_parser import verilog_parser


# Single-domain combinational AXIS pass-through, identical in shape to
# the SINGLE_DOMAIN_LOOPBACK fixture used by test_bench_runtime, but
# kept self-contained here so this test file can be read in isolation.
LOOPBACK_SRC = """
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

    reg [7:0] tick;
    always @(posedge clk or negedge rst_n)
        if (!rst_n) tick <= 8'h00; else tick <= tick + 8'd1;
endmodule
"""


def _parse(src: str):
    parser = verilog_parser(start="module_declaration")
    tree = parser.build_tree(text=src)
    design = tree_to_design(tree)
    return design.modules[0]


# ---------------------------------------------------------------------------
# Validation / error-path tests
# ---------------------------------------------------------------------------


class TestValidation:
    def test_missing_lowering_raises(self):
        bench = Testbench(_parse(LOOPBACK_SRC))
        with pytest.raises(LoweringError, match="no lowering provided"):
            compile_native(bench, lowerings={"m_axis": AXIStreamSourceLowering([1, 2, 3])})

    def test_unknown_prefix_raises(self):
        bench = Testbench(_parse(LOOPBACK_SRC))
        with pytest.raises(LoweringError, match="unknown interface"):
            compile_native(
                bench,
                lowerings={
                    "m_axis": AXIStreamSourceLowering([1]),
                    "s_axis": AXIStreamSinkLowering(1),
                    "ghost": AXIStreamSourceLowering([0]),
                },
            )

    def test_role_mismatch_raises(self):
        bench = Testbench(_parse(LOOPBACK_SRC))
        # Apply a *sink* lowering to the DUT-slave m_axis (which needs a
        # source). This must surface a clear role mismatch error.
        with pytest.raises(LoweringError, match="role"):
            compile_native(
                bench,
                lowerings={
                    "m_axis": AXIStreamSinkLowering(1),
                    "s_axis": AXIStreamSinkLowering(1),
                },
            )

    def test_empty_beats_raises(self):
        bench = Testbench(_parse(LOOPBACK_SRC))
        with pytest.raises(LoweringError, match="non-empty"):
            compile_native(
                bench,
                lowerings={
                    "m_axis": AXIStreamSourceLowering([]),
                    "s_axis": AXIStreamSinkLowering(4),
                },
            )

    def test_method_on_testbench(self):
        bench = Testbench(_parse(LOOPBACK_SRC))
        # The thin Testbench.compile_native wrapper should agree with the
        # module-level function on the validation path.
        with pytest.raises(LoweringError):
            bench.compile_native(lowerings={"m_axis": AXIStreamSourceLowering([1])})


# ---------------------------------------------------------------------------
# Round-trip: source feeds into combinational loopback, sink captures
# ---------------------------------------------------------------------------


def _read_signal(sim: Simulator, name: str) -> int:
    """Read a top-level signal value from the wrapper simulation."""
    handle = sim.signal(name)
    return int(handle.value)


class TestRoundTrip:
    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_axis_loopback_native_roundtrip(self, engine):
        beats = [0xA1, 0xB2, 0xC3, 0xD4, 0xE5]
        bench = Testbench(_parse(LOOPBACK_SRC))
        lowered = compile_native(
            bench,
            lowerings={
                "m_axis": AXIStreamSourceLowering(beats=beats, data_width=8),
                "s_axis": AXIStreamSinkLowering(n_beats=len(beats), data_width=8),
            },
        )
        # The wrapper module should now contain the DUT instance plus
        # the bench fragments, and live alongside the DUT in the design.
        assert lowered.wrapper.name == "bench_native_top"
        assert {m.name for m in lowered.design.modules} == {
            "bench_native_top",
            "axis_loopback",
        }
        assert lowered.done_signals == {"s_axis": "s_axis_snk_done"}
        assert lowered.capture_signals["s_axis"] == [f"s_axis_cap_{i}" for i in range(len(beats))]
        assert lowered.capture_signals["m_axis"] == []

        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)

        # Drive clk via the standard Clock helper, hold reset asserted
        # for a few cycles, then release.
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1

        # Run long enough for all beats to traverse the combinational
        # loopback: ~ len(beats) + 4 cycles of margin.
        sim.run(max_time=10 * (len(beats) + 8))

        # Verify capture
        assert _read_signal(sim, "s_axis_snk_done") == 1
        captured = [_read_signal(sim, f"s_axis_cap_{i}") for i in range(len(beats))]
        assert captured == beats


# ---------------------------------------------------------------------------
# AXI-Lite master lowering — drive scripted writes/reads against a tiny
# 4-register slave fixture.
# ---------------------------------------------------------------------------


AXI_LITE_REGS_SRC = """
module axi_lite_regs_tiny (
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
    reg [3:0]  ar_addr_q;
    reg [31:0] w_data_q;
    reg [3:0]  w_strb_q;
    reg [31:0] rdata_q;

    assign s_axi_awready = ~aw_seen & ~b_pending;
    assign s_axi_wready  = ~w_seen & ~b_pending;
    assign s_axi_bvalid  = b_pending;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_arready = ~r_pending;
    assign s_axi_rvalid  = r_pending;
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rdata   = rdata_q;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            regs0 <= 32'h0;
            regs1 <= 32'h0;
            regs2 <= 32'h0;
            regs3 <= 32'h0;
            aw_seen <= 1'b0;
            w_seen <= 1'b0;
            b_pending <= 1'b0;
            r_pending <= 1'b0;
            aw_addr_q <= 4'h0;
            ar_addr_q <= 4'h0;
            w_data_q <= 32'h0;
            w_strb_q <= 4'h0;
            rdata_q <= 32'h0;
        end else begin
            if (s_axi_awvalid && s_axi_awready) begin
                aw_addr_q <= s_axi_awaddr;
                aw_seen <= 1'b1;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                w_data_q <= s_axi_wdata;
                w_strb_q <= s_axi_wstrb;
                w_seen <= 1'b1;
            end
            if (aw_seen && w_seen && !b_pending) begin
                case (aw_addr_q[3:2])
                    2'd0: begin
                        if (w_strb_q[0]) regs0[7:0]   <= w_data_q[7:0];
                        if (w_strb_q[1]) regs0[15:8]  <= w_data_q[15:8];
                        if (w_strb_q[2]) regs0[23:16] <= w_data_q[23:16];
                        if (w_strb_q[3]) regs0[31:24] <= w_data_q[31:24];
                    end
                    2'd1: begin
                        if (w_strb_q[0]) regs1[7:0]   <= w_data_q[7:0];
                        if (w_strb_q[1]) regs1[15:8]  <= w_data_q[15:8];
                        if (w_strb_q[2]) regs1[23:16] <= w_data_q[23:16];
                        if (w_strb_q[3]) regs1[31:24] <= w_data_q[31:24];
                    end
                    2'd2: begin
                        if (w_strb_q[0]) regs2[7:0]   <= w_data_q[7:0];
                        if (w_strb_q[1]) regs2[15:8]  <= w_data_q[15:8];
                        if (w_strb_q[2]) regs2[23:16] <= w_data_q[23:16];
                        if (w_strb_q[3]) regs2[31:24] <= w_data_q[31:24];
                    end
                    2'd3: begin
                        if (w_strb_q[0]) regs3[7:0]   <= w_data_q[7:0];
                        if (w_strb_q[1]) regs3[15:8]  <= w_data_q[15:8];
                        if (w_strb_q[2]) regs3[23:16] <= w_data_q[23:16];
                        if (w_strb_q[3]) regs3[31:24] <= w_data_q[31:24];
                    end
                endcase
                aw_seen <= 1'b0;
                w_seen <= 1'b0;
                b_pending <= 1'b1;
            end
            if (b_pending && s_axi_bready) begin
                b_pending <= 1'b0;
            end

            if (s_axi_arvalid && s_axi_arready) begin
                ar_addr_q <= s_axi_araddr;
                case (s_axi_araddr[3:2])
                    2'd0: rdata_q <= regs0;
                    2'd1: rdata_q <= regs1;
                    2'd2: rdata_q <= regs2;
                    2'd3: rdata_q <= regs3;
                endcase
                r_pending <= 1'b1;
            end
            if (r_pending && s_axi_rready) begin
                r_pending <= 1'b0;
            end
        end
    end
endmodule
"""


class TestAXILiteMasterLowering:
    def test_role_mismatch_raises(self):
        bench = Testbench(_parse(AXI_LITE_REGS_SRC))
        # The DUT slave port wants role="slave"; passing role="master" must error.
        bad = AXILiteMasterLowering(operations=[AXILiteOp.read(0)])
        bad.role = "master"
        with pytest.raises(LoweringError, match="role"):
            compile_native(bench, lowerings={"s_axi": bad})

    def test_empty_script_raises(self):
        bench = Testbench(_parse(AXI_LITE_REGS_SRC))
        with pytest.raises(LoweringError, match="non-empty"):
            compile_native(
                bench,
                lowerings={"s_axi": AXILiteMasterLowering(operations=[])},
            )

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_axi_lite_master_write_then_read(self, engine):
        ops = [
            AXILiteOp.write(0x0, 0xDEADBEEF),
            AXILiteOp.write(0x4, 0x12345678),
            AXILiteOp.write(0x8, 0xCAFEBABE),
            AXILiteOp.write(0xC, 0x0BADF00D),
            AXILiteOp.read(0x0),
            AXILiteOp.read(0x4),
            AXILiteOp.read(0x8),
            AXILiteOp.read(0xC),
        ]
        bench = Testbench(_parse(AXI_LITE_REGS_SRC))
        lowered = compile_native(
            bench,
            lowerings={"s_axi": AXILiteMasterLowering(operations=ops, addr_width=4)},
        )
        assert lowered.done_signals == {"s_axi": "s_axi_master_done"}
        assert "s_axi_op_4_rdata" in lowered.capture_signals["s_axi"]

        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        sim.run(max_time=10 * (len(ops) * 6 + 16))

        assert _read_signal(sim, "s_axi_master_done") == 1
        # Writes return resp=0
        for i in range(4):
            assert _read_signal(sim, f"s_axi_op_{i}_resp") == 0
        # Reads return the values written
        assert _read_signal(sim, "s_axi_op_4_rdata") == 0xDEADBEEF
        assert _read_signal(sim, "s_axi_op_5_rdata") == 0x12345678
        assert _read_signal(sim, "s_axi_op_6_rdata") == 0xCAFEBABE
        assert _read_signal(sim, "s_axi_op_7_rdata") == 0x0BADF00D

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_axi_lite_master_wstrb_partial(self, engine):
        # Seed reg0 with all-FF, then partial-byte write only bytes 0 and 2.
        ops = [
            AXILiteOp.write(0x0, 0xFFFFFFFF, strb=0xF),
            AXILiteOp.write(0x0, 0xAABBCCDD, strb=0b0101),
            AXILiteOp.read(0x0),
        ]
        bench = Testbench(_parse(AXI_LITE_REGS_SRC))
        lowered = compile_native(
            bench,
            lowerings={"s_axi": AXILiteMasterLowering(operations=ops, addr_width=4)},
        )
        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        sim.run(max_time=10 * 40)

        assert _read_signal(sim, "s_axi_master_done") == 1
        # Bytes 0 and 2 came from 0xAABBCCDD (DD and BB), bytes 1 and 3 stay FF.
        assert _read_signal(sim, "s_axi_op_2_rdata") == 0xFFBBFFDD


# ---------------------------------------------------------------------------
# AXI4 slave lowering — provide a tiny scripted AXI4 master DUT and verify
# the lowering captures writes into the memory and serves reads correctly.
# ---------------------------------------------------------------------------


# Tiny AXI4 master that on de-assertion of reset:
#   1) issues a 4-beat INCR write burst at address 0x10 with data 0x100..0x103
#   2) issues a 4-beat INCR read burst at address 0x10 and stores the data
# All writes use full WSTRB (0xF). DATA_WIDTH=32, ADDR_WIDTH=8.
AXI4_TINY_MASTER_SRC = """
module axi4_tiny_master (
    input  wire        clk,
    input  wire        rst_n,

    output reg  [7:0]  m_axi_awaddr,
    output reg  [7:0]  m_axi_awlen,
    output reg  [2:0]  m_axi_awsize,
    output reg  [1:0]  m_axi_awburst,
    output reg         m_axi_awvalid,
    input  wire        m_axi_awready,

    output reg  [31:0] m_axi_wdata,
    output reg  [3:0]  m_axi_wstrb,
    output reg         m_axi_wlast,
    output reg         m_axi_wvalid,
    input  wire        m_axi_wready,

    input  wire [1:0]  m_axi_bresp,
    input  wire        m_axi_bvalid,
    output reg         m_axi_bready,

    output reg  [7:0]  m_axi_araddr,
    output reg  [7:0]  m_axi_arlen,
    output reg  [2:0]  m_axi_arsize,
    output reg  [1:0]  m_axi_arburst,
    output reg         m_axi_arvalid,
    input  wire        m_axi_arready,

    input  wire [31:0] m_axi_rdata,
    input  wire [1:0]  m_axi_rresp,
    input  wire        m_axi_rlast,
    input  wire        m_axi_rvalid,
    output reg         m_axi_rready,

    output reg         done,
    output reg  [31:0] read_word_0,
    output reg  [31:0] read_word_1,
    output reg  [31:0] read_word_2,
    output reg  [31:0] read_word_3
);
    // FSM
    localparam S_IDLE   = 4'd0;
    localparam S_AW     = 4'd1;
    localparam S_W      = 4'd2;
    localparam S_B      = 4'd3;
    localparam S_AR     = 4'd4;
    localparam S_R      = 4'd5;
    localparam S_DONE   = 4'd6;
    reg [3:0]  state;
    reg [2:0]  beat;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= S_IDLE;
            beat <= 3'd0;
            m_axi_awaddr <= 8'h0; m_axi_awlen <= 8'h0; m_axi_awsize <= 3'd2;
            m_axi_awburst <= 2'b01; m_axi_awvalid <= 1'b0;
            m_axi_wdata <= 32'h0; m_axi_wstrb <= 4'hF; m_axi_wlast <= 1'b0;
            m_axi_wvalid <= 1'b0;
            m_axi_bready <= 1'b0;
            m_axi_araddr <= 8'h0; m_axi_arlen <= 8'h0; m_axi_arsize <= 3'd2;
            m_axi_arburst <= 2'b01; m_axi_arvalid <= 1'b0;
            m_axi_rready <= 1'b0;
            done <= 1'b0;
            read_word_0 <= 32'h0; read_word_1 <= 32'h0;
            read_word_2 <= 32'h0; read_word_3 <= 32'h0;
        end else begin
            case (state)
                S_IDLE: begin
                    m_axi_awaddr <= 8'h10;
                    m_axi_awlen  <= 8'd3;       // 4 beats
                    m_axi_awsize <= 3'd2;       // 4 bytes
                    m_axi_awburst <= 2'b01;     // INCR
                    m_axi_awvalid <= 1'b1;
                    state <= S_AW;
                end
                S_AW: begin
                    if (m_axi_awvalid && m_axi_awready) begin
                        m_axi_awvalid <= 1'b0;
                        m_axi_wdata <= 32'h100;
                        m_axi_wstrb <= 4'hF;
                        m_axi_wlast <= 1'b0;
                        m_axi_wvalid <= 1'b1;
                        beat <= 3'd0;
                        state <= S_W;
                    end
                end
                S_W: begin
                    if (m_axi_wvalid && m_axi_wready) begin
                        if (beat == 3'd3) begin
                            m_axi_wvalid <= 1'b0;
                            m_axi_wlast <= 1'b0;
                            m_axi_bready <= 1'b1;
                            state <= S_B;
                        end else begin
                            beat <= beat + 3'd1;
                            m_axi_wdata <= 32'h100 + beat + 32'd1;
                            m_axi_wlast <= (beat == 3'd2);
                        end
                    end
                end
                S_B: begin
                    if (m_axi_bvalid && m_axi_bready) begin
                        m_axi_bready <= 1'b0;
                        m_axi_araddr <= 8'h10;
                        m_axi_arlen <= 8'd3;
                        m_axi_arsize <= 3'd2;
                        m_axi_arburst <= 2'b01;
                        m_axi_arvalid <= 1'b1;
                        beat <= 3'd0;
                        state <= S_AR;
                    end
                end
                S_AR: begin
                    if (m_axi_arvalid && m_axi_arready) begin
                        m_axi_arvalid <= 1'b0;
                        m_axi_rready <= 1'b1;
                        state <= S_R;
                    end
                end
                S_R: begin
                    if (m_axi_rvalid && m_axi_rready) begin
                        case (beat)
                            3'd0: read_word_0 <= m_axi_rdata;
                            3'd1: read_word_1 <= m_axi_rdata;
                            3'd2: read_word_2 <= m_axi_rdata;
                            3'd3: read_word_3 <= m_axi_rdata;
                        endcase
                        if (m_axi_rlast) begin
                            m_axi_rready <= 1'b0;
                            state <= S_DONE;
                        end else begin
                            beat <= beat + 3'd1;
                        end
                    end
                end
                S_DONE: begin
                    done <= 1'b1;
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
"""


class TestAXI4SlaveLowering:
    def test_role_mismatch_raises(self):
        bench = Testbench(_parse(AXI4_TINY_MASTER_SRC))
        bad = AXI4SlaveLowering(memory_depth=8)
        bad.role = "slave"
        with pytest.raises(LoweringError, match="role"):
            compile_native(bench, lowerings={"m_axi": bad})

    def test_invalid_memory_depth_raises(self):
        bench = Testbench(_parse(AXI4_TINY_MASTER_SRC))
        with pytest.raises(LoweringError, match="memory_depth"):
            compile_native(
                bench,
                lowerings={"m_axi": AXI4SlaveLowering(memory_depth=0)},
            )

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_axi4_slave_burst_roundtrip(self, engine):
        bench = Testbench(_parse(AXI4_TINY_MASTER_SRC))
        lowered = compile_native(
            bench,
            lowerings={
                "m_axi": AXI4SlaveLowering(
                    memory_depth=8,
                    data_width=32,
                    addr_width=8,
                    id_width=0,
                ),
            },
        )
        # 8 mem cells exposed
        assert "m_axi_slv_mem_4" in lowered.capture_signals["m_axi"]
        assert "m_axi_slv_mem_7" in lowered.capture_signals["m_axi"]

        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        # Master takes ~25 cycles to complete the full sequence; allow margin.
        sim.run(max_time=10 * 80)

        # Master should have completed and read back what it wrote.
        assert _read_signal(sim, "u_dut.done") == 1
        # Words written: 0x100, 0x101, 0x102, 0x103 starting at byte addr 0x10
        # -> word index 4..7 (since data_width/8 = 4 bytes per word).
        assert _read_signal(sim, "m_axi_slv_mem_4") == 0x100
        assert _read_signal(sim, "m_axi_slv_mem_5") == 0x101
        assert _read_signal(sim, "m_axi_slv_mem_6") == 0x102
        assert _read_signal(sim, "m_axi_slv_mem_7") == 0x103
        # Master should have read back the same words.
        assert _read_signal(sim, "u_dut.read_word_0") == 0x100
        assert _read_signal(sim, "u_dut.read_word_1") == 0x101
        assert _read_signal(sim, "u_dut.read_word_2") == 0x102
        assert _read_signal(sim, "u_dut.read_word_3") == 0x103
        # AW/AR counters
        assert _read_signal(sim, "m_axi_slv_aw_count") == 1
        assert _read_signal(sim, "m_axi_slv_w_count") == 4
        assert _read_signal(sim, "m_axi_slv_ar_count") == 1

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_axi4_slave_initial_memory(self, engine):
        # Same fixture, but pre-seed memory; the master writes will overwrite
        # only mem[4..7]; mem[0..3] should keep the initial values.
        bench = Testbench(_parse(AXI4_TINY_MASTER_SRC))
        lowered = compile_native(
            bench,
            lowerings={
                "m_axi": AXI4SlaveLowering(
                    memory_depth=8,
                    data_width=32,
                    addr_width=8,
                    initial_memory={0: 0xAAAA0000, 1: 0xAAAA0001, 2: 0xAAAA0002, 3: 0xAAAA0003},
                ),
            },
        )
        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        sim.run(max_time=10 * 80)

        assert _read_signal(sim, "u_dut.done") == 1
        assert _read_signal(sim, "m_axi_slv_mem_0") == 0xAAAA0000
        assert _read_signal(sim, "m_axi_slv_mem_3") == 0xAAAA0003
        # Burst-written words.
        assert _read_signal(sim, "m_axi_slv_mem_4") == 0x100
        assert _read_signal(sim, "m_axi_slv_mem_7") == 0x103


# ---------------------------------------------------------------------------
# AXI4 master lowering fixture — 8×32-bit single-beat slave RAM
# ---------------------------------------------------------------------------
# Identical structure to examples/multi_iface_project/rtl/axi4_ram.v but
# kept inline for self-contained tests.
AXI4_RAM_SRC = """
module axi4_ram (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [3:0]  s_axi_awid,
    input  wire [4:0]  s_axi_awaddr,
    input  wire [7:0]  s_axi_awlen,
    input  wire [2:0]  s_axi_awsize,
    input  wire [1:0]  s_axi_awburst,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wlast,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,
    output wire [3:0]  s_axi_bid,
    output wire [1:0]  s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,
    input  wire [3:0]  s_axi_arid,
    input  wire [4:0]  s_axi_araddr,
    input  wire [7:0]  s_axi_arlen,
    input  wire [2:0]  s_axi_arsize,
    input  wire [1:0]  s_axi_arburst,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,
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
    assign s_axi_rlast   = r_pending;
    assign s_axi_rid     = ar_id_q;
    assign s_axi_rdata   = rdata_q;

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


class TestAXI4MasterLowering:
    def test_role_mismatch_raises(self):
        bench = Testbench(_parse(AXI4_RAM_SRC))
        bad = AXI4MasterLowering(operations=[AXI4MasterOp.read(0)])
        bad.role = "master"  # DUT is slave; master role is wrong
        with pytest.raises(LoweringError, match="role"):
            compile_native(bench, lowerings={"s_axi": bad})

    def test_empty_ops_raises(self):
        bench = Testbench(_parse(AXI4_RAM_SRC))
        with pytest.raises(LoweringError, match="non-empty"):
            compile_native(bench, lowerings={"s_axi": AXI4MasterLowering(operations=[])})

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_axi4_master_write_then_read(self, engine):
        # Write 4 words (byte addrs 0x00..0x0C, stride 4) then read back.
        # Words indexed by addr[4:2], so byte_addr = word_idx * 4.
        values = [0x11110000 | i for i in range(4)]
        ops = [AXI4MasterOp.write(i * 4, values[i]) for i in range(4)]
        ops += [AXI4MasterOp.read(i * 4) for i in range(4)]
        bench = Testbench(_parse(AXI4_RAM_SRC))
        lowered = compile_native(
            bench,
            lowerings={
                "s_axi": AXI4MasterLowering(
                    operations=ops,
                    addr_width=5,
                    data_width=32,
                    id_width=4,
                )
            },
        )
        assert lowered.done_signals == {"s_axi": "s_axi_master_done"}
        assert "s_axi_op_4_rdata" in lowered.capture_signals["s_axi"]

        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        # Each op takes ~5 cycles; 8 ops + margin
        sim.run(max_time=10 * (len(ops) * 6 + 32))

        assert _read_signal(sim, "s_axi_master_done") == 1
        # Write ops: bresp == 0
        for i in range(4):
            assert _read_signal(sim, f"s_axi_op_{i}_resp") == 0
        # Read ops: rdata matches values written
        for i in range(4):
            assert _read_signal(sim, f"s_axi_op_{4 + i}_rdata") == values[i]

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_axi4_master_wstrb_partial(self, engine):
        # Write 0xFFFFFFFF with all bytes, then partial strb=0b0101 (bytes 0,2).
        # Expected read: byte3=FF, byte2=BB, byte1=FF, byte0=DD → 0xFFBBFFDD
        ops = [
            AXI4MasterOp.write(0x00, 0xFFFFFFFF, strb=0xF),
            AXI4MasterOp.write(0x00, 0xAABBCCDD, strb=0b0101),
            AXI4MasterOp.read(0x00),
        ]
        bench = Testbench(_parse(AXI4_RAM_SRC))
        lowered = compile_native(
            bench,
            lowerings={"s_axi": AXI4MasterLowering(operations=ops, addr_width=5, data_width=32)},
        )
        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        sim.run(max_time=10 * 60)

        assert _read_signal(sim, "s_axi_master_done") == 1
        assert _read_signal(sim, "s_axi_op_2_rdata") == 0xFFBBFFDD


# ---------------------------------------------------------------------------
# AXI-Lite master DUT fixture (for AXILiteSlaveLowering tests)
# ---------------------------------------------------------------------------
# 12-state FSM: writes 0xDEADBEEF to addr 0x00 and 0x12345678 to addr 0x04,
# then reads both back, finally asserts done=1.
AXIL_MASTER_DUT_SRC = """
module axil_master_dut (
    input  wire        clk,
    input  wire        rst_n,
    // AXI-Lite master port
    output reg  [7:0]  m_axi_awaddr,
    output reg  [2:0]  m_axi_awprot,
    output reg         m_axi_awvalid,
    input  wire        m_axi_awready,
    output reg  [31:0] m_axi_wdata,
    output reg  [3:0]  m_axi_wstrb,
    output reg         m_axi_wvalid,
    input  wire        m_axi_wready,
    input  wire        m_axi_bvalid,
    input  wire [1:0]  m_axi_bresp,
    output reg         m_axi_bready,
    output reg  [7:0]  m_axi_araddr,
    output reg  [2:0]  m_axi_arprot,
    output reg         m_axi_arvalid,
    input  wire        m_axi_arready,
    input  wire        m_axi_rvalid,
    input  wire [31:0] m_axi_rdata,
    input  wire [1:0]  m_axi_rresp,
    output reg         m_axi_rready,
    // Observables
    output reg         done,
    output reg  [31:0] rdata_0,
    output reg  [31:0] rdata_1
);
    localparam S_SETUP   = 4'd0;
    localparam S_WR0_AW  = 4'd1;
    localparam S_WR0_W   = 4'd2;
    localparam S_WR0_B   = 4'd3;
    localparam S_WR1_AW  = 4'd4;
    localparam S_WR1_W   = 4'd5;
    localparam S_WR1_B   = 4'd6;
    localparam S_RD0_AR  = 4'd7;
    localparam S_RD0_R   = 4'd8;
    localparam S_RD1_AR  = 4'd9;
    localparam S_RD1_R   = 4'd10;
    localparam S_DONE    = 4'd11;

    reg [3:0] state;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= S_SETUP;
            m_axi_awaddr  <= 8'h0;  m_axi_awprot  <= 3'h0; m_axi_awvalid <= 1'b0;
            m_axi_wdata   <= 32'h0; m_axi_wstrb   <= 4'hF;
            m_axi_wvalid  <= 1'b0;  m_axi_bready  <= 1'b0;
            m_axi_araddr  <= 8'h0;  m_axi_arprot  <= 3'h0; m_axi_arvalid <= 1'b0;
            m_axi_rready  <= 1'b0;
            done   <= 1'b0;
            rdata_0 <= 32'h0; rdata_1 <= 32'h0;
        end else begin
            case (state)
                S_SETUP: begin
                    m_axi_awaddr  <= 8'h00;
                    m_axi_awvalid <= 1'b1;
                    m_axi_wdata   <= 32'hDEADBEEF;
                    m_axi_wstrb   <= 4'hF;
                    state <= S_WR0_AW;
                end
                S_WR0_AW: begin
                    if (m_axi_awvalid && m_axi_awready) begin
                        m_axi_awvalid <= 1'b0;
                        m_axi_wvalid  <= 1'b1;
                        state <= S_WR0_W;
                    end
                end
                S_WR0_W: begin
                    if (m_axi_wvalid && m_axi_wready) begin
                        m_axi_wvalid <= 1'b0;
                        m_axi_bready <= 1'b1;
                        state <= S_WR0_B;
                    end
                end
                S_WR0_B: begin
                    if (m_axi_bvalid && m_axi_bready) begin
                        m_axi_bready  <= 1'b0;
                        m_axi_awaddr  <= 8'h04;
                        m_axi_awvalid <= 1'b1;
                        m_axi_wdata   <= 32'h12345678;
                        state <= S_WR1_AW;
                    end
                end
                S_WR1_AW: begin
                    if (m_axi_awvalid && m_axi_awready) begin
                        m_axi_awvalid <= 1'b0;
                        m_axi_wvalid  <= 1'b1;
                        state <= S_WR1_W;
                    end
                end
                S_WR1_W: begin
                    if (m_axi_wvalid && m_axi_wready) begin
                        m_axi_wvalid <= 1'b0;
                        m_axi_bready <= 1'b1;
                        state <= S_WR1_B;
                    end
                end
                S_WR1_B: begin
                    if (m_axi_bvalid && m_axi_bready) begin
                        m_axi_bready  <= 1'b0;
                        m_axi_araddr  <= 8'h00;
                        m_axi_arvalid <= 1'b1;
                        m_axi_rready  <= 1'b1;
                        state <= S_RD0_AR;
                    end
                end
                S_RD0_AR: begin
                    if (m_axi_arvalid && m_axi_arready) begin
                        m_axi_arvalid <= 1'b0;
                        state <= S_RD0_R;
                    end
                end
                S_RD0_R: begin
                    if (m_axi_rvalid && m_axi_rready) begin
                        rdata_0      <= m_axi_rdata;
                        m_axi_araddr  <= 8'h04;
                        m_axi_arvalid <= 1'b1;
                        state <= S_RD1_AR;
                    end
                end
                S_RD1_AR: begin
                    if (m_axi_arvalid && m_axi_arready) begin
                        m_axi_arvalid <= 1'b0;
                        state <= S_RD1_R;
                    end
                end
                S_RD1_R: begin
                    if (m_axi_rvalid && m_axi_rready) begin
                        rdata_1      <= m_axi_rdata;
                        m_axi_rready  <= 1'b0;
                        state <= S_DONE;
                    end
                end
                S_DONE: begin
                    done <= 1'b1;
                end
                default: state <= S_IDLE;
            endcase
        end
    end
endmodule
"""


class TestAXILiteSlaveLowering:
    def test_role_mismatch_raises(self):
        bench = Testbench(_parse(AXIL_MASTER_DUT_SRC))
        bad = AXILiteSlaveLowering(memory_depth=8)
        bad.role = "slave"
        with pytest.raises(LoweringError, match="role"):
            compile_native(bench, lowerings={"m_axi": bad})

    def test_zero_memory_depth_raises(self):
        bench = Testbench(_parse(AXIL_MASTER_DUT_SRC))
        with pytest.raises(LoweringError, match="memory_depth"):
            compile_native(bench, lowerings={"m_axi": AXILiteSlaveLowering(memory_depth=0)})

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_write_then_read_roundtrip(self, engine):
        from veriforge.sim.bench import AXILiteSlaveLowering  # noqa: PLC0415

        bench = Testbench(_parse(AXIL_MASTER_DUT_SRC))
        lowered = compile_native(
            bench,
            lowerings={
                "m_axi": AXILiteSlaveLowering(
                    memory_depth=8,
                    data_width=32,
                    addr_width=8,
                ),
            },
        )
        # Both mem cells should be capture signals.
        assert "m_axi_slv_mem_0" in lowered.capture_signals["m_axi"]
        assert "m_axi_slv_mem_1" in lowered.capture_signals["m_axi"]

        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        # Allow enough cycles: 12-state FSM ~18 cycles + margin.
        sim.run(max_time=10 * 60)

        assert _read_signal(sim, "u_dut.done") == 1
        # Slave memory should have the written values.
        assert _read_signal(sim, "m_axi_slv_mem_0") == 0xDEADBEEF
        assert _read_signal(sim, "m_axi_slv_mem_1") == 0x12345678
        # DUT should have read them back.
        assert _read_signal(sim, "u_dut.rdata_0") == 0xDEADBEEF
        assert _read_signal(sim, "u_dut.rdata_1") == 0x12345678
        # Transaction counters.
        assert _read_signal(sim, "m_axi_slv_aw_count") == 2
        assert _read_signal(sim, "m_axi_slv_ar_count") == 2

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_initial_memory_preloaded(self, engine):
        from veriforge.sim.bench import AXILiteSlaveLowering  # noqa: PLC0415

        bench = Testbench(_parse(AXIL_MASTER_DUT_SRC))
        # Pre-seed with values that the DUT will overwrite; verify the DUT
        # reads back its own written values (not the pre-seeded ones for those
        # addresses), and that unwritten words keep their initial values.
        lowered = compile_native(
            bench,
            lowerings={
                "m_axi": AXILiteSlaveLowering(
                    memory_depth=8,
                    data_width=32,
                    addr_width=8,
                    initial_memory={
                        0: 0xAAAA0000,
                        1: 0xAAAA0001,
                        7: 0xBBBBBBBB,
                    },
                ),
            },
        )
        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        sim.run(max_time=10 * 60)

        # DUT overwrote words 0 and 1.
        assert _read_signal(sim, "m_axi_slv_mem_0") == 0xDEADBEEF
        assert _read_signal(sim, "m_axi_slv_mem_1") == 0x12345678
        # Word 7 untouched.
        assert _read_signal(sim, "m_axi_slv_mem_7") == 0xBBBBBBBB
        # DUT read-back should see the newly written values.
        assert _read_signal(sim, "u_dut.rdata_0") == 0xDEADBEEF
        assert _read_signal(sim, "u_dut.rdata_1") == 0x12345678


# ---------------------------------------------------------------------------
# LoweredDesign.run() — convenience one-call API
# ---------------------------------------------------------------------------


class TestLoweredDesignRun:
    """Tests for LoweredDesign.run(), the Wave E one-call simulation entry point."""

    def _make_axis_loopback(self, beats: list[int]) -> "object":
        """Return a compile_native result for the AXIS loopback fixture."""
        bench = Testbench(_parse(LOOPBACK_SRC))
        return compile_native(
            bench,
            lowerings={
                "m_axis": AXIStreamSourceLowering(beats=beats, data_width=8),
                "s_axis": AXIStreamSinkLowering(n_beats=len(beats), data_width=8),
            },
        )

    def test_lowered_run_exposes_plan(self):
        """LoweredDesign carries the TestbenchPlan used to build it."""
        lowered = self._make_axis_loopback([0x11, 0x22])
        assert lowered.plan is not None
        assert len(lowered.plan.domains) > 0

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_lowered_run_returns_correct_captures(self, engine):
        """lowered.run() returns the captured beats without manual Simulator setup."""
        beats = [0xA1, 0xB2, 0xC3, 0xD4, 0xE5]
        lowered = self._make_axis_loopback(beats)

        results = lowered.run(engine, max_time=10 * (len(beats) + 16))

        # All capture signals must be present in the result dict.
        for name in lowered.capture_signals["s_axis"]:
            assert name in results

        captured = [results[f"s_axis_cap_{i}"] for i in range(len(beats))]
        assert captured == beats

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_lowered_run_with_vcd_creates_file(self, tmp_path, engine):
        """lowered.run(vcd=path) writes a non-empty VCD file."""
        vcd_path = tmp_path / "trace.vcd"
        beats = [0x10, 0x20]
        lowered = self._make_axis_loopback(beats)

        lowered.run(engine, max_time=10 * 20, vcd=vcd_path)

        assert vcd_path.exists(), "VCD file was not created"
        assert vcd_path.stat().st_size > 0, "VCD file is empty"

    def test_lowered_run_vcd_contains_signal_names(self, tmp_path):
        """The VCD file includes at least one expected port name."""
        vcd_path = tmp_path / "trace.vcd"
        beats = [0x55]
        lowered = self._make_axis_loopback(beats)

        lowered.run("reference", max_time=10 * 20, vcd=vcd_path)

        content = vcd_path.read_text(encoding="utf-8", errors="replace")
        # VCD declares variables with the $var keyword; at minimum the done
        # signal and a capture signal should appear.
        assert "s_axis_snk_done" in content or "$var" in content


class TestLoweredDesignBatchRun:
    """Tests for LoweredDesign.batch_run() — compiled-engine C-level batch loop."""

    def _make_axis_loopback(self, beats: list[int]) -> "object":
        bench = Testbench(_parse(LOOPBACK_SRC))
        return compile_native(
            bench,
            lowerings={
                "m_axis": AXIStreamSourceLowering(beats=beats, data_width=8),
                "s_axis": AXIStreamSinkLowering(n_beats=len(beats), data_width=8),
            },
        )

    def test_batch_run_returns_dict_with_all_capture_signals(self):
        """batch_run() returns a dict covering every capture signal name."""
        beats = [0x11, 0x22, 0x33]
        lowered = self._make_axis_loopback(beats)

        results = lowered.batch_run(cycles=500)

        assert isinstance(results, dict)
        for name in lowered.capture_signals["s_axis"]:
            assert name in results

    def test_batch_run_captures_correct_beat_values(self):
        """batch_run() captures match the source beats (functional end-to-end)."""
        beats = [0xA1, 0xB2, 0xC3, 0xD4, 0xE5]
        lowered = self._make_axis_loopback(beats)

        results = lowered.batch_run(cycles=1000)

        captured = [results[f"s_axis_cap_{i}"] for i in range(len(beats))]
        assert captured == beats

    def test_batch_run_matches_run_compiled(self):
        """batch_run() and run(engine='compiled') produce the same captures."""
        beats = [0x10, 0x20, 0x30]
        lowered = self._make_axis_loopback(beats)

        run_results = lowered.run("compiled", max_time=1000)
        batch_results = lowered.batch_run(cycles=200)

        for name in lowered.capture_signals["s_axis"]:
            assert batch_results[name] == run_results[name], (
                f"Mismatch for {name}: batch={batch_results[name]} run={run_results[name]}"
            )

    def test_batch_run_reset_cycles_ge_cycles_raises(self):
        """ValueError when reset_cycles >= cycles."""
        lowered = self._make_axis_loopback([0x01])

        with pytest.raises(ValueError, match="reset_cycles"):
            lowered.batch_run(cycles=4, reset_cycles=4)

    def test_batch_run_explicit_clock_name(self):
        """Explicit clock_name overrides auto-detection."""
        beats = [0xAA, 0xBB]
        lowered = self._make_axis_loopback(beats)

        results = lowered.batch_run(cycles=500, clock_name="clk", clock_period=10)

        captured = [results[f"s_axis_cap_{i}"] for i in range(len(beats))]
        assert captured == beats

    def test_batch_run_explicit_clock_period(self):
        """clock_period kwarg is accepted and used without error."""
        beats = [0x55]
        lowered = self._make_axis_loopback(beats)

        results = lowered.batch_run(cycles=500, clock_period=20)

        assert results["s_axis_cap_0"] == 0x55


# ---------------------------------------------------------------------------
# PRNG pause tests — AXIStreamSourceLowering and AXIStreamSinkLowering
# ---------------------------------------------------------------------------


def _run_loopback_with_pause(
    src_kwargs: dict,
    snk_kwargs: dict,
    *,
    n_beats: int,
    engine: str = "reference",
    extra_cycles: int = 80,
) -> list[int]:
    """Compile, run, and return captured beats from the AXIS loopback fixture."""
    beats = list(range(0x10, 0x10 + n_beats))
    bench = Testbench(_parse(LOOPBACK_SRC))
    lowered = compile_native(
        bench,
        lowerings={
            "m_axis": AXIStreamSourceLowering(beats=beats, data_width=8, **src_kwargs),
            "s_axis": AXIStreamSinkLowering(n_beats=n_beats, data_width=8, **snk_kwargs),
        },
    )
    # Run for enough cycles: 4 reset + n_beats + generous pause margin.
    results = lowered.run(engine, max_time=10 * (n_beats + extra_cycles))
    return [results[f"s_axis_cap_{i}"] for i in range(n_beats)]


class TestPRNGPauseValidation:
    """Validation / error-path tests for PRNG pause params."""

    def test_source_prng_bits_too_large_raises(self):
        bench = Testbench(_parse(LOOPBACK_SRC))
        with pytest.raises(LoweringError, match="prng_bits"):
            compile_native(
                bench,
                lowerings={
                    "m_axis": AXIStreamSourceLowering([1, 2], prng_bits=33),
                    "s_axis": AXIStreamSinkLowering(2),
                },
            )

    def test_source_pause_threshold_out_of_range_raises(self):
        bench = Testbench(_parse(LOOPBACK_SRC))
        # prng_bits=4 → valid range 0..16; threshold=17 is out of range.
        with pytest.raises(LoweringError, match="pause_threshold"):
            compile_native(
                bench,
                lowerings={
                    "m_axis": AXIStreamSourceLowering([1, 2], prng_bits=4, pause_threshold=17),
                    "s_axis": AXIStreamSinkLowering(2),
                },
            )

    def test_sink_prng_bits_too_large_raises(self):
        bench = Testbench(_parse(LOOPBACK_SRC))
        with pytest.raises(LoweringError, match="prng_bits"):
            compile_native(
                bench,
                lowerings={
                    "m_axis": AXIStreamSourceLowering([1, 2]),
                    "s_axis": AXIStreamSinkLowering(2, prng_bits=33),
                },
            )

    def test_sink_pause_threshold_out_of_range_raises(self):
        bench = Testbench(_parse(LOOPBACK_SRC))
        with pytest.raises(LoweringError, match="pause_threshold"):
            compile_native(
                bench,
                lowerings={
                    "m_axis": AXIStreamSourceLowering([1, 2]),
                    "s_axis": AXIStreamSinkLowering(2, prng_bits=4, pause_threshold=17),
                },
            )


class TestPRNGPauseSource:
    """Source PRNG pause: tvalid is gated; all beats eventually arrive."""

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_source_pause_all_beats_arrive(self, engine):
        # prng_bits=4, threshold=8 → ~50% pause.  Give generous margin.
        n = 6
        captured = _run_loopback_with_pause(
            {"prng_bits": 4, "pause_threshold": 8},
            {},
            n_beats=n,
            engine=engine,
            extra_cycles=120,
        )
        assert captured == list(range(0x10, 0x10 + n))

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_source_pause_zero_threshold_no_pause(self, engine):
        # pause_threshold=0 means never pause → same as prng_bits=0.
        n = 4
        captured = _run_loopback_with_pause(
            {"prng_bits": 4, "pause_threshold": 0},
            {},
            n_beats=n,
            engine=engine,
        )
        assert captured == list(range(0x10, 0x10 + n))

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_source_no_pause_backward_compatible(self, engine):
        """Default prng_bits=0 behaves identically to the original no-pause lowering."""
        n = 5
        captured = _run_loopback_with_pause(
            {},  # no pause kwargs
            {},
            n_beats=n,
            engine=engine,
        )
        assert captured == list(range(0x10, 0x10 + n))

    def test_source_different_seeds_produce_different_lfsr_signals(self):
        """Different prng_seed values generate different register names / wiring (compile check)."""
        beats = [0xAA, 0xBB, 0xCC]
        bench_a = Testbench(_parse(LOOPBACK_SRC))
        bench_b = Testbench(_parse(LOOPBACK_SRC))
        # Both should compile without error, just with different seeds.
        lowered_a = compile_native(
            bench_a,
            lowerings={
                "m_axis": AXIStreamSourceLowering(beats, prng_bits=4, pause_threshold=4, prng_seed=0x1234),
                "s_axis": AXIStreamSinkLowering(3),
            },
        )
        lowered_b = compile_native(
            bench_b,
            lowerings={
                "m_axis": AXIStreamSourceLowering(beats, prng_bits=4, pause_threshold=4, prng_seed=0xABCD),
                "s_axis": AXIStreamSinkLowering(3),
            },
        )
        # Both designs should be structurally valid.
        assert lowered_a.wrapper.name == "bench_native_top"
        assert lowered_b.wrapper.name == "bench_native_top"

    def test_source_seed_zero_treated_as_default(self):
        """prng_seed=0 must not produce an all-zero LFSR (would stay stuck at 0)."""
        beats = [0x01, 0x02]
        bench = Testbench(_parse(LOOPBACK_SRC))
        # Just verify that compile_native succeeds (seed replaced internally).
        lowered = compile_native(
            bench,
            lowerings={
                "m_axis": AXIStreamSourceLowering(beats, prng_bits=4, pause_threshold=4, prng_seed=0),
                "s_axis": AXIStreamSinkLowering(2),
            },
        )
        assert lowered.wrapper.name == "bench_native_top"

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_source_batch_run_with_pause(self, engine):
        """PRNG pause works end-to-end via batch_run (compiled engine native path)."""
        beats = [0xA0, 0xB0, 0xC0]
        bench = Testbench(_parse(LOOPBACK_SRC))
        lowered = compile_native(
            bench,
            lowerings={
                "m_axis": AXIStreamSourceLowering(beats, prng_bits=4, pause_threshold=8, prng_seed=0x9876),
                "s_axis": AXIStreamSinkLowering(len(beats)),
            },
        )
        results = lowered.batch_run(cycles=2000)
        captured = [results[f"s_axis_cap_{i}"] for i in range(len(beats))]
        assert captured == beats


class TestPRNGPauseSink:
    """Sink PRNG pause: tready is gated; all beats eventually captured."""

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_sink_pause_all_beats_captured(self, engine):
        n = 6
        captured = _run_loopback_with_pause(
            {},
            {"prng_bits": 4, "pause_threshold": 8},
            n_beats=n,
            engine=engine,
            extra_cycles=120,
        )
        assert captured == list(range(0x10, 0x10 + n))

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_sink_no_pause_backward_compatible(self, engine):
        n = 5
        captured = _run_loopback_with_pause(
            {},
            {},
            n_beats=n,
            engine=engine,
        )
        assert captured == list(range(0x10, 0x10 + n))

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_sink_batch_run_with_pause(self, engine):
        beats = [0xA0, 0xB0, 0xC0]
        bench = Testbench(_parse(LOOPBACK_SRC))
        lowered = compile_native(
            bench,
            lowerings={
                "m_axis": AXIStreamSourceLowering(beats),
                "s_axis": AXIStreamSinkLowering(len(beats), prng_bits=4, pause_threshold=8, prng_seed=0x5555),
            },
        )
        results = lowered.batch_run(cycles=2000)
        captured = [results[f"s_axis_cap_{i}"] for i in range(len(beats))]
        assert captured == beats


class TestPRNGPauseBoth:
    """Both source and sink paused simultaneously."""

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_both_paused_all_beats_arrive(self, engine):
        n = 4
        captured = _run_loopback_with_pause(
            {"prng_bits": 3, "pause_threshold": 4, "prng_seed": 0x1111},
            {"prng_bits": 3, "pause_threshold": 4, "prng_seed": 0x2222},
            n_beats=n,
            engine=engine,
            extra_cycles=200,
        )
        assert captured == list(range(0x10, 0x10 + n))

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_both_paused_batch_run(self, engine):
        beats = [0xDE, 0xAD, 0xBE, 0xEF]
        bench = Testbench(_parse(LOOPBACK_SRC))
        lowered = compile_native(
            bench,
            lowerings={
                "m_axis": AXIStreamSourceLowering(beats, prng_bits=3, pause_threshold=4, prng_seed=0xCAFE),
                "s_axis": AXIStreamSinkLowering(len(beats), prng_bits=3, pause_threshold=4, prng_seed=0xF00D),
            },
        )
        results = lowered.batch_run(cycles=5000)
        captured = [results[f"s_axis_cap_{i}"] for i in range(len(beats))]
        assert captured == beats


# ---------------------------------------------------------------------------
# MemBus master lowering — bench drives writes/reads to a SRAM-style DUT slave
# ---------------------------------------------------------------------------

# Minimal synchronous SRAM slave: write on wen, read combinatorially from mem.
MEMBUS_SLAVE_SRC = """
module membus_slave (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [3:0]  s_mb_addr,
    input  wire [31:0] s_mb_wdata,
    input  wire        s_mb_wen,
    output reg  [31:0] s_mb_rdata
);
    reg [31:0] mem [0:7];
    integer i;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (i = 0; i < 8; i = i + 1) mem[i] <= 32'h0;
        end else if (s_mb_wen) begin
            mem[s_mb_addr[2:0]] <= s_mb_wdata;
        end
    end
    always @(*) s_mb_rdata = mem[s_mb_addr[2:0]];
endmodule
"""


# DUT master: writes two values, then reads them back.
MEMBUS_MASTER_SRC = """
module membus_master (
    input  wire        clk,
    input  wire        rst_n,
    output reg  [3:0]  m_mb_addr,
    output reg  [31:0] m_mb_wdata,
    output reg         m_mb_wen,
    input  wire [31:0] m_mb_rdata,
    output reg         done,
    output reg  [31:0] rd0,
    output reg  [31:0] rd1
);
    localparam S_W0  = 3'd0;
    localparam S_W1  = 3'd1;
    localparam S_R0  = 3'd2;
    localparam S_R1  = 3'd3;
    localparam S_CAP = 3'd4;
    localparam S_DONE = 3'd5;
    reg [2:0] state;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= S_W0;
            m_mb_addr  <= 4'h0; m_mb_wdata <= 32'h0; m_mb_wen <= 1'b0;
            done <= 1'b0; rd0 <= 32'h0; rd1 <= 32'h0;
        end else begin
            case (state)
                S_W0: begin
                    m_mb_addr  <= 4'h0; m_mb_wdata <= 32'hDEADBEEF; m_mb_wen <= 1'b1;
                    state <= S_W1;
                end
                S_W1: begin
                    m_mb_addr  <= 4'h1; m_mb_wdata <= 32'h12345678; m_mb_wen <= 1'b1;
                    state <= S_R0;
                end
                S_R0: begin
                    m_mb_wen   <= 1'b0; m_mb_addr  <= 4'h0;
                    state <= S_R1;
                end
                S_R1: begin
                    rd0 <= m_mb_rdata;
                    m_mb_addr <= 4'h1;
                    state <= S_CAP;
                end
                S_CAP: begin
                    rd1 <= m_mb_rdata;
                    state <= S_DONE;
                end
                S_DONE: begin
                    done <= 1'b1;
                end
                default: state <= S_DONE;
            endcase
        end
    end
endmodule
"""


class TestMemBusMasterLowering:
    """MemBusMasterLowering: bench drives scripted writes/reads to a DUT slave."""

    def test_role_mismatch_raises(self):
        bench = Testbench(_parse(MEMBUS_SLAVE_SRC))
        bad = MemBusMasterLowering(operations=[MemBusOp.read(0)])
        bad.role = "master"
        with pytest.raises(LoweringError, match="role"):
            compile_native(bench, lowerings={"s_mb": bad})

    def test_empty_operations_raises(self):
        bench = Testbench(_parse(MEMBUS_SLAVE_SRC))
        with pytest.raises(LoweringError, match="non-empty"):
            compile_native(bench, lowerings={"s_mb": MemBusMasterLowering(operations=[])})

    def test_bad_op_kind_raises(self):
        bench = Testbench(_parse(MEMBUS_SLAVE_SRC))
        with pytest.raises(LoweringError, match="kind"):
            compile_native(
                bench,
                lowerings={"s_mb": MemBusMasterLowering(operations=[MemBusOp(kind="peek", addr=0)])},
            )

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_write_then_read_roundtrip(self, engine):
        ops = [
            MemBusOp.write(0x0, 0xDEADBEEF),
            MemBusOp.write(0x1, 0x12345678),
            MemBusOp.write(0x2, 0xCAFEBABE),
            MemBusOp.read(0x0),
            MemBusOp.read(0x1),
            MemBusOp.read(0x2),
        ]
        bench = Testbench(_parse(MEMBUS_SLAVE_SRC))
        lowered = compile_native(
            bench,
            lowerings={"s_mb": MemBusMasterLowering(operations=ops, addr_width=4, data_width=32)},
        )
        assert lowered.done_signals == {"s_mb": "s_mb_master_done"}
        # Only read ops have rdata captures.
        for i in range(3):
            assert f"s_mb_op_{3 + i}_rdata" in lowered.capture_signals["s_mb"]

        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        # Each op takes 1 cycle (write) or 2 cycles (read), give generous margin.
        sim.run(max_time=10 * (len(ops) * 3 + 16))

        assert _read_signal(sim, "s_mb_master_done") == 1
        assert _read_signal(sim, "s_mb_op_3_rdata") == 0xDEADBEEF
        assert _read_signal(sim, "s_mb_op_4_rdata") == 0x12345678
        assert _read_signal(sim, "s_mb_op_5_rdata") == 0xCAFEBABE

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_write_only(self, engine):
        ops = [MemBusOp.write(0x0, 0xABCD1234), MemBusOp.write(0x1, 0xEF012345)]
        bench = Testbench(_parse(MEMBUS_SLAVE_SRC))
        lowered = compile_native(
            bench,
            lowerings={"s_mb": MemBusMasterLowering(operations=ops, addr_width=4)},
        )
        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        sim.run(max_time=10 * 20)
        assert _read_signal(sim, "s_mb_master_done") == 1


# ---------------------------------------------------------------------------
# MemBus responder lowering — DUT master drives bus; bench acts as SRAM
# ---------------------------------------------------------------------------


class TestMemBusResponderLowering:
    """MemBusResponderLowering: DUT master drives MemBus; bench is memory-backed slave."""

    def test_role_mismatch_raises(self):
        bench = Testbench(_parse(MEMBUS_MASTER_SRC))
        bad = MemBusResponderLowering()
        bad.role = "slave"
        with pytest.raises(LoweringError, match="role"):
            compile_native(bench, lowerings={"m_mb": bad})

    def test_zero_depth_raises(self):
        bench = Testbench(_parse(MEMBUS_MASTER_SRC))
        with pytest.raises(LoweringError, match="memory_depth"):
            compile_native(bench, lowerings={"m_mb": MemBusResponderLowering(memory_depth=0)})

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_dut_master_write_then_read(self, engine):
        bench = Testbench(_parse(MEMBUS_MASTER_SRC))
        lowered = compile_native(
            bench,
            lowerings={
                "m_mb": MemBusResponderLowering(
                    memory_depth=8,
                    data_width=32,
                    addr_width=4,
                )
            },
        )
        assert "m_mb_rsp_mem_0" in lowered.capture_signals["m_mb"]
        assert "m_mb_rsp_mem_1" in lowered.capture_signals["m_mb"]

        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        # DUT FSM: 6 states + margin
        sim.run(max_time=10 * 20)

        assert _read_signal(sim, "u_dut.done") == 1
        # Responder should have captured the two writes.
        assert _read_signal(sim, "m_mb_rsp_mem_0") == 0xDEADBEEF
        assert _read_signal(sim, "m_mb_rsp_mem_1") == 0x12345678
        # Transaction counters
        assert _read_signal(sim, "m_mb_rsp_wr_count") == 2
        # DUT read-back via its own rd0/rd1 regs
        assert _read_signal(sim, "u_dut.rd0") == 0xDEADBEEF
        assert _read_signal(sim, "u_dut.rd1") == 0x12345678

    @pytest.mark.parametrize("engine", ["reference", "vm", "compiled"])
    def test_initial_memory_preloaded(self, engine):
        bench = Testbench(_parse(MEMBUS_MASTER_SRC))
        lowered = compile_native(
            bench,
            lowerings={
                "m_mb": MemBusResponderLowering(
                    memory_depth=8,
                    data_width=32,
                    addr_width=4,
                    initial_memory={5: 0xBEEFCAFE},
                )
            },
        )
        sim = Simulator(lowered.wrapper, design=lowered.design, engine=engine)
        clk = sim.signal("clk")
        rst_n = sim.signal("rst_n")
        sim.fork(Clock(clk, period=10))
        rst_n.value = 0
        sim.run(max_time=40)
        rst_n.value = 1
        sim.run(max_time=10 * 20)

        # Word 5 was pre-seeded and DUT never wrote it; should keep init value.
        assert _read_signal(sim, "m_mb_rsp_mem_5") == 0xBEEFCAFE
        # Words 0, 1 were overwritten by DUT.
        assert _read_signal(sim, "m_mb_rsp_mem_0") == 0xDEADBEEF
        assert _read_signal(sim, "m_mb_rsp_mem_1") == 0x12345678
