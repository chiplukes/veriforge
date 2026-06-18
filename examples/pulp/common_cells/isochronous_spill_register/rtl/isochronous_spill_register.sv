module isochronous_spill_register #(
    parameter int DATA_WIDTH = 8,
    parameter bit Bypass = 1'b0
) (
    input  logic                  src_clk_i,
    input  logic                  src_rst_ni,
    input  logic                  src_valid_i,
    output logic                  src_ready_o,
    input  logic [DATA_WIDTH-1:0] src_data_i,
    input  logic                  dst_clk_i,
    input  logic                  dst_rst_ni,
    output logic                  dst_valid_o,
    input  logic                  dst_ready_i,
    output logic [DATA_WIDTH-1:0] dst_data_o
);

    if (Bypass) begin : gen_bypass
        assign dst_valid_o = src_valid_i;
        assign src_ready_o = dst_ready_i;
        assign dst_data_o = src_data_i;
    end else begin : gen_isochronous_spill_register
        logic [1:0] rd_pointer_q;
        logic [1:0] wr_pointer_q;
        logic [DATA_WIDTH-1:0] mem0_q;
        logic [DATA_WIDTH-1:0] mem1_q;

        always @(posedge src_clk_i or negedge src_rst_ni) begin
            if (!src_rst_ni) begin
                wr_pointer_q <= 2'b00;
            end else if (src_valid_i && src_ready_o) begin
                wr_pointer_q <= wr_pointer_q + 2'b01;
            end
        end

        always @(posedge dst_clk_i or negedge dst_rst_ni) begin
            if (!dst_rst_ni) begin
                rd_pointer_q <= 2'b00;
            end else if (dst_valid_o && dst_ready_i) begin
                rd_pointer_q <= rd_pointer_q + 2'b01;
            end
        end

        always @(posedge src_clk_i or negedge src_rst_ni) begin
            if (!src_rst_ni) begin
                mem0_q <= '0;
                mem1_q <= '0;
            end else if (src_valid_i && src_ready_o) begin
                if (wr_pointer_q[0]) begin
                    mem1_q <= src_data_i;
                end else begin
                    mem0_q <= src_data_i;
                end
            end
        end

        assign src_ready_o = (rd_pointer_q ^ wr_pointer_q) != 2'b10;
        assign dst_valid_o = (rd_pointer_q ^ wr_pointer_q) != 2'b00;
        assign dst_data_o = rd_pointer_q[0] ? mem1_q : mem0_q;
    end

endmodule
