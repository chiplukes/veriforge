module edge_propagator_tx (
    input  logic clk_i,
    input  logic rstn_i,
    input  logic valid_i,
    input  logic ack_i,
    output logic valid_o
);

    logic [1:0] sync_a;
    logic       r_input_reg;
    logic       s_input_reg_next;

    assign s_input_reg_next = valid_i | (r_input_reg & ~sync_a[0]);

    always @(negedge rstn_i or posedge clk_i) begin
        if (~rstn_i) begin
            r_input_reg <= 1'b0;
            sync_a <= 2'b00;
        end else begin
            r_input_reg <= s_input_reg_next;
            sync_a <= {ack_i, sync_a[1]};
        end
    end

    assign valid_o = r_input_reg;

endmodule
