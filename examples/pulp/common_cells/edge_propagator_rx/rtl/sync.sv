module sync #(
    parameter int unsigned STAGES = 2,
    parameter bit RESET_VALUE = 1'b0
) (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic serial_i,
    output logic serial_o
);

    logic [STAGES-1:0] reg_q;

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            reg_q <= {STAGES{RESET_VALUE}};
        end else begin
            reg_q <= {reg_q[STAGES-2:0], serial_i};
        end
    end

    assign serial_o = reg_q[STAGES-1];

endmodule
