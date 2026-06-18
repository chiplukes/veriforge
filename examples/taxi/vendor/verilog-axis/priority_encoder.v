/*

Copyright (c) 2014-2021 Alex Forencich

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

*/

// Language: Verilog 2001

`resetall
`timescale 1ns / 1ps
`default_nettype none

/*
 * Priority encoder module (simulation-compatible behavioral implementation).
 * Uses ascending-only loops to avoid signed-integer underflow issues in
 * event-driven simulators that do not handle descending integer loops.
 *
 * LSB_HIGH_PRIORITY=0: highest-index set bit wins (keep overwriting = last wins).
 * LSB_HIGH_PRIORITY=1: lowest-index set bit wins (set only on first match).
 */
module priority_encoder #
(
    parameter WIDTH = 4,
    // LSB priority selection
    parameter LSB_HIGH_PRIORITY = 0
)
(
    input  wire [WIDTH-1:0]         input_unencoded,
    output reg                      output_valid,
    output reg  [$clog2(WIDTH)-1:0] output_encoded,
    output wire [WIDTH-1:0]         output_unencoded
);

integer i;

always @(*) begin : enc_proc
    output_valid   = 1'b0;
    output_encoded = {$clog2(WIDTH){1'b0}};

    if (LSB_HIGH_PRIORITY) begin
        // Scan ascending; only record the FIRST (lowest-index) hit
        for (i = 0; i < WIDTH; i = i + 1) begin
            if (input_unencoded[i] && !output_valid) begin
                output_encoded = i;
                output_valid   = 1'b1;
            end
        end
    end else begin
        // Scan ascending; keep overwriting so the LAST (highest-index) hit wins
        for (i = 0; i < WIDTH; i = i + 1) begin
            if (input_unencoded[i]) begin
                output_encoded = i;
                output_valid   = 1'b1;
            end
        end
    end
end

assign output_unencoded = output_valid ? ({{WIDTH-1{1'b0}}, 1'b1} << output_encoded) : {WIDTH{1'b0}};

endmodule

`resetall
