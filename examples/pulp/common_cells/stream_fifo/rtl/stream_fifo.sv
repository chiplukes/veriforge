module stream_fifo #(
    parameter bit FALL_THROUGH = 1'b0,
    parameter int DATA_WIDTH = 8,
    parameter int DEPTH = 3,
    parameter int ADDR_DEPTH = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
    input  logic                  clk_i,
    input  logic                  rst_ni,
    input  logic                  flush_i,
    input  logic                  testmode_i,
    output logic [ADDR_DEPTH-1:0] usage_o,
    input  logic [DATA_WIDTH-1:0] data_i,
    input  logic                  valid_i,
    output logic                  ready_o,
    output logic [DATA_WIDTH-1:0] data_o,
    output logic                  valid_o,
    input  logic                  ready_i
);

    logic push;
    logic pop;
    logic empty;
    logic full;

    assign push = valid_i & ~full;
    assign pop = ready_i & ~empty;
    assign ready_o = ~full;
    assign valid_o = ~empty;

    fifo_v3 #(
        .FALL_THROUGH(FALL_THROUGH),
        .DATA_WIDTH(DATA_WIDTH),
        .DEPTH(DEPTH)
    ) fifo_i (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .flush_i(flush_i),
        .testmode_i(testmode_i),
        .full_o(full),
        .empty_o(empty),
        .usage_o(usage_o),
        .data_i(data_i),
        .push_i(push),
        .data_o(data_o),
        .pop_i(pop)
    );

endmodule
