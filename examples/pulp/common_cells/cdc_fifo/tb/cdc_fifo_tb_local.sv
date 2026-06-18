module cdc_fifo_tb_local;

    localparam int WIDTH = 8;
    localparam int LogDepth = 1;

    logic             src_rst_ni;
    logic             src_clk_i;
    logic [WIDTH-1:0] src_data_i;
    logic             src_valid_i;
    logic             src_ready_o;

    logic             dst_rst_ni;
    logic             dst_clk_i;
    logic [WIDTH-1:0] dst_data_o;
    logic             dst_valid_o;
    logic             dst_ready_i;

    cdc_fifo_2phase #(
        .WIDTH(WIDTH),
        .LOG_DEPTH(LogDepth)
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
