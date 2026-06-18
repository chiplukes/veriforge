`resetall
`timescale 1ns / 1ps
`default_nettype none

// Flat-port wrapper: axis_adapter configured as 8-bit → 16-bit upscaler.
// Ties off tkeep/tid/tdest/tuser so the parser sees only the core AXI-Stream
// signals (tvalid, tready, tdata, tlast) on each side.
//
// Copyright (c) 2014-2023 Alex Forencich (axis_adapter.v, MIT license)
// Wrapper: verilog-tools contributors
module axis_adapter_8to16_wrap (
    input  wire        clk,
    input  wire        rst,

    // 8-bit AXI-Stream input (slave)
    input  wire [7:0]  s_axis_tdata,
    input  wire        s_axis_tvalid,
    output wire        s_axis_tready,
    input  wire        s_axis_tlast,

    // 16-bit AXI-Stream output (master)
    output wire [15:0] m_axis_tdata,
    output wire        m_axis_tvalid,
    input  wire        m_axis_tready,
    output wire        m_axis_tlast
);

axis_adapter #(
    .S_DATA_WIDTH (8),
    .M_DATA_WIDTH (16),
    .ID_ENABLE    (0),
    .DEST_ENABLE  (0),
    .USER_ENABLE  (0)
) u_dut (
    .clk          (clk),
    .rst          (rst),
    .s_axis_tdata (s_axis_tdata),
    .s_axis_tkeep (1'b1),
    .s_axis_tvalid(s_axis_tvalid),
    .s_axis_tready(s_axis_tready),
    .s_axis_tlast (s_axis_tlast),
    .s_axis_tid   (8'b0),
    .s_axis_tdest (8'b0),
    .s_axis_tuser (1'b0),
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
