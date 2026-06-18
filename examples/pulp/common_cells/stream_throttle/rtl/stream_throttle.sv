module stream_throttle #(
    parameter int MAX_NUM_PENDING = 3,
    parameter int CNT_WIDTH = (MAX_NUM_PENDING > 1) ? $clog2(MAX_NUM_PENDING + 1) : 1
) (
    input  logic                 clk_i,
    input  logic                 rst_ni,
    input  logic                 req_valid_i,
    output logic                 req_valid_o,
    input  logic                 req_ready_i,
    output logic                 req_ready_o,
    input  logic                 rsp_valid_i,
    input  logic                 rsp_ready_i,
    input  logic [CNT_WIDTH-1:0] credit_i
);

    logic [CNT_WIDTH-1:0] credit_d;
    logic [CNT_WIDTH-1:0] credit_q;
    logic credit_available;

    always @(*) begin
        credit_d = credit_q;

        if (req_ready_o & req_valid_o) begin
            credit_d = credit_d + 'd1;
        end

        if (rsp_valid_i & rsp_ready_i) begin
            credit_d = credit_d - 'd1;
        end
    end

    assign credit_available = credit_q <= (credit_i - 'd1);
    assign req_valid_o = req_valid_i & credit_available;
    assign req_ready_o = req_ready_i & credit_available;

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            credit_q <= '0;
        end else begin
            credit_q <= credit_d;
        end
    end

endmodule
