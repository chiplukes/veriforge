module cdc_2phase_clearable #(
    parameter int unsigned SYNC_STAGES = 3,
    parameter bit CLEAR_ON_ASYNC_RESET = 1'b0
) (
    input  logic       src_rst_ni,
    input  logic       src_clk_i,
    input  logic       src_clear_i,
    output logic       src_clear_pending_o,
    input  logic [7:0] src_data_i,
    input  logic       src_valid_i,
    output logic       src_ready_o,

    input  logic       dst_rst_ni,
    input  logic       dst_clk_i,
    input  logic       dst_clear_i,
    output logic       dst_clear_pending_o,
    output logic [7:0] dst_data_o,
    output logic       dst_valid_o,
    input  logic       dst_ready_i
);

    logic       s_src_clear_req;
    logic       s_src_clear_ack_q;
    logic       s_src_ready;
    logic       s_src_isolate_req;
    logic       s_src_isolate_ack_q;
    logic       s_dst_clear_req;
    logic       s_dst_clear_ack_q;
    logic       s_dst_valid;
    logic       s_dst_isolate_req;
    logic       s_dst_isolate_ack_q;

    logic       async_req;
    logic       async_ack;
    logic [7:0] async_data;

    cdc_2phase_src_clearable #(
        .SYNC_STAGES(SYNC_STAGES)
    ) i_src (
        .rst_ni(src_rst_ni),
        .clk_i(src_clk_i),
        .clear_i(s_src_clear_req),
        .data_i(src_data_i),
        .valid_i(src_valid_i && !s_src_isolate_req),
        .ready_o(s_src_ready),
        .async_req_o(async_req),
        .async_ack_i(async_ack),
        .async_data_o(async_data)
    );

    assign src_ready_o = s_src_ready && !s_src_isolate_req;

    cdc_2phase_dst_clearable #(
        .SYNC_STAGES(SYNC_STAGES)
    ) i_dst (
        .rst_ni(dst_rst_ni),
        .clk_i(dst_clk_i),
        .clear_i(s_dst_clear_req),
        .data_o(dst_data_o),
        .valid_o(s_dst_valid),
        .ready_i(dst_ready_i && !s_dst_isolate_req),
        .async_req_i(async_req),
        .async_ack_o(async_ack),
        .async_data_i(async_data)
    );

    assign dst_valid_o = s_dst_valid && !s_dst_isolate_req;

    cdc_reset_ctrlr #(
        .SYNC_STAGES(SYNC_STAGES - 1),
        .CLEAR_ON_ASYNC_RESET(CLEAR_ON_ASYNC_RESET)
    ) i_cdc_reset_ctrlr (
        .a_clk_i(src_clk_i),
        .a_rst_ni(src_rst_ni),
        .a_clear_i(src_clear_i),
        .a_clear_o(s_src_clear_req),
        .a_clear_ack_i(s_src_clear_ack_q),
        .a_isolate_o(s_src_isolate_req),
        .a_isolate_ack_i(s_src_isolate_ack_q),
        .b_clk_i(dst_clk_i),
        .b_rst_ni(dst_rst_ni),
        .b_clear_i(dst_clear_i),
        .b_clear_o(s_dst_clear_req),
        .b_clear_ack_i(s_dst_clear_ack_q),
        .b_isolate_o(s_dst_isolate_req),
        .b_isolate_ack_i(s_dst_isolate_ack_q)
    );

    always @(posedge src_clk_i or negedge src_rst_ni) begin
        if (!src_rst_ni) begin
            s_src_isolate_ack_q <= 1'b0;
            s_src_clear_ack_q <= 1'b0;
        end else begin
            s_src_isolate_ack_q <= s_src_isolate_req;
            s_src_clear_ack_q <= s_src_clear_req;
        end
    end

    always @(posedge dst_clk_i or negedge dst_rst_ni) begin
        if (!dst_rst_ni) begin
            s_dst_isolate_ack_q <= 1'b0;
            s_dst_clear_ack_q <= 1'b0;
        end else begin
            s_dst_isolate_ack_q <= s_dst_isolate_req;
            s_dst_clear_ack_q <= s_dst_clear_req;
        end
    end

    assign src_clear_pending_o = s_src_isolate_req;
    assign dst_clear_pending_o = s_dst_isolate_req;

endmodule

module cdc_2phase_src_clearable #(
    parameter int unsigned SYNC_STAGES = 3
) (
    input  logic       rst_ni,
    input  logic       clk_i,
    input  logic       clear_i,
    input  logic [7:0] data_i,
    input  logic       valid_i,
    output logic       ready_o,
    output logic       async_req_o,
    input  logic       async_ack_i,
    output logic [7:0] async_data_o
);

    logic req_src_d;
    logic req_src_q;
    logic ack_synced;
    logic [7:0] data_src_d;
    logic [7:0] data_src_q;

    sync #(
        .STAGES(SYNC_STAGES)
    ) i_sync (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .serial_i(async_ack_i),
        .serial_o(ack_synced)
    );

    always_comb begin
        data_src_d = data_src_q;
        req_src_d = req_src_q;
        if (clear_i) begin
            req_src_d = 1'b0;
        end else if (valid_i && ready_o) begin
            req_src_d = ~req_src_q;
            data_src_d = data_i;
        end
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            data_src_q <= '0;
            req_src_q <= 1'b0;
        end else begin
            data_src_q <= data_src_d;
            req_src_q <= req_src_d;
        end
    end

    assign ready_o = req_src_q == ack_synced;
    assign async_req_o = req_src_q;
    assign async_data_o = data_src_q;

endmodule

module cdc_2phase_dst_clearable #(
    parameter int unsigned SYNC_STAGES = 3
) (
    input  logic       rst_ni,
    input  logic       clk_i,
    input  logic       clear_i,
    output logic [7:0] data_o,
    output logic       valid_o,
    input  logic       ready_i,
    input  logic       async_req_i,
    output logic       async_ack_o,
    input  logic [7:0] async_data_i
);

    logic ack_dst_d;
    logic ack_dst_q;
    logic req_synced;
    logic req_synced_q1;
    logic [7:0] data_dst_d;
    logic [7:0] data_dst_q;

    sync #(
        .STAGES(SYNC_STAGES)
    ) i_sync (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .serial_i(async_req_i),
        .serial_o(req_synced)
    );

    always_comb begin
        ack_dst_d = ack_dst_q;
        if (clear_i) begin
            ack_dst_d = 1'b0;
        end else if (valid_o && ready_i) begin
            ack_dst_d = req_synced_q1;
        end
    end

    always_comb begin
        data_dst_d = data_dst_q;
        if (clear_i) begin
            data_dst_d = '0;
        end else if ((req_synced != req_synced_q1) && !valid_o) begin
            data_dst_d = async_data_i;
        end
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            ack_dst_q <= 1'b0;
            req_synced_q1 <= 1'b0;
            data_dst_q <= '0;
        end else begin
            ack_dst_q <= ack_dst_d;
            req_synced_q1 <= req_synced;
            data_dst_q <= data_dst_d;
        end
    end

    assign valid_o = ack_dst_q != req_synced_q1;
    assign data_o = data_dst_q;
    assign async_ack_o = ack_dst_q;

endmodule
