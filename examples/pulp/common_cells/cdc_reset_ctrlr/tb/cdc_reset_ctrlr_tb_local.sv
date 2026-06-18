module cdc_reset_ctrlr_tb_local;

    logic a_clk_i;
    logic a_rst_ni;
    logic a_clear_i;
    logic a_clear_o;
    logic a_clear_ack_i;
    logic a_isolate_o;
    logic a_isolate_ack_i;

    logic b_clk_i;
    logic b_rst_ni;
    logic b_clear_i;
    logic b_clear_o;
    logic b_clear_ack_i;
    logic b_isolate_o;
    logic b_isolate_ack_i;

    cdc_reset_ctrlr #(
        .CLEAR_ON_ASYNC_RESET(1'b0)
    ) dut (
        .a_clk_i(a_clk_i),
        .a_rst_ni(a_rst_ni),
        .a_clear_i(a_clear_i),
        .a_clear_o(a_clear_o),
        .a_clear_ack_i(a_clear_ack_i),
        .a_isolate_o(a_isolate_o),
        .a_isolate_ack_i(a_isolate_ack_i),
        .b_clk_i(b_clk_i),
        .b_rst_ni(b_rst_ni),
        .b_clear_i(b_clear_i),
        .b_clear_o(b_clear_o),
        .b_clear_ack_i(b_clear_ack_i),
        .b_isolate_o(b_isolate_o),
        .b_isolate_ack_i(b_isolate_ack_i)
    );

endmodule

module cdc_reset_ctrlr_async_reset_tb_local;

    logic a_clk_i;
    logic a_rst_ni;
    logic a_clear_i;
    logic a_clear_o;
    logic a_clear_ack_i;
    logic a_isolate_o;
    logic a_isolate_ack_i;

    logic b_clk_i;
    logic b_rst_ni;
    logic b_clear_i;
    logic b_clear_o;
    logic b_clear_ack_i;
    logic b_isolate_o;
    logic b_isolate_ack_i;

    cdc_reset_ctrlr #(
        .CLEAR_ON_ASYNC_RESET(1'b1)
    ) dut (
        .a_clk_i(a_clk_i),
        .a_rst_ni(a_rst_ni),
        .a_clear_i(a_clear_i),
        .a_clear_o(a_clear_o),
        .a_clear_ack_i(a_clear_ack_i),
        .a_isolate_o(a_isolate_o),
        .a_isolate_ack_i(a_isolate_ack_i),
        .b_clk_i(b_clk_i),
        .b_rst_ni(b_rst_ni),
        .b_clear_i(b_clear_i),
        .b_clear_o(b_clear_o),
        .b_clear_ack_i(b_clear_ack_i),
        .b_isolate_o(b_isolate_o),
        .b_isolate_ack_i(b_isolate_ack_i)
    );

endmodule
