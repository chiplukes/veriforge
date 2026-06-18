// Copyright (c) 2020 ETH Zurich and University of Bologna
//
// Copyright and related rights are licensed under the Solderpad Hardware
// License, Version 0.51 (the "License"); you may not use this file except in
// compliance with the License.  You may obtain a copy of the License at
// http://solderpad.org/licenses/SHL-0.51. Unless required by applicable law
// or agreed to in writing, software, hardware and materials distributed under
// this License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
// CONDITIONS OF ANY KIND, either express or implied. See the License for the
// specific language governing permissions and limitations under the License.

/// Parser-friendly local mailbox surface.
module axi_lite_mailbox #(
  parameter int unsigned MailboxDepth = 32'd0,
  parameter bit IrqEdgeTrig = 1'b0,
  parameter bit IrqActHigh = 1'b1,
  parameter int unsigned AxiAddrWidth = 32'd0,
  parameter int unsigned AxiDataWidth = 32'd0,
  parameter type req_lite_t = logic,
  parameter type resp_lite_t = logic,
  parameter type addr_t = logic [AxiAddrWidth-1:0]
) (
  input logic clk_i,
  input logic rst_ni,
  input logic test_i,
  input req_lite_t [1:0] slv_reqs_i,
  output resp_lite_t [1:0] slv_resps_o,
  output logic [1:0] irq_o,
  input addr_t [1:0] base_addr_i
);
  typedef logic [AxiDataWidth-1:0] data_t;
  typedef logic [1:0] usage_t;

  resp_lite_t [1:0] slv_resps;
  logic [1:0] irq_raw;
  logic [1:0] clear_irq_unused;

  data_t mbox_0_to_1_mem0_q;
  data_t mbox_0_to_1_mem1_q;
  data_t mbox_1_to_0_mem0_q;
  data_t mbox_1_to_0_mem1_q;
  data_t p0_r_data;
  data_t p1_r_data;
  data_t p0_w_data;
  data_t p1_w_data;
  logic mbox_0_to_1_rd_ptr_q;
  logic mbox_0_to_1_wr_ptr_q;
  usage_t mbox_0_to_1_count_q;
  logic mbox_1_to_0_rd_ptr_q;
  logic mbox_1_to_0_wr_ptr_q;
  usage_t mbox_1_to_0_count_q;
  logic p0_w_full;
  usage_t p0_w_usage;
  logic p0_w_push;
  logic p0_w_flush;
  logic p0_r_empty;
  usage_t p0_r_usage;
  logic p0_r_pop;
  logic p0_r_flush;
  logic p1_w_full;
  usage_t p1_w_usage;
  logic p1_w_push;
  logic p1_w_flush;
  logic p1_r_empty;
  usage_t p1_r_usage;
  logic p1_r_pop;
  logic p1_r_flush;
  logic _unused_cfg;

  always_comb begin
    slv_resps_o = slv_resps;
    irq_o[0] = IrqActHigh ? irq_raw[0] : ~irq_raw[0];
    irq_o[1] = IrqActHigh ? irq_raw[1] : ~irq_raw[1];
  end

  assign _unused_cfg = test_i | IrqEdgeTrig;
  assign p0_w_full = (mbox_0_to_1_count_q == MailboxDepth);
  assign p0_w_usage = mbox_0_to_1_count_q;
  assign p1_r_empty = (mbox_0_to_1_count_q == 0);
  assign p1_r_usage = mbox_0_to_1_count_q;
  assign p1_w_full = (mbox_1_to_0_count_q == MailboxDepth);
  assign p1_w_usage = mbox_1_to_0_count_q;
  assign p0_r_empty = (mbox_1_to_0_count_q == 0);
  assign p0_r_usage = mbox_1_to_0_count_q;

  axi_lite_mailbox_slave #(
      .MailboxDepth(MailboxDepth),
      .AxiAddrWidth(AxiAddrWidth),
      .AxiDataWidth(AxiDataWidth),
      .req_lite_t(req_lite_t),
      .resp_lite_t(resp_lite_t),
      .addr_t(addr_t),
      .data_t(data_t),
      .usage_t(usage_t)
  ) i_port0 (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .slv_req_i(slv_reqs_i[0]),
      .slv_resp_o(slv_resps[0]),
      .base_addr_i(base_addr_i[0]),
      .mbox_w_data_o(p0_w_data),
      .mbox_w_full_i(p0_w_full),
      .mbox_w_push_o(p0_w_push),
      .mbox_w_flush_o(p0_w_flush),
      .mbox_w_usage_i(p0_w_usage),
      .mbox_r_data_i(p0_r_data),
      .mbox_r_empty_i(p0_r_empty),
      .mbox_r_pop_o(p0_r_pop),
      .mbox_r_flush_o(p0_r_flush),
      .mbox_r_usage_i(p0_r_usage),
      .irq_o(irq_raw[0]),
      .clear_irq_o(clear_irq_unused[0])
  );

  axi_lite_mailbox_slave #(
      .MailboxDepth(MailboxDepth),
      .AxiAddrWidth(AxiAddrWidth),
      .AxiDataWidth(AxiDataWidth),
      .req_lite_t(req_lite_t),
      .resp_lite_t(resp_lite_t),
      .addr_t(addr_t),
      .data_t(data_t),
      .usage_t(usage_t)
  ) i_port1 (
      .clk_i(clk_i),
      .rst_ni(rst_ni),
      .slv_req_i(slv_reqs_i[1]),
      .slv_resp_o(slv_resps[1]),
      .base_addr_i(base_addr_i[1]),
      .mbox_w_data_o(p1_w_data),
      .mbox_w_full_i(p1_w_full),
      .mbox_w_push_o(p1_w_push),
      .mbox_w_flush_o(p1_w_flush),
      .mbox_w_usage_i(p1_w_usage),
      .mbox_r_data_i(p1_r_data),
      .mbox_r_empty_i(p1_r_empty),
      .mbox_r_pop_o(p1_r_pop),
      .mbox_r_flush_o(p1_r_flush),
      .mbox_r_usage_i(p1_r_usage),
      .irq_o(irq_raw[1]),
      .clear_irq_o(clear_irq_unused[1])
  );

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      mbox_0_to_1_mem0_q <= '0;
      mbox_0_to_1_mem1_q <= '0;
      mbox_1_to_0_mem0_q <= '0;
      mbox_1_to_0_mem1_q <= '0;
      p0_r_data <= '0;
      p1_r_data <= '0;
      mbox_0_to_1_rd_ptr_q <= 1'b0;
      mbox_0_to_1_wr_ptr_q <= 1'b0;
      mbox_0_to_1_count_q <= '0;
      mbox_1_to_0_rd_ptr_q <= 1'b0;
      mbox_1_to_0_wr_ptr_q <= 1'b0;
      mbox_1_to_0_count_q <= '0;
    end else begin
      if (mbox_0_to_1_rd_ptr_q) begin
        p1_r_data <= mbox_0_to_1_mem1_q;
      end else begin
        p1_r_data <= mbox_0_to_1_mem0_q;
      end

      if (mbox_1_to_0_rd_ptr_q) begin
        p0_r_data <= mbox_1_to_0_mem1_q;
      end else begin
        p0_r_data <= mbox_1_to_0_mem0_q;
      end

      if (p0_w_flush || p1_r_flush) begin
        mbox_0_to_1_rd_ptr_q <= 1'b0;
        mbox_0_to_1_wr_ptr_q <= 1'b0;
        mbox_0_to_1_count_q <= '0;
      end else begin
        if (p0_w_push && (mbox_0_to_1_count_q != MailboxDepth)) begin
          if (mbox_0_to_1_wr_ptr_q) begin
            mbox_0_to_1_mem1_q <= p0_w_data;
          end else begin
            mbox_0_to_1_mem0_q <= p0_w_data;
          end
          mbox_0_to_1_wr_ptr_q <= ~mbox_0_to_1_wr_ptr_q;
        end
        if (p1_r_pop && (mbox_0_to_1_count_q != 0)) begin
          mbox_0_to_1_rd_ptr_q <= ~mbox_0_to_1_rd_ptr_q;
        end
        case ({p0_w_push && (mbox_0_to_1_count_q != MailboxDepth), p1_r_pop && (mbox_0_to_1_count_q != 0)})
          2'b10: mbox_0_to_1_count_q <= mbox_0_to_1_count_q + 1'b1;
          2'b01: mbox_0_to_1_count_q <= mbox_0_to_1_count_q - 1'b1;
          default: mbox_0_to_1_count_q <= mbox_0_to_1_count_q;
        endcase
      end

      if (p1_w_flush || p0_r_flush) begin
        mbox_1_to_0_rd_ptr_q <= 1'b0;
        mbox_1_to_0_wr_ptr_q <= 1'b0;
        mbox_1_to_0_count_q <= '0;
      end else begin
        if (p1_w_push && (mbox_1_to_0_count_q != MailboxDepth)) begin
          if (mbox_1_to_0_wr_ptr_q) begin
            mbox_1_to_0_mem1_q <= p1_w_data;
          end else begin
            mbox_1_to_0_mem0_q <= p1_w_data;
          end
          mbox_1_to_0_wr_ptr_q <= ~mbox_1_to_0_wr_ptr_q;
        end
        if (p0_r_pop && (mbox_1_to_0_count_q != 0)) begin
          mbox_1_to_0_rd_ptr_q <= ~mbox_1_to_0_rd_ptr_q;
        end
        case ({p1_w_push && (mbox_1_to_0_count_q != MailboxDepth), p0_r_pop && (mbox_1_to_0_count_q != 0)})
          2'b10: mbox_1_to_0_count_q <= mbox_1_to_0_count_q + 1'b1;
          2'b01: mbox_1_to_0_count_q <= mbox_1_to_0_count_q - 1'b1;
          default: mbox_1_to_0_count_q <= mbox_1_to_0_count_q;
        endcase
      end
    end
  end
