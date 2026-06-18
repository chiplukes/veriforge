module stream_fifo_optimal_wrap_tb_depth2;

    logic       clk;
    logic       rst_n;
    logic       flush;
    logic [0:0] usage;
    logic [7:0] data_i;
    logic       valid_i;
    logic       ready_o;
    logic [7:0] data_o;
    logic       valid_o;
    logic       ready_i;

    stream_fifo_optimal_wrap #(
        .DEPTH(2),
        .DATA_WIDTH(8)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .testmode_i(1'b0),
        .usage_o(usage),
        .data_i(data_i),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_o(data_o),
        .valid_o(valid_o),
        .ready_i(ready_i)
    );

endmodule

module stream_fifo_optimal_wrap_tb_depth3;

    logic       clk;
    logic       rst_n;
    logic       flush;
    logic [1:0] usage;
    logic [7:0] data_i;
    logic       valid_i;
    logic       ready_o;
    logic [7:0] data_o;
    logic       valid_o;
    logic       ready_i;

    stream_fifo_optimal_wrap #(
        .DEPTH(3),
        .DATA_WIDTH(8)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .testmode_i(1'b0),
        .usage_o(usage),
        .data_i(data_i),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_o(data_o),
        .valid_o(valid_o),
        .ready_i(ready_i)
    );

endmodule
