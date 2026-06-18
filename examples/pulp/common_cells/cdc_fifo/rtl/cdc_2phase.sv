module cdc_2phase #(
    parameter int WIDTH = 1
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

    logic             async_req;
    logic             async_ack;
    logic [WIDTH-1:0] async_data;

    logic req_src_q;
    logic ack_src_q0;
    logic ack_src_q1;
    logic [WIDTH-1:0] data_src_q;

    logic req_dst_q0;
    logic req_dst_q1;
    logic ack_dst_q;
    logic [WIDTH-1:0] data_dst_q;

    assign src_ready_o = (req_src_q == ack_src_q1);
    assign async_req = req_src_q;
    assign async_data = data_src_q;

    assign dst_valid_o = (req_dst_q1 != ack_dst_q);
    assign dst_data_o = data_dst_q;
    assign async_ack = ack_dst_q;

    always @(posedge src_clk_i or negedge src_rst_ni) begin
        if (!src_rst_ni) begin
            req_src_q <= 1'b0;
            ack_src_q0 <= 1'b0;
            ack_src_q1 <= 1'b0;
            data_src_q <= '0;
        end else begin
            ack_src_q0 <= async_ack;
            ack_src_q1 <= ack_src_q0;
            if (src_valid_i && src_ready_o) begin
                req_src_q <= ~req_src_q;
                data_src_q <= src_data_i;
            end
        end
    end

    always @(posedge dst_clk_i or negedge dst_rst_ni) begin
        if (!dst_rst_ni) begin
            req_dst_q0 <= 1'b0;
            req_dst_q1 <= 1'b0;
            ack_dst_q <= 1'b0;
            data_dst_q <= '0;
        end else begin
            req_dst_q0 <= async_req;
            req_dst_q1 <= req_dst_q0;
            if (req_dst_q1 != ack_dst_q) begin
                data_dst_q <= async_data;
                if (dst_ready_i) begin
                    ack_dst_q <= req_dst_q1;
                end
            end
        end
    end

endmodule
