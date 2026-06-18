module plru_tree #(
    parameter int unsigned ENTRIES = 4
) (
    input  logic               clk_i,
    input  logic               rst_ni,
    input  logic [ENTRIES-1:0] used_i,
    output logic [ENTRIES-1:0] plru_o
);
    logic [2:0] tree_d;
    logic [2:0] tree_q;

    always @(*) begin
        tree_d = tree_q;

        case (used_i)
            4'b0001: begin
                tree_d[0] = 1'b1;
                tree_d[1] = 1'b1;
            end
            4'b0010: begin
                tree_d[0] = 1'b1;
                tree_d[1] = 1'b0;
            end
            4'b0100: begin
                tree_d[0] = 1'b0;
                tree_d[2] = 1'b1;
            end
            4'b1000: begin
                tree_d[0] = 1'b0;
                tree_d[2] = 1'b0;
            end
            default: begin
            end
        endcase
    end

    always @(*) begin
        if (tree_q[0] == 1'b0) begin
            if (tree_q[1] == 1'b0)
                plru_o = 4'b0001;
            else
                plru_o = 4'b0010;
        end else begin
            if (tree_q[2] == 1'b0)
                plru_o = 4'b0100;
            else
                plru_o = 4'b1000;
        end
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            tree_q <= 3'b000;
        end else begin
            tree_q <= tree_d;
        end
    end

endmodule
