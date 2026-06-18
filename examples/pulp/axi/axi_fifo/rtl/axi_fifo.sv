// Copyright (c) 2014-2022 ETH Zurich, University of Bologna
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
// - Noah Huetter <huettern@ethz.ch>
// - Florian Zaruba <zarubaf@iis.ee.ethz.ch>
// - Fabian Schuiki <fschuiki@iis.ee.ethz.ch>

/// A parser-friendly local copy of the AXI FIFO surface.
///
/// The upstream implementation instantiates typed `fifo_v3` helpers and an
/// interface wrapper. The local executable path for this example uses a flat
/// deterministic shell in `tb/axi_fifo_tb.sv`; this typed module is kept for
/// parser coverage of the imported AXI FIFO surface.
module axi_fifo #(
    parameter int unsigned Depth       = 32'd1,
    parameter bit          FallThrough = 1'b0,
    parameter type         aw_chan_t   = logic,
    parameter type         w_chan_t    = logic,
    parameter type         b_chan_t    = logic,
    parameter type         ar_chan_t   = logic,
    parameter type         r_chan_t    = logic,
    parameter type         axi_req_t   = logic,
    parameter type         axi_resp_t  = logic
) (
    input  logic      clk_i,
    input  logic      rst_ni,
    input  logic      test_i,
    input  axi_req_t  slv_req_i,
    output axi_resp_t slv_resp_o,
    output axi_req_t  mst_req_o,
    input  axi_resp_t mst_resp_i
);
    logic [31:0] _unused_cfg;
    logic _unused_test_i;

    assign _unused_cfg = Depth + FallThrough;
    assign _unused_test_i = test_i & clk_i & rst_ni;

    if (Depth == 0) begin : gen_passthrough
        assign mst_req_o.aw = slv_req_i.aw;
        assign mst_req_o.aw_valid = slv_req_i.aw_valid;
        assign mst_req_o.w = slv_req_i.w;
        assign mst_req_o.w_valid = slv_req_i.w_valid;
        assign mst_req_o.b_ready = slv_req_i.b_ready;
        assign mst_req_o.ar = slv_req_i.ar;
        assign mst_req_o.ar_valid = slv_req_i.ar_valid;
        assign mst_req_o.r_ready = slv_req_i.r_ready;

        assign slv_resp_o.aw_ready = mst_resp_i.aw_ready;
        assign slv_resp_o.w_ready = mst_resp_i.w_ready;
        assign slv_resp_o.b = mst_resp_i.b;
        assign slv_resp_o.b_valid = mst_resp_i.b_valid;
        assign slv_resp_o.ar_ready = mst_resp_i.ar_ready;
        assign slv_resp_o.r = mst_resp_i.r;
        assign slv_resp_o.r_valid = mst_resp_i.r_valid;
    end else begin : gen_depth1
        logic aw_pending_q;
        logic w_pending_q;
        logic b_pending_q;
        logic ar_pending_q;
        logic r_pending_q;

        aw_chan_t aw_chan_q;
        w_chan_t w_chan_q;
        b_chan_t b_chan_q;
        ar_chan_t ar_chan_q;
        r_chan_t r_chan_q;

        logic aw_push;
        logic aw_pop;
        logic w_push;
        logic w_pop;
        logic b_push;
        logic b_pop;
        logic ar_push;
        logic ar_pop;
        logic r_push;
        logic r_pop;

        assign slv_resp_o.aw_ready = ~aw_pending_q;
        assign mst_req_o.aw = aw_chan_q;
        assign mst_req_o.aw_valid = aw_pending_q;

        assign slv_resp_o.w_ready = ~w_pending_q;
        assign mst_req_o.w = w_chan_q;
        assign mst_req_o.w_valid = w_pending_q;

        assign slv_resp_o.b = b_chan_q;
        assign slv_resp_o.b_valid = b_pending_q;
        assign mst_req_o.b_ready = ~b_pending_q;

        assign slv_resp_o.ar_ready = ~ar_pending_q;
        assign mst_req_o.ar = ar_chan_q;
        assign mst_req_o.ar_valid = ar_pending_q;

        assign slv_resp_o.r = r_chan_q;
        assign slv_resp_o.r_valid = r_pending_q;
        assign mst_req_o.r_ready = ~r_pending_q;

        assign aw_push = slv_req_i.aw_valid & slv_resp_o.aw_ready;
        assign aw_pop = mst_req_o.aw_valid & mst_resp_i.aw_ready;
        assign w_push = slv_req_i.w_valid & slv_resp_o.w_ready;
        assign w_pop = mst_req_o.w_valid & mst_resp_i.w_ready;
        assign b_push = mst_resp_i.b_valid & mst_req_o.b_ready;
        assign b_pop = slv_resp_o.b_valid & slv_req_i.b_ready;
        assign ar_push = slv_req_i.ar_valid & slv_resp_o.ar_ready;
        assign ar_pop = mst_req_o.ar_valid & mst_resp_i.ar_ready;
        assign r_push = mst_resp_i.r_valid & mst_req_o.r_ready;
        assign r_pop = slv_resp_o.r_valid & slv_req_i.r_ready;

        always_ff @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                aw_pending_q <= 1'b0;
                w_pending_q <= 1'b0;
                b_pending_q <= 1'b0;
                ar_pending_q <= 1'b0;
                r_pending_q <= 1'b0;
                aw_chan_q <= '0;
                w_chan_q <= '0;
                b_chan_q <= '0;
                ar_chan_q <= '0;
                r_chan_q <= '0;
            end else begin
                if (aw_pop) begin
                    aw_pending_q <= 1'b0;
                end
                if (w_pop) begin
                    w_pending_q <= 1'b0;
                end
                if (b_pop) begin
                    b_pending_q <= 1'b0;
                end
                if (ar_pop) begin
                    ar_pending_q <= 1'b0;
                end
                if (r_pop) begin
                    r_pending_q <= 1'b0;
                end

                if (aw_push) begin
                    aw_pending_q <= 1'b1;
                    aw_chan_q <= slv_req_i.aw;
                end
                if (w_push) begin
                    w_pending_q <= 1'b1;
                    w_chan_q <= slv_req_i.w;
                end
                if (b_push) begin
                    b_pending_q <= 1'b1;
                    b_chan_q <= mst_resp_i.b;
                end
                if (ar_push) begin
                    ar_pending_q <= 1'b1;
                    ar_chan_q <= slv_req_i.ar;
                end
                if (r_push) begin
                    r_pending_q <= 1'b1;
                    r_chan_q <= mst_resp_i.r;
                end
            end
        end
    end
endmodule
