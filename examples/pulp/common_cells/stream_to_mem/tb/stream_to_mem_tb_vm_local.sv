module stream_to_mem_vm_buf0 (
  input  logic         clk,
  input  logic         rst_n,
  input  logic [15:0]  req_i,
  input  logic         req_valid_i,
  output logic         req_ready_o,
  output logic [15:0]  resp_o,
  output logic         resp_valid_o,
  input  logic         resp_ready_i,
  output logic [15:0]  mem_req_o,
  output logic         mem_req_valid_o,
  input  logic         mem_req_ready_i,
  input  logic [15:0]  mem_resp_i,
  input  logic         mem_resp_valid_i
);
  stream_to_mem #(
    .DataWidth ( 16 ),
    .BufDepth  ( 0 )
  ) i_dut (
    .clk_i            ( clk              ),
    .rst_ni           ( rst_n            ),
    .req_i            ( req_i            ),
    .req_valid_i      ( req_valid_i      ),
    .req_ready_o      ( req_ready_o      ),
    .resp_o           ( resp_o           ),
    .resp_valid_o     ( resp_valid_o     ),
    .resp_ready_i     ( resp_ready_i     ),
    .mem_req_o        ( mem_req_o        ),
    .mem_req_valid_o  ( mem_req_valid_o  ),
    .mem_req_ready_i  ( mem_req_ready_i  ),
    .mem_resp_i       ( mem_resp_i       ),
    .mem_resp_valid_i ( mem_resp_valid_i )
  );
endmodule

module stream_to_mem_vm_buf1 (
  input  logic         clk,
  input  logic         rst_n,
  input  logic [15:0]  req_i,
  input  logic         req_valid_i,
  output logic         req_ready_o,
  output logic [15:0]  resp_o,
  output logic         resp_valid_o,
  input  logic         resp_ready_i,
  output logic [15:0]  mem_req_o,
  output logic         mem_req_valid_o,
  input  logic         mem_req_ready_i,
  input  logic [15:0]  mem_resp_i,
  input  logic         mem_resp_valid_i
);
  stream_to_mem #(
    .DataWidth ( 16 ),
    .BufDepth  ( 1 )
  ) i_dut (
    .clk_i            ( clk              ),
    .rst_ni           ( rst_n            ),
    .req_i            ( req_i            ),
    .req_valid_i      ( req_valid_i      ),
    .req_ready_o      ( req_ready_o      ),
    .resp_o           ( resp_o           ),
    .resp_valid_o     ( resp_valid_o     ),
    .resp_ready_i     ( resp_ready_i     ),
    .mem_req_o        ( mem_req_o        ),
    .mem_req_valid_o  ( mem_req_valid_o  ),
    .mem_req_ready_i  ( mem_req_ready_i  ),
    .mem_resp_i       ( mem_resp_i       ),
    .mem_resp_valid_i ( mem_resp_valid_i )
  );
endmodule

module stream_to_mem_vm_buf2 (
  input  logic         clk,
  input  logic         rst_n,
  input  logic [15:0]  req_i,
  input  logic         req_valid_i,
  output logic         req_ready_o,
  output logic [15:0]  resp_o,
  output logic         resp_valid_o,
  input  logic         resp_ready_i,
  output logic [15:0]  mem_req_o,
  output logic         mem_req_valid_o,
  input  logic         mem_req_ready_i,
  input  logic [15:0]  mem_resp_i,
  input  logic         mem_resp_valid_i
);
  stream_to_mem #(
    .DataWidth ( 16 ),
    .BufDepth  ( 2 )
  ) i_dut (
    .clk_i            ( clk              ),
    .rst_ni           ( rst_n            ),
    .req_i            ( req_i            ),
    .req_valid_i      ( req_valid_i      ),
    .req_ready_o      ( req_ready_o      ),
    .resp_o           ( resp_o           ),
    .resp_valid_o     ( resp_valid_o     ),
    .resp_ready_i     ( resp_ready_i     ),
    .mem_req_o        ( mem_req_o        ),
    .mem_req_valid_o  ( mem_req_valid_o  ),
    .mem_req_ready_i  ( mem_req_ready_i  ),
    .mem_resp_i       ( mem_resp_i       ),
    .mem_resp_valid_i ( mem_resp_valid_i )
  );
endmodule
