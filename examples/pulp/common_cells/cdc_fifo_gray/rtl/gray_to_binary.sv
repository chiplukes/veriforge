module gray_to_binary (
    input  logic [1:0] A,
    output logic [1:0] Z
);

    assign Z[1] = A[1];
    assign Z[0] = A[1] ^ A[0];

endmodule
