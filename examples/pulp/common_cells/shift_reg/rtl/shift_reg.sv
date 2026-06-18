module shift_reg #(
    parameter int unsigned Width = 8,
    parameter int unsigned Depth = 1
) (
    input  logic             clk_i,
    input  logic             rst_ni,
    input  logic [Width-1:0] d_i,
    output logic [Width-1:0] d_o
);

    shift_reg_gated #(
        .Width(Width),
        .Depth(Depth)
    ) i_shift_reg_gated (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .valid_i(1'b1),
        .data_i(d_i),
        .valid_o(),
        .data_o(d_o)
    );

endmodule
