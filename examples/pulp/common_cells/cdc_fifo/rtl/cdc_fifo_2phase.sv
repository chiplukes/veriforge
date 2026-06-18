module cdc_fifo_2phase #(
    parameter int WIDTH = 8,
    parameter int LOG_DEPTH = 1
) (
    input  logic             src_rst_ni,
    input  logic             src_clk_i,
    input  logic [WIDTH-1:0] src_data_i,
    input  logic             src_valid_i,
    output logic             src_ready_o,

    input  logic             dst_rst_ni,
    input  logic             dst_clk_i,
    output logic [WIDTH-1:0] dst_data_o,
    output logic             dst_valid_o,
    input  logic             dst_ready_i
);

    localparam int Depth = 1 << LOG_DEPTH;
    localparam int PtrWidth = LOG_DEPTH + 1;
    localparam logic [PtrWidth-1:0] PtrFull = (1 << LOG_DEPTH);
    localparam logic [PtrWidth-1:0] PtrEmpty = '0;

    logic [WIDTH-1:0] fifo_data_q [Depth];
    logic [PtrWidth-1:0] src_wptr_q;
    logic [PtrWidth-1:0] dst_wptr;
    logic [PtrWidth-1:0] src_rptr;
    logic [PtrWidth-1:0] dst_rptr_q;
    logic [LOG_DEPTH-1:0] fifo_widx;
    logic [LOG_DEPTH-1:0] fifo_ridx;
    integer init_idx;

    assign fifo_widx = src_wptr_q[LOG_DEPTH-1:0];
    assign fifo_ridx = dst_rptr_q[LOG_DEPTH-1:0];

    assign src_ready_o = ((src_wptr_q ^ src_rptr) != PtrFull);
    assign dst_valid_o = ((dst_rptr_q ^ dst_wptr) != PtrEmpty);
    assign dst_data_o = fifo_data_q[fifo_ridx];

    initial begin
        for (init_idx = 0; init_idx < Depth; init_idx = init_idx + 1) begin
            fifo_data_q[init_idx] = '0;
        end
    end

    always @(posedge src_clk_i or negedge src_rst_ni) begin
        if (!src_rst_ni) begin
            src_wptr_q <= '0;
        end else if (src_valid_i && src_ready_o) begin
            fifo_data_q[fifo_widx] <= src_data_i;
            src_wptr_q <= src_wptr_q + 1'b1;
        end
    end

    always @(posedge dst_clk_i or negedge dst_rst_ni) begin
        if (!dst_rst_ni) begin
            dst_rptr_q <= '0;
        end else if (dst_valid_o && dst_ready_i) begin
            dst_rptr_q <= dst_rptr_q + 1'b1;
        end
    end

    cdc_2phase #(
        .WIDTH(PTR_WIDTH)
    ) i_cdc_wptr (
        .src_rst_ni(src_rst_ni),
        .src_clk_i(src_clk_i),
        .src_data_i(src_wptr_q),
        .src_valid_i(1'b1),
        .src_ready_o(),
        .dst_rst_ni(dst_rst_ni),
        .dst_clk_i(dst_clk_i),
        .dst_data_o(dst_wptr),
        .dst_valid_o(),
        .dst_ready_i(1'b1)
    );

    cdc_2phase #(
        .WIDTH(PtrWidth)
    ) i_cdc_rptr (
        .src_rst_ni(dst_rst_ni),
        .src_clk_i(dst_clk_i),
        .src_data_i(dst_rptr_q),
        .src_valid_i(1'b1),
        .src_ready_o(),
        .dst_rst_ni(src_rst_ni),
        .dst_clk_i(src_clk_i),
        .dst_data_o(src_rptr),
        .dst_valid_o(),
        .dst_ready_i(1'b1)
    );

endmodule
