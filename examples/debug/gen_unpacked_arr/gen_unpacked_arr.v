// Minimal reproduction of Bug 1: unpacked reg array element access inside
// a generate-for loop.
//
// In axi_crossbar_addr.v:
//   reg [ID_WIDTH-1:0] thread_id_reg[S_INT_THREADS-1:0];
//   ...
//   generate for (n = 0; n < S_INT_THREADS; n = n + 1) begin
//       always @(posedge clk) begin
//           if (thread_trans_start[n]) thread_id_reg[n] <= s_axi_aid;
//       end
//   end endgenerate
//
// After genvar substitution: thread_id_reg[0] should be treated as element 0
// of the unpacked array (full WIDTH bits), not as bit 0 of a flat vector.

`resetall
`timescale 1ns / 1ps
`default_nettype none

module gen_unpacked_arr #(
    parameter COUNT = 4,
    parameter WIDTH = 8
) (
    input  wire                   clk,
    input  wire [COUNT*WIDTH-1:0] data_in,  // packed input (WIDTH bits per element)
    output wire [COUNT*WIDTH-1:0] data_out  // packed output (WIDTH bits per element)
);

    // Unpacked array: COUNT elements, each WIDTH bits wide
    reg [WIDTH-1:0] arr [COUNT-1:0];

    genvar n;
    generate
        for (n = 0; n < COUNT; n = n + 1) begin : gen_arr
            // Write element n from packed data_in slice
            always @(posedge clk) begin
                arr[n] <= data_in[n*WIDTH +: WIDTH];
            end
            // Read element n into packed data_out slice
            assign data_out[n*WIDTH +: WIDTH] = arr[n];
        end
    endgenerate

endmodule

`resetall
