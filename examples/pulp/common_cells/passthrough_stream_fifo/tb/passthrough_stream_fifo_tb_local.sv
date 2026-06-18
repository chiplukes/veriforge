module passthrough_stream_fifo_tb_same_cycle;

    logic       clk;
    logic       rst_n;
    logic       flush;
    logic [7:0] data_i;
    logic       valid_i;
    logic       ready_o;
    logic [7:0] data_o;
    logic       valid_o;
    logic       ready_i;

    passthrough_stream_fifo #(
        .SAME_CYCLE_RW(1'b1)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .testmode_i(1'b0),
        .data_i(data_i),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_o(data_o),
        .valid_o(valid_o),
        .ready_i(ready_i)
    );

endmodule

module passthrough_stream_fifo_tb_no_same_cycle;

    logic       clk;
    logic       rst_n;
    logic       flush;
    logic [7:0] data_i;
    logic       valid_i;
    logic       ready_o;
    logic [7:0] data_o;
    logic       valid_o;
    logic       ready_i;

    passthrough_stream_fifo #(
        .SAME_CYCLE_RW(1'b0)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .testmode_i(1'b0),
        .data_i(data_i),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_o(data_o),
        .valid_o(valid_o),
        .ready_i(ready_i)
    );

endmodule
