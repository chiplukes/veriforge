module axi_lite_regs_basic_tb;
  logic clk;
  logic rst_n;
  logic [31:0] slv_aw_addr;
  logic [2:0]  slv_aw_prot;
  logic        slv_aw_valid;
  logic        slv_aw_ready;
  logic [31:0] slv_w_data;
  logic [3:0]  slv_w_strb;
  logic        slv_w_valid;
  logic        slv_w_ready;
  logic [1:0]  slv_b_resp;
  logic        slv_b_valid;
  logic        slv_b_ready;
  logic [31:0] slv_ar_addr;
  logic [2:0]  slv_ar_prot;
  logic        slv_ar_valid;
  logic        slv_ar_ready;
  logic [31:0] slv_r_data;
  logic [1:0]  slv_r_resp;
  logic        slv_r_valid;
  logic        slv_r_ready;
  logic [5:0]  wr_active;
  logic [5:0]  rd_active;
  logic [47:0] reg_d_flat;
  logic [5:0]  reg_load;
  logic [47:0] reg_q_flat;

  axi_lite_regs_typed_flat #(
    .RegNumBytes   ( 6                ),
    .AxiReadOnly   ( 6'b010010        ),
    .RegRstValFlat ( 48'h605040302010 )
  ) i_dut (
    .clk_i        ( clk         ),
    .rst_ni       ( rst_n       ),
    .slv_aw_addr  ( slv_aw_addr ),
    .slv_aw_prot  ( slv_aw_prot ),
    .slv_aw_valid ( slv_aw_valid ),
    .slv_aw_ready ( slv_aw_ready ),
    .slv_w_data   ( slv_w_data   ),
    .slv_w_strb   ( slv_w_strb   ),
    .slv_w_valid  ( slv_w_valid  ),
    .slv_w_ready  ( slv_w_ready  ),
    .slv_b_resp   ( slv_b_resp   ),
    .slv_b_valid  ( slv_b_valid  ),
    .slv_b_ready  ( slv_b_ready  ),
    .slv_ar_addr  ( slv_ar_addr  ),
    .slv_ar_prot  ( slv_ar_prot  ),
    .slv_ar_valid ( slv_ar_valid ),
    .slv_ar_ready ( slv_ar_ready ),
    .slv_r_data   ( slv_r_data   ),
    .slv_r_resp   ( slv_r_resp   ),
    .slv_r_valid  ( slv_r_valid  ),
    .slv_r_ready  ( slv_r_ready  ),
    .wr_active_o  ( wr_active    ),
    .rd_active_o  ( rd_active    ),
    .reg_d_flat_i ( reg_d_flat   ),
    .reg_load_i   ( reg_load     ),
    .reg_q_flat_o ( reg_q_flat   )
  );
endmodule

module axi_lite_regs_prot_tb;
  logic clk;
  logic rst_n;
  logic [31:0] slv_aw_addr;
  logic [2:0]  slv_aw_prot;
  logic        slv_aw_valid;
  logic        slv_aw_ready;
  logic [31:0] slv_w_data;
  logic [3:0]  slv_w_strb;
  logic        slv_w_valid;
  logic        slv_w_ready;
  logic [1:0]  slv_b_resp;
  logic        slv_b_valid;
  logic        slv_b_ready;
  logic [31:0] slv_ar_addr;
  logic [2:0]  slv_ar_prot;
  logic        slv_ar_valid;
  logic        slv_ar_ready;
  logic [31:0] slv_r_data;
  logic [1:0]  slv_r_resp;
  logic        slv_r_valid;
  logic        slv_r_ready;
  logic [3:0]  wr_active;
  logic [3:0]  rd_active;
  logic [31:0] reg_d_flat;
  logic [3:0]  reg_load;
  logic [31:0] reg_q_flat;

  axi_lite_regs_typed_flat #(
    .RegNumBytes   ( 4                ),
    .PrivProtOnly  ( 1'b1             ),
    .SecuProtOnly  ( 1'b1             ),
    .RegRstValFlat ( 32'h44332211     )
  ) i_dut (
    .clk_i        ( clk         ),
    .rst_ni       ( rst_n       ),
    .slv_aw_addr  ( slv_aw_addr ),
    .slv_aw_prot  ( slv_aw_prot ),
    .slv_aw_valid ( slv_aw_valid ),
    .slv_aw_ready ( slv_aw_ready ),
    .slv_w_data   ( slv_w_data   ),
    .slv_w_strb   ( slv_w_strb   ),
    .slv_w_valid  ( slv_w_valid  ),
    .slv_w_ready  ( slv_w_ready  ),
    .slv_b_resp   ( slv_b_resp   ),
    .slv_b_valid  ( slv_b_valid  ),
    .slv_b_ready  ( slv_b_ready  ),
    .slv_ar_addr  ( slv_ar_addr  ),
    .slv_ar_prot  ( slv_ar_prot  ),
    .slv_ar_valid ( slv_ar_valid ),
    .slv_ar_ready ( slv_ar_ready ),
    .slv_r_data   ( slv_r_data   ),
    .slv_r_resp   ( slv_r_resp   ),
    .slv_r_valid  ( slv_r_valid  ),
    .slv_r_ready  ( slv_r_ready  ),
    .wr_active_o  ( wr_active    ),
    .rd_active_o  ( rd_active    ),
    .reg_d_flat_i ( reg_d_flat   ),
    .reg_load_i   ( reg_load     ),
    .reg_q_flat_o ( reg_q_flat   )
  );
endmodule

module axi_lite_regs_basic_exec_tb;
  logic clk;
  logic rst_n;
  logic [31:0] slv_aw_addr;
  logic [2:0]  slv_aw_prot;
  logic        slv_aw_valid;
  logic        slv_aw_ready;
  logic [31:0] slv_w_data;
  logic [3:0]  slv_w_strb;
  logic        slv_w_valid;
  logic        slv_w_ready;
  logic [1:0]  slv_b_resp;
  logic        slv_b_valid;
  logic        slv_b_ready;
  logic [31:0] slv_ar_addr;
  logic [2:0]  slv_ar_prot;
  logic        slv_ar_valid;
  logic        slv_ar_ready;
  logic [31:0] slv_r_data;
  logic [1:0]  slv_r_resp;
  logic        slv_r_valid;
  logic        slv_r_ready;
  logic [5:0]  wr_active;
  logic [5:0]  rd_active;
  logic [47:0] reg_d_flat;
  logic [5:0]  reg_load;
  logic [47:0] reg_q_flat;

  axi_lite_regs_typed_flat #(
    .RegNumBytes   ( 6                ),
    .AxiReadOnly   ( 6'b010010        ),
    .RegRstValFlat ( 48'h605040302010 )
  ) i_dut (
    .clk_i        ( clk         ),
    .rst_ni       ( rst_n       ),
    .slv_aw_addr  ( slv_aw_addr ),
    .slv_aw_prot  ( slv_aw_prot ),
    .slv_aw_valid ( slv_aw_valid ),
    .slv_aw_ready ( slv_aw_ready ),
    .slv_w_data   ( slv_w_data   ),
    .slv_w_strb   ( slv_w_strb   ),
    .slv_w_valid  ( slv_w_valid  ),
    .slv_w_ready  ( slv_w_ready  ),
    .slv_b_resp   ( slv_b_resp   ),
    .slv_b_valid  ( slv_b_valid  ),
    .slv_b_ready  ( slv_b_ready  ),
    .slv_ar_addr  ( slv_ar_addr  ),
    .slv_ar_prot  ( slv_ar_prot  ),
    .slv_ar_valid ( slv_ar_valid ),
    .slv_ar_ready ( slv_ar_ready ),
    .slv_r_data   ( slv_r_data   ),
    .slv_r_resp   ( slv_r_resp   ),
    .slv_r_valid  ( slv_r_valid  ),
    .slv_r_ready  ( slv_r_ready  ),
    .wr_active_o  ( wr_active    ),
    .rd_active_o  ( rd_active    ),
    .reg_d_flat_i ( reg_d_flat   ),
    .reg_load_i   ( reg_load     ),
    .reg_q_flat_o ( reg_q_flat   )
  );
endmodule

module axi_lite_regs_prot_exec_tb;
  logic clk;
  logic rst_n;
  logic [31:0] slv_aw_addr;
  logic [2:0]  slv_aw_prot;
  logic        slv_aw_valid;
  logic        slv_aw_ready;
  logic [31:0] slv_w_data;
  logic [3:0]  slv_w_strb;
  logic        slv_w_valid;
  logic        slv_w_ready;
  logic [1:0]  slv_b_resp;
  logic        slv_b_valid;
  logic        slv_b_ready;
  logic [31:0] slv_ar_addr;
  logic [2:0]  slv_ar_prot;
  logic        slv_ar_valid;
  logic        slv_ar_ready;
  logic [31:0] slv_r_data;
  logic [1:0]  slv_r_resp;
  logic        slv_r_valid;
  logic        slv_r_ready;
  logic [3:0]  wr_active;
  logic [3:0]  rd_active;
  logic [31:0] reg_d_flat;
  logic [3:0]  reg_load;
  logic [31:0] reg_q_flat;

  axi_lite_regs_typed_flat #(
    .RegNumBytes   ( 4            ),
    .PrivProtOnly  ( 1'b1         ),
    .SecuProtOnly  ( 1'b1         ),
    .RegRstValFlat ( 32'h44332211 )
  ) i_dut (
    .clk_i        ( clk         ),
    .rst_ni       ( rst_n       ),
    .slv_aw_addr  ( slv_aw_addr ),
    .slv_aw_prot  ( slv_aw_prot ),
    .slv_aw_valid ( slv_aw_valid ),
    .slv_aw_ready ( slv_aw_ready ),
    .slv_w_data   ( slv_w_data   ),
    .slv_w_strb   ( slv_w_strb   ),
    .slv_w_valid  ( slv_w_valid  ),
    .slv_w_ready  ( slv_w_ready  ),
    .slv_b_resp   ( slv_b_resp   ),
    .slv_b_valid  ( slv_b_valid  ),
    .slv_b_ready  ( slv_b_ready  ),
    .slv_ar_addr  ( slv_ar_addr  ),
    .slv_ar_prot  ( slv_ar_prot  ),
    .slv_ar_valid ( slv_ar_valid ),
    .slv_ar_ready ( slv_ar_ready ),
    .slv_r_data   ( slv_r_data   ),
    .slv_r_resp   ( slv_r_resp   ),
    .slv_r_valid  ( slv_r_valid  ),
    .slv_r_ready  ( slv_r_ready  ),
    .wr_active_o  ( wr_active    ),
    .rd_active_o  ( rd_active    ),
    .reg_d_flat_i ( reg_d_flat   ),
    .reg_load_i   ( reg_load     ),
    .reg_q_flat_o ( reg_q_flat   )
  );
endmodule

module axi_lite_regs_typed_flat #(
  parameter int unsigned RegNumBytes = 6,
  parameter bit PrivProtOnly = 1'b0,
  parameter bit SecuProtOnly = 1'b0,
  parameter logic [RegNumBytes-1:0] AxiReadOnly = {RegNumBytes{1'b0}},
  parameter logic [RegNumBytes*8-1:0] RegRstValFlat = {RegNumBytes{8'h00}}
) (
  input  logic clk_i,
  input  logic rst_ni,
  input  logic [31:0] slv_aw_addr,
  input  logic [2:0]  slv_aw_prot,
  input  logic        slv_aw_valid,
  output logic        slv_aw_ready,
  input  logic [31:0] slv_w_data,
  input  logic [3:0]  slv_w_strb,
  input  logic        slv_w_valid,
  output logic        slv_w_ready,
  output logic [1:0]  slv_b_resp,
  output logic        slv_b_valid,
  input  logic        slv_b_ready,
  input  logic [31:0] slv_ar_addr,
  input  logic [2:0]  slv_ar_prot,
  input  logic        slv_ar_valid,
  output logic        slv_ar_ready,
  output logic [31:0] slv_r_data,
  output logic [1:0]  slv_r_resp,
  output logic        slv_r_valid,
  input  logic        slv_r_ready,
  output logic [RegNumBytes-1:0] wr_active_o,
  output logic [RegNumBytes-1:0] rd_active_o,
  input  logic [RegNumBytes*8-1:0] reg_d_flat_i,
  input  logic [RegNumBytes-1:0] reg_load_i,
  output logic [RegNumBytes*8-1:0] reg_q_flat_o
);
  typedef struct packed {
    logic [31:0] addr;
    logic [2:0]  prot;
  } lite_aw_chan_t;

  typedef struct packed {
    logic [31:0] data;
    logic [3:0]  strb;
  } lite_w_chan_t;

  typedef struct packed {
    logic [1:0] resp;
  } lite_b_chan_t;

  typedef struct packed {
    logic [31:0] addr;
    logic [2:0]  prot;
  } lite_ar_chan_t;

  typedef struct packed {
    logic [31:0] data;
    logic [1:0]  resp;
  } lite_r_chan_t;

  typedef struct packed {
    lite_aw_chan_t aw;
    logic          aw_valid;
    lite_w_chan_t  w;
    logic          w_valid;
    logic          b_ready;
    lite_ar_chan_t ar;
    logic          ar_valid;
    logic          r_ready;
  } req_lite_t;

  typedef struct packed {
    logic         aw_ready;
    logic         w_ready;
    lite_b_chan_t b;
    logic         b_valid;
    logic         ar_ready;
    lite_r_chan_t r;
    logic         r_valid;
  } resp_lite_t;

  req_lite_t  axi_req;
  resp_lite_t axi_resp;
  logic [7:0] reg_d_bytes [RegNumBytes];
  logic [7:0] reg_q [RegNumBytes];

  for (genvar i = 0; i < RegNumBytes; i++) begin : gen_flatten
    assign reg_d_bytes[i] = reg_d_flat_i[i*8 +: 8];
    assign reg_q_flat_o[i*8 +: 8] = reg_q[i];
  end

  always_comb begin
    axi_req.aw.addr = slv_aw_addr;
    axi_req.aw.prot = slv_aw_prot;
    axi_req.aw_valid = slv_aw_valid;
    axi_req.w.data = slv_w_data;
    axi_req.w.strb = slv_w_strb;
    axi_req.w_valid = slv_w_valid;
    axi_req.b_ready = slv_b_ready;
    axi_req.ar.addr = slv_ar_addr;
    axi_req.ar.prot = slv_ar_prot;
    axi_req.ar_valid = slv_ar_valid;
    axi_req.r_ready = slv_r_ready;
  end

  assign slv_aw_ready = axi_resp.aw_ready;
  assign slv_w_ready = axi_resp.w_ready;
  assign slv_b_resp = axi_resp.b.resp;
  assign slv_b_valid = axi_resp.b_valid;
  assign slv_ar_ready = axi_resp.ar_ready;
  assign slv_r_data = axi_resp.r.data;
  assign slv_r_resp = axi_resp.r.resp;
  assign slv_r_valid = axi_resp.r_valid;

  axi_lite_regs #(
    .RegNumBytes   ( RegNumBytes   ),
    .AxiAddrWidth  ( 32           ),
    .AxiDataWidth  ( 32           ),
    .PrivProtOnly  ( PrivProtOnly  ),
    .SecuProtOnly  ( SecuProtOnly  ),
    .AxiReadOnly   ( AxiReadOnly   ),
    .RegRstValFlat ( RegRstValFlat ),
    .req_lite_t    ( req_lite_t    ),
    .resp_lite_t   ( resp_lite_t   )
  ) i_dut (
    .clk_i       ( clk_i       ),
    .rst_ni      ( rst_ni      ),
    .axi_req_i   ( axi_req     ),
    .axi_resp_o  ( axi_resp    ),
    .wr_active_o ( wr_active_o ),
    .rd_active_o ( rd_active_o ),
    .reg_d_i     ( reg_d_bytes ),
    .reg_load_i  ( reg_load_i  ),
    .reg_q_o     ( reg_q       )
  );
endmodule

module axi_lite_regs_basic_typed_exec_tb;
  logic clk;
  logic rst_n;
  logic [31:0] slv_aw_addr;
  logic [2:0]  slv_aw_prot;
  logic        slv_aw_valid;
  logic        slv_aw_ready;
  logic [31:0] slv_w_data;
  logic [3:0]  slv_w_strb;
  logic        slv_w_valid;
  logic        slv_w_ready;
  logic [1:0]  slv_b_resp;
  logic        slv_b_valid;
  logic        slv_b_ready;
  logic [31:0] slv_ar_addr;
  logic [2:0]  slv_ar_prot;
  logic        slv_ar_valid;
  logic        slv_ar_ready;
  logic [31:0] slv_r_data;
  logic [1:0]  slv_r_resp;
  logic        slv_r_valid;
  logic        slv_r_ready;
  logic [5:0]  wr_active;
  logic [5:0]  rd_active;
  logic [47:0] reg_d_flat;
  logic [5:0]  reg_load;
  logic [47:0] reg_q_flat;

  typedef struct packed {
    logic [31:0] addr;
    logic [2:0]  prot;
  } lite_aw_chan_t;

  typedef struct packed {
    logic [31:0] data;
    logic [3:0]  strb;
  } lite_w_chan_t;

  typedef struct packed {
    logic [1:0] resp;
  } lite_b_chan_t;

  typedef struct packed {
    logic [31:0] addr;
    logic [2:0]  prot;
  } lite_ar_chan_t;

  typedef struct packed {
    logic [31:0] data;
    logic [1:0]  resp;
  } lite_r_chan_t;

  typedef struct packed {
    lite_aw_chan_t aw;
    logic          aw_valid;
    lite_w_chan_t  w;
    logic          w_valid;
    logic          b_ready;
    lite_ar_chan_t ar;
    logic          ar_valid;
    logic          r_ready;
  } req_lite_t;

  typedef struct packed {
    logic         aw_ready;
    logic         w_ready;
    lite_b_chan_t b;
    logic         b_valid;
    logic         ar_ready;
    lite_r_chan_t r;
    logic         r_valid;
  } resp_lite_t;

  req_lite_t  axi_req;
  resp_lite_t axi_resp;
  logic [7:0] reg_d_bytes [6];
  logic [7:0] reg_q [6];
  assign reg_d_bytes[0] = reg_d_flat[7:0];
  assign reg_d_bytes[1] = reg_d_flat[15:8];
  assign reg_d_bytes[2] = reg_d_flat[23:16];
  assign reg_d_bytes[3] = reg_d_flat[31:24];
  assign reg_d_bytes[4] = reg_d_flat[39:32];
  assign reg_d_bytes[5] = reg_d_flat[47:40];
  assign reg_q_flat = {reg_q[5], reg_q[4], reg_q[3], reg_q[2], reg_q[1], reg_q[0]};

  always_comb begin
    axi_req.aw.addr = slv_aw_addr;
    axi_req.aw.prot = slv_aw_prot;
    axi_req.aw_valid = slv_aw_valid;
    axi_req.w.data = slv_w_data;
    axi_req.w.strb = slv_w_strb;
    axi_req.w_valid = slv_w_valid;
    axi_req.b_ready = slv_b_ready;
    axi_req.ar.addr = slv_ar_addr;
    axi_req.ar.prot = slv_ar_prot;
    axi_req.ar_valid = slv_ar_valid;
    axi_req.r_ready = slv_r_ready;
  end

  assign slv_aw_ready = axi_resp.aw_ready;
  assign slv_w_ready = axi_resp.w_ready;
  assign slv_b_resp = axi_resp.b.resp;
  assign slv_b_valid = axi_resp.b_valid;
  assign slv_ar_ready = axi_resp.ar_ready;
  assign slv_r_data = axi_resp.r.data;
  assign slv_r_resp = axi_resp.r.resp;
  assign slv_r_valid = axi_resp.r_valid;

  axi_lite_regs #(
    .RegNumBytes   ( 6                ),
    .AxiAddrWidth  ( 32               ),
    .AxiDataWidth  ( 32               ),
    .AxiReadOnly   ( 6'b010010        ),
    .RegRstValFlat ( 48'h605040302010 ),
    .req_lite_t    ( req_lite_t       ),
    .resp_lite_t   ( resp_lite_t      )
  ) i_dut (
    .clk_i       ( clk         ),
    .rst_ni      ( rst_n       ),
    .axi_req_i   ( axi_req     ),
    .axi_resp_o  ( axi_resp    ),
    .wr_active_o ( wr_active   ),
    .rd_active_o ( rd_active   ),
    .reg_d_i     ( reg_d_bytes ),
    .reg_load_i  ( reg_load    ),
    .reg_q_o     ( reg_q       )
  );
endmodule

module axi_lite_regs_prot_typed_exec_tb;
  logic clk;
  logic rst_n;
  logic [31:0] slv_aw_addr;
  logic [2:0]  slv_aw_prot;
  logic        slv_aw_valid;
  logic        slv_aw_ready;
  logic [31:0] slv_w_data;
  logic [3:0]  slv_w_strb;
  logic        slv_w_valid;
  logic        slv_w_ready;
  logic [1:0]  slv_b_resp;
  logic        slv_b_valid;
  logic        slv_b_ready;
  logic [31:0] slv_ar_addr;
  logic [2:0]  slv_ar_prot;
  logic        slv_ar_valid;
  logic        slv_ar_ready;
  logic [31:0] slv_r_data;
  logic [1:0]  slv_r_resp;
  logic        slv_r_valid;
  logic        slv_r_ready;
  logic [3:0]  wr_active;
  logic [3:0]  rd_active;
  logic [31:0] reg_d_flat;
  logic [3:0]  reg_load;
  logic [31:0] reg_q_flat;

  typedef struct packed {
    logic [31:0] addr;
    logic [2:0]  prot;
  } lite_aw_chan_t;

  typedef struct packed {
    logic [31:0] data;
    logic [3:0]  strb;
  } lite_w_chan_t;

  typedef struct packed {
    logic [1:0] resp;
  } lite_b_chan_t;

  typedef struct packed {
    logic [31:0] addr;
    logic [2:0]  prot;
  } lite_ar_chan_t;

  typedef struct packed {
    logic [31:0] data;
    logic [1:0]  resp;
  } lite_r_chan_t;

  typedef struct packed {
    lite_aw_chan_t aw;
    logic          aw_valid;
    lite_w_chan_t  w;
    logic          w_valid;
    logic          b_ready;
    lite_ar_chan_t ar;
    logic          ar_valid;
    logic          r_ready;
  } req_lite_t;

  typedef struct packed {
    logic         aw_ready;
    logic         w_ready;
    lite_b_chan_t b;
    logic         b_valid;
    logic         ar_ready;
    lite_r_chan_t r;
    logic         r_valid;
  } resp_lite_t;

  req_lite_t  axi_req;
  resp_lite_t axi_resp;
  logic [7:0] reg_d_bytes [4];
  logic [7:0] reg_q [4];

  assign reg_d_bytes[0] = reg_d_flat[7:0];
  assign reg_d_bytes[1] = reg_d_flat[15:8];
  assign reg_d_bytes[2] = reg_d_flat[23:16];
  assign reg_d_bytes[3] = reg_d_flat[31:24];
  assign reg_q_flat = {reg_q[3], reg_q[2], reg_q[1], reg_q[0]};

  assign axi_req.aw.addr = slv_aw_addr;
  assign axi_req.aw.prot = slv_aw_prot;
  assign axi_req.aw_valid = slv_aw_valid;
  assign axi_req.w.data = slv_w_data;
  assign axi_req.w.strb = slv_w_strb;
  assign axi_req.w_valid = slv_w_valid;
  assign axi_req.b_ready = slv_b_ready;
  assign axi_req.ar.addr = slv_ar_addr;
  assign axi_req.ar.prot = slv_ar_prot;
  assign axi_req.ar_valid = slv_ar_valid;
  assign axi_req.r_ready = slv_r_ready;

  assign slv_aw_ready = axi_resp.aw_ready;
  assign slv_w_ready = axi_resp.w_ready;
  assign slv_b_resp = axi_resp.b.resp;
  assign slv_b_valid = axi_resp.b_valid;
  assign slv_ar_ready = axi_resp.ar_ready;
  assign slv_r_data = axi_resp.r.data;
  assign slv_r_resp = axi_resp.r.resp;
  assign slv_r_valid = axi_resp.r_valid;

  axi_lite_regs #(
    .RegNumBytes   ( 4            ),
    .AxiAddrWidth  ( 32           ),
    .AxiDataWidth  ( 32           ),
    .PrivProtOnly  ( 1'b1         ),
    .SecuProtOnly  ( 1'b1         ),
    .RegRstValFlat ( 32'h44332211 ),
    .req_lite_t    ( req_lite_t   ),
    .resp_lite_t   ( resp_lite_t  )
  ) i_dut (
    .clk_i       ( clk         ),
    .rst_ni      ( rst_n       ),
    .axi_req_i   ( axi_req     ),
    .axi_resp_o  ( axi_resp    ),
    .wr_active_o ( wr_active   ),
    .rd_active_o ( rd_active   ),
    .reg_d_i     ( reg_d_bytes ),
    .reg_load_i  ( reg_load    ),
    .reg_q_o     ( reg_q       )
  );
endmodule
