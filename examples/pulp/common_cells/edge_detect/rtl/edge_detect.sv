module edge_detect (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic d_i,
    output logic re_o,
    output logic fe_o
);

    sync_wedge i_sync_wedge (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .en_i(1'b1),
        .serial_i(d_i),
        .r_edge_o(re_o),
        .f_edge_o(fe_o),
        .serial_o()
    );

endmodule
