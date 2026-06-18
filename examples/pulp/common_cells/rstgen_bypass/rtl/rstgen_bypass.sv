module rstgen_bypass #(
    parameter int NUM_REGS = 4
) (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic rst_test_mode_ni,
    input  logic test_mode_i,
    output logic rst_no,
    output logic init_no
);

    logic rst_n;
    logic [NUM_REGS-1:0] synch_regs_q;

    tc_clk_mux2 i_tc_clk_mux2_rst_n (
        .clk0_i(rst_ni),
        .clk1_i(rst_test_mode_ni),
        .clk_sel_i(test_mode_i),
        .clk_o(rst_n)
    );

    tc_clk_mux2 i_tc_clk_mux2_rst_no (
        .clk0_i(synch_regs_q[NUM_REGS - 1]),
        .clk1_i(rst_test_mode_ni),
        .clk_sel_i(test_mode_i),
        .clk_o(rst_no)
    );

    tc_clk_mux2 i_tc_clk_mux2_init_no (
        .clk0_i(synch_regs_q[NUM_REGS - 1]),
        .clk1_i(1'b1),
        .clk_sel_i(test_mode_i),
        .clk_o(init_no)
    );

    always @(posedge clk_i or negedge rst_n) begin
        if (!rst_n) begin
            synch_regs_q <= '0;
        end else begin
            synch_regs_q <= {synch_regs_q[NUM_REGS - 2:0], 1'b1};
        end
    end

endmodule
