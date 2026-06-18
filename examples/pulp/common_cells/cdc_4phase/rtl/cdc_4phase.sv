module cdc_4phase #(
    parameter int unsigned SYNC_STAGES = 2,
    parameter bit DECOUPLED = 1'b1,
    parameter bit SEND_RESET_MSG = 1'b0,
    parameter logic [7:0] RESET_MSG = 8'h00
) (
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

    logic async_req;
    logic async_ack;
    logic [7:0] async_data;

    cdc_4phase_src #(
        .SYNC_STAGES(SYNC_STAGES),
        .DECOUPLED(DECOUPLED),
        .SEND_RESET_MSG(SEND_RESET_MSG),
        .RESET_MSG(RESET_MSG)
    ) i_src (
        .rst_ni(src_rst_ni),
        .clk_i(src_clk_i),
        .data_i(src_data_i),
        .valid_i(src_valid_i),
        .ready_o(src_ready_o),
        .async_req_o(async_req),
        .async_ack_i(async_ack),
        .async_data_o(async_data)
    );

    cdc_4phase_dst #(
        .SYNC_STAGES(SYNC_STAGES),
        .DECOUPLED(DECOUPLED)
    ) i_dst (
        .rst_ni(dst_rst_ni),
        .clk_i(dst_clk_i),
        .data_o(dst_data_o),
        .valid_o(dst_valid_o),
        .ready_i(dst_ready_i),
        .async_req_i(async_req),
        .async_ack_o(async_ack),
        .async_data_i(async_data)
    );

endmodule

module cdc_4phase_src #(
    parameter int unsigned SYNC_STAGES = 2,
    parameter bit DECOUPLED = 1'b1,
    parameter bit SEND_RESET_MSG = 1'b0,
    parameter logic [7:0] RESET_MSG = 8'h00
) (
    input  logic       rst_ni,
    input  logic       clk_i,
    input  logic [7:0] data_i,
    input  logic       valid_i,
    output logic       ready_o,
    output logic       async_req_o,
    input  logic       async_ack_i,
    output logic [7:0] async_data_o
);

    logic req_src_q;
    logic [7:0] data_src_q;
    logic src_busy_q;
    logic ack_synced;

    sync #(
        .STAGES(SYNC_STAGES)
    ) i_sync (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .serial_i(async_ack_i),
        .serial_o(ack_synced)
    );

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            if (SEND_RESET_MSG) begin
                req_src_q <= 1'b1;
                data_src_q <= RESET_MSG;
                src_busy_q <= 1'b1;
            end else begin
                req_src_q <= 1'b0;
                data_src_q <= '0;
                src_busy_q <= 1'b0;
            end
        end else if (src_busy_q) begin
            if (req_src_q && ack_synced) begin
                req_src_q <= 1'b0;
            end else if (!req_src_q && !ack_synced) begin
                src_busy_q <= 1'b0;
            end
        end else if (valid_i) begin
            req_src_q <= 1'b1;
            data_src_q <= data_i;
            src_busy_q <= 1'b1;
        end
    end

    always_comb begin
        ready_o = !src_busy_q;
        if (!DECOUPLED && src_busy_q) begin
            ready_o = 1'b0;
        end
    end

    assign async_req_o = req_src_q;
    assign async_data_o = data_src_q;

endmodule

module cdc_4phase_dst #(
    parameter int unsigned SYNC_STAGES = 2,
    parameter bit DECOUPLED = 1'b1
) (
    input  logic       rst_ni,
    input  logic       clk_i,
    output logic [7:0] data_o,
    output logic       valid_o,
    input  logic       ready_i,
    input  logic       async_req_i,
    output logic       async_ack_o,
    input  logic [7:0] async_data_i
);

    logic req_synced;
    logic req_synced_q;
    logic req_edge;

    sync #(
        .STAGES(SYNC_STAGES)
    ) i_sync (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .serial_i(async_req_i),
        .serial_o(req_synced)
    );

    assign req_edge = req_synced && !req_synced_q;

    if (DECOUPLED) begin : gen_decoupled
        logic ack_hold_q;

        always @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                req_synced_q <= 1'b0;
                ack_hold_q <= 1'b0;
            end else begin
                req_synced_q <= req_synced;
                if (!req_synced) begin
                    ack_hold_q <= 1'b0;
                end else if (req_edge) begin
                    ack_hold_q <= 1'b1;
                end
            end
        end

        spill_register #(
            .DATA_WIDTH(8),
            .Bypass(1'b0)
        ) i_spill_register (
            .clk_i(clk_i),
            .rst_ni(rst_ni),
            .valid_i(req_edge),
            .ready_o(),
            .data_i(async_data_i),
            .valid_o(valid_o),
            .ready_i(ready_i),
            .data_o(data_o)
        );

        assign async_ack_o = ack_hold_q;
    end else begin : gen_not_decoupled
        logic [7:0] data_q;
        logic valid_q;
        logic ack_hold_q;

        always @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                req_synced_q <= 1'b0;
                data_q <= '0;
                valid_q <= 1'b0;
                ack_hold_q <= 1'b0;
            end else begin
                req_synced_q <= req_synced;
                if (!req_synced) begin
                    ack_hold_q <= 1'b0;
                end
                if (req_edge) begin
                    data_q <= async_data_i;
                    valid_q <= 1'b1;
                end else if (valid_q && ready_i) begin
                    valid_q <= 1'b0;
                    ack_hold_q <= 1'b1;
                end
            end
        end

        assign valid_o = valid_q;
        assign data_o = data_q;
        assign async_ack_o = ack_hold_q;
    end

endmodule
