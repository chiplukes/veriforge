module stream_delay_tb_local;

    logic       clk;
    logic       rst_n;
    logic [7:0] data_i;
    logic       ready_o;
    logic       valid_i;
    logic [7:0] data_o;
    logic       ready_i;
    logic       valid_o;

    stream_delay dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .payload_i(data_i),
        .ready_o(ready_o),
        .valid_i(valid_i),
        .payload_o(data_o),
        .ready_i(ready_i),
        .valid_o(valid_o)
    );

endmodule
