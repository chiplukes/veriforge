module isochronous_spill_register_tb_local (
    input  logic       src_clk_i,
    input  logic       src_rst_ni,
    input  logic       src_valid_i,
    output logic       src_ready_o,
    input  logic [7:0] src_data_i,
    input  logic       dst_clk_i,
    input  logic       dst_rst_ni,
    output logic       dst_valid_o,
    input  logic       dst_ready_i,
    output logic [7:0] dst_data_o
);

    isochronous_spill_register #(
        .DATA_WIDTH(8),
        .Bypass(1'b0)
    ) dut (
        .src_clk_i(src_clk_i),
        .src_rst_ni(src_rst_ni),
        .src_valid_i(src_valid_i),
        .src_ready_o(src_ready_o),
        .src_data_i(src_data_i),
        .dst_clk_i(dst_clk_i),
        .dst_rst_ni(dst_rst_ni),
        .dst_valid_o(dst_valid_o),
        .dst_ready_i(dst_ready_i),
        .dst_data_o(dst_data_o)
    );

endmodule

module isochronous_spill_register_bypass_tb_local (
    input  logic       src_clk_i,
    input  logic       src_rst_ni,
    input  logic       src_valid_i,
    output logic       src_ready_o,
    input  logic [7:0] src_data_i,
    input  logic       dst_clk_i,
    input  logic       dst_rst_ni,
    output logic       dst_valid_o,
    input  logic       dst_ready_i,
    output logic [7:0] dst_data_o
);

    isochronous_spill_register #(
        .DATA_WIDTH(8),
        .Bypass(1'b1)
    ) dut (
        .src_clk_i(src_clk_i),
        .src_rst_ni(src_rst_ni),
        .src_valid_i(src_valid_i),
        .src_ready_o(src_ready_o),
        .src_data_i(src_data_i),
        .dst_clk_i(dst_clk_i),
        .dst_rst_ni(dst_rst_ni),
        .dst_valid_o(dst_valid_o),
        .dst_ready_i(dst_ready_i),
        .dst_data_o(dst_data_o)
    );

endmodule