endmodule


module axi_lite_mailbox_slave #(
  parameter int unsigned MailboxDepth = 32'd16,
  parameter int unsigned AxiAddrWidth = 32'd32,
  parameter int unsigned AxiDataWidth = 32'd32,
  parameter type req_lite_t = logic,
  parameter type resp_lite_t = logic,
  parameter type addr_t = logic [AxiAddrWidth-1:0],
  parameter type data_t = logic [AxiDataWidth-1:0],
  parameter type usage_t = logic
) (
  input logic clk_i,
  input logic rst_ni,
  input req_lite_t slv_req_i,
  output resp_lite_t slv_resp_o,
  input addr_t base_addr_i,
  output data_t mbox_w_data_o,
  input logic mbox_w_full_i,
  output logic mbox_w_push_o,
  output logic mbox_w_flush_o,
  input usage_t mbox_w_usage_i,
  input data_t mbox_r_data_i,
  input logic mbox_r_empty_i,
  output logic mbox_r_pop_o,
  output logic mbox_r_flush_o,
  input usage_t mbox_r_usage_i,
  output logic irq_o,
  output logic clear_irq_o
);
  localparam logic [1:0] RESP_OKAY = 2'b00;
  localparam logic [1:0] RESP_SLVERR = 2'b10;
  localparam addr_t REG_MBOXW = addr_t'(32'h0);
  localparam addr_t REG_MBOXR = addr_t'(32'h4);
  localparam addr_t REG_STATUS = addr_t'(32'h8);
  localparam addr_t REG_ERROR = addr_t'(32'hC);
  localparam addr_t REG_WIRQT = addr_t'(32'h10);
  localparam addr_t REG_RIRQT = addr_t'(32'h14);
  localparam addr_t REG_IRQS = addr_t'(32'h18);
  localparam addr_t REG_IRQEN = addr_t'(32'h1C);
  localparam addr_t REG_IRQP = addr_t'(32'h20);
  localparam addr_t REG_CTRL = addr_t'(32'h24);
  localparam data_t DEPTH_LIMIT = data_t'((MailboxDepth > 0) ? (MailboxDepth - 1) : 0);

  logic [1:0] error_q;
  logic [1:0] error_n;
  data_t wirqt_q;
  data_t wirqt_n;
  data_t rirqt_q;
  data_t rirqt_n;
  logic [2:0] irqs_q;
  logic [2:0] irqs_n;
  logic [2:0] irqen_q;
  logic [2:0] irqen_n;
  logic [1:0] b_resp_q;
  logic [1:0] b_resp_n;
  logic b_valid_q;
  logic b_valid_n;
  data_t r_data_q;
  data_t r_data_n;
  logic [1:0] r_resp_q;
  logic [1:0] r_resp_n;
  logic r_valid_q;
  logic r_valid_n;
  logic [3:0] status_w;
  logic aw_dec_valid;
  logic ar_dec_valid;
  addr_t aw_offset;
  addr_t ar_offset;

  assign aw_dec_valid = (slv_req_i.aw.addr >= base_addr_i)
      && (slv_req_i.aw.addr < (base_addr_i + addr_t'(32'h28)))
      && (slv_req_i.aw.addr[1:0] == 2'b00);
  assign ar_dec_valid = (slv_req_i.ar.addr >= base_addr_i)
      && (slv_req_i.ar.addr < (base_addr_i + addr_t'(32'h28)))
      && (slv_req_i.ar.addr[1:0] == 2'b00);
  assign aw_offset = slv_req_i.aw.addr - base_addr_i;
  assign ar_offset = slv_req_i.ar.addr - base_addr_i;
  assign status_w = {
      (mbox_r_usage_i > rirqt_q[1:0]),
      (mbox_w_usage_i > wirqt_q[1:0]),
      mbox_w_full_i,
      mbox_r_empty_i
  };

  always_comb begin
    slv_resp_o = '0;
    slv_resp_o.aw_ready = (~b_valid_q) & slv_req_i.w_valid;
    slv_resp_o.w_ready = (~b_valid_q) & slv_req_i.aw_valid;
    slv_resp_o.b.resp = b_resp_q;
    slv_resp_o.b_valid = b_valid_q;
    slv_resp_o.ar_ready = ~r_valid_q;
    slv_resp_o.r.data = r_data_q;
    slv_resp_o.r.resp = r_resp_q;
    slv_resp_o.r_valid = r_valid_q;

    mbox_w_data_o = '0;
    mbox_w_push_o = 1'b0;
    mbox_w_flush_o = 1'b0;
    mbox_r_pop_o = 1'b0;
    mbox_r_flush_o = 1'b0;
    clear_irq_o = 1'b0;
    irq_o = |(irqs_q & irqen_q);

    error_n = error_q;
    wirqt_n = wirqt_q;
    rirqt_n = rirqt_q;
    irqs_n = irqs_q;
    irqen_n = irqen_q;
    b_resp_n = b_resp_q;
    b_valid_n = b_valid_q;
    r_data_n = r_data_q;
    r_resp_n = r_resp_q;
    r_valid_n = r_valid_q;

    if (b_valid_q && slv_req_i.b_ready) begin
      b_valid_n = 1'b0;
    end
    if (r_valid_q && slv_req_i.r_ready) begin
      r_valid_n = 1'b0;
    end

    if (!irqs_q[1] && status_w[3]) begin
      irqs_n[1] = 1'b1;
    end
    if (!irqs_q[0] && status_w[2]) begin
      irqs_n[0] = 1'b1;
    end

    if (slv_req_i.ar_valid && !r_valid_q) begin
      r_valid_n = 1'b1;
      r_data_n = '0;
      r_resp_n = RESP_SLVERR;
      if (ar_dec_valid) begin
        case (ar_offset)
          REG_MBOXW: begin
            r_data_n = data_t'(32'hFEEDC0DE);
            r_resp_n = RESP_OKAY;
          end
          REG_MBOXR: begin
            if (!mbox_r_empty_i) begin
              r_data_n = mbox_r_data_i;
              r_resp_n = RESP_OKAY;
              mbox_r_pop_o = 1'b1;
            end else begin
              r_data_n = data_t'(32'hFEEDDEAD);
              error_n[0] = 1'b1;
              irqs_n[2] = 1'b1;
            end
          end
          REG_STATUS: begin
            r_data_n = data_t'({28'h0, status_w});
            r_resp_n = RESP_OKAY;
          end
          REG_ERROR: begin
            r_data_n = data_t'({30'h0, error_q});
            r_resp_n = RESP_OKAY;
            error_n = 2'b00;
          end
          REG_WIRQT: begin
            r_data_n = wirqt_q;
            r_resp_n = RESP_OKAY;
          end
          REG_RIRQT: begin
            r_data_n = rirqt_q;
            r_resp_n = RESP_OKAY;
          end
          REG_IRQS: begin
            r_data_n = data_t'({29'h0, irqs_q});
            r_resp_n = RESP_OKAY;
          end
          REG_IRQEN: begin
            r_data_n = data_t'({29'h0, irqen_q});
            r_resp_n = RESP_OKAY;
          end
          REG_IRQP: begin
            r_data_n = data_t'({29'h0, (irqs_q & irqen_q)});
            r_resp_n = RESP_OKAY;
          end
          REG_CTRL: begin
            r_data_n = data_t'({30'h0, mbox_r_flush_o, mbox_w_flush_o});
            r_resp_n = RESP_OKAY;
          end
          default: begin
          end
        endcase
      end
    end

    if (slv_req_i.aw_valid && slv_req_i.w_valid && !b_valid_q) begin
      b_valid_n = 1'b1;
      b_resp_n = RESP_SLVERR;
      if (aw_dec_valid) begin
        case (aw_offset)
          REG_MBOXW: begin
            if (!mbox_w_full_i) begin
              if (slv_req_i.w.strb[0]) mbox_w_data_o[7:0] = slv_req_i.w.data[7:0];
              if (slv_req_i.w.strb[1]) mbox_w_data_o[15:8] = slv_req_i.w.data[15:8];
              if (slv_req_i.w.strb[2]) mbox_w_data_o[23:16] = slv_req_i.w.data[23:16];
              if (slv_req_i.w.strb[3]) mbox_w_data_o[31:24] = slv_req_i.w.data[31:24];
              mbox_w_push_o = 1'b1;
              b_resp_n = RESP_OKAY;
            end else begin
              error_n[1] = 1'b1;
              irqs_n[2] = 1'b1;
            end
          end
          REG_WIRQT: begin
            if (slv_req_i.w.strb[0]) wirqt_n[7:0] = slv_req_i.w.data[7:0];
            if (slv_req_i.w.strb[1]) wirqt_n[15:8] = slv_req_i.w.data[15:8];
            if (slv_req_i.w.strb[2]) wirqt_n[23:16] = slv_req_i.w.data[23:16];
            if (slv_req_i.w.strb[3]) wirqt_n[31:24] = slv_req_i.w.data[31:24];
            if (wirqt_n >= MailboxDepth) begin
              wirqt_n = DEPTH_LIMIT;
            end
            b_resp_n = RESP_OKAY;
          end
          REG_RIRQT: begin
            if (slv_req_i.w.strb[0]) rirqt_n[7:0] = slv_req_i.w.data[7:0];
            if (slv_req_i.w.strb[1]) rirqt_n[15:8] = slv_req_i.w.data[15:8];
            if (slv_req_i.w.strb[2]) rirqt_n[23:16] = slv_req_i.w.data[23:16];
            if (slv_req_i.w.strb[3]) rirqt_n[31:24] = slv_req_i.w.data[31:24];
            if (rirqt_n >= MailboxDepth) begin
              rirqt_n = DEPTH_LIMIT;
            end
            b_resp_n = RESP_OKAY;
          end
          REG_IRQS: begin
            if (slv_req_i.w.strb[0]) begin
              if (slv_req_i.w.data[2]) irqs_n[2] = 1'b0;
              if (slv_req_i.w.data[1]) irqs_n[1] = 1'b0;
              if (slv_req_i.w.data[0]) irqs_n[0] = 1'b0;
              clear_irq_o = |slv_req_i.w.data[2:0];
            end
            b_resp_n = RESP_OKAY;
          end
          REG_IRQEN: begin
            if (slv_req_i.w.strb[0]) begin
              irqen_n = slv_req_i.w.data[2:0];
            end
            b_resp_n = RESP_OKAY;
          end
          REG_CTRL: begin
            if (slv_req_i.w.strb[0]) begin
              mbox_r_flush_o = slv_req_i.w.data[1];
              mbox_w_flush_o = slv_req_i.w.data[0];
            end
            b_resp_n = RESP_OKAY;
          end
          default: begin
          end
        endcase
      end
    end
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      error_q <= 2'b00;
      wirqt_q <= '0;
      rirqt_q <= '0;
      irqs_q <= 3'b000;
      irqen_q <= 3'b000;
      b_resp_q <= RESP_OKAY;
      b_valid_q <= 1'b0;
      r_data_q <= '0;
      r_resp_q <= RESP_OKAY;
      r_valid_q <= 1'b0;
    end else begin
      error_q <= error_n;
      wirqt_q <= wirqt_n;
      rirqt_q <= rirqt_n;
      irqs_q <= irqs_n;
      irqen_q <= irqen_n;
      b_resp_q <= b_resp_n;
      b_valid_q <= b_valid_n;
      r_data_q <= r_data_n;
      r_resp_q <= r_resp_n;
      r_valid_q <= r_valid_n;
    end
  end
endmodule
