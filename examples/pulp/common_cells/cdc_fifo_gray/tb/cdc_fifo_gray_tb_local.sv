module cdc_fifo_gray_tb_local;

    logic       src_rst_ni;
    logic       src_clk_i;
    logic [7:0] src_data_i;
    logic       src_valid_i;
    logic       src_ready_o;

    logic       dst_rst_ni;
    logic       dst_clk_i;
    logic [7:0] dst_data_o;
    logic       dst_valid_o;
    logic       dst_ready_i;

    cdc_fifo_gray dut (
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
