/*
 * Minimal testbench for DarkRISCV — no timing, no clock generator.
 * Clock and reset are driven externally via batch_run().
 *
 * This avoids Python coroutines entirely, allowing the compiled
 * engine to run the entire simulation in C.
 */

`timescale 1ns / 1ps
`include "../rtl/config.vh"

module darksimv;

    reg CLK = 0;
    reg RES = 1;

    wire TX;
    wire RX = 1;

    darksocv soc0
    (
        .XCLK(CLK),
        .XRES(|RES),
        .IPORT(0),
        .UART_RXD(RX),
        .UART_TXD(TX)
    );

endmodule
