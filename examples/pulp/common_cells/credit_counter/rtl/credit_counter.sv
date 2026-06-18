module credit_counter #(
    parameter bit INIT_CREDIT_EMPTY = 1'b0
) (
    input  logic       clk_i,
    input  logic       rst_ni,
    output logic [2:0] credit_o,
    input  logic       credit_give_i,
    input  logic       credit_take_i,
    input  logic       credit_init_i,
    output logic       credit_left_o,
    output logic       credit_crit_o,
    output logic       credit_full_o
);

    localparam logic [2:0] NUM_CREDITS = 3'd3;
    localparam logic [2:0] INIT_NUM_CREDITS = INIT_CREDIT_EMPTY ? 3'd0 : NUM_CREDITS;

    logic [2:0] credit_d;
    logic [2:0] credit_q;
    logic increment;
    logic decrement;

    assign decrement = credit_take_i & ~credit_give_i;
    assign increment = ~credit_take_i & credit_give_i;

    always @(*) begin
        credit_d = credit_q;
        if (decrement) begin
            credit_d = credit_q - 1'b1;
        end else if (increment) begin
            credit_d = credit_q + 1'b1;
        end
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            credit_q <= INIT_NUM_CREDITS;
        end else if (credit_init_i) begin
            credit_q <= INIT_NUM_CREDITS;
        end else begin
            credit_q <= credit_d;
        end
    end

    assign credit_o = credit_q;
    assign credit_left_o = (credit_q != 3'd0);
    assign credit_crit_o = (credit_q == (NUM_CREDITS - 1'b1));
    assign credit_full_o = (credit_q == NUM_CREDITS);

endmodule
