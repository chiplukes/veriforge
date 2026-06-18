module stream_arbiter (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic [31:0] inp_data_i,
    input  logic [3:0]  inp_valid_i,
    output logic [3:0]  inp_ready_o,
    output logic [7:0]  oup_data_o,
    output logic        oup_valid_o,
    input  logic        oup_ready_i
);

    stream_arbiter_flushable i_arb (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .flush_i(1'b0),
        .inp_data_i(inp_data_i),
        .inp_valid_i(inp_valid_i),
        .inp_ready_o(inp_ready_o),
        .oup_data_o(oup_data_o),
        .oup_valid_o(oup_valid_o),
        .oup_ready_i(oup_ready_i)
    );

endmodule
