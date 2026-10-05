// Expose the packed lanes of stream_mux as independent stream interfaces.
module stream_mux_bench_top (
    input  logic [7:0] in0_data_i,
    input  logic       in0_valid_i,
    output logic       in0_ready_o,
    input  logic [7:0] in1_data_i,
    input  logic       in1_valid_i,
    output logic       in1_ready_o,
    input  logic [7:0] in2_data_i,
    input  logic       in2_valid_i,
    output logic       in2_ready_o,
    input  logic [1:0] select_i,
    output logic [7:0] out_data_o,
    output logic       out_valid_o,
    input  logic       out_ready_i
);
    stream_mux dut (
        .inp_data_i({in2_data_i, in1_data_i, in0_data_i}),
        .inp_valid_i({in2_valid_i, in1_valid_i, in0_valid_i}),
        .inp_ready_o({in2_ready_o, in1_ready_o, in0_ready_o}),
        .inp_sel_i(select_i),
        .oup_data_o(out_data_o),
        .oup_valid_o(out_valid_o),
        .oup_ready_i(out_ready_i)
    );
endmodule
