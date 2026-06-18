module stream_arbiter_flushable (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        flush_i,
    input  logic [31:0] inp_data_i,
    input  logic [3:0]  inp_valid_i,
    output logic [3:0]  inp_ready_o,
    output logic [7:0]  oup_data_o,
    output logic        oup_valid_o,
    input  logic        oup_ready_i
);

    rr_arb_tree #(
        .NumIn(4),
        .DataWidth(8),
        .ExtPrio(1'b0),
        .AxiVldRdy(1'b1),
        .LockIn(1'b1)
    ) i_arbiter (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .flush_i(flush_i),
        .rr_i('0),
        .req_i(inp_valid_i),
        .gnt_o(inp_ready_o),
        .data_i(inp_data_i),
        .gnt_i(oup_ready_i),
        .req_o(oup_valid_o),
        .data_o(oup_data_o),
        .idx_o()
    );

endmodule
