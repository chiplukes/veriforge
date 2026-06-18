module stream_throttle_tb_local;

    logic       clk;
    logic       rst_n;
    logic       req_valid_i;
    logic       req_valid_o;
    logic       req_ready_i;
    logic       req_ready_o;
    logic       rsp_valid_i;
    logic       rsp_ready_i;
    logic [1:0] credit_i;

    stream_throttle #(
        .MAX_NUM_PENDING(3)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .req_valid_i(req_valid_i),
        .req_valid_o(req_valid_o),
        .req_ready_i(req_ready_i),
        .req_ready_o(req_ready_o),
        .rsp_valid_i(rsp_valid_i),
        .rsp_ready_i(rsp_ready_i),
        .credit_i(credit_i)
    );

endmodule
