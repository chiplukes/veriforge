// Minimal reproduction of the generate-for PartSelect LHS assignment pattern
// found in axi_crossbar_wr.v lines 599-600.
//
//   assign int_axi_bvalid[n*S_COUNT +: S_COUNT] = int_m_axi_bvalid[n] << b_select;
//   assign int_m_axi_bready[n]                  = int_axi_bready[b_select*M_COUNT+n];
//
// With M_COUNT=4, S_COUNT=4 this creates 16-bit wide buses and 4 loop iterations.

`resetall
`timescale 1ns / 1ps
`default_nettype none

module gen_partselect #(
    parameter M_COUNT = 4,
    parameter S_COUNT = 4
) (
    // Per-master bvalid (M_COUNT bits, one per master port)
    input  wire [M_COUNT-1:0]           int_m_axi_bvalid,
    // Select: which slave slot each master's response goes to (log2(S_COUNT) bits each)
    input  wire [M_COUNT*2-1:0]         b_select,   // 2 bits per master (CL_S_COUNT=2 for S_COUNT=4)

    // Per-slave bready (S_COUNT*M_COUNT bits, fully expanded)
    input  wire [S_COUNT*M_COUNT-1:0]   int_axi_bready,

    // Outputs
    output wire [M_COUNT*S_COUNT-1:0]   int_axi_bvalid,  // forwarded bvalid, per (master,slave)
    output wire [M_COUNT-1:0]           int_m_axi_bready // back-pressure to each master
);

genvar n;
generate
    for (n = 0; n < M_COUNT; n = n + 1) begin : gen_b_fwd
        wire [1:0] bsel = b_select[n*2 +: 2];  // CL_S_COUNT bits

        // The two patterns being tested:
        //  LHS PartSelect driven from a shifted scalar: signal[n*S_COUNT +: S_COUNT]
        assign int_axi_bvalid[n*S_COUNT +: S_COUNT] = int_m_axi_bvalid[n] << bsel;
        //  RHS PartSelect (already worked before fix): signal[bsel*M_COUNT+n]
        assign int_m_axi_bready[n] = int_axi_bready[bsel*M_COUNT + n];
    end
endgenerate

endmodule

`resetall
