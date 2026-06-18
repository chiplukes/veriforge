module stream_fork_dynamic_tb_local;

    logic       clk;
    logic       rst_n;
    logic       valid_i;
    logic       ready_o;
    logic [2:0] sel_i;
    logic       sel_valid_i;
    logic       sel_ready_o;
    logic [2:0] valid_o;
    logic [2:0] ready_i;

    stream_fork_dynamic #(
        .N_OUP(3)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .sel_i(sel_i),
        .sel_valid_i(sel_valid_i),
        .sel_ready_o(sel_ready_o),
        .valid_o(valid_o),
        .ready_i(ready_i)
    );

endmodule
