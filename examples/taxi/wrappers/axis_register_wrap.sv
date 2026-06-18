// SPDX-License-Identifier: CERN-OHL-S-2.0
/*
 * Flat-port wrapper for taxi_axis_register.
 * Exposes standard s_axis_* / m_axis_* ports for use with verilog-tools
 * auto-detection and simulation.
 *
 * Parameters:
 *   DATA_W   — AXI-Stream data width in bits (default 8)
 *   REG_TYPE — 0=bypass, 1=simple buffer, 2=skid buffer (default 2)
 *
 * Copyright (c) 2014-2025 FPGA Ninja, LLC (taxi_axis_register.sv)
 * Wrapper Copyright (c) 2025 chiplukes/verilog-tools contributors
 */
`resetall
`timescale 1ns / 1ps
`default_nettype none

module axis_register_wrap #(
    parameter DATA_W   = 8,
    parameter REG_TYPE = 2
) (
    input  wire               clk,
    input  wire               rst,
    // AXI-Stream sink (input)
    input  wire [DATA_W-1:0]  s_axis_tdata,
    input  wire               s_axis_tvalid,
    output wire               s_axis_tready,
    input  wire               s_axis_tlast,
    // AXI-Stream source (output)
    output wire [DATA_W-1:0]  m_axis_tdata,
    output wire               m_axis_tvalid,
    input  wire               m_axis_tready,
    output wire               m_axis_tlast
);

taxi_axis_if #(.DATA_W(DATA_W), .KEEP_EN(1'b0)) s_if ();
taxi_axis_if #(.DATA_W(DATA_W), .KEEP_EN(1'b0)) m_if ();

// Flat → interface
assign s_if.tdata  = s_axis_tdata;
assign s_if.tvalid = s_axis_tvalid;
assign s_if.tlast  = s_axis_tlast;
assign s_axis_tready = s_if.tready;

// Interface → flat
assign m_axis_tdata  = m_if.tdata;
assign m_axis_tvalid = m_if.tvalid;
assign m_axis_tlast  = m_if.tlast;
assign m_if.tready = m_axis_tready;

taxi_axis_register #(
    .REG_TYPE(REG_TYPE)
) u_dut (
    .clk    (clk),
    .rst    (rst),
    .s_axis (s_if),
    .m_axis (m_if)
);

endmodule
`resetall
