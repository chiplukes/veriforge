module cc_onehot (
    input  logic [3:0] d_i,
    output logic       is_onehot_o
);
    assign is_onehot_o = (d_i == 4'b0001)
        | (d_i == 4'b0010)
        | (d_i == 4'b0100)
        | (d_i == 4'b1000);
endmodule
