module spill_register_flushable #(
    parameter int DATA_WIDTH = 8,
    parameter bit Bypass = 1'b0
) (
    input  logic                  clk_i,
    input  logic                  rst_ni,
    input  logic                  valid_i,
    input  logic                  flush_i,
    output logic                  ready_o,
    input  logic [DATA_WIDTH-1:0] data_i,
    output logic                  valid_o,
    input  logic                  ready_i,
    output logic [DATA_WIDTH-1:0] data_o
);

    if (Bypass) begin : gen_bypass
        assign valid_o = valid_i;
        assign ready_o = ready_i;
        assign data_o = data_i;
    end else begin : gen_spill_reg
        logic [DATA_WIDTH-1:0] a_data_q;
        logic a_full_q;
        logic a_fill;
        logic a_drain;
        logic [DATA_WIDTH-1:0] b_data_q;
        logic b_full_q;
        logic b_fill;
        logic b_drain;

        always @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                a_data_q <= '0;
            end else if (a_fill) begin
                a_data_q <= data_i;
            end
        end

        always @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                a_full_q <= 1'b0;
            end else if (a_fill || a_drain) begin
                a_full_q <= a_fill;
            end
        end

        always @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                b_data_q <= '0;
            end else if (b_fill) begin
                b_data_q <= a_data_q;
            end
        end

        always @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                b_full_q <= 1'b0;
            end else if (b_fill || b_drain) begin
                b_full_q <= b_fill;
            end
        end

        assign a_fill = valid_i && ready_o && !flush_i;
        assign a_drain = (a_full_q && !b_full_q) || flush_i;
        assign b_fill = a_drain && !ready_i && !flush_i;
        assign b_drain = (b_full_q && ready_i) || flush_i;

        assign ready_o = !a_full_q || !b_full_q;
        assign valid_o = a_full_q || b_full_q;
        assign data_o = b_full_q ? b_data_q : a_data_q;
    end

endmodule
