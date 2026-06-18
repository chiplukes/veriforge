module cdc_reset_ctrlr #(
    parameter int unsigned SYNC_STAGES = 2,
    parameter bit CLEAR_ON_ASYNC_RESET = 1'b0
) (
    input  logic a_clk_i,
    input  logic a_rst_ni,
    input  logic a_clear_i,
    output logic a_clear_o,
    input  logic a_clear_ack_i,
    output logic a_isolate_o,
    input  logic a_isolate_ack_i,

    input  logic b_clk_i,
    input  logic b_rst_ni,
    input  logic b_clear_i,
    output logic b_clear_o,
    input  logic b_clear_ack_i,
    output logic b_isolate_o,
    input  logic b_isolate_ack_i
);

    logic async_a2b_req;
    logic async_b2a_ack;
    logic [7:0] async_a2b_next_phase;
    logic async_b2a_req;
    logic async_a2b_ack;
    logic [7:0] async_b2a_next_phase;

    cdc_reset_ctrlr_half #(
        .SYNC_STAGES(SYNC_STAGES),
        .CLEAR_ON_ASYNC_RESET(CLEAR_ON_ASYNC_RESET)
    ) i_cdc_reset_ctrlr_half_a (
        .clk_i(a_clk_i),
        .rst_ni(a_rst_ni),
        .clear_i(a_clear_i),
        .clear_o(a_clear_o),
        .clear_ack_i(a_clear_ack_i),
        .isolate_o(a_isolate_o),
        .isolate_ack_i(a_isolate_ack_i),
        .async_next_phase_o(async_a2b_next_phase),
        .async_req_o(async_a2b_req),
        .async_ack_i(async_b2a_ack),
        .async_next_phase_i(async_b2a_next_phase),
        .async_req_i(async_b2a_req),
        .async_ack_o(async_a2b_ack)
    );

    cdc_reset_ctrlr_half #(
        .SYNC_STAGES(SYNC_STAGES),
        .CLEAR_ON_ASYNC_RESET(CLEAR_ON_ASYNC_RESET)
    ) i_cdc_reset_ctrlr_half_b (
        .clk_i(b_clk_i),
        .rst_ni(b_rst_ni),
        .clear_i(b_clear_i),
        .clear_o(b_clear_o),
        .clear_ack_i(b_clear_ack_i),
        .isolate_o(b_isolate_o),
        .isolate_ack_i(b_isolate_ack_i),
        .async_next_phase_o(async_b2a_next_phase),
        .async_req_o(async_b2a_req),
        .async_ack_i(async_a2b_ack),
        .async_next_phase_i(async_a2b_next_phase),
        .async_req_i(async_a2b_req),
        .async_ack_o(async_b2a_ack)
    );

endmodule

