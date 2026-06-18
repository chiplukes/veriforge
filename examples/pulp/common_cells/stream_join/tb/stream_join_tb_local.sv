module stream_join_tb_local;

    logic [2:0] inp_valid_i;
    logic [2:0] inp_ready_o;
    logic       oup_valid_o;
    logic       oup_ready_i;

    stream_join #(
        .N_INP(3)
    ) dut (
        .inp_valid_i(inp_valid_i),
        .inp_ready_o(inp_ready_o),
        .oup_valid_o(oup_valid_o),
        .oup_ready_i(oup_ready_i)
    );

endmodule
