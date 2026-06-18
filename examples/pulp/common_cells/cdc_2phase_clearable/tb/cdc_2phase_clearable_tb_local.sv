module cdc_2phase_clearable_tb_local;

    logic       src_rst_ni;
    logic       src_clk_i;
    logic       src_clear_i;
    logic       src_clear_pending_o;
    logic [7:0] src_data_i;
    logic       src_valid_i;
    logic       src_ready_o;

    logic       dst_rst_ni;
    logic       dst_clk_i;
    logic       dst_clear_i;
    logic       dst_clear_pending_o;
    logic [7:0] dst_data_o;
    logic       dst_valid_o;
    logic       dst_ready_i;

    cdc_2phase_clearable #(
        .CLEAR_ON_ASYNC_RESET(1'b0)
    ) dut (
        .src_rst_ni(src_rst_ni),
        .src_clk_i(src_clk_i),
        .src_clear_i(src_clear_i),
        .src_clear_pending_o(src_clear_pending_o),
        .src_data_i(src_data_i),
        .src_valid_i(src_valid_i),
        .src_ready_o(src_ready_o),
        .dst_rst_ni(dst_rst_ni),
        .dst_clk_i(dst_clk_i),
        .dst_clear_i(dst_clear_i),
        .dst_clear_pending_o(dst_clear_pending_o),
        .dst_data_o(dst_data_o),
        .dst_valid_o(dst_valid_o),
        .dst_ready_i(dst_ready_i)
    );

endmodule

module cdc_2phase_clearable_async_reset_tb_local;

    logic       src_rst_ni;
    logic       src_clk_i;
    logic       src_clear_i;
    logic       src_clear_pending_o;
    logic [7:0] src_data_i;
    logic       src_valid_i;
    logic       src_ready_o;

    logic       dst_rst_ni;
    logic       dst_clk_i;
    logic       dst_clear_i;
    logic       dst_clear_pending_o;
    logic [7:0] dst_data_o;
    logic       dst_valid_o;
    logic       dst_ready_i;

    cdc_2phase_clearable #(
        .CLEAR_ON_ASYNC_RESET(1'b1)
    ) dut (
        .src_rst_ni(src_rst_ni),
        .src_clk_i(src_clk_i),
        .src_clear_i(src_clear_i),
        .src_clear_pending_o(src_clear_pending_o),
        .src_data_i(src_data_i),
        .src_valid_i(src_valid_i),
        .src_ready_o(src_ready_o),
        .dst_rst_ni(dst_rst_ni),
        .dst_clk_i(dst_clk_i),
        .dst_clear_i(dst_clear_i),
        .dst_clear_pending_o(dst_clear_pending_o),
        .dst_data_o(dst_data_o),
        .dst_valid_o(dst_valid_o),
        .dst_ready_i(dst_ready_i)
    );

endmodule
