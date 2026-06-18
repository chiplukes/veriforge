// Copyright (c) 2014-2018 ETH Zurich, University of Bologna
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
// - Fabian Schuiki <fschuiki@iis.ee.ethz.ch>
// - Wolfgang Roenninger <wroennin@iis.ee.ethz.ch>
// - Andreas Kurth <akurth@iis.ee.ethz.ch>

/// An AXI4-Lite to AXI4 adapter.
module axi_lite_to_axi #(
  parameter int unsigned AxiDataWidth = 32'd0,
  parameter type req_lite_t = logic,
  parameter type resp_lite_t = logic,
  parameter type axi_req_t = logic,
  parameter type axi_resp_t = logic
) (
  input req_lite_t slv_req_lite_i,
  output resp_lite_t slv_resp_lite_o,
  input axi_pkg::cache_t slv_aw_cache_i,
  input axi_pkg::cache_t slv_ar_cache_i,
  output axi_req_t mst_req_o,
  input axi_resp_t mst_resp_i
);
  localparam int unsigned AxiSize = $clog2(AxiDataWidth / 8);
  localparam logic [2:0] AxiSizeLiteral = AxiSize;

  assign mst_req_o.aw.addr = slv_req_lite_i.aw.addr;
  assign mst_req_o.aw.prot = slv_req_lite_i.aw.prot;
  assign mst_req_o.aw.size = AxiSizeLiteral;
  assign mst_req_o.aw.burst = 2'b00;
  assign mst_req_o.aw.cache = slv_aw_cache_i;
  assign mst_req_o.aw_valid = slv_req_lite_i.aw_valid;
  assign mst_req_o.w.data = slv_req_lite_i.w.data;
  assign mst_req_o.w.strb = slv_req_lite_i.w.strb;
  assign mst_req_o.w.last = 1'b1;
  assign mst_req_o.w_valid = slv_req_lite_i.w_valid;
  assign mst_req_o.b_ready = slv_req_lite_i.b_ready;
  assign mst_req_o.ar.addr = slv_req_lite_i.ar.addr;
  assign mst_req_o.ar.prot = slv_req_lite_i.ar.prot;
  assign mst_req_o.ar.size = AxiSizeLiteral;
  assign mst_req_o.ar.burst = 2'b00;
  assign mst_req_o.ar.cache = slv_ar_cache_i;
  assign mst_req_o.ar_valid = slv_req_lite_i.ar_valid;
  assign mst_req_o.r_ready = slv_req_lite_i.r_ready;

  assign slv_resp_lite_o.aw_ready = mst_resp_i.aw_ready;
  assign slv_resp_lite_o.w_ready = mst_resp_i.w_ready;
  assign slv_resp_lite_o.b.resp = mst_resp_i.b.resp;
  assign slv_resp_lite_o.b_valid = mst_resp_i.b_valid;
  assign slv_resp_lite_o.ar_ready = mst_resp_i.ar_ready;
  assign slv_resp_lite_o.r.data = mst_resp_i.r.data;
  assign slv_resp_lite_o.r.resp = mst_resp_i.r.resp;
  assign slv_resp_lite_o.r_valid = mst_resp_i.r_valid;
endmodule
