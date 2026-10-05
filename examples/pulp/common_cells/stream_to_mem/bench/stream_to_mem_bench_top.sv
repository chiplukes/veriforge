// Give the request, memory request, and response paths stream-style names.
module stream_to_mem_bench_top (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic [15:0] req_data_i,
    input  logic        req_valid_i,
    output logic        req_ready_o,
    output logic [15:0] resp_data_o,
    output logic        resp_valid_o,
    input  logic        resp_ready_i,
    output logic [15:0] mem_req_data_o,
    output logic        mem_req_valid_o,
    input  logic        mem_req_ready_i,
    input  logic [15:0] mem_resp_data_i,
    input  logic        mem_resp_valid_i
);
    stream_to_mem #(.DataWidth(16), .BufDepth(2)) dut (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .req_i(req_data_i),
        .req_valid_i(req_valid_i),
        .req_ready_o(req_ready_o),
        .resp_o(resp_data_o),
        .resp_valid_o(resp_valid_o),
        .resp_ready_i(resp_ready_i),
        .mem_req_o(mem_req_data_o),
        .mem_req_valid_o(mem_req_valid_o),
        .mem_req_ready_i(mem_req_ready_i),
        .mem_resp_i(mem_resp_data_i),
        .mem_resp_valid_i(mem_resp_valid_i)
    );
endmodule
