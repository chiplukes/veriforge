module isochronous_4phase_handshake (
    input  logic src_clk_i,
    input  logic src_rst_ni,
    input  logic src_valid_i,
    output logic src_ready_o,
    input  logic dst_clk_i,
    input  logic dst_rst_ni,
    output logic dst_valid_o,
    input  logic dst_ready_i
);

    logic src_req_q;
    logic src_ack_q;
    logic dst_req_q;
    logic dst_ack_q;

    always @(posedge src_clk_i or negedge src_rst_ni) begin
        if (!src_rst_ni) begin
            src_req_q <= 1'b0;
        end else if (src_valid_i && src_ready_o) begin
            src_req_q <= ~src_req_q;
        end
    end

    always @(posedge src_clk_i or negedge src_rst_ni) begin
        if (!src_rst_ni) begin
            src_ack_q <= 1'b0;
        end else begin
            src_ack_q <= dst_ack_q;
        end
    end

    assign src_ready_o = (src_req_q == src_ack_q);

    always @(posedge dst_clk_i or negedge dst_rst_ni) begin
        if (!dst_rst_ni) begin
            dst_ack_q <= 1'b0;
        end else if (dst_valid_o && dst_ready_i) begin
            dst_ack_q <= ~dst_ack_q;
        end
    end

    always @(posedge dst_clk_i or negedge dst_rst_ni) begin
        if (!dst_rst_ni) begin
            dst_req_q <= 1'b0;
        end else begin
            dst_req_q <= src_req_q;
        end
    end

    assign dst_valid_o = (dst_req_q != dst_ack_q);

endmodule
