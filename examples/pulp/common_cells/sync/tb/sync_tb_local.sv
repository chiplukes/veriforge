module sync_tb_local (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic serial_i,
    output logic serial_o
);

    sync #(
        .STAGES(3),
        .RESET_VALUE(1'b0)
    ) dut (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .serial_i(serial_i),
        .serial_o(serial_o)
    );

endmodule

module sync_reset_one_tb_local (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic serial_i,
    output logic serial_o
);

    sync #(
        .STAGES(3),
        .RESET_VALUE(1'b1)
    ) dut (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .serial_i(serial_i),
        .serial_o(serial_o)
    );

endmodule
