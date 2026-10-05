// Expose packed request lanes separately while retaining the flush control.
module stream_arbiter_flushable_bench_top (
    input  logic       clk_i,
    input  logic       rst_ni,
    input  logic       flush_i,
    input  logic [7:0] in0_data_i,
    input  logic       in0_valid_i,
    output logic       in0_ready_o,
    input  logic [7:0] in1_data_i,
    input  logic       in1_valid_i,
    output logic       in1_ready_o,
    input  logic [7:0] in2_data_i,
    input  logic       in2_valid_i,
    output logic       in2_ready_o,
    input  logic [7:0] in3_data_i,
    input  logic       in3_valid_i,
    output logic       in3_ready_o,
    output logic [7:0] out_data_o,
    output logic       out_valid_o,
    input  logic       out_ready_i
);
    stream_arbiter_flushable dut (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .flush_i(flush_i),
        .inp_data_i({in3_data_i, in2_data_i, in1_data_i, in0_data_i}),
        .inp_valid_i({in3_valid_i, in2_valid_i, in1_valid_i, in0_valid_i}),
        .inp_ready_o({in3_ready_o, in2_ready_o, in1_ready_o, in0_ready_o}),
        .oup_data_o(out_data_o),
        .oup_valid_o(out_valid_o),
        .oup_ready_i(out_ready_i)
    );
endmodule
