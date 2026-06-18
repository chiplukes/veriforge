// axi_lite_regs_flat: a flat-port AXI-Lite register file equivalent to the
// pulp `axi_lite_regs` cell. The pulp original takes parametric struct
// typedefs (`req_lite_t` / `resp_lite_t`) which the verilog-tools parser
// and reference simulator do not currently expand. This file exposes the
// same protocol via flat ports so the auto-detect pipeline + bench
// framework can drive it end-to-end.
//
// * 4 x 32-bit registers, word-aligned (addresses 0x0, 0x4, 0x8, 0xC)
// * Single outstanding transaction per channel (no AXI-Lite reordering)
// * RESP is always OKAY (2'b00)
// * WSTRB is honored (per-byte enables)
//
// All ports use the canonical AXI-Lite naming `<prefix>_<suffix>` so the
// flat detector picks the bundle up as `prefix='s_axi'`.
module axi_lite_regs_flat #(
    parameter int unsigned ADDR_WIDTH = 4,
    parameter int unsigned DATA_WIDTH = 32,
    parameter int unsigned NUM_REGS   = 4
) (
    input  logic                    clk_i,
    input  logic                    rst_ni,

    // Write address channel
    input  logic [ADDR_WIDTH-1:0]   s_axi_awaddr,
    input  logic [2:0]              s_axi_awprot,
    input  logic                    s_axi_awvalid,
    output logic                    s_axi_awready,

    // Write data channel
    input  logic [DATA_WIDTH-1:0]   s_axi_wdata,
    input  logic [DATA_WIDTH/8-1:0] s_axi_wstrb,
    input  logic                    s_axi_wvalid,
    output logic                    s_axi_wready,

    // Write response channel
    output logic [1:0]              s_axi_bresp,
    output logic                    s_axi_bvalid,
    input  logic                    s_axi_bready,

    // Read address channel
    input  logic [ADDR_WIDTH-1:0]   s_axi_araddr,
    input  logic [2:0]              s_axi_arprot,
    input  logic                    s_axi_arvalid,
    output logic                    s_axi_arready,

    // Read data channel
    output logic [DATA_WIDTH-1:0]   s_axi_rdata,
    output logic [1:0]              s_axi_rresp,
    output logic                    s_axi_rvalid,
    input  logic                    s_axi_rready
);

    logic [DATA_WIDTH-1:0] regs_q [NUM_REGS];

    // Latched address/data for the current write transaction.
    logic [ADDR_WIDTH-1:0]   aw_addr_q;
    logic [DATA_WIDTH-1:0]   w_data_q;
    logic [DATA_WIDTH/8-1:0] w_strb_q;
    logic                    aw_seen_q;
    logic                    w_seen_q;
    logic                    b_pending_q;

    logic [ADDR_WIDTH-1:0]   ar_addr_q;
    logic                    r_pending_q;

    // ── handshake combinationals ────────────────────────────────────────
    assign s_axi_awready = !aw_seen_q && !b_pending_q;
    assign s_axi_wready  = !w_seen_q  && !b_pending_q;
    assign s_axi_bvalid  = b_pending_q;
    assign s_axi_bresp   = 2'b00;

    assign s_axi_arready = !r_pending_q;
    assign s_axi_rvalid  = r_pending_q;
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rdata   = regs_q[ar_addr_q[ADDR_WIDTH-1:2]];

    // ── sequential state ────────────────────────────────────────────────
    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            aw_addr_q   <= '0;
            w_data_q    <= '0;
            w_strb_q    <= '0;
            aw_seen_q   <= 1'b0;
            w_seen_q    <= 1'b0;
            b_pending_q <= 1'b0;
            ar_addr_q   <= '0;
            r_pending_q <= 1'b0;
            for (int i = 0; i < NUM_REGS; i++) begin
                regs_q[i] <= '0;
            end
        end else begin
            // Capture write address.
            if (s_axi_awvalid && s_axi_awready) begin
                aw_addr_q <= s_axi_awaddr;
                aw_seen_q <= 1'b1;
            end
            // Capture write data.
            if (s_axi_wvalid && s_axi_wready) begin
                w_data_q <= s_axi_wdata;
                w_strb_q <= s_axi_wstrb;
                w_seen_q <= 1'b1;
            end
            // Once both halves are latched, perform write and raise BVALID.
            if (!b_pending_q && aw_seen_q && w_seen_q) begin
                for (int b = 0; b < DATA_WIDTH/8; b++) begin
                    if (w_strb_q[b]) begin
                        regs_q[aw_addr_q[ADDR_WIDTH-1:2]][8*b +: 8] <= w_data_q[8*b +: 8];
                    end
                end
                aw_seen_q   <= 1'b0;
                w_seen_q    <= 1'b0;
                b_pending_q <= 1'b1;
            end
            // BREADY accepts the response.
            if (b_pending_q && s_axi_bready) begin
                b_pending_q <= 1'b0;
            end

            // Read channel.
            if (s_axi_arvalid && s_axi_arready) begin
                ar_addr_q   <= s_axi_araddr;
                r_pending_q <= 1'b1;
            end
            if (r_pending_q && s_axi_rready) begin
                r_pending_q <= 1'b0;
            end
        end
    end

endmodule
