module spill_register #(
    parameter int DATA_WIDTH = 8,
    parameter bit Bypass = 1'b0
) (
    input  logic                  clk_i,
    input  logic                  rst_ni,
    input  logic                  valid_i,
    output logic                  ready_o,
    input  logic [DATA_WIDTH-1:0] data_i,
    output logic                  valid_o,
    input  logic                  ready_i,
    output logic [DATA_WIDTH-1:0] data_o
);

    spill_register_flushable #(
        .DATA_WIDTH(DATA_WIDTH),
        .Bypass(Bypass)
    ) spill_register_flushable_i (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .valid_i(valid_i),
        .flush_i(1'b0),
        .ready_o(ready_o),
        .data_i(data_i),
        .valid_o(valid_o),
        .ready_i(ready_i),
        .data_o(data_o)
    );

endmodule
