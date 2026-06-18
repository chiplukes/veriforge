module stream_fork_tb_local;

    logic       clk;
    logic       rst_n;
    logic       valid_i;
    logic       ready_o;
    logic [2:0] valid_o;
    logic [2:0] ready_i;

    stream_fork #(
        .N_OUP(3)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .valid_o(valid_o),
        .ready_i(ready_i)
    );

endmodule
