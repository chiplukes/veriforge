// Expose data, selector mask, and each output as ready/valid streams.
module stream_fork_dynamic_bench_top (
    input  logic       clk_i,
    input  logic       rst_ni,
    input  logic [7:0] in_data_i,
    input  logic       in_valid_i,
    output logic       in_ready_o,
    input  logic [2:0] mask_data_i,
    input  logic       mask_valid_i,
    output logic       mask_ready_o,
    output logic [7:0] out0_data_o,
    output logic       out0_valid_o,
    input  logic       out0_ready_i,
    output logic [7:0] out1_data_o,
    output logic       out1_valid_o,
    input  logic       out1_ready_i,
    output logic [7:0] out2_data_o,
    output logic       out2_valid_o,
    input  logic       out2_ready_i
);
    assign out0_data_o = in_data_i;
    assign out1_data_o = in_data_i;
    assign out2_data_o = in_data_i;

    stream_fork_dynamic #(.N_OUP(3)) dut (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .valid_i(in_valid_i),
        .ready_o(in_ready_o),
        .sel_i(mask_data_i),
        .sel_valid_i(mask_valid_i),
        .sel_ready_o(mask_ready_o),
        .valid_o({out2_valid_o, out1_valid_o, out0_valid_o}),
        .ready_i({out2_ready_i, out1_ready_i, out0_ready_i})
    );
endmodule
