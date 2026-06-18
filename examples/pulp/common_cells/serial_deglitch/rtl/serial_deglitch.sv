module serial_deglitch #(
    parameter int unsigned SIZE = 3
) (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic en_i,
    input  logic d_i,
    output logic q_o
);
    logic [SIZE-1:0] count_q;
    logic [SIZE-1:0] count_d;
    logic q_q;
    logic q_d;

    assign q_o = q_q;

    always @(*) begin
        count_d = count_q;
        q_d = q_q;

        if (en_i) begin
            if (d_i == 1'b1 && count_q != SIZE[SIZE-1:0]) begin
                count_d = count_q + 1'b1;
            end else if (d_i == 1'b0 && count_q != '0) begin
                count_d = count_q - 1'b1;
            end
        end

        if (count_d == SIZE[SIZE-1:0]) begin
            q_d = 1'b1;
        end else if (count_d == '0) begin
            q_d = 1'b0;
        end
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            count_q <= '0;
            q_q <= 1'b0;
        end else begin
            count_q <= count_d;
            q_q <= q_d;
        end
    end

endmodule
