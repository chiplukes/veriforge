module binary_to_gray #(
    parameter int N = 2
) (
    input  logic [N-1:0] A,
    output logic [N-1:0] Z
);

    assign Z = A ^ (A >> 1);

endmodule
