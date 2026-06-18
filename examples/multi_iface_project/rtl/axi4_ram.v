// AXI4 single-beat slave memory — 8 × 32-bit words.
//
// Detected interface:
//   s_axi  (axi4, slave)  — bench AXI4Master drives AW/W/AR channels
//
// Limitations (intentional for a simulation example):
//   - AWLEN must be 0 (single beat per transaction; INCR burst of length 1)
//   - No ID tracking for write interleaving (AWID echoed on BID, ARID on RID)
//   - WSTRB byte-enables are honored
//   - Address bits [4:2] index into the 8-word array (byte address / 4)
//   - Memory is uninitialized at reset; writes must precede reads in the bench
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
    // Single-beat: rlast is always asserted with the data beat
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
            // Latch wdata/wstrb at W-channel handshake so data is stable when
            // the write is committed one cycle later.
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
