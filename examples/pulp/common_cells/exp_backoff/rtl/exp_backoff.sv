module exp_backoff #(
    parameter int unsigned Seed = 16'hBEEF,
    parameter int unsigned MaxExp = 4
) (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic set_i,
    input  logic clr_i,
    output logic is_zero_o
);
    localparam int unsigned WIDTH = 16;

    logic [WIDTH-1:0] lfsr_d;
    logic [WIDTH-1:0] lfsr_q;
    logic [WIDTH-1:0] cnt_d;
    logic [WIDTH-1:0] cnt_q;
    logic [WIDTH-1:0] mask_d;
    logic [WIDTH-1:0] mask_q;
    logic             lfsr_feedback;

    assign lfsr_feedback = lfsr_q[0] ^ lfsr_q[2] ^ lfsr_q[3] ^ lfsr_q[5];
    assign lfsr_d = set_i ? {lfsr_feedback, lfsr_q[WIDTH-1:1]} : lfsr_q;
    assign mask_d = clr_i ? '0 :
                    set_i ? {{(WIDTH-MaxExp){1'b0}}, mask_q[MaxExp-2:0], 1'b1} :
                    mask_q;
    assign cnt_d = clr_i ? '0 :
                   set_i ? (mask_q & lfsr_q) :
                   (!is_zero_o ? cnt_q - 1'b1 : '0);
    assign is_zero_o = (cnt_q == '0);

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            lfsr_q <= Seed;
            mask_q <= '0;
            cnt_q <= '0;
        end else begin
            lfsr_q <= lfsr_d;
            mask_q <= mask_d;
            cnt_q <= cnt_d;
        end
    end

endmodule
