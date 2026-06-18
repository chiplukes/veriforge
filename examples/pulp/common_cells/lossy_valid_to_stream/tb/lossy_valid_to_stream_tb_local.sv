module lossy_valid_to_stream_tb_local;

    logic       clk;
    logic       rst_n;
    logic       valid_i;
    logic [7:0] data_i;
    logic       valid_o;
    logic       ready_i;
    logic [7:0] data_o;
    logic       busy_o;

    lossy_valid_to_stream #(
        .DATA_WIDTH(8)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .valid_i(valid_i),
        .data_i(data_i),
        .valid_o(valid_o),
        .ready_i(ready_i),
        .data_o(data_o),
        .busy_o(busy_o)
    );

endmodule
