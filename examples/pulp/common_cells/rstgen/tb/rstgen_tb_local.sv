module rstgen_tb_local (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic test_mode_i,
    output logic rst_no,
    output logic init_no
);

    rstgen dut (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .test_mode_i(test_mode_i),
        .rst_no(rst_no),
        .init_no(init_no)
    );

endmodule
