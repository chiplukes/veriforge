// Copyright (c) 2014-2020 ETH Zurich, University of Bologna
//
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License.  You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.
//
// Authors:
// - Wolfgang Roenninger <wroennin@iis.ee.ethz.ch>
// - Andreas Kurth <akurth@iis.ee.ethz.ch>
// - Fabian Schuiki <fschuiki@iis.ee.ethz.ch>

/// A parser-friendly local copy of the AXI4+ATOP to AXI4-Lite adapter.
///
/// The upstream file routes through additional helper modules for atomics and
/// burst splitting. This local copy preserves the typed top-level and
/// ID-reflection surface but wires directly into the ID-reflect stage so the
/// imported example stays self-contained.
module axi_to_axi_lite #(
  parameter int unsigned AxiAddrWidth    = 32'd0,
  parameter int unsigned AxiDataWidth    = 32'd0,
  parameter int unsigned AxiIdWidth      = 32'd0,
  parameter int unsigned AxiUserWidth    = 32'd0,
  parameter int unsigned AxiMaxWriteTxns = 32'd0,
  parameter int unsigned AxiMaxReadTxns  = 32'd0,
  parameter bit          FullBW          = 0,
  parameter bit          FallThrough     = 1'b1,
  parameter type         full_req_t      = logic,
  parameter type         full_resp_t     = logic,
  parameter type         lite_req_t      = logic,
  parameter type         lite_resp_t     = logic
) (
  input  logic       clk_i,
  input  logic       rst_ni,
  input  logic       test_i,
  input  full_req_t  slv_req_i,
  output full_resp_t slv_resp_o,
  output lite_req_t  mst_req_o,
  input  lite_resp_t mst_resp_i
);
  logic _unused_test_i;
  logic _unused_fullbw;
  logic [31:0] _unused_cfg;

  assign _unused_test_i = test_i;
  assign _unused_fullbw = FullBW;
  assign _unused_cfg = AxiAddrWidth + AxiDataWidth + AxiUserWidth;

  axi_to_axi_lite_id_reflect #(
    .AxiIdWidth      ( AxiIdWidth      ),
    .AxiMaxWriteTxns ( AxiMaxWriteTxns ),
    .AxiMaxReadTxns  ( AxiMaxReadTxns  ),
    .FallThrough     ( FallThrough     ),
    .full_req_t      ( full_req_t      ),
    .full_resp_t     ( full_resp_t     ),
    .lite_req_t      ( lite_req_t      ),
    .lite_resp_t     ( lite_resp_t     )
  ) i_axi_to_axi_lite_id_reflect (
    .clk_i      ( clk_i      ),
    .rst_ni     ( rst_ni     ),
    .test_i     ( test_i     ),
    .slv_req_i  ( slv_req_i  ),
    .slv_resp_o ( slv_resp_o ),
    .mst_req_o  ( mst_req_o  ),
    .mst_resp_i ( mst_resp_i )
  );
endmodule


module axi_to_axi_lite_id_reflect #(
  parameter int unsigned AxiIdWidth      = 32'd0,
  parameter int unsigned AxiMaxWriteTxns = 32'd0,
  parameter int unsigned AxiMaxReadTxns  = 32'd0,
  parameter bit          FallThrough     = 1'b1,
  parameter type         full_req_t      = logic,
  parameter type         full_resp_t     = logic,
  parameter type         lite_req_t      = logic,
  parameter type         lite_resp_t     = logic
) (
  input  logic       clk_i,
  input  logic       rst_ni,
  input  logic       test_i,
  input  full_req_t  slv_req_i,
  output full_resp_t slv_resp_o,
  output lite_req_t  mst_req_o,
  input  lite_resp_t mst_resp_i
);
  typedef logic [AxiIdWidth-1:0] id_t;

  logic aw_pending_q;
  logic ar_pending_q;
  id_t  aw_reflect_id_q;
  id_t  ar_reflect_id_q;
  logic aw_accept;
  logic aw_complete;
  logic ar_accept;
  logic ar_complete;
  logic _unused_test_i;
  logic _unused_fallthrough;
  logic [31:0] _unused_depth_cfg;

  assign _unused_test_i = test_i;
  assign _unused_fallthrough = FallThrough;
  assign _unused_depth_cfg = AxiMaxWriteTxns + AxiMaxReadTxns;

  assign slv_resp_o.aw_ready = mst_resp_i.aw_ready & ~aw_pending_q;
  assign slv_resp_o.w_ready = mst_resp_i.w_ready;
  assign slv_resp_o.b.id = aw_reflect_id_q;
  assign slv_resp_o.b.resp = mst_resp_i.b.resp;
  assign slv_resp_o.b_valid = mst_resp_i.b_valid & aw_pending_q;
  assign slv_resp_o.ar_ready = mst_resp_i.ar_ready & ~ar_pending_q;
  assign slv_resp_o.r.id = ar_reflect_id_q;
  assign slv_resp_o.r.data = mst_resp_i.r.data;
  assign slv_resp_o.r.resp = mst_resp_i.r.resp;
  assign slv_resp_o.r.last = 1'b1;
  assign slv_resp_o.r_valid = mst_resp_i.r_valid & ar_pending_q;

  assign mst_req_o.aw.addr = slv_req_i.aw.addr;
  assign mst_req_o.aw.prot = slv_req_i.aw.prot;
  assign mst_req_o.aw_valid = slv_req_i.aw_valid & ~aw_pending_q;
  assign mst_req_o.w.data = slv_req_i.w.data;
  assign mst_req_o.w.strb = slv_req_i.w.strb;
  assign mst_req_o.w_valid = slv_req_i.w_valid;
  assign mst_req_o.b_ready = slv_req_i.b_ready & aw_pending_q;
  assign mst_req_o.ar.addr = slv_req_i.ar.addr;
  assign mst_req_o.ar.prot = slv_req_i.ar.prot;
  assign mst_req_o.ar_valid = slv_req_i.ar_valid & ~ar_pending_q;
  assign mst_req_o.r_ready = slv_req_i.r_ready & ar_pending_q;

  assign aw_accept = mst_req_o.aw_valid & slv_resp_o.aw_ready;
  assign aw_complete = slv_resp_o.b_valid & mst_req_o.b_ready;
  assign ar_accept = mst_req_o.ar_valid & slv_resp_o.ar_ready;
  assign ar_complete = slv_resp_o.r_valid & mst_req_o.r_ready;

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      aw_pending_q <= 1'b0;
      ar_pending_q <= 1'b0;
      aw_reflect_id_q <= '0;
      ar_reflect_id_q <= '0;
    end else begin
      if (aw_complete) begin
        aw_pending_q <= 1'b0;
      end
      if (ar_complete) begin
        ar_pending_q <= 1'b0;
      end
      if (aw_accept) begin
        aw_pending_q <= 1'b1;
        aw_reflect_id_q <= slv_req_i.aw.id;
      end
      if (ar_accept) begin
        ar_pending_q <= 1'b1;
        ar_reflect_id_q <= slv_req_i.ar.id;
      end
    end
  end
endmodule
