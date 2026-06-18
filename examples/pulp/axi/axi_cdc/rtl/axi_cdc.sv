// Copyright (c) 2019 ETH Zurich and University of Bologna.
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License. You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.
// SPDX-License-Identifier: SHL-0.51
module axi_cdc #(
    parameter type aw_chan_t = logic,
    parameter type w_chan_t = logic,
    parameter type b_chan_t = logic,
    parameter type ar_chan_t = logic,
    parameter type r_chan_t = logic,
    parameter type axi_req_t = logic,
    parameter type axi_resp_t = logic,
    parameter int unsigned LogDepth = 1,
    parameter int unsigned SyncStages = 2
) (
    input logic src_clk_i,
    input logic src_rst_ni,
    input axi_req_t src_req_i,
    output axi_resp_t src_resp_o,
    input logic dst_clk_i,
    input logic dst_rst_ni,
    output axi_req_t dst_req_o,
    input axi_resp_t dst_resp_i
);
    localparam int unsigned AW_WIDTH = $bits(aw_chan_t);
    localparam int unsigned W_WIDTH = $bits(w_chan_t);
    localparam int unsigned B_WIDTH = $bits(b_chan_t);
    localparam int unsigned AR_WIDTH = $bits(ar_chan_t);
    localparam int unsigned R_WIDTH = $bits(r_chan_t);

    logic [AW_WIDTH-1:0] aw_src_data;
    logic [AW_WIDTH-1:0] aw_dst_data;
    logic [W_WIDTH-1:0] w_src_data;
    logic [W_WIDTH-1:0] w_dst_data;
    logic [B_WIDTH-1:0] b_dst_data;
    logic [B_WIDTH-1:0] b_src_data;
    logic [AR_WIDTH-1:0] ar_src_data;
    logic [AR_WIDTH-1:0] ar_dst_data;
    logic [R_WIDTH-1:0] r_dst_data;
    logic [R_WIDTH-1:0] r_src_data;

    logic aw_src_ready;
    logic aw_dst_valid;
    logic w_src_ready;
    logic w_dst_valid;
    logic b_dst_ready;
    logic b_src_valid;
    logic ar_src_ready;
    logic ar_dst_valid;
    logic r_dst_ready;
    logic r_src_valid;

    logic unused_sync_stages;

    assign unused_sync_stages = SyncStages != 0;

    assign aw_src_data = src_req_i.aw;
    assign w_src_data = src_req_i.w;
    assign b_dst_data = dst_resp_i.b;
    assign ar_src_data = src_req_i.ar;
    assign r_dst_data = dst_resp_i.r;

    always_comb begin
        src_resp_o = '0;
        src_resp_o.aw_ready = aw_src_ready;
        src_resp_o.w_ready = w_src_ready;
        src_resp_o.b = b_src_data;
        src_resp_o.b_valid = b_src_valid;
        src_resp_o.ar_ready = ar_src_ready;
        src_resp_o.r = r_src_data;
        src_resp_o.r_valid = r_src_valid;

        dst_req_o = '0;
        dst_req_o.aw = aw_dst_data;
        dst_req_o.aw_valid = aw_dst_valid;
        dst_req_o.w = w_dst_data;
        dst_req_o.w_valid = w_dst_valid;
        dst_req_o.b_ready = b_dst_ready;
        dst_req_o.ar = ar_dst_data;
        dst_req_o.ar_valid = ar_dst_valid;
        dst_req_o.r_ready = r_dst_ready;
    end

    cdc_fifo_2phase #(
        .WIDTH(AW_WIDTH),
        .LOG_DEPTH(LogDepth)
    ) i_aw_cdc (
        .src_rst_ni(src_rst_ni),
        .src_clk_i(src_clk_i),
        .src_data_i(aw_src_data),
        .src_valid_i(src_req_i.aw_valid),
        .src_ready_o(aw_src_ready),
        .dst_rst_ni(dst_rst_ni),
        .dst_clk_i(dst_clk_i),
        .dst_data_o(aw_dst_data),
        .dst_valid_o(aw_dst_valid),
        .dst_ready_i(dst_resp_i.aw_ready)
    );

    cdc_fifo_2phase #(
        .WIDTH(W_WIDTH),
        .LOG_DEPTH(LogDepth)
    ) i_w_cdc (
        .src_rst_ni(src_rst_ni),
        .src_clk_i(src_clk_i),
        .src_data_i(w_src_data),
        .src_valid_i(src_req_i.w_valid),
        .src_ready_o(w_src_ready),
        .dst_rst_ni(dst_rst_ni),
        .dst_clk_i(dst_clk_i),
        .dst_data_o(w_dst_data),
        .dst_valid_o(w_dst_valid),
        .dst_ready_i(dst_resp_i.w_ready)
    );

    cdc_fifo_2phase #(
        .WIDTH(B_WIDTH),
        .LOG_DEPTH(LogDepth)
    ) i_b_cdc (
        .src_rst_ni(dst_rst_ni),
        .src_clk_i(dst_clk_i),
        .src_data_i(b_dst_data),
        .src_valid_i(dst_resp_i.b_valid),
        .src_ready_o(b_dst_ready),
        .dst_rst_ni(src_rst_ni),
        .dst_clk_i(src_clk_i),
        .dst_data_o(b_src_data),
        .dst_valid_o(b_src_valid),
        .dst_ready_i(src_req_i.b_ready)
    );

    cdc_fifo_2phase #(
        .WIDTH(AR_WIDTH),
        .LOG_DEPTH(LogDepth)
    ) i_ar_cdc (
        .src_rst_ni(src_rst_ni),
        .src_clk_i(src_clk_i),
        .src_data_i(ar_src_data),
        .src_valid_i(src_req_i.ar_valid),
        .src_ready_o(ar_src_ready),
        .dst_rst_ni(dst_rst_ni),
        .dst_clk_i(dst_clk_i),
        .dst_data_o(ar_dst_data),
        .dst_valid_o(ar_dst_valid),
        .dst_ready_i(dst_resp_i.ar_ready)
    );

    cdc_fifo_2phase #(
        .WIDTH(R_WIDTH),
        .LOG_DEPTH(LogDepth)
    ) i_r_cdc (
        .src_rst_ni(dst_rst_ni),
        .src_clk_i(dst_clk_i),
        .src_data_i(r_dst_data),
        .src_valid_i(dst_resp_i.r_valid),
        .src_ready_o(r_dst_ready),
        .dst_rst_ni(src_rst_ni),
        .dst_clk_i(src_clk_i),
        .dst_data_o(r_src_data),
        .dst_valid_o(r_src_valid),
        .dst_ready_i(src_req_i.r_ready)
    );
endmodule
