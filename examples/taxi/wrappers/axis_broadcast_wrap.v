`resetall
`timescale 1ns / 1ps
`default_nettype none

// Flat-port wrapper for axis_broadcast with M_COUNT=2.
// Unpacks the concatenated output buses into separate m_axis_0_* / m_axis_1_* ports
// so verilog-tools can auto-detect two independent AXI-Stream sink interfaces.
//
// Copyright (c) 2019 Alex Forencich (axis_broadcast.v, MIT license)
// Wrapper: verilog-tools contributors
module axis_broadcast_wrap #(
    parameter DATA_WIDTH = 8
) (
    input  wire                  clk,
    input  wire                  rst,
    // Single AXI-Stream input (slave)
    input  wire [DATA_WIDTH-1:0] s_axis_tdata,
    input  wire                  s_axis_tvalid,
    output wire                  s_axis_tready,
    input  wire                  s_axis_tlast,
    // Broadcast output 0 (master)
    output wire [DATA_WIDTH-1:0] m_axis_0_tdata,
    output wire                  m_axis_0_tvalid,
    input  wire                  m_axis_0_tready,
    output wire                  m_axis_0_tlast,
    // Broadcast output 1 (master)
    output wire [DATA_WIDTH-1:0] m_axis_1_tdata,
    output wire                  m_axis_1_tvalid,
    input  wire                  m_axis_1_tready,
    output wire                  m_axis_1_tlast
);

// Concatenated internal buses
wire [2*DATA_WIDTH-1:0] m_axis_tdata_packed;
wire [1:0]              m_axis_tvalid_packed;
wire [1:0]              m_axis_tready_packed;
wire [1:0]              m_axis_tlast_packed;

// Unpack: output 0 is lower slice, output 1 is upper slice
assign m_axis_0_tdata  = m_axis_tdata_packed[DATA_WIDTH-1:0];
assign m_axis_0_tvalid = m_axis_tvalid_packed[0];
assign m_axis_0_tlast  = m_axis_tlast_packed[0];
assign m_axis_1_tdata  = m_axis_tdata_packed[2*DATA_WIDTH-1:DATA_WIDTH];
assign m_axis_1_tvalid = m_axis_tvalid_packed[1];
assign m_axis_1_tlast  = m_axis_tlast_packed[1];
assign m_axis_tready_packed = {m_axis_1_tready, m_axis_0_tready};

axis_broadcast #(
    .M_COUNT    (2),
    .DATA_WIDTH (DATA_WIDTH),
    .KEEP_ENABLE(0),
    .LAST_ENABLE(1),
    .ID_ENABLE  (0),
    .DEST_ENABLE(0),
    .USER_ENABLE(0)
) u_dut (
    .clk           (clk),
    .rst           (rst),
    .s_axis_tdata  (s_axis_tdata),
    .s_axis_tkeep  (1'b1),
    .s_axis_tvalid (s_axis_tvalid),
    .s_axis_tready (s_axis_tready),
    .s_axis_tlast  (s_axis_tlast),
    .s_axis_tid    (8'b0),
    .s_axis_tdest  (8'b0),
    .s_axis_tuser  (1'b0),
    .m_axis_tdata  (m_axis_tdata_packed),
    .m_axis_tkeep  (),
    .m_axis_tvalid (m_axis_tvalid_packed),
    .m_axis_tready (m_axis_tready_packed),
    .m_axis_tlast  (m_axis_tlast_packed),
    .m_axis_tid    (),
    .m_axis_tdest  (),
    .m_axis_tuser  ()
);

endmodule
`resetall
