// Expose packed lanes as streams with input select and output index sidebands.
module stream_xbar_bench_top (
    input  logic       clk_i,
    input  logic       rst_ni,
    input  logic       flush_i,
    input  logic [7:0] in0_data_i,
    input  logic       in0_sel_i,
    input  logic       in0_valid_i,
    output logic       in0_ready_o,
    input  logic [7:0] in1_data_i,
    input  logic       in1_sel_i,
    input  logic       in1_valid_i,
    output logic       in1_ready_o,
    input  logic [7:0] in2_data_i,
    input  logic       in2_sel_i,
    input  logic       in2_valid_i,
    output logic       in2_ready_o,
    output logic [7:0] out0_data_o,
    output logic [1:0] out0_idx_o,
    output logic       out0_valid_o,
    input  logic       out0_ready_i,
    output logic [7:0] out1_data_o,
    output logic [1:0] out1_idx_o,
    output logic       out1_valid_o,
    input  logic       out1_ready_i
);
    stream_xbar #(.OutSpillReg(1'b0)) dut (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .flush_i(flush_i),
        .data_i({in2_data_i, in1_data_i, in0_data_i}),
        .sel_i({in2_sel_i, in1_sel_i, in0_sel_i}),
        .valid_i({in2_valid_i, in1_valid_i, in0_valid_i}),
        .ready_o({in2_ready_o, in1_ready_o, in0_ready_o}),
        .data_o({out1_data_o, out0_data_o}),
        .idx_o({out1_idx_o, out0_idx_o}),
        .valid_o({out1_valid_o, out0_valid_o}),
        .ready_i({out1_ready_i, out0_ready_i})
    );
endmodule
