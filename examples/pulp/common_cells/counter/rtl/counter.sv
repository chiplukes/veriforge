module counter #(
    parameter int unsigned WIDTH = 4,
    parameter bit STICKY_OVERFLOW = 1'b0
) (
    input  logic             clk_i,
    input  logic             rst_ni,
    input  logic             clear_i,
    input  logic             en_i,
    input  logic             load_i,
    input  logic             down_i,
    input  logic [WIDTH-1:0] d_i,
    output logic [WIDTH-1:0] q_o,
    output logic             overflow_o
);
    delta_counter #(
        .WIDTH(WIDTH),
        .STICKY_OVERFLOW(STICKY_OVERFLOW)
    ) i_counter (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .clear_i(clear_i),
        .en_i(en_i),
        .load_i(load_i),
        .down_i(down_i),
        .delta_i({{WIDTH - 1{1'b0}}, 1'b1}),
        .d_i(d_i),
        .q_o(q_o),
        .overflow_o(overflow_o)
    );

endmodule
