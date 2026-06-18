module heaviside (
    input  logic [2:0] x_i,
    output logic [7:0] mask_o
);
    assign mask_o = (x_i == 3'd7) ? 8'hFF : ((8'h01 << (x_i + 1'b1)) - 8'h01);
endmodule
