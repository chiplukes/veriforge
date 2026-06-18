module so0_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        flush,
    input  logic [31:0] data_i,
    input  logic [7:0]  sel_i,
    input  logic [3:0]  valid_i,
    output logic [3:0]  ready_o,
    output logic [31:0] data_o,
    output logic [7:0]  idx_o,
    output logic [3:0]  valid_o,
    input  logic [3:0]  ready_i
);

    stream_omega_net #(
        .SpillReg(1'b0)
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

module so1_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        flush,
    input  logic [31:0] data_i,
    input  logic [7:0]  sel_i,
    input  logic [3:0]  valid_i,
    output logic [3:0]  ready_o,
    output logic [31:0] data_o,
    output logic [7:0]  idx_o,
    output logic [3:0]  valid_o,
    input  logic [3:0]  ready_i
);

    stream_omega_net #(
        .SpillReg(1'b1)
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
