module stream_register_tb_local;

    logic       clk;
    logic       rst_n;
    logic       clr;
    logic       valid_i;
    logic       ready_o;
    logic [7:0] data_i;
    logic       valid_o;
    logic       ready_i;
    logic [7:0] data_o;

    stream_register dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .clr_i(clr),
        .testmode_i(1'b0),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_i(data_i),
        .valid_o(valid_o),
        .ready_i(ready_i),
        .data_o(data_o)
    );

endmodule
