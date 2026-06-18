module max_counter #(
    parameter int unsigned WIDTH = 4
) (
    input  logic             clk_i,
    input  logic             rst_ni,
    input  logic             clear_i,
    input  logic             clear_max_i,
    input  logic             en_i,
    input  logic             load_i,
    input  logic             down_i,
    input  logic [WIDTH-1:0] delta_i,
    input  logic [WIDTH-1:0] d_i,
    output logic [WIDTH-1:0] q_o,
    output logic [WIDTH-1:0] max_o,
    output logic             overflow_o,
    output logic             overflow_max_o
);
    logic [WIDTH-1:0] max_d;
    logic [WIDTH-1:0] max_q;
    logic overflow_max_d;
    logic overflow_max_q;

    delta_counter #(
        .WIDTH(WIDTH),
        .STICKY_OVERFLOW(1'b1)
    ) i_counter (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .clear_i(clear_i),
        .en_i(en_i),
        .load_i(load_i),
        .down_i(down_i),
        .delta_i(delta_i),
        .d_i(d_i),
        .q_o(q_o),
        .overflow_o(overflow_o)
    );

    always @(*) begin
        max_d = max_q;
        max_o = max_q;
        overflow_max_d = overflow_max_q;
        if (clear_max_i) begin
            max_d = '0;
            overflow_max_d = 1'b0;
        end else if (q_o > max_q) begin
            max_d = q_o;
            max_o = q_o;
            if (overflow_o) begin
                overflow_max_d = 1'b1;
            end
        end
    end

    assign overflow_max_o = overflow_max_q;

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            max_q <= '0;
            overflow_max_q <= 1'b0;
        end else begin
            max_q <= max_d;
            overflow_max_q <= overflow_max_d;
        end
    end

endmodule
