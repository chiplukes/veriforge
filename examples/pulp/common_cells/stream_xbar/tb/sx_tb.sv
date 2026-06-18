module sx0_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        flush,
    input  logic [23:0] data_i,
    input  logic [2:0]  sel_i,
    input  logic [2:0]  valid_i,
    output logic [2:0]  ready_o,
    output logic [15:0] data_o,
    output logic [3:0]  idx_o,
    output logic [1:0]  valid_o,
    input  logic [1:0]  ready_i
);

    stream_xbar #(
        .OutSpillReg(1'b0)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .data_i(data_i),
        .sel_i(sel_i),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_o(data_o),
        .idx_o(idx_o),
        .valid_o(valid_o),
        .ready_i(ready_i)
    );

endmodule

module sx1_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        flush,
    input  logic [23:0] data_i,
    input  logic [2:0]  sel_i,
    input  logic [2:0]  valid_i,
    output logic [2:0]  ready_o,
    output logic [15:0] data_o,
    output logic [3:0]  idx_o,
    output logic [1:0]  valid_o,
    input  logic [1:0]  ready_i
);

    stream_xbar #(
        .OutSpillReg(1'b1)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .data_i(data_i),
        .sel_i(sel_i),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_o(data_o),
        .idx_o(idx_o),
        .valid_o(valid_o),
        .ready_i(ready_i)
    );

endmodule
