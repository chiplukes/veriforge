`resetall
`timescale 1ns / 1ps
`default_nettype none

// Flat-port wrapper for axis_arb_mux with S_COUNT=2.
// Expands the packed input buses into separate s_axis_0_* / s_axis_1_* ports
// so verilog-tools can auto-detect two independent AXI-Stream source interfaces.
//
// Copyright (c) 2014-2018 Alex Forencich (axis_arb_mux.v + arbiter.v, MIT license)
// Wrapper: verilog-tools contributors
module axis_arb_mux_wrap #(
    parameter DATA_WIDTH = 8
) (
    input  wire               clk,
    input  wire               rst,
    // Input stream 0 (slave)
    input  wire [DATA_WIDTH-1:0] s_axis_0_tdata,
    input  wire               s_axis_0_tvalid,
    output wire               s_axis_0_tready,
    input  wire               s_axis_0_tlast,
    // Input stream 1 (slave)
    input  wire [DATA_WIDTH-1:0] s_axis_1_tdata,
    input  wire               s_axis_1_tvalid,
    output wire               s_axis_1_tready,
    input  wire               s_axis_1_tlast,
    // Merged output (master)
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire               m_axis_tvalid,
    input  wire               m_axis_tready,
    output wire               m_axis_tlast
);

// Concatenated internal buses
wire [2*DATA_WIDTH-1:0] s_axis_tdata_packed;
wire [1:0]              s_axis_tvalid_packed;
wire [1:0]              s_axis_tready_packed;
wire [1:0]              s_axis_tlast_packed;

// Pack individual streams into buses (stream 0 → lower bits, stream 1 → upper)
assign s_axis_tdata_packed  = {s_axis_1_tdata,  s_axis_0_tdata};
assign s_axis_tvalid_packed = {s_axis_1_tvalid, s_axis_0_tvalid};
assign s_axis_tlast_packed  = {s_axis_1_tlast,  s_axis_0_tlast};
assign s_axis_0_tready      = s_axis_tready_packed[0];
assign s_axis_1_tready      = s_axis_tready_packed[1];

axis_arb_mux #(
    .S_COUNT             (2),
    .DATA_WIDTH          (DATA_WIDTH),
    .KEEP_ENABLE         (0),
    .LAST_ENABLE         (1),
    .ID_ENABLE           (0),
    .DEST_ENABLE         (0),
    .USER_ENABLE         (0),
    .ARB_TYPE_ROUND_ROBIN(1)
) u_dut (
    .clk          (clk),
    .rst          (rst),
    .s_axis_tdata (s_axis_tdata_packed),
    .s_axis_tkeep ({2{1'b1}}),
    .s_axis_tvalid(s_axis_tvalid_packed),
    .s_axis_tready(s_axis_tready_packed),
    .s_axis_tlast (s_axis_tlast_packed),
    .s_axis_tid   ({2{8'b0}}),
    .s_axis_tdest ({2{8'b0}}),
    .s_axis_tuser ({2{1'b0}}),
    .m_axis_tdata (m_axis_tdata),
    .m_axis_tkeep (),
    .m_axis_tvalid(m_axis_tvalid),
    .m_axis_tready(m_axis_tready),
    .m_axis_tlast (m_axis_tlast),
    .m_axis_tid   (),
    .m_axis_tdest (),
    .m_axis_tuser ()
);

endmodule
`resetall
