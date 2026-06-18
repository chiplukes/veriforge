module trip_counter #(
    parameter int unsigned WIDTH = 4
) (
    input  logic             clk_i,
    input  logic             rst_ni,
    input  logic             en_i,
    input  logic [WIDTH-1:0] delta_i,
    input  logic [WIDTH-1:0] bound_i,
    output logic [WIDTH-1:0] q_o,
    output logic             last_o,
    output logic             trip_o
);

    delta_counter #(
        .WIDTH(WIDTH)
    ) i_delta_counter (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .clear_i(trip_o),
        .en_i(en_i),
        .load_i(1'b0),
        .down_i(1'b0),
        .delta_i(delta_i),
        .d_i('0),
        .q_o(q_o),
        .overflow_o()
    );

    assign last_o = (q_o == bound_i);
    assign trip_o = last_o && en_i;

endmodule
