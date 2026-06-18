module stream_delay (
    input  logic       clk_i,
    input  logic       rst_ni,
    input  logic [7:0] payload_i,
    output logic       ready_o,
    input  logic       valid_i,
    output logic [7:0] payload_o,
    input  logic       ready_i,
    output logic       valid_o
);

    localparam logic [1:0] FixedDelay = 2'd2;

    typedef enum logic [1:0] {
        Idle,
        WaitDelay,
        Ready
    } state_e;

    state_e state_d;
    state_e state_q;
    logic [1:0] delay_d;
    logic [1:0] delay_q;

    assign payload_o = payload_i;

    always_comb begin
        state_d = state_q;
        delay_d = delay_q;
        valid_o = 1'b0;
        ready_o = 1'b0;

        unique case (state_q)
            Idle: begin
                delay_d = '0;
                if (valid_i) begin
                    state_d = WaitDelay;
                    delay_d = FixedDelay - 1'b1;
                end
            end
            WaitDelay: begin
                if (delay_q == 1) begin
                    delay_d = '0;
                    state_d = Ready;
                end else begin
                    delay_d = delay_q - 1'b1;
                end
            end
            Ready: begin
                valid_o = 1'b1;
                ready_o = ready_i;
                if (ready_i) begin
                    state_d = Idle;
                    delay_d = '0;
                end
            end
            default: begin
                state_d = Idle;
                delay_d = '0;
            end
        endcase
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            state_q <= Idle;
            delay_q <= '0;
        end else begin
            state_q <= state_d;
            delay_q <= delay_d;
        end
    end

endmodule
