module lfsr_8bit #(
    parameter logic [7:0] SEED = 8'hA5,
    parameter int unsigned WIDTH = 8
) (
    input  logic                     clk_i,
    input  logic                     rst_ni,
    input  logic                     en_i,
    output logic [WIDTH-1:0]         refill_way_oh,
    output logic [$clog2(WIDTH)-1:0] refill_way_bin
);
    localparam int unsigned LogWidth = $clog2(WIDTH);

    logic [7:0] shift_d;
    logic [7:0] shift_q;
    logic       shift_in;

    always @(*) begin
        shift_in = !(shift_q[7] ^ shift_q[3] ^ shift_q[2] ^ shift_q[1]);
        shift_d = shift_q;

        if (en_i) begin
            shift_d = {shift_q[6:0], shift_in};
        end

        refill_way_oh = '0;
        refill_way_oh[shift_q[LogWidth-1:0]] = 1'b1;
        refill_way_bin = shift_q[LogWidth-1:0];
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            shift_q <= SEED;
        end else begin
            shift_q <= shift_d;
        end
    end

endmodule
