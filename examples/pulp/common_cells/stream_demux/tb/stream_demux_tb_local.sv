module stream_demux_tb_local;

    logic       inp_valid_i;
    logic       inp_ready_o;
    logic [1:0] oup_sel_i;
    logic [2:0] oup_valid_o;
    logic [2:0] oup_ready_i;

    stream_demux #(
        .N_OUP(3)
    ) dut (
        .inp_valid_i(inp_valid_i),
        .inp_ready_o(inp_ready_o),
        .oup_sel_i(oup_sel_i),
        .oup_valid_o(oup_valid_o),
        .oup_ready_i(oup_ready_i)
    );

endmodule
