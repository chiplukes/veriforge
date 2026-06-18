`resetall
`timescale 1ns / 1ps
`default_nettype none

// True dual-clock wrapper for axis_async_fifo.
// Exposes separate s_clk/m_clk so the read and write domains can run at
// different rates, exercising the gray-code CDC pointer logic in the
// FIFO. Used by the truly-async testbench to validate the bench
// framework's multi-domain runner against a real CDC DUT.
module axis_async_fifo_dualclk_wrap #(
    parameter DATA_WIDTH = 8,
    parameter DEPTH      = 32
) (
    input  wire                  s_clk,
    input  wire                  s_rst,
    input  wire [DATA_WIDTH-1:0] s_axis_tdata,
    input  wire                  s_axis_tvalid,
    output wire                  s_axis_tready,
    input  wire                  s_axis_tlast,
    input  wire                  m_clk,
    input  wire                  m_rst,
    output wire [DATA_WIDTH-1:0] m_axis_tdata,
    output wire                  m_axis_tvalid,
    input  wire                  m_axis_tready,
    output wire                  m_axis_tlast
);

axis_async_fifo #(
    .DEPTH        (DEPTH),
    .DATA_WIDTH   (DATA_WIDTH),
    .KEEP_ENABLE  (0),
    .LAST_ENABLE  (1),
    .ID_ENABLE    (0),
    .DEST_ENABLE  (0),
    .USER_ENABLE  (0),
    .PAUSE_ENABLE (0)
) u_fifo (
    .s_clk        (s_clk),
    .s_rst        (s_rst),
    .s_axis_tdata (s_axis_tdata),
    .s_axis_tkeep (1'b1),
    .s_axis_tvalid(s_axis_tvalid),
    .s_axis_tready(s_axis_tready),
    .s_axis_tlast (s_axis_tlast),
    .s_axis_tid   (8'b0),
    .s_axis_tdest (8'b0),
    .s_axis_tuser (1'b0),

    .m_clk        (m_clk),
    .m_rst        (m_rst),
    .m_axis_tdata (m_axis_tdata),
    .m_axis_tkeep (),
    .m_axis_tvalid(m_axis_tvalid),
    .m_axis_tready(m_axis_tready),
    .m_axis_tlast (m_axis_tlast),
    .m_axis_tid   (),
    .m_axis_tdest (),
    .m_axis_tuser (),

    .s_pause_req  (1'b0),
    .s_pause_ack  (),
    .m_pause_req  (1'b0),
    .m_pause_ack  (),

    .s_status_depth        (),
    .s_status_depth_commit (),
    .s_status_overflow     (),
    .s_status_bad_frame    (),
    .s_status_good_frame   (),
    .m_status_depth        (),
    .m_status_depth_commit (),
    .m_status_overflow     (),
    .m_status_bad_frame    (),
    .m_status_good_frame   ()
);

endmodule
`resetall
