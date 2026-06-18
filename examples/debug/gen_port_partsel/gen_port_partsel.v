// Minimal reproduction of Bug 2: output port connected to PartSelect slice
// inside a generate-for loop.
//
// In axi_crossbar_wr.v:
//   axi_register_wr #(...) axi_register_wr_inst (.s_axi_bid(int_m_axi_bid[n*M_ID_WIDTH +: M_ID_WIDTH]));
//
// The _wire_port_connections function should create:
//   assign int_m_axi_bid[n*M_ID_WIDTH +: M_ID_WIDTH] = genblk[n].child_inst.data_out;
//
// Each child drives a distinct constant so we can verify all slices are populated.

`resetall
`timescale 1ns / 1ps
`default_nettype none

module child_src #(
    parameter WIDTH = 8,
    parameter VALUE = 8'hAB
) (
    output wire [WIDTH-1:0] data_out
);
    assign data_out = VALUE[WIDTH-1:0];
endmodule

module gen_port_partsel #(
    parameter COUNT = 4,
    parameter WIDTH = 8
) (
    output wire [COUNT*WIDTH-1:0] combined_out
);

genvar n;
generate
    for (n = 0; n < COUNT; n = n + 1) begin : gen_inst
        child_src #(
            .WIDTH(WIDTH),
            .VALUE(8'hA0 | n)  // n=0->0xA0, n=1->0xA1, n=2->0xA2, n=3->0xA3
        ) child_inst (
            .data_out(combined_out[n*WIDTH +: WIDTH])
        );
    end
endgenerate

endmodule

`resetall
