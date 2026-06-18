module stream_to_mem #(
  parameter int unsigned DataWidth = 16,
  parameter int unsigned BufDepth = 1
) (
  input  logic                   clk_i,
  input  logic                   rst_ni,
  input  logic [DataWidth-1:0]   req_i,
  input  logic                   req_valid_i,
  output logic                   req_ready_o,
  output logic [DataWidth-1:0]   resp_o,
  output logic                   resp_valid_o,
  input  logic                   resp_ready_i,
  output logic [DataWidth-1:0]   mem_req_o,
  output logic                   mem_req_valid_o,
  input  logic                   mem_req_ready_i,
  input  logic [DataWidth-1:0]   mem_resp_i,
  input  logic                   mem_resp_valid_i
);

  localparam int unsigned ActualDepth = (BufDepth > 0) ? BufDepth : 1;
  localparam int unsigned PtrWidth = (ActualDepth > 1) ? $clog2(ActualDepth) : 1;
  localparam int unsigned OutstandingWidth = $clog2(BufDepth + 1) + 1;

  logic [DataWidth-1:0] resp_buf_d [ActualDepth];
  logic [DataWidth-1:0] resp_buf_q [ActualDepth];
  logic [PtrWidth-1:0]  rd_ptr_d;
  logic [PtrWidth-1:0]  rd_ptr_q;
  logic [PtrWidth-1:0]  wr_ptr_d;
  logic [PtrWidth-1:0]  wr_ptr_q;
  logic [PtrWidth:0]    buf_count_d;
  logic [PtrWidth:0]    buf_count_q;
  logic [OutstandingWidth-1:0] cnt_d;
  logic [OutstandingWidth-1:0] cnt_q;
  logic req_ready;
  logic buf_ready;
  logic push_buf;
  logic pop_buf;
  integer comb_idx;
  integer seq_idx;

  assign mem_req_o = req_i;

  if (BufDepth > 0) begin : gen_buf
    always_comb begin
      req_ready_o = 1'b0;
      resp_o = '0;
      resp_valid_o = 1'b0;
      mem_req_valid_o = 1'b0;
      req_ready = 1'b0;
      buf_ready = 1'b0;
      push_buf = 1'b0;
      pop_buf = 1'b0;
      cnt_d = cnt_q;
      rd_ptr_d = rd_ptr_q;
      wr_ptr_d = wr_ptr_q;
      buf_count_d = buf_count_q;
      for (comb_idx = 0; comb_idx < ActualDepth; comb_idx = comb_idx + 1) begin
        resp_buf_d[comb_idx] = resp_buf_q[comb_idx];
      end

      if (buf_count_q != 0) begin
        resp_o = resp_buf_q[rd_ptr_q];
        resp_valid_o = 1'b1;
      end else if (mem_resp_valid_i) begin
        resp_o = mem_resp_i;
        resp_valid_o = 1'b1;
      end

      pop_buf = (buf_count_q != 0) && resp_valid_o && resp_ready_i;
      buf_ready = (buf_count_q < BufDepth);
      push_buf = mem_resp_valid_i && !((buf_count_q == 0) && resp_ready_i) && buf_ready;

      if (pop_buf) begin
        if (rd_ptr_q == ActualDepth - 1) begin
          rd_ptr_d = '0;
        end else begin
          rd_ptr_d = rd_ptr_q + 1'b1;
        end
        buf_count_d = buf_count_d - 1'b1;
      end

      if (push_buf) begin
        resp_buf_d[wr_ptr_q] = mem_resp_i;
        if (wr_ptr_q == ActualDepth - 1) begin
          wr_ptr_d = '0;
        end else begin
          wr_ptr_d = wr_ptr_q + 1'b1;
        end
        buf_count_d = buf_count_d + 1'b1;
      end

      req_ready = (cnt_q < BufDepth) || (resp_valid_o && resp_ready_i);
      req_ready_o = mem_req_ready_i && req_ready;
      mem_req_valid_o = req_valid_i && req_ready;

      if (req_valid_i && req_ready_o) begin
        cnt_d = cnt_d + 1'b1;
      end
      if (resp_valid_o && resp_ready_i) begin
        cnt_d = cnt_d - 1'b1;
      end
    end

    always @(posedge clk_i or negedge rst_ni) begin
      if (!rst_ni) begin
        cnt_q <= '0;
        rd_ptr_q <= '0;
        wr_ptr_q <= '0;
        buf_count_q <= '0;
        for (seq_idx = 0; seq_idx < ActualDepth; seq_idx = seq_idx + 1) begin
          resp_buf_q[seq_idx] <= '0;
        end
      end else begin
        cnt_q <= cnt_d;
        rd_ptr_q <= rd_ptr_d;
        wr_ptr_q <= wr_ptr_d;
        buf_count_q <= buf_count_d;
        for (seq_idx = 0; seq_idx < ActualDepth; seq_idx = seq_idx + 1) begin
          resp_buf_q[seq_idx] <= resp_buf_d[seq_idx];
        end
      end
    end
  end else begin : gen_no_buf
    assign mem_req_valid_o = req_valid_i;
    assign resp_valid_o = mem_req_valid_o && mem_req_ready_i && mem_resp_valid_i;
    assign req_ready_o = resp_ready_i && resp_valid_o;
    assign resp_o = mem_resp_i;
  end

endmodule
