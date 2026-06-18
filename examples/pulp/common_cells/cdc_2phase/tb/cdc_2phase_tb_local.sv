module cdc_2phase_tb_local (
    input  logic       src_rst_ni,
    input  logic       src_clk_i,
    input  logic [7:0] src_data_i,
    input  logic       src_valid_i,
    output logic       src_ready_o,
    input  logic       dst_rst_ni,
    input  logic       dst_clk_i,
    output logic [7:0] dst_data_o,
    output logic       dst_valid_o,
    input  logic       dst_ready_i
);

    cdc_2phase #(
        .WIDTH(8)
    ) dut (
        .src_rst_ni(src_rst_ni),
        .src_clk_i(src_clk_i),
        .src_data_i(src_data_i),
        .src_valid_i(src_valid_i),
        .src_ready_o(src_ready_o),
        .dst_rst_ni(dst_rst_ni),
        .dst_clk_i(dst_clk_i),
        .dst_data_o(dst_data_o),
        .dst_valid_o(dst_valid_o),
        .dst_ready_i(dst_ready_i)
    );

endmodule
