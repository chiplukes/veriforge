module axi_lite_regs #(
  parameter int unsigned RegNumBytes = 32'd0,
  parameter int unsigned AxiAddrWidth = 32'd0,
  parameter int unsigned AxiDataWidth = 32'd32,
  parameter bit PrivProtOnly = 1'b0,
  parameter bit SecuProtOnly = 1'b0,
  parameter logic [RegNumBytes-1:0] AxiReadOnly = {RegNumBytes{1'b0}},
  parameter logic [RegNumBytes*8-1:0] RegRstValFlat = {RegNumBytes{8'h00}},
  parameter type req_lite_t = logic,
  parameter type resp_lite_t = logic
) (
  input  logic clk_i,
  input  logic rst_ni,
  input  req_lite_t axi_req_i,
  output resp_lite_t axi_resp_o,
  output logic [RegNumBytes-1:0] wr_active_o,
  output logic [RegNumBytes-1:0] rd_active_o,
  input  logic [7:0] reg_d_i [RegNumBytes],
  input  logic [RegNumBytes-1:0] reg_load_i,
  output logic [7:0] reg_q_o [RegNumBytes]
);
  localparam int unsigned AxiStrbWidth = AxiDataWidth / 8;
  localparam logic [1:0] RespOkay = 2'b00;
  localparam logic [1:0] RespSlverr = 2'b10;

  logic [7:0] reg_q [RegNumBytes];
  logic [7:0] reg_rst_bytes [RegNumBytes];
  logic [1:0] b_resp_q;
  logic       b_valid_q;
  logic [31:0] r_data_q;
  logic [1:0] r_resp_q;
  logic       r_valid_q;

  integer aw_base_idx;
  integer ar_base_idx;
  integer aw_idx0;
  integer aw_idx1;
  integer aw_idx2;
  integer aw_idx3;
  integer ar_idx0;
  integer ar_idx1;
  integer ar_idx2;
  integer ar_idx3;
  integer idx;

  logic aw_prot_ok;
  logic ar_prot_ok;
  logic aw_in_range;
  logic ar_in_range;
  logic write_conflict;
  logic write_all_ro;
  logic write_accept;
  logic read_accept;

  always_comb begin
    aw_base_idx = axi_req_i.aw.addr;
    aw_base_idx = aw_base_idx - (aw_base_idx % 4);
    ar_base_idx = axi_req_i.ar.addr;
    ar_base_idx = ar_base_idx - (ar_base_idx % 4);

    aw_idx0 = aw_base_idx;
    aw_idx1 = aw_base_idx + 1;
    aw_idx2 = aw_base_idx + 2;
    aw_idx3 = aw_base_idx + 3;
    ar_idx0 = ar_base_idx;
    ar_idx1 = ar_base_idx + 1;
    ar_idx2 = ar_base_idx + 2;
    ar_idx3 = ar_base_idx + 3;

    aw_prot_ok = (PrivProtOnly ? axi_req_i.aw.prot[0] : 1'b1) &
                 (SecuProtOnly ? axi_req_i.aw.prot[1] : 1'b1);
    ar_prot_ok = (PrivProtOnly ? axi_req_i.ar.prot[0] : 1'b1) &
                 (SecuProtOnly ? axi_req_i.ar.prot[1] : 1'b1);
    aw_in_range = (aw_base_idx >= 0) && (aw_base_idx < RegNumBytes);
    ar_in_range = (ar_base_idx >= 0) && (ar_base_idx < RegNumBytes);

    write_all_ro = 1'b1;
    write_conflict = 1'b0;
    wr_active_o = '0;
    rd_active_o = '0;

    if (axi_req_i.w.strb[0] && (aw_idx0 < RegNumBytes)) begin
      wr_active_o[aw_idx0] = (~b_valid_q) & axi_req_i.aw_valid & axi_req_i.w_valid;
      if (!AxiReadOnly[aw_idx0]) begin
        write_all_ro = 1'b0;
        if (reg_load_i[aw_idx0]) begin
          write_conflict = 1'b1;
        end
      end
    end
    if (axi_req_i.w.strb[1] && (aw_idx1 < RegNumBytes)) begin
      wr_active_o[aw_idx1] = (~b_valid_q) & axi_req_i.aw_valid & axi_req_i.w_valid;
      if (!AxiReadOnly[aw_idx1]) begin
        write_all_ro = 1'b0;
        if (reg_load_i[aw_idx1]) begin
          write_conflict = 1'b1;
        end
      end
    end
    if (axi_req_i.w.strb[2] && (aw_idx2 < RegNumBytes)) begin
      wr_active_o[aw_idx2] = (~b_valid_q) & axi_req_i.aw_valid & axi_req_i.w_valid;
      if (!AxiReadOnly[aw_idx2]) begin
        write_all_ro = 1'b0;
        if (reg_load_i[aw_idx2]) begin
          write_conflict = 1'b1;
        end
      end
    end
    if (axi_req_i.w.strb[3] && (aw_idx3 < RegNumBytes)) begin
      wr_active_o[aw_idx3] = (~b_valid_q) & axi_req_i.aw_valid & axi_req_i.w_valid;
      if (!AxiReadOnly[aw_idx3]) begin
        write_all_ro = 1'b0;
        if (reg_load_i[aw_idx3]) begin
          write_conflict = 1'b1;
        end
      end
    end

    if ((!ar_in_range) || (!ar_prot_ok)) begin
      rd_active_o = '0;
    end else if ((!r_valid_q) && axi_req_i.ar_valid) begin
      if (ar_idx0 < RegNumBytes) rd_active_o[ar_idx0] = 1'b1;
      if (ar_idx1 < RegNumBytes) rd_active_o[ar_idx1] = 1'b1;
      if (ar_idx2 < RegNumBytes) rd_active_o[ar_idx2] = 1'b1;
      if (ar_idx3 < RegNumBytes) rd_active_o[ar_idx3] = 1'b1;
    end

    write_accept = (~b_valid_q) & axi_req_i.aw_valid & axi_req_i.w_valid & (~write_conflict);
    read_accept = (~r_valid_q) & axi_req_i.ar_valid;

    axi_resp_o.aw_ready = (~b_valid_q) & axi_req_i.w_valid & (~write_conflict);
    axi_resp_o.w_ready = (~b_valid_q) & axi_req_i.aw_valid & (~write_conflict);
    axi_resp_o.b.resp = b_resp_q;
    axi_resp_o.b_valid = b_valid_q;
    axi_resp_o.ar_ready = ~r_valid_q;
    axi_resp_o.r.data = r_data_q;
    axi_resp_o.r.resp = r_resp_q;
    axi_resp_o.r_valid = r_valid_q;
  end

  always_ff @(posedge clk_i or negedge rst_ni) begin
    if (!rst_ni) begin
      b_resp_q <= 2'b00;
      b_valid_q <= 1'b0;
      r_data_q <= '0;
      r_resp_q <= 2'b00;
      r_valid_q <= 1'b0;
      for (idx = 0; idx < RegNumBytes; idx = idx + 1) begin
        reg_q[idx] <= reg_rst_bytes[idx];
      end
    end else begin
      for (idx = 0; idx < RegNumBytes; idx = idx + 1) begin
        if (reg_load_i[idx]) begin
          reg_q[idx] <= reg_d_i[idx];
        end
      end

      if (b_valid_q && axi_req_i.b_ready) begin
        b_valid_q <= 1'b0;
      end
      if (r_valid_q && axi_req_i.r_ready) begin
        r_valid_q <= 1'b0;
      end

      if (write_accept) begin
        b_valid_q <= 1'b1;
        if (aw_in_range && aw_prot_ok && !write_all_ro) begin
          b_resp_q <= 2'b00;
        end else begin
          b_resp_q <= 2'b10;
        end

        if (aw_prot_ok) begin
          if (axi_req_i.w.strb[0] && (aw_idx0 < RegNumBytes) && (!AxiReadOnly[aw_idx0]) && (!reg_load_i[aw_idx0])) begin
            reg_q[aw_idx0] <= axi_req_i.w.data[7:0];
          end
          if (axi_req_i.w.strb[1] && (aw_idx1 < RegNumBytes) && (!AxiReadOnly[aw_idx1]) && (!reg_load_i[aw_idx1])) begin
            reg_q[aw_idx1] <= axi_req_i.w.data[15:8];
          end
          if (axi_req_i.w.strb[2] && (aw_idx2 < RegNumBytes) && (!AxiReadOnly[aw_idx2]) && (!reg_load_i[aw_idx2])) begin
            reg_q[aw_idx2] <= axi_req_i.w.data[23:16];
          end
          if (axi_req_i.w.strb[3] && (aw_idx3 < RegNumBytes) && (!AxiReadOnly[aw_idx3]) && (!reg_load_i[aw_idx3])) begin
            reg_q[aw_idx3] <= axi_req_i.w.data[31:24];
          end
        end
      end

      if (read_accept) begin
        r_valid_q <= 1'b1;
        r_data_q <= 32'hBA5E1E55;
        r_resp_q <= 2'b10;
        if (ar_in_range && ar_prot_ok) begin
          r_data_q <= 32'h00000000;
          r_resp_q <= 2'b00;
          if (ar_idx0 < RegNumBytes) r_data_q[7:0] <= reg_q[ar_idx0];
          if (ar_idx1 < RegNumBytes) r_data_q[15:8] <= reg_q[ar_idx1];
          if (ar_idx2 < RegNumBytes) r_data_q[23:16] <= reg_q[ar_idx2];
          if (ar_idx3 < RegNumBytes) r_data_q[31:24] <= reg_q[ar_idx3];
        end
      end
    end
  end

  for (genvar i = 0; i < RegNumBytes; i++) begin : gen_reg_q_o
    assign reg_rst_bytes[i] = RegRstValFlat[i*8 +: 8];
    assign reg_q_o[i] = reg_q[i];
  end
endmodule
