module fall_through_register #(
    parameter int DATA_WIDTH = 8
) (
    input  logic                  clk_i,
    input  logic                  rst_ni,
    input  logic                  clr_i,
    input  logic                  testmode_i,
    input  logic                  valid_i,
    output logic                  ready_o,
    input  logic [DATA_WIDTH-1:0] data_i,
    output logic                  valid_o,
    input  logic                  ready_i,
    output logic [DATA_WIDTH-1:0] data_o
);

    logic fifo_empty;
    logic fifo_full;

    fifo_v3 #(
        .FALL_THROUGH(1'b1),
        .DATA_WIDTH(DATA_WIDTH),
        .DEPTH(1)
    ) i_fifo (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .flush_i(clr_i),
        .testmode_i(testmode_i),
        .full_o(fifo_full),
        .empty_o(fifo_empty),
        .usage_o(),
        .data_i(data_i),
        .push_i(valid_i && !fifo_full),
        .data_o(data_o),
        .pop_i(ready_i && !fifo_empty)
    );

    assign ready_o = !fifo_full;
    assign valid_o = !fifo_empty;

endmodule
