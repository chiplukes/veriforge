// SPDX-License-Identifier: CERN-OHL-S-2.0
/*
 * Flat-port wrapper for taxi_axil_ram.
 * Combines the taxi split wr/rd interface ports into a single flat AXI-Lite slave.
 *
 * Parameters:
 *   ADDR_W          — address bus width (default 8 → 64 32-bit words)
 *   PIPELINE_OUTPUT — extra pipeline register on read output (default 0)
 *
 * Copyright (c) 2018-2025 FPGA Ninja, LLC (taxi_axil_ram.sv)
 * Wrapper Copyright (c) 2025 chiplukes/verilog-tools contributors
 */
`resetall
`timescale 1ns / 1ps
`default_nettype none

module axil_ram_wrap #(
    parameter ADDR_W          = 8,
    parameter PIPELINE_OUTPUT = 0
) (
    input  wire             clk,
    input  wire             rst,
    // Write address channel
    input  wire [ADDR_W-1:0] s_axil_awaddr,
    input  wire [2:0]        s_axil_awprot,
    input  wire              s_axil_awvalid,
    output wire              s_axil_awready,
    // Write data channel
    input  wire [31:0]       s_axil_wdata,
    input  wire [3:0]        s_axil_wstrb,
    input  wire              s_axil_wvalid,
    output wire              s_axil_wready,
    // Write response channel
    output wire [1:0]        s_axil_bresp,
    output wire              s_axil_bvalid,
    input  wire              s_axil_bready,
    // Read address channel
    input  wire [ADDR_W-1:0] s_axil_araddr,
    input  wire [2:0]        s_axil_arprot,
    input  wire              s_axil_arvalid,
    output wire              s_axil_arready,
    // Read data channel
    output wire [31:0]       s_axil_rdata,
    output wire [1:0]        s_axil_rresp,
    output wire              s_axil_rvalid,
    input  wire              s_axil_rready
);

taxi_axil_if #(.DATA_W(32), .ADDR_W(ADDR_W)) axil_if ();

// Flat → write address
assign axil_if.awaddr  = s_axil_awaddr;
assign axil_if.awprot  = s_axil_awprot;
assign axil_if.awvalid = s_axil_awvalid;
assign s_axil_awready  = axil_if.awready;
// Flat → write data
assign axil_if.wdata   = s_axil_wdata;
assign axil_if.wstrb   = s_axil_wstrb;
assign axil_if.wvalid  = s_axil_wvalid;
assign s_axil_wready   = axil_if.wready;
// Write response → flat
assign s_axil_bresp    = axil_if.bresp;
assign s_axil_bvalid   = axil_if.bvalid;
assign axil_if.bready  = s_axil_bready;
// Flat → read address
assign axil_if.araddr  = s_axil_araddr;
assign axil_if.arprot  = s_axil_arprot;
assign axil_if.arvalid = s_axil_arvalid;
assign s_axil_arready  = axil_if.arready;
// Read data → flat
assign s_axil_rdata    = axil_if.rdata;
assign s_axil_rresp    = axil_if.rresp;
assign s_axil_rvalid   = axil_if.rvalid;
assign axil_if.rready  = s_axil_rready;

taxi_axil_ram #(
    .ADDR_W         (ADDR_W),
    .PIPELINE_OUTPUT(PIPELINE_OUTPUT[0])
) u_dut (
    .clk        (clk),
    .rst        (rst),
    .s_axil_wr  (axil_if),
    .s_axil_rd  (axil_if)
);

endmodule
`resetall
