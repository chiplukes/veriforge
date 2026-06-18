module spill_reg_tb (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       valid_i,
    output logic       ready_o,
    input  logic [7:0] data_i,
    output logic       valid_o,
    input  logic       ready_i,
    output logic [7:0] data_o
);

    spill_register #(
        .DATA_WIDTH(8),
        .Bypass(1'b0)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_i(data_i),
        .valid_o(valid_o),
        .ready_i(ready_i),
        .data_o(data_o)
    );

endmodule

module spill_reg_bp_tb (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       valid_i,
    output logic       ready_o,
    input  logic [7:0] data_i,
    output logic       valid_o,
    input  logic       ready_i,
    output logic [7:0] data_o
);

    spill_register #(
        .DATA_WIDTH(8),
        .Bypass(1'b1)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_i(data_i),
        .valid_o(valid_o),
        .ready_i(ready_i),
        .data_o(data_o)
    );

endmodule
