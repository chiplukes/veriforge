// -----------------------------------------------------------------------------
// multi_domain_axis_dut.v
//
// Two-clock-domain AXI-Stream pass-through DUT used by the Python testbench
// example `multi_domain_axis.py`. Each domain is intentionally combinational
// so the testbench can focus on demonstrating sideband + element packing
// behavior end-to-end without DUT-specific corner cases.
//
// Domain P (pclk / presetn): "Pixel" stream
//   - 48-bit TDATA, 4-bit TKEEP, 1-bit TUSER, 1-bit TLAST
//   - Carries 4x 12-bit pixel elements per beat, big-endian packing.
//   - TUSER is only meaningful on the TLAST beat: 1 = good frame, 0 = corrupt.
//
// Domain R (rclk / rresetn): "Router" stream
//   - 8-bit TDATA (1x 8-bit element per beat), 4-bit TDEST, 4-bit TID, 1-bit TLAST
//   - Carries individually addressed/identified packets.
// -----------------------------------------------------------------------------

module multi_domain_axis_dut (
    // Domain P clock / reset
    input  wire        pclk,
    input  wire        presetn,

    // Domain R clock / reset
    input  wire        rclk,
    input  wire        rresetn,

    // -------- Pixel stream input (DUT slave) --------
    input  wire        pix_in_tvalid,
    output wire        pix_in_tready,
    input  wire [47:0] pix_in_tdata,
    input  wire [3:0]  pix_in_tkeep,
    input  wire        pix_in_tuser,
    input  wire        pix_in_tlast,

    // -------- Pixel stream output (DUT master) --------
    output wire        pix_out_tvalid,
    input  wire        pix_out_tready,
    output wire [47:0] pix_out_tdata,
    output wire [3:0]  pix_out_tkeep,
    output wire        pix_out_tuser,
    output wire        pix_out_tlast,

    // -------- Router stream input (DUT slave) --------
    input  wire        rtr_in_tvalid,
    output wire        rtr_in_tready,
    input  wire [7:0]  rtr_in_tdata,
    input  wire [3:0]  rtr_in_tdest,
    input  wire [3:0]  rtr_in_tid,
    input  wire        rtr_in_tlast,

    // -------- Router stream output (DUT master) --------
    output wire        rtr_out_tvalid,
    input  wire        rtr_out_tready,
    output wire [7:0]  rtr_out_tdata,
    output wire [3:0]  rtr_out_tdest,
    output wire [3:0]  rtr_out_tid,
    output wire        rtr_out_tlast
);
    // Pixel stream pass-through (Domain P).
    assign pix_out_tvalid = pix_in_tvalid;
    assign pix_out_tdata  = pix_in_tdata;
    assign pix_out_tkeep  = pix_in_tkeep;
    assign pix_out_tuser  = pix_in_tuser;
    assign pix_out_tlast  = pix_in_tlast;
    assign pix_in_tready  = pix_out_tready;

    // Router stream pass-through (Domain R).
    assign rtr_out_tvalid = rtr_in_tvalid;
    assign rtr_out_tdata  = rtr_in_tdata;
    assign rtr_out_tdest  = rtr_in_tdest;
    assign rtr_out_tid    = rtr_in_tid;
    assign rtr_out_tlast  = rtr_in_tlast;
    assign rtr_in_tready  = rtr_out_tready;

    // Per-domain heartbeat counters give the testbench planner a registered
    // anchor so it can confidently tie clock/reset names to physical edges.
    reg [7:0] p_tick;
    reg [7:0] r_tick;
    always @(posedge pclk or negedge presetn)
        if (!presetn) p_tick <= 8'h00; else p_tick <= p_tick + 8'd1;
    always @(posedge rclk or negedge rresetn)
        if (!rresetn) r_tick <= 8'h00; else r_tick <= r_tick + 8'd1;
endmodule
