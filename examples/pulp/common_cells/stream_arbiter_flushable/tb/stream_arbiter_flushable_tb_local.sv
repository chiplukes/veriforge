module stream_arbiter_flushable_tb_local;

    logic        clk;
    logic        rst_n;
    logic        flush_i;
    logic [31:0] inp_data_i;
    logic [3:0]  inp_valid_i;
    logic [3:0]  inp_ready_o;
    logic [7:0]  oup_data_o;
    logic        oup_valid_o;
    logic        oup_ready_i;

    stream_arbiter_flushable dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush_i),
        .inp_data_i(inp_data_i),
        .inp_valid_i(inp_valid_i),
        .inp_ready_o(inp_ready_o),
        .oup_data_o(oup_data_o),
        .oup_valid_o(oup_valid_o),
        .oup_ready_i(oup_ready_i)
    );

endmodule
