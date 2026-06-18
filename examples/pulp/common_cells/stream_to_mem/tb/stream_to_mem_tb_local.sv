module stream_to_mem_tb_local;
  localparam int unsigned DataWidth = 16;

  logic clk;
  logic rst_n;
  integer errors;

  logic [DataWidth-1:0] req0;
  logic                 req0_valid;
  logic                 req0_ready;
  logic [DataWidth-1:0] resp0;
  logic                 resp0_valid;
  logic                 resp0_ready;
  logic [DataWidth-1:0] mem_req0;
  logic                 mem_req0_valid;
  logic                 mem_req0_ready;
  logic [DataWidth-1:0] mem_resp0;
  logic                 mem_resp0_valid;

  logic [DataWidth-1:0] req1;
  logic                 req1_valid;
  logic                 req1_ready;
  logic [DataWidth-1:0] resp1;
  logic                 resp1_valid;
  logic                 resp1_ready;
  logic [DataWidth-1:0] mem_req1;
  logic                 mem_req1_valid;
  logic                 mem_req1_ready;
  logic [DataWidth-1:0] mem_resp1;
  logic                 mem_resp1_valid;
  logic [DataWidth-1:0] mem1_pipe_data;
  logic                 mem1_pipe_valid;

  logic [DataWidth-1:0] req2;
  logic                 req2_valid;
  logic                 req2_ready;
  logic [DataWidth-1:0] resp2;
  logic                 resp2_valid;
  logic                 resp2_ready;
  logic [DataWidth-1:0] mem_req2;
  logic                 mem_req2_valid;
  logic                 mem_req2_ready;
  logic [DataWidth-1:0] mem_resp2;
  logic                 mem_resp2_valid;
  logic [DataWidth-1:0] mem2_pipe_data0;
  logic [DataWidth-1:0] mem2_pipe_data1;
  logic                 mem2_pipe_valid0;
  logic                 mem2_pipe_valid1;

  stream_to_mem #(
    .DataWidth ( DataWidth ),
    .BufDepth  ( 0 )
  ) i_dut_buf0 (
    .clk_i            ( clk            ),
    .rst_ni           ( rst_n          ),
    .req_i            ( req0           ),
    .req_valid_i      ( req0_valid     ),
    .req_ready_o      ( req0_ready     ),
    .resp_o           ( resp0          ),
    .resp_valid_o     ( resp0_valid    ),
    .resp_ready_i     ( resp0_ready    ),
    .mem_req_o        ( mem_req0       ),
    .mem_req_valid_o  ( mem_req0_valid ),
    .mem_req_ready_i  ( mem_req0_ready ),
    .mem_resp_i       ( mem_resp0      ),
    .mem_resp_valid_i ( mem_resp0_valid )
  );

  stream_to_mem #(
    .DataWidth ( DataWidth ),
    .BufDepth  ( 1 )
  ) i_dut_buf1 (
    .clk_i            ( clk            ),
    .rst_ni           ( rst_n          ),
    .req_i            ( req1           ),
    .req_valid_i      ( req1_valid     ),
    .req_ready_o      ( req1_ready     ),
    .resp_o           ( resp1          ),
    .resp_valid_o     ( resp1_valid    ),
    .resp_ready_i     ( resp1_ready    ),
    .mem_req_o        ( mem_req1       ),
    .mem_req_valid_o  ( mem_req1_valid ),
    .mem_req_ready_i  ( mem_req1_ready ),
    .mem_resp_i       ( mem_resp1      ),
    .mem_resp_valid_i ( mem_resp1_valid )
  );

  stream_to_mem #(
    .DataWidth ( DataWidth ),
    .BufDepth  ( 2 )
  ) i_dut_buf2 (
    .clk_i            ( clk            ),
    .rst_ni           ( rst_n          ),
    .req_i            ( req2           ),
    .req_valid_i      ( req2_valid     ),
    .req_ready_o      ( req2_ready     ),
    .resp_o           ( resp2          ),
    .resp_valid_o     ( resp2_valid    ),
    .resp_ready_i     ( resp2_ready    ),
    .mem_req_o        ( mem_req2       ),
    .mem_req_valid_o  ( mem_req2_valid ),
    .mem_req_ready_i  ( mem_req2_ready ),
    .mem_resp_i       ( mem_resp2      ),
    .mem_resp_valid_i ( mem_resp2_valid )
  );

  assign mem_req0_ready = 1'b1;
  assign mem_resp0 = mem_req0 ^ 16'h00ff;
  assign mem_resp0_valid = mem_req0_valid & mem_req0_ready;

  assign mem_req1_ready = 1'b1;
  assign mem_req2_ready = 1'b1;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      mem1_pipe_data <= '0;
      mem1_pipe_valid <= 1'b0;
      mem_resp1 <= '0;
      mem_resp1_valid <= 1'b0;
    end else begin
      mem_resp1 <= mem1_pipe_data;
      mem_resp1_valid <= mem1_pipe_valid;
      mem1_pipe_data <= mem_req1 + 16'h0100;
      mem1_pipe_valid <= mem_req1_valid & mem_req1_ready;
    end
  end

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      mem2_pipe_data0 <= '0;
      mem2_pipe_data1 <= '0;
      mem2_pipe_valid0 <= 1'b0;
      mem2_pipe_valid1 <= 1'b0;
      mem_resp2 <= '0;
      mem_resp2_valid <= 1'b0;
    end else begin
      mem_resp2 <= mem2_pipe_data1;
      mem_resp2_valid <= mem2_pipe_valid1;
      mem2_pipe_data1 <= mem2_pipe_data0;
      mem2_pipe_valid1 <= mem2_pipe_valid0;
      mem2_pipe_data0 <= mem_req2 + 16'h0200;
      mem2_pipe_valid0 <= mem_req2_valid & mem_req2_ready;
    end
  end

  initial begin
    clk = 1'b0;
  end

  always #5 clk = ~clk;

  initial begin
    rst_n = 1'b0;
    #25;
    rst_n = 1'b1;
  end

  initial begin
    errors = 0;

    req0 = '0;
    req0_valid = 1'b0;
    resp0_ready = 1'b0;

    req1 = '0;
    req1_valid = 1'b0;
    resp1_ready = 1'b0;

    req2 = '0;
    req2_valid = 1'b0;
    resp2_ready = 1'b0;

    #36;

    resp0_ready = 1'b1;
    req0 = 16'h1234;
    req0_valid = 1'b1;
    #1;
    if (req0_ready !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf0 request was not accepted");
    end
    if (mem_req0_valid !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf0 memory request valid missing");
    end
    if (resp0_valid !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf0 response valid missing");
    end
    if (resp0 !== 16'h12cb) begin
      errors = errors + 1;
      $display("FAIL buf0 response mismatch expected=12cb actual=%h", resp0);
    end
    @(posedge clk);
    req0_valid = 1'b0;
    #1;
    if (resp0_valid !== 1'b0) begin
      errors = errors + 1;
      $display("FAIL buf0 response should clear after handshake");
    end

    req1 = 16'h0011;
    req1_valid = 1'b1;
    resp1_ready = 1'b0;
    #1;
    if (req1_ready !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf1 first request was not accepted");
    end
    @(posedge clk);
    req1_valid = 1'b0;
    @(posedge clk);
    #1;
    if (resp1_valid !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf1 missing buffered response");
    end
    if (resp1 !== 16'h0111) begin
      errors = errors + 1;
      $display("FAIL buf1 first response mismatch expected=0111 actual=%h", resp1);
    end

    req1 = 16'h0022;
    req1_valid = 1'b1;
    #1;
    if (req1_ready !== 1'b0) begin
      errors = errors + 1;
      $display("FAIL buf1 request should stall while buffer is full");
    end
    if (mem_req1_valid !== 1'b0) begin
      errors = errors + 1;
      $display("FAIL buf1 memory request should be blocked while stalled");
    end

    resp1_ready = 1'b1;
    #1;
    if (req1_ready !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf1 request should reopen when response drains");
    end
    if (mem_req1_valid !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf1 second request did not issue while draining response");
    end
    @(posedge clk);
    #1;
    if (resp1_valid !== 1'b0) begin
      errors = errors + 1;
      $display("FAIL buf1 should have a one-cycle bubble before the second response");
    end

    req1_valid = 1'b0;
    @(posedge clk);
    req1_valid = 1'b0;
    #1;
    if (resp1_valid !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf1 second response missing");
    end
    if (resp1 !== 16'h0122) begin
      errors = errors + 1;
      $display("FAIL buf1 second response mismatch expected=0122 actual=%h", resp1);
    end
    @(posedge clk);
    #1;
    if (resp1_valid !== 1'b0) begin
      errors = errors + 1;
      $display("FAIL buf1 response should clear after second handshake");
    end

    resp2_ready = 1'b0;
    req2 = 16'h0033;
    req2_valid = 1'b1;
    #1;
    if (req2_ready !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf2 first request was not accepted");
    end
    @(posedge clk);
    req2 = 16'h0044;
    req2_valid = 1'b1;
    #1;
    if (req2_ready !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf2 second request was not accepted");
    end
    @(posedge clk);
    req2 = 16'h0055;
    req2_valid = 1'b1;
    @(posedge clk);
    #1;
    if (req2_ready !== 1'b0) begin
      errors = errors + 1;
      $display("FAIL buf2 third request should stall at outstanding limit");
    end
    if (resp2_valid !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf2 first buffered response missing");
    end
    if (resp2 !== 16'h0233) begin
      errors = errors + 1;
      $display("FAIL buf2 first response mismatch expected=0233 actual=%h", resp2);
    end

    @(posedge clk);
    #1;
    if (resp2 !== 16'h0233) begin
      errors = errors + 1;
      $display("FAIL buf2 first response should remain stable while stalled");
    end

    resp2_ready = 1'b1;
    #1;
    if (req2_ready !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf2 third request should issue while draining first response");
    end
    if (mem_req2_valid !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf2 third request did not reach memory");
    end
    @(posedge clk);
    req2_valid = 1'b0;
    #1;
    if (resp2_valid !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf2 second response missing");
    end
    if (resp2 !== 16'h0244) begin
      errors = errors + 1;
      $display("FAIL buf2 second response mismatch expected=0244 actual=%h", resp2);
    end
    @(posedge clk);
    #1;
    if (resp2_valid !== 1'b0) begin
      errors = errors + 1;
      $display("FAIL buf2 should have a one-cycle bubble before the third response");
    end

    @(posedge clk);
    #1;
    if (resp2_valid !== 1'b1) begin
      errors = errors + 1;
      $display("FAIL buf2 third response missing");
    end
    if (resp2 !== 16'h0255) begin
      errors = errors + 1;
      $display("FAIL buf2 third response mismatch expected=0255 actual=%h", resp2);
    end
    @(posedge clk);
    #1;
    if (resp2_valid !== 1'b0) begin
      errors = errors + 1;
      $display("FAIL buf2 response should clear after final handshake");
    end

    if (errors == 0) begin
      $display("PASS stream_to_mem deterministic checks");
    end else begin
      $display("FAIL stream_to_mem deterministic checks errors=%0d", errors);
    end
    $finish;
  end
endmodule
