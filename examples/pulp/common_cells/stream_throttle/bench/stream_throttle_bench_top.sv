// Expose request handshakes as streams; response completion remains a pulse.
module stream_throttle_bench_top (
    input  logic       clk_i,
    input  logic       rst_ni,
    input  logic [7:0] in_data_i,
    input  logic       in_valid_i,
    output logic       in_ready_o,
    output logic [7:0] out_data_o,
    output logic       out_valid_o,
    input  logic       out_ready_i,
    input  logic       rsp_valid_i,
    input  logic       rsp_ready_i,
    input  logic [1:0] credit_i
);
    assign out_data_o = in_data_i;

    stream_throttle dut (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .req_valid_i(in_valid_i),
        .req_ready_o(in_ready_o),
        .req_valid_o(out_valid_o),
        .req_ready_i(out_ready_i),
        .rsp_valid_i(rsp_valid_i),
        .rsp_ready_i(rsp_ready_i),
        .credit_i(credit_i)
    );
endmodule
