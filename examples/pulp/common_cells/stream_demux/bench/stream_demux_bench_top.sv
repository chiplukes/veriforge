// Expose each demux output as a stream. Payload follows the selected handshake.
module stream_demux_bench_top (
    input  logic [7:0] in_data_i,
    input  logic       in_valid_i,
    output logic       in_ready_o,
    input  logic [1:0] select_i,
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

    stream_demux #(.N_OUP(3)) dut (
        .inp_valid_i(in_valid_i),
        .inp_ready_o(in_ready_o),
        .oup_sel_i(select_i),
        .oup_valid_o({out2_valid_o, out1_valid_o, out0_valid_o}),
        .oup_ready_i({out2_ready_i, out1_ready_i, out0_ready_i})
    );
endmodule
