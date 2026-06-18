module stream_filter_tb_local;

    logic valid_i;
    logic ready_o;
    logic drop_i;
    logic valid_o;
    logic ready_i;

    stream_filter dut (
        .valid_i(valid_i),
        .ready_o(ready_o),
        .drop_i(drop_i),
        .valid_o(valid_o),
        .ready_i(ready_i)
    );

endmodule
