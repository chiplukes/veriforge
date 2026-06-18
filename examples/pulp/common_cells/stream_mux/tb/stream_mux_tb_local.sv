module stream_mux_tb_local;

    logic [23:0] inp_data_i;
    logic [2:0]  inp_valid_i;
    logic [2:0]  inp_ready_o;
    logic [1:0]  inp_sel_i;
    logic [7:0]  oup_data_o;
    logic        oup_valid_o;
    logic        oup_ready_i;

    stream_mux dut (
        .inp_data_i(inp_data_i),
        .inp_valid_i(inp_valid_i),
        .inp_ready_o(inp_ready_o),
        .inp_sel_i(inp_sel_i),
        .oup_data_o(oup_data_o),
        .oup_valid_o(oup_valid_o),
        .oup_ready_i(oup_ready_i)
    );

endmodule
