module isochronous_4phase_handshake_tb_local;

    logic src_clk_i;
    logic src_rst_ni;
    logic src_valid_i;
    logic src_ready_o;

    logic dst_clk_i;
    logic dst_rst_ni;
    logic dst_valid_o;
    logic dst_ready_i;

    isochronous_4phase_handshake dut (
        .src_clk_i(src_clk_i),
        .src_rst_ni(src_rst_ni),
        .src_valid_i(src_valid_i),
        .src_ready_o(src_ready_o),
        .dst_clk_i(dst_clk_i),
        .dst_rst_ni(dst_rst_ni),
        .dst_valid_o(dst_valid_o),
        .dst_ready_i(dst_ready_i)
    );

endmodule