module cdc_reset_ctrlr_half #(
    parameter int unsigned SYNC_STAGES = 2,
    parameter bit CLEAR_ON_ASYNC_RESET = 1'b0
) (
    input  logic       clk_i,
    input  logic       rst_ni,
    input  logic       clear_i,
    output logic       isolate_o,
    input  logic       isolate_ack_i,
    output logic       clear_o,
    input  logic       clear_ack_i,
    output logic [7:0] async_next_phase_o,
    output logic       async_req_o,
    input  logic       async_ack_i,
    input  logic [7:0] async_next_phase_i,
    input  logic       async_req_i,
    output logic       async_ack_o
);

    localparam logic [7:0] CLEAR_PHASE_IDLE = 8'h00;
    localparam logic [7:0] CLEAR_PHASE_ISOLATE = 8'h01;
    localparam logic [7:0] CLEAR_PHASE_CLEAR = 8'h02;
    localparam logic [7:0] CLEAR_PHASE_POST_CLEAR = 8'h03;

    typedef enum logic [3:0] {
        IDLE,
        ISOLATE,
        WAIT_ISOLATE_PHASE_ACK,
        WAIT_ISOLATE_ACK,
        CLEAR,
        WAIT_CLEAR_PHASE_ACK,
        WAIT_CLEAR_ACK,
        POST_CLEAR,
        FINISHED
    } initiator_state_e;

    initiator_state_e initiator_state_d;
    initiator_state_e initiator_state_q;

    logic [7:0] initiator_clear_seq_phase;
    logic initiator_phase_transition_req;
    logic initiator_phase_transition_ack;
    logic initiator_isolate_out;
    logic initiator_clear_out;

    logic [7:0] receiver_phase_q;
    logic [7:0] receiver_next_phase;
    logic receiver_phase_req;
    logic receiver_phase_ack;
    logic receiver_isolate_out;
    logic receiver_clear_out;

    always_comb begin
        initiator_state_d = initiator_state_q;
        initiator_phase_transition_req = 1'b0;
        initiator_isolate_out = 1'b0;
        initiator_clear_out = 1'b0;
        initiator_clear_seq_phase = CLEAR_PHASE_IDLE;

        case (initiator_state_q)
            IDLE: begin
                if (clear_i) begin
                    initiator_state_d = ISOLATE;
                end
            end

            ISOLATE: begin
                initiator_phase_transition_req = 1'b1;
                initiator_clear_seq_phase = CLEAR_PHASE_ISOLATE;
                initiator_isolate_out = 1'b1;
                if (initiator_phase_transition_ack && isolate_ack_i) begin
                    initiator_state_d = CLEAR;
                end else if (initiator_phase_transition_ack) begin
                    initiator_state_d = WAIT_ISOLATE_ACK;
                end else if (isolate_ack_i) begin
                    initiator_state_d = WAIT_ISOLATE_PHASE_ACK;
                end
            end

            WAIT_ISOLATE_ACK: begin
                initiator_isolate_out = 1'b1;
                initiator_clear_seq_phase = CLEAR_PHASE_ISOLATE;
                if (isolate_ack_i) begin
                    initiator_state_d = CLEAR;
                end
            end

            WAIT_ISOLATE_PHASE_ACK: begin
                initiator_phase_transition_req = 1'b1;
                initiator_clear_seq_phase = CLEAR_PHASE_ISOLATE;
                initiator_isolate_out = 1'b1;
                if (initiator_phase_transition_ack) begin
                    initiator_state_d = CLEAR;
                end
            end

            CLEAR: begin
                initiator_isolate_out = 1'b1;
                initiator_clear_out = 1'b1;
                initiator_phase_transition_req = 1'b1;
                initiator_clear_seq_phase = CLEAR_PHASE_CLEAR;
                if (initiator_phase_transition_ack && clear_ack_i) begin
                    initiator_state_d = POST_CLEAR;
                end else if (initiator_phase_transition_ack) begin
                    initiator_state_d = WAIT_CLEAR_ACK;
                end else if (clear_ack_i) begin
                    initiator_state_d = WAIT_CLEAR_PHASE_ACK;
                end
            end

            WAIT_CLEAR_ACK: begin
                initiator_isolate_out = 1'b1;
                initiator_clear_out = 1'b1;
                initiator_clear_seq_phase = CLEAR_PHASE_CLEAR;
                if (clear_ack_i) begin
                    initiator_state_d = POST_CLEAR;
                end
            end

            WAIT_CLEAR_PHASE_ACK: begin
                initiator_phase_transition_req = 1'b1;
                initiator_clear_seq_phase = CLEAR_PHASE_CLEAR;
                initiator_isolate_out = 1'b1;
                initiator_clear_out = 1'b1;
                if (initiator_phase_transition_ack) begin
                    initiator_state_d = POST_CLEAR;
                end
            end

            POST_CLEAR: begin
                initiator_isolate_out = 1'b1;
                initiator_phase_transition_req = 1'b1;
                initiator_clear_seq_phase = CLEAR_PHASE_POST_CLEAR;
                if (initiator_phase_transition_ack) begin
                    initiator_state_d = FINISHED;
                end
            end

            FINISHED: begin
                initiator_isolate_out = 1'b1;
                initiator_phase_transition_req = 1'b1;
                initiator_clear_seq_phase = CLEAR_PHASE_IDLE;
                if (initiator_phase_transition_ack) begin
                    initiator_state_d = IDLE;
                end
            end

            default: begin
                initiator_state_d = ISOLATE;
            end
        endcase
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            if (CLEAR_ON_ASYNC_RESET) begin
                initiator_state_q <= ISOLATE;
            end else begin
                initiator_state_q <= IDLE;
            end
        end else begin
            initiator_state_q <= initiator_state_d;
        end
    end

    cdc_4phase_ctrl_src #(
        .SYNC_STAGES(SYNC_STAGES),
        .SEND_RESET_MSG(CLEAR_ON_ASYNC_RESET),
        .RESET_MSG(CLEAR_PHASE_ISOLATE)
    ) i_state_transition_cdc_src (
        .rst_ni(rst_ni),
        .clk_i(clk_i),
        .data_i(initiator_clear_seq_phase),
        .valid_i(initiator_phase_transition_req),
        .ready_o(initiator_phase_transition_ack),
        .async_req_o(async_req_o),
        .async_ack_i(async_ack_i),
        .async_data_o(async_next_phase_o)
    );

    cdc_4phase_ctrl_dst #(
        .SYNC_STAGES(SYNC_STAGES)
    ) i_state_transition_cdc_dst (
        .rst_ni(rst_ni),
        .clk_i(clk_i),
        .data_o(receiver_next_phase),
        .valid_o(receiver_phase_req),
        .ready_i(receiver_phase_ack),
        .async_req_i(async_req_i),
        .async_ack_o(async_ack_o),
        .async_data_i(async_next_phase_i)
    );

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            receiver_phase_q <= CLEAR_PHASE_IDLE;
        end else if (receiver_phase_req && receiver_phase_ack) begin
            receiver_phase_q <= receiver_next_phase;
        end
    end

    always_comb begin
        receiver_isolate_out = 1'b0;
        receiver_clear_out = 1'b0;
        receiver_phase_ack = 1'b0;

        if (receiver_phase_req) begin
            case (receiver_next_phase)
                CLEAR_PHASE_IDLE: begin
                    receiver_phase_ack = 1'b1;
                end

                CLEAR_PHASE_ISOLATE: begin
                    receiver_isolate_out = 1'b1;
                    receiver_phase_ack = isolate_ack_i;
                end

                CLEAR_PHASE_CLEAR: begin
                    receiver_isolate_out = 1'b1;
                    receiver_clear_out = 1'b1;
                    receiver_phase_ack = clear_ack_i;
                end

                CLEAR_PHASE_POST_CLEAR: begin
                    receiver_isolate_out = 1'b1;
                    receiver_phase_ack = 1'b1;
                end

                default: begin
                end
            endcase
        end else begin
            case (receiver_phase_q)
                CLEAR_PHASE_IDLE: begin
                end

                CLEAR_PHASE_ISOLATE: begin
                    receiver_isolate_out = 1'b1;
                end

                CLEAR_PHASE_CLEAR: begin
                    receiver_isolate_out = 1'b1;
                    receiver_clear_out = 1'b1;
                end

                CLEAR_PHASE_POST_CLEAR: begin
                    receiver_isolate_out = 1'b1;
                end

                default: begin
                end
            endcase
        end
    end

    assign clear_o = initiator_clear_out || receiver_clear_out;
    assign isolate_o = initiator_isolate_out || receiver_isolate_out;

endmodule
