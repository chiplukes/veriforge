// Expose the packed handshake lanes as independent, data-less streams.
module stream_join_bench_top (
    input  logic in0_valid_i,
    output logic in0_ready_o,
    input  logic in1_valid_i,
    output logic in1_ready_o,
    input  logic in2_valid_i,
    output logic in2_ready_o,
    output logic out_valid_o,
    input  logic out_ready_i
);
    stream_join #(.N_INP(3)) dut (
        .inp_valid_i({in2_valid_i, in1_valid_i, in0_valid_i}),
        .inp_ready_o({in2_ready_o, in1_ready_o, in0_ready_o}),
        .oup_valid_o(out_valid_o),
        .oup_ready_i(out_ready_i)
    );
endmodule
