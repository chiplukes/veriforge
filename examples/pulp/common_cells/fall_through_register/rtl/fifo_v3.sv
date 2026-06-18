module fifo_v3 #(
    parameter bit FALL_THROUGH = 1'b0,
    parameter int DATA_WIDTH = 8,
    parameter int DEPTH = 8,
    parameter int ADDR_DEPTH = (DEPTH > 1) ? $clog2(DEPTH) : 1
) (
    input  logic                  clk_i,
    input  logic                  rst_ni,
    input  logic                  flush_i,
    input  logic                  testmode_i,
    output logic                  full_o,
    output logic                  empty_o,
    output logic [ADDR_DEPTH-1:0] usage_o,
    input  logic [DATA_WIDTH-1:0] data_i,
    input  logic                  push_i,
    output logic [DATA_WIDTH-1:0] data_o,
    input  logic                  pop_i
);

    localparam int FifoDepth = (DEPTH > 0) ? DEPTH : 1;
    localparam logic [ADDR_DEPTH:0] FifoSize = DEPTH;

    logic [ADDR_DEPTH-1:0] read_pointer_q;
    logic [ADDR_DEPTH-1:0] write_pointer_q;
    logic [ADDR_DEPTH:0] status_cnt_q;
    logic [DATA_WIDTH-1:0] mem_q [FifoDepth];

    assign usage_o = status_cnt_q[ADDR_DEPTH-1:0];
    assign full_o = (status_cnt_q == FifoSize);
    assign empty_o = (status_cnt_q == 0) && !(FALL_THROUGH && push_i);
    assign data_o = (FALL_THROUGH && (status_cnt_q == 0)) ? data_i : mem_q[read_pointer_q];

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            read_pointer_q <= '0;
            write_pointer_q <= '0;
            status_cnt_q <= '0;
        end else if (flush_i) begin
            read_pointer_q <= '0;
            write_pointer_q <= '0;
            status_cnt_q <= '0;
        end else begin
            if (push_i && !full_o) begin
                if (!(FALL_THROUGH && (status_cnt_q == 0) && pop_i)) begin
                    mem_q[write_pointer_q] <= data_i;
                    if (write_pointer_q == DEPTH - 1) begin
                        write_pointer_q <= '0;
                    end else begin
                        write_pointer_q <= write_pointer_q + 1'b1;
                    end
                end
            end

            if (pop_i && !empty_o) begin
                if (!(FALL_THROUGH && (status_cnt_q == 0) && push_i)) begin
                    if (read_pointer_q == DEPTH - 1) begin
                        read_pointer_q <= '0;
                    end else begin
                        read_pointer_q <= read_pointer_q + 1'b1;
                    end
                end
            end

            if ((push_i && !full_o) && !(pop_i && !empty_o)) begin
                status_cnt_q <= status_cnt_q + 1'b1;
            end else if (!(push_i && !full_o) && (pop_i && !empty_o)) begin
                status_cnt_q <= status_cnt_q - 1'b1;
            end
        end
    end

endmodule
