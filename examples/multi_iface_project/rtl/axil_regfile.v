// AXI-Lite slave register file — 4 × 32-bit registers.
//
// Detected interface:
//   s_axi  (axi_lite, slave)  — bench master drives AW/W/AR channels
//
// Supports WSTRB byte-enable on writes.  Single-outstanding (one write or
// read in flight at a time).  No timeout or error response — BRESP/RRESP
// always return OKAY (2'b00).
module axil_regfile (
    input  wire        clk,
    input  wire        rst_n,

    // Write address channel
    input  wire [3:0]  s_axi_awaddr,
    input  wire [2:0]  s_axi_awprot,
    input  wire        s_axi_awvalid,
    output wire        s_axi_awready,

    // Write data channel
    input  wire [31:0] s_axi_wdata,
    input  wire [3:0]  s_axi_wstrb,
    input  wire        s_axi_wvalid,
    output wire        s_axi_wready,

    // Write response channel
    output wire [1:0]  s_axi_bresp,
    output wire        s_axi_bvalid,
    input  wire        s_axi_bready,

    // Read address channel
    input  wire [3:0]  s_axi_araddr,
    input  wire [2:0]  s_axi_arprot,
    input  wire        s_axi_arvalid,
    output wire        s_axi_arready,

    // Read data channel
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
            if (s_axi_awvalid && s_axi_awready) begin
                aw_addr_q <= s_axi_awaddr;
                aw_seen   <= 1;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                w_data_q <= s_axi_wdata;
                w_strb_q <= s_axi_wstrb;
                w_seen   <= 1;
            end
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
                    2'd0:    rdata_q <= regs0;
                    2'd1:    rdata_q <= regs1;
                    2'd2:    rdata_q <= regs2;
                    default: rdata_q <= regs3;
                endcase
            end
            if (s_axi_rvalid && s_axi_rready) r_pending <= 0;
        end
    end
endmodule
