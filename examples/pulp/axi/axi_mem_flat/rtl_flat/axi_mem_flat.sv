// axi_mem_flat: a flat-port AXI4 (full) memory equivalent to the kind of
// pulp `axi_mem` / `axi_to_mem` cell used as a simple slave target. The
// pulp originals take parametric struct typedefs (`axi_req_t`/`axi_resp_t`)
// that the verilog-tools parser and reference simulator do not expand.
// This file exposes the same protocol via flat ports so the auto-detect
// pipeline + bench framework can drive AXI4 bursts end-to-end.
//
// * 16 x 32-bit words, word-aligned (addresses 0x00..0x3C)
// * INCR bursts only (FIXED/WRAP not implemented)
// * Single outstanding transaction per channel (no AXI reordering)
// * AWSIZE/ARSIZE expected to equal the data width (no narrow bursts)
// * RESP is always OKAY (2'b00); WSTRB is honored on writes
//
// All ports use the canonical AXI4 naming `<prefix>_<suffix>` so the
// flat detector picks the bundle up as `prefix='s_axi'`.
module axi_mem_flat #(
    parameter int unsigned ADDR_WIDTH = 6,
    parameter int unsigned DATA_WIDTH = 32,
    parameter int unsigned ID_WIDTH   = 4,
    parameter int unsigned NUM_WORDS  = 16
) (
    input  logic                      clk_i,
    input  logic                      rst_ni,

    // Write address channel
    input  logic [ID_WIDTH-1:0]       s_axi_awid,
    input  logic [ADDR_WIDTH-1:0]     s_axi_awaddr,
    input  logic [7:0]                s_axi_awlen,
    input  logic [2:0]                s_axi_awsize,
    input  logic [1:0]                s_axi_awburst,
    input  logic                      s_axi_awvalid,
    output logic                      s_axi_awready,

    // Write data channel
    input  logic [DATA_WIDTH-1:0]     s_axi_wdata,
    input  logic [DATA_WIDTH/8-1:0]   s_axi_wstrb,
    input  logic                      s_axi_wlast,
    input  logic                      s_axi_wvalid,
    output logic                      s_axi_wready,

    // Write response channel
    output logic [ID_WIDTH-1:0]       s_axi_bid,
    output logic [1:0]                s_axi_bresp,
    output logic                      s_axi_bvalid,
    input  logic                      s_axi_bready,

    // Read address channel
    input  logic [ID_WIDTH-1:0]       s_axi_arid,
    input  logic [ADDR_WIDTH-1:0]     s_axi_araddr,
    input  logic [7:0]                s_axi_arlen,
    input  logic [2:0]                s_axi_arsize,
    input  logic [1:0]                s_axi_arburst,
    input  logic                      s_axi_arvalid,
    output logic                      s_axi_arready,

    // Read data channel
    output logic [ID_WIDTH-1:0]       s_axi_rid,
    output logic [DATA_WIDTH-1:0]     s_axi_rdata,
    output logic [1:0]                s_axi_rresp,
    output logic                      s_axi_rlast,
    output logic                      s_axi_rvalid,
    input  logic                      s_axi_rready
);

    localparam int unsigned BYTES_PER_WORD = DATA_WIDTH / 8;
    localparam int unsigned WORD_INDEX_LSB = $clog2(BYTES_PER_WORD);

    logic [DATA_WIDTH-1:0] mem_q [NUM_WORDS];

    // ── Write side state ───────────────────────────────────────────────
    logic [ID_WIDTH-1:0]   aw_id_q;
    logic [ADDR_WIDTH-1:0] aw_addr_q;
    logic [7:0]            aw_len_q;
    logic [7:0]            aw_beat_q;
    logic                  aw_active_q;

    logic                  b_pending_q;
    logic [ID_WIDTH-1:0]   b_id_q;

    assign s_axi_awready = !aw_active_q && !b_pending_q;
    assign s_axi_wready  = aw_active_q && !b_pending_q;
    assign s_axi_bvalid  = b_pending_q;
    assign s_axi_bresp   = 2'b00;
    assign s_axi_bid     = b_id_q;

    // ── Read side state ────────────────────────────────────────────────
    logic [ID_WIDTH-1:0]   ar_id_q;
    logic [ADDR_WIDTH-1:0] ar_addr_q;
    logic [7:0]            ar_len_q;
    logic [7:0]            ar_beat_q;
    logic                  ar_active_q;

    logic                  r_valid_q;
    logic                  r_last_q;
    logic [DATA_WIDTH-1:0] r_data_q;
    logic [ID_WIDTH-1:0]   r_id_q;

    assign s_axi_arready = !ar_active_q;
    assign s_axi_rvalid  = r_valid_q;
    assign s_axi_rdata   = r_data_q;
    assign s_axi_rlast   = r_last_q;
    assign s_axi_rresp   = 2'b00;
    assign s_axi_rid     = r_id_q;

    // ── Sequential ─────────────────────────────────────────────────────
    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            aw_id_q     <= '0;
            aw_addr_q   <= '0;
            aw_len_q    <= '0;
            aw_beat_q   <= '0;
            aw_active_q <= 1'b0;
            b_pending_q <= 1'b0;
            b_id_q      <= '0;

            ar_id_q     <= '0;
            ar_addr_q   <= '0;
            ar_len_q    <= '0;
            ar_beat_q   <= '0;
            ar_active_q <= 1'b0;

            r_valid_q   <= 1'b0;
            r_last_q    <= 1'b0;
            r_data_q    <= '0;
            r_id_q      <= '0;

            for (int i = 0; i < NUM_WORDS; i++) begin
                mem_q[i] <= '0;
            end
        end else begin
            // ── Write address accept ─────────────────────────────────
            if (s_axi_awvalid && s_axi_awready) begin
                aw_id_q     <= s_axi_awid;
                aw_addr_q   <= s_axi_awaddr;
                aw_len_q    <= s_axi_awlen;
                aw_beat_q   <= '0;
                aw_active_q <= 1'b1;
            end

            // ── Write data accept ────────────────────────────────────
            if (aw_active_q && s_axi_wvalid && s_axi_wready) begin
                for (int b = 0; b < BYTES_PER_WORD; b++) begin
                    if (s_axi_wstrb[b]) begin
                        mem_q[aw_addr_q[ADDR_WIDTH-1:WORD_INDEX_LSB]][8*b +: 8] <= s_axi_wdata[8*b +: 8];
                    end
                end
                if (s_axi_wlast) begin
                    aw_active_q <= 1'b0;
                    b_pending_q <= 1'b1;
                    b_id_q      <= aw_id_q;
                end else begin
                    aw_addr_q <= aw_addr_q + BYTES_PER_WORD[ADDR_WIDTH-1:0];
                    aw_beat_q <= aw_beat_q + 8'd1;
                end
            end

            // ── Write response retire ────────────────────────────────
            if (b_pending_q && s_axi_bready) begin
                b_pending_q <= 1'b0;
            end

            // ── Read address accept ──────────────────────────────────
            if (s_axi_arvalid && s_axi_arready) begin
                ar_id_q     <= s_axi_arid;
                ar_addr_q   <= s_axi_araddr;
                ar_len_q    <= s_axi_arlen;
                ar_beat_q   <= '0;
                ar_active_q <= 1'b1;
                r_valid_q   <= 1'b1;
                r_data_q    <= mem_q[s_axi_araddr[ADDR_WIDTH-1:WORD_INDEX_LSB]];
                r_last_q    <= (s_axi_arlen == 8'd0);
                r_id_q      <= s_axi_arid;
            end else if (ar_active_q && r_valid_q && s_axi_rready) begin
                if (r_last_q) begin
                    ar_active_q <= 1'b0;
                    r_valid_q   <= 1'b0;
                    r_last_q    <= 1'b0;
                end else begin
                    ar_addr_q <= ar_addr_q + BYTES_PER_WORD[ADDR_WIDTH-1:0];
                    ar_beat_q <= ar_beat_q + 8'd1;
                    r_data_q  <= mem_q[(ar_addr_q + BYTES_PER_WORD[ADDR_WIDTH-1:0]) >> WORD_INDEX_LSB];
                    r_last_q  <= ((ar_beat_q + 8'd1) == ar_len_q);
                end
            end
        end
    end

endmodule
