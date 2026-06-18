module delta_counter #(
    parameter int unsigned WIDTH = 4,
    parameter bit STICKY_OVERFLOW = 1'b0
) (
    input  logic             clk_i,
    input  logic             rst_ni,
    input  logic             clear_i,
    input  logic             en_i,
    input  logic             load_i,
    input  logic             down_i,
    input  logic [WIDTH-1:0] delta_i,
    input  logic [WIDTH-1:0] d_i,
    output logic [WIDTH-1:0] q_o,
    output logic             overflow_o
);
    logic [WIDTH:0] counter_q;
    logic [WIDTH:0] counter_d;

    if (STICKY_OVERFLOW) begin : gen_sticky_overflow
        logic overflow_d;
        logic overflow_q;

        always @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                overflow_q <= 1'b0;
            end else begin
                overflow_q <= overflow_d;
            end
        end

        always @(*) begin
            overflow_d = overflow_q;
            if (clear_i || load_i) begin
                overflow_d = 1'b0;
            end else if (!overflow_q && en_i) begin
                if (down_i) begin
                    overflow_d = delta_i > counter_q[WIDTH-1:0];
                end else begin
                    overflow_d = counter_q[WIDTH-1:0] > ({WIDTH{1'b1}} - delta_i);
                end
            end
        end

        assign overflow_o = overflow_q;
    end else begin : gen_transient_overflow
        assign overflow_o = counter_q[WIDTH];
    end

    assign q_o = counter_q[WIDTH-1:0];

    always @(*) begin
        counter_d = counter_q;

        if (clear_i) begin
            counter_d = '0;
        end else if (load_i) begin
            counter_d = {1'b0, d_i};
        end else if (en_i) begin
            if (down_i) begin
                counter_d = counter_q - delta_i;
            end else begin
                counter_d = counter_q + delta_i;
            end
        end
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            counter_q <= '0;
        end else begin
            counter_q <= counter_d;
        end
    end

endmodule
