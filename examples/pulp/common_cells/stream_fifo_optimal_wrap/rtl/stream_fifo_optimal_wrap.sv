module stream_fifo_optimal_wrap #(
    parameter int DEPTH = 8,
    parameter int DATA_WIDTH = 8,
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

    if (DEPTH == 2) begin : gen_spill
        spill_register_flushable #(
            .DATA_WIDTH(DATA_WIDTH),
            .Bypass(1'b0)
        ) spill_i (
            .clk_i(clk_i),
            .rst_ni(rst_ni),
            .valid_i(valid_i),
            .flush_i(flush_i),
            .ready_o(ready_o),
            .data_i(data_i),
            .valid_o(valid_o),
            .ready_i(ready_i),
            .data_o(data_o)
        );

        assign usage_o = 'x;
    end else begin : gen_fifo
        stream_fifo #(
            .FALL_THROUGH(1'b0),
            .DATA_WIDTH(DATA_WIDTH),
            .DEPTH(DEPTH)
        ) fifo_i (
            .clk_i(clk_i),
            .rst_ni(rst_ni),
            .flush_i(flush_i),
            .testmode_i(testmode_i),
            .usage_o(usage_o),
            .data_i(data_i),
            .valid_i(valid_i),
            .ready_o(ready_o),
            .data_o(data_o),
            .valid_o(valid_o),
            .ready_i(ready_i)
        );
    end

endmodule
