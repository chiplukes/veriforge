module shift_reg_gated #(
    parameter int unsigned Width = 8,
    parameter int unsigned Depth = 3
) (
    input  logic             clk_i,
    input  logic             rst_ni,
    input  logic             valid_i,
    input  logic [Width-1:0] data_i,
    output logic             valid_o,
    output logic [Width-1:0] data_o
);

    if (Depth == 0) begin : gen_pass_through
        assign valid_o = valid_i;
        assign data_o = data_i;
    end else begin : gen_shift_reg
        logic [Depth-1:0] valid_q;
        logic [Depth-1:0][Width-1:0] data_q;
        integer i;

        assign valid_o = valid_q[Depth-1];
        assign data_o = data_q[Depth-1];

        always @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                valid_q <= '0;
                data_q <= '0;
            end else begin
                valid_q[0] <= valid_i;
                if (valid_i) begin
                    data_q[0] <= data_i;
                end

                for (i = 1; i < Depth; i = i + 1) begin
                    valid_q[i] <= valid_q[i-1];
                    if (valid_q[i-1]) begin
                        data_q[i] <= data_q[i-1];
                    end
                end
            end
        end
    end

endmodule
