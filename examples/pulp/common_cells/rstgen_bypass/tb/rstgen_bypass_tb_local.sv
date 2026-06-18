module rstgen_bypass_tb_local (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic rst_test_mode_ni,
    input  logic test_mode_i,
    output logic rst_no,
    output logic init_no
);

    rstgen_bypass #(
        .NUM_REGS(4)
    ) dut (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .rst_test_mode_ni(rst_test_mode_ni),
        .test_mode_i(test_mode_i),
        .rst_no(rst_no),
        .init_no(init_no)
    );

endmodule
