module edge_propagator_rx (
    input  logic clk_i,
    input  logic rstn_i,
    input  logic valid_i,
    output logic ack_o,
    output logic valid_o
);

    sync_wedge i_sync_clkb (
        .clk_i(clk_i),
        .rst_ni(rstn_i),
        .en_i(1'b1),
        .serial_i(valid_i),
        .r_edge_o(valid_o),
        .f_edge_o(),
        .serial_o(ack_o)
    );

endmodule
