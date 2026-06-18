module tc_clk_mux2 (
    input  logic clk0_i,
    input  logic clk1_i,
    input  logic clk_sel_i,
    output logic clk_o
);

    assign clk_o = clk_sel_i ? clk1_i : clk0_i;

endmodule
