module lzc #(
    parameter bit MODE = 1'b0
) (
    input  logic [7:0] in_i,
    output logic [2:0] cnt_o,
    output logic       empty_o
);
    always_comb begin
        empty_o = 1'b0;
        cnt_o = 3'd0;

        if (in_i == 8'b00000000) begin
            empty_o = 1'b1;
            cnt_o = 3'd7;
        end else if (MODE) begin
            casex (in_i)
                8'b1xxxxxxx: cnt_o = 3'd0;
                8'b01xxxxxx: cnt_o = 3'd1;
                8'b001xxxxx: cnt_o = 3'd2;
                8'b0001xxxx: cnt_o = 3'd3;
                8'b00001xxx: cnt_o = 3'd4;
                8'b000001xx: cnt_o = 3'd5;
                8'b0000001x: cnt_o = 3'd6;
                default:     cnt_o = 3'd7;
            endcase
        end else begin
            casex (in_i)
                8'bxxxxxxx1: cnt_o = 3'd0;
                8'bxxxxxx10: cnt_o = 3'd1;
                8'bxxxxx100: cnt_o = 3'd2;
                8'bxxxx1000: cnt_o = 3'd3;
                8'bxxx10000: cnt_o = 3'd4;
                8'bxx100000: cnt_o = 3'd5;
                8'bx1000000: cnt_o = 3'd6;
                default:     cnt_o = 3'd7;
            endcase
        end
    end
endmodule
