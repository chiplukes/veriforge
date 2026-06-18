module fifo_v3_tb_depth3 (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       flush,
    input  logic       push,
    input  logic       pop,
    input  logic [7:0] data_i,
    output logic       full,
    output logic       empty,
    output logic [1:0] usage,
    output logic [7:0] data_o
);

    fifo_v3 #(
        .FALL_THROUGH(1'b0),
        .DATA_WIDTH(8),
        .DEPTH(3)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .testmode_i(1'b0),
        .full_o(full),
        .empty_o(empty),
        .usage_o(usage),
        .data_i(data_i),
        .push_i(push),
        .data_o(data_o),
        .pop_i(pop)
    );

endmodule

module fifo_v3_tb_ft_depth3 (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       flush,
    input  logic       push,
    input  logic       pop,
    input  logic [7:0] data_i,
    output logic       full,
    output logic       empty,
    output logic [1:0] usage,
    output logic [7:0] data_o
);

    fifo_v3 #(
        .FALL_THROUGH(1'b1),
        .DATA_WIDTH(8),
        .DEPTH(3)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .testmode_i(1'b0),
        .full_o(full),
        .empty_o(empty),
        .usage_o(usage),
        .data_i(data_i),
        .push_i(push),
        .data_o(data_o),
        .pop_i(pop)
    );

endmodule

module fifo_v3_tb_depth1 (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       flush,
    input  logic       push,
    input  logic       pop,
    input  logic [7:0] data_i,
    output logic       full,
    output logic       empty,
    output logic [1:0] usage,
    output logic [7:0] data_o
);

    logic usage_bit;

    fifo_v3 #(
        .FALL_THROUGH(1'b0),
        .DATA_WIDTH(8),
        .DEPTH(1)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .testmode_i(1'b0),
        .full_o(full),
        .empty_o(empty),
        .usage_o(usage_bit),
        .data_i(data_i),
        .push_i(push),
        .data_o(data_o),
        .pop_i(pop)
    );

    assign usage = {1'b0, usage_bit};

endmodule

module fifo_v3_tb_ft_depth1 (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       flush,
    input  logic       push,
    input  logic       pop,
    input  logic [7:0] data_i,
    output logic       full,
    output logic       empty,
    output logic [1:0] usage,
    output logic [7:0] data_o
);

    logic usage_bit;

    fifo_v3 #(
        .FALL_THROUGH(1'b1),
        .DATA_WIDTH(8),
        .DEPTH(1)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .testmode_i(1'b0),
        .full_o(full),
        .empty_o(empty),
        .usage_o(usage_bit),
        .data_i(data_i),
        .push_i(push),
        .data_o(data_o),
        .pop_i(pop)
    );

    assign usage = {1'b0, usage_bit};

endmodule
