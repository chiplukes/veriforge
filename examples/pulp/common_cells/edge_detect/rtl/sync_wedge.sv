module sync_wedge #(
    parameter int unsigned STAGES = 2
) (
    input  logic clk_i,
    input  logic rst_ni,
    input  logic en_i,
    input  logic serial_i,
    output logic r_edge_o,
    output logic f_edge_o,
    output logic serial_o
);

    logic serial;
    logic serial_q;

    assign serial_o = serial_q;
    assign f_edge_o = (~serial) & serial_q;
    assign r_edge_o = serial & (~serial_q);

    sync #(
        .STAGES(STAGES)
    ) i_sync (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .serial_i(serial_i),
        .serial_o(serial)
    );

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            serial_q <= 1'b0;
        end else if (en_i) begin
            serial_q <= serial;
        end
    end

endmodule
