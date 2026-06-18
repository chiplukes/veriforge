module cdc_fifo_gray (
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

    logic [7:0] fifo_data_q [2];
    logic [1:0] src_wptr_q;
    logic [1:0] dst_rptr_q;
    logic [1:0] src_rptr_sync;
    logic [1:0] dst_wptr_sync;
    logic [1:0] src_wptr_bin;
    logic [1:0] dst_rptr_bin;
    logic [1:0] src_rptr_bin;
    logic [1:0] dst_wptr_bin;
    logic [1:0] src_wptr_next;
    logic [1:0] dst_rptr_next;
    logic [1:0] src_wptr_gray_next;
    logic [1:0] dst_rptr_gray_next;
    logic [7:0] dst_data_pre_spill;
    logic dst_valid_pre_spill;
    logic dst_ready_pre_spill;

    gray_to_binary i_src_wptr_g2b (
        .A(src_wptr_q),
        .Z(src_wptr_bin)
    );

    gray_to_binary i_dst_rptr_g2b (
        .A(dst_rptr_q),
        .Z(dst_rptr_bin)
    );

    gray_to_binary i_src_rptr_g2b (
        .A(src_rptr_sync),
        .Z(src_rptr_bin)
    );

    gray_to_binary i_dst_wptr_g2b (
        .A(dst_wptr_sync),
        .Z(dst_wptr_bin)
    );

    assign src_wptr_next = src_wptr_bin + 2'b01;
    assign dst_rptr_next = dst_rptr_bin + 2'b01;

    binary_to_gray #(
        .N(2)
    ) i_src_wptr_b2g (
        .A(src_wptr_next),
        .Z(src_wptr_gray_next)
    );

    binary_to_gray #(
        .N(2)
    ) i_dst_rptr_b2g (
        .A(dst_rptr_next),
        .Z(dst_rptr_gray_next)
    );

    sync i_sync_src_rptr_0 (
        .clk_i(src_clk_i),
        .rst_ni(src_rst_ni),
        .serial_i(dst_rptr_q[0]),
        .serial_o(src_rptr_sync[0])
    );

    sync i_sync_src_rptr_1 (
        .clk_i(src_clk_i),
        .rst_ni(src_rst_ni),
        .serial_i(dst_rptr_q[1]),
        .serial_o(src_rptr_sync[1])
    );

    sync i_sync_dst_wptr_0 (
        .clk_i(dst_clk_i),
        .rst_ni(dst_rst_ni),
        .serial_i(src_wptr_q[0]),
        .serial_o(dst_wptr_sync[0])
    );

    sync i_sync_dst_wptr_1 (
        .clk_i(dst_clk_i),
        .rst_ni(dst_rst_ni),
        .serial_i(src_wptr_q[1]),
        .serial_o(dst_wptr_sync[1])
    );

    assign src_ready_o = (src_wptr_bin ^ src_rptr_bin) != 2'b10;
    assign dst_valid_pre_spill = (dst_rptr_bin ^ dst_wptr_bin) != 2'b00;
    assign dst_data_pre_spill = fifo_data_q[dst_rptr_bin[0]];

    always @(posedge src_clk_i or negedge src_rst_ni) begin
        if (!src_rst_ni) begin
            fifo_data_q[0] <= '0;
            fifo_data_q[1] <= '0;
            src_wptr_q <= 2'b00;
        end else if (src_valid_i && src_ready_o) begin
            if (src_wptr_bin[0]) begin
                fifo_data_q[1] <= src_data_i;
            end else begin
                fifo_data_q[0] <= src_data_i;
            end
            src_wptr_q <= src_wptr_gray_next;
        end
    end

    always @(posedge dst_clk_i or negedge dst_rst_ni) begin
        if (!dst_rst_ni) begin
            dst_rptr_q <= 2'b00;
        end else if (dst_valid_pre_spill && dst_ready_pre_spill) begin
            dst_rptr_q <= dst_rptr_gray_next;
        end
    end

    spill_register #(
        .DATA_WIDTH(8)
    ) i_spill_register (
        .clk_i(dst_clk_i),
        .rst_ni(dst_rst_ni),
        .valid_i(dst_valid_pre_spill),
        .ready_o(dst_ready_pre_spill),
        .data_i(dst_data_pre_spill),
        .valid_o(dst_valid_o),
        .ready_i(dst_ready_i),
        .data_o(dst_data_o)
    );

endmodule
