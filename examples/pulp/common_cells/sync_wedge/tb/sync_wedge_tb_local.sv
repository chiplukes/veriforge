module sync_wedge_tb_local (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic en_i,
    input  logic serial_i,
    output logic r_edge_o,
    output logic f_edge_o,
    output logic serial_o
);

    sync_wedge #(
        .STAGES(2)
    ) dut (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .en_i(en_i),
        .serial_i(serial_i),
        .r_edge_o(r_edge_o),
        .f_edge_o(f_edge_o),
        .serial_o(serial_o)
    );

endmodule
