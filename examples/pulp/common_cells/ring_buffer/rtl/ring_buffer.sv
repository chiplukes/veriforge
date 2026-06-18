module ring_buffer #(
    parameter int unsigned Depth = 4,
    parameter int unsigned DataWidth = 8
) (
    input  logic                 clk_i,
    input  logic                 rst_ni,
    input  logic                 wvalid_i,
    output logic                 wready_o,
    input  logic [DataWidth-1:0] wdata_i,
    input  logic                 rvalid_i,
    output logic                 rready_o,
    input  logic [1:0]           raddr_i,
    output logic [DataWidth-1:0] rdata_o,
    input  logic                 advance_i,
    input  logic [2:0]           step_i,
    output logic [1:0]           wptr_o,
    output logic [1:0]           rptr_o,
    output logic                 full_o,
    output logic                 empty_o
);

    logic [DataWidth-1:0] mem_d [0:Depth-1];
    logic [DataWidth-1:0] mem_q [0:Depth-1];
    logic [2:0] rptr_d;
    logic [2:0] rptr_q;
    logic [2:0] wptr_d;
    logic [2:0] wptr_q;

    always @(*) begin
        mem_d = mem_q;
        rptr_d = rptr_q;
        wptr_d = wptr_q;

        if (wvalid_i && wready_o) begin
            mem_d[wptr_q[1:0]] = wdata_i;
            wptr_d = wptr_q + 3'b001;
        end

        if (advance_i) begin
            rptr_d = rptr_q + step_i;
        end
    end

    assign wptr_o = wptr_q[1:0];
    assign rptr_o = rptr_q[1:0];

    assign empty_o = (wptr_q == rptr_q);
    assign full_o = (wptr_q[1:0] == rptr_q[1:0]) && !empty_o;

    assign rready_o = ((rptr_o < wptr_o) && ((raddr_i >= rptr_o) && (raddr_i < wptr_o))) ||
                      ((rptr_o > wptr_o) && ((raddr_i >= rptr_o) || (raddr_i < wptr_o))) ||
                      ((rptr_o == wptr_o) && !empty_o);

    assign wready_o = !full_o;
    assign rdata_o = mem_q[raddr_i];

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            rptr_q <= 3'b000;
            wptr_q <= 3'b000;
            mem_q[0] <= '0;
            mem_q[1] <= '0;
            mem_q[2] <= '0;
            mem_q[3] <= '0;
        end else begin
            rptr_q <= rptr_d;
            wptr_q <= wptr_d;
            mem_q[0] <= mem_d[0];
            mem_q[1] <= mem_d[1];
            mem_q[2] <= mem_d[2];
            mem_q[3] <= mem_d[3];
        end
    end

endmodule
