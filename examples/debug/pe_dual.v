// Test: two priority_encoder instances in one wrapper.
// Mirrors arbiter.v structure (priority_encoder_inst + priority_encoder_masked).
`default_nettype none
module pe_dual #(
    parameter WIDTH = 4
)(
    input  wire [WIDTH-1:0] req_a,
    input  wire [WIDTH-1:0] req_b,
    output wire             valid_a,
    output wire             valid_b,
    output wire [WIDTH-1:0] mask_a,
    output wire [WIDTH-1:0] mask_b
);

priority_encoder #(.WIDTH(WIDTH), .LSB_HIGH_PRIORITY(0))
    pe_a (.input_unencoded(req_a), .output_valid(valid_a),
          .output_encoded(), .output_unencoded(mask_a));

priority_encoder #(.WIDTH(WIDTH), .LSB_HIGH_PRIORITY(0))
    pe_b (.input_unencoded(req_b), .output_valid(valid_b),
          .output_encoded(), .output_unencoded(mask_b));

endmodule
