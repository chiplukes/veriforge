module axi_lite_xbar #(
    parameter axi_pkg::xbar_cfg_t Cfg = '0,
    parameter type aw_chan_t = logic,
    parameter type w_chan_t = logic,
    parameter type b_chan_t = logic,
    parameter type ar_chan_t = logic,
    parameter type r_chan_t = logic,
    parameter type axi_req_t = logic,
    parameter type axi_resp_t = logic,
    parameter type rule_t = axi_pkg::xbar_rule_64_t,
    parameter int unsigned MstIdxWidth = 1
) (
    input  logic                clk_i,
    input  logic                rst_ni,
    input  logic                test_i,
    input  axi_req_t  [1:0]     slv_ports_req_i,
    output axi_resp_t [1:0]     slv_ports_resp_o,
    output axi_req_t  [1:0]     mst_ports_req_o,
    input  axi_resp_t [1:0]     mst_ports_resp_i,
    input  rule_t [1:0]         addr_map_i,
    input  logic  [1:0]         en_default_mst_port_i,
    input  logic  [1:0]         default_mst_port_i
);
    localparam axi_pkg::resp_t RESP_OKAY = axi_pkg::RESP_OKAY;
    localparam axi_pkg::resp_t RESP_DECERR = axi_pkg::RESP_DECERR;
    localparam logic [31:0] DECERR_DATA = 32'hBADC_AB1E;

    axi_req_t [1:0] mst_stage;
    axi_resp_t [1:0] slv_stage;

    logic wr_busy0_q;
    logic wr_owner0_q;
    logic wr_busy1_q;
    logic wr_owner1_q;
    logic rd_busy0_q;
    logic rd_owner0_q;
    logic rd_busy1_q;
    logic rd_owner1_q;

    axi_pkg::resp_t slv0_b_resp_q;
    logic slv0_b_valid_q;
    logic [31:0] slv0_r_data_q;
    axi_pkg::resp_t slv0_r_resp_q;
    logic slv0_r_valid_q;

    axi_pkg::resp_t slv1_b_resp_q;
    logic slv1_b_valid_q;
    logic [31:0] slv1_r_data_q;
    axi_pkg::resp_t slv1_r_resp_q;
    logic slv1_r_valid_q;

    logic slv0_aw_hits_t0;
    logic slv0_aw_hits_t1;
    logic slv1_aw_hits_t0;
    logic slv1_aw_hits_t1;
    logic slv0_ar_hits_t0;
    logic slv0_ar_hits_t1;
    logic slv1_ar_hits_t0;
    logic slv1_ar_hits_t1;
    logic slv0_write_req;
    logic slv1_write_req;
    logic slv0_read_req;
    logic slv1_read_req;
    logic grant_slv0_wr_t0;
    logic grant_slv1_wr_t0;
    logic grant_slv0_wr_t1;
    logic grant_slv1_wr_t1;
    logic grant_slv0_wr_err;
    logic grant_slv1_wr_err;
    logic grant_slv0_rd_t0;
    logic grant_slv1_rd_t0;
    logic grant_slv0_rd_t1;
    logic grant_slv1_rd_t1;
    logic grant_slv0_rd_err;
    logic grant_slv1_rd_err;

    assign slv0_aw_hits_t0 = (slv_ports_req_i[0].aw.addr >= addr_map_i[0].start_addr)
        && (slv_ports_req_i[0].aw.addr < addr_map_i[0].end_addr);
    assign slv0_aw_hits_t1 = (slv_ports_req_i[0].aw.addr >= addr_map_i[1].start_addr)
        && (slv_ports_req_i[0].aw.addr < addr_map_i[1].end_addr);
    assign slv1_aw_hits_t0 = (slv_ports_req_i[1].aw.addr >= addr_map_i[0].start_addr)
        && (slv_ports_req_i[1].aw.addr < addr_map_i[0].end_addr);
    assign slv1_aw_hits_t1 = (slv_ports_req_i[1].aw.addr >= addr_map_i[1].start_addr)
        && (slv_ports_req_i[1].aw.addr < addr_map_i[1].end_addr);

    assign slv0_ar_hits_t0 = (slv_ports_req_i[0].ar.addr >= addr_map_i[0].start_addr)
        && (slv_ports_req_i[0].ar.addr < addr_map_i[0].end_addr);
    assign slv0_ar_hits_t1 = (slv_ports_req_i[0].ar.addr >= addr_map_i[1].start_addr)
        && (slv_ports_req_i[0].ar.addr < addr_map_i[1].end_addr);
    assign slv1_ar_hits_t0 = (slv_ports_req_i[1].ar.addr >= addr_map_i[0].start_addr)
        && (slv_ports_req_i[1].ar.addr < addr_map_i[0].end_addr);
    assign slv1_ar_hits_t1 = (slv_ports_req_i[1].ar.addr >= addr_map_i[1].start_addr)
        && (slv_ports_req_i[1].ar.addr < addr_map_i[1].end_addr);

    assign slv0_write_req = slv_ports_req_i[0].aw_valid && slv_ports_req_i[0].w_valid && !slv0_b_valid_q;
    assign slv1_write_req = slv_ports_req_i[1].aw_valid && slv_ports_req_i[1].w_valid && !slv1_b_valid_q;
    assign slv0_read_req = slv_ports_req_i[0].ar_valid && !slv0_r_valid_q;
    assign slv1_read_req = slv_ports_req_i[1].ar_valid && !slv1_r_valid_q;

    assign grant_slv0_wr_t0 = slv0_write_req && slv0_aw_hits_t0 && !wr_busy0_q;
    assign grant_slv1_wr_t0 = slv1_write_req && slv1_aw_hits_t0 && !wr_busy0_q && !grant_slv0_wr_t0;
    assign grant_slv0_wr_t1 = slv0_write_req && slv0_aw_hits_t1 && !wr_busy1_q;
    assign grant_slv1_wr_t1 = slv1_write_req && slv1_aw_hits_t1 && !wr_busy1_q && !grant_slv0_wr_t1;
    assign grant_slv0_wr_err = slv0_write_req && !slv0_aw_hits_t0 && !slv0_aw_hits_t1;
    assign grant_slv1_wr_err = slv1_write_req && !slv1_aw_hits_t0 && !slv1_aw_hits_t1;

    assign grant_slv0_rd_t0 = slv0_read_req && slv0_ar_hits_t0 && !rd_busy0_q;
    assign grant_slv1_rd_t0 = slv1_read_req && slv1_ar_hits_t0 && !rd_busy0_q && !grant_slv0_rd_t0;
    assign grant_slv0_rd_t1 = slv0_read_req && slv0_ar_hits_t1 && !rd_busy1_q;
    assign grant_slv1_rd_t1 = slv1_read_req && slv1_ar_hits_t1 && !rd_busy1_q && !grant_slv0_rd_t1;
    assign grant_slv0_rd_err = slv0_read_req && !slv0_ar_hits_t0 && !slv0_ar_hits_t1;
    assign grant_slv1_rd_err = slv1_read_req && !slv1_ar_hits_t0 && !slv1_ar_hits_t1;

    always_comb begin
        mst_stage = '0;
        slv_stage = '0;

        if (slv0_b_valid_q) begin
            slv_stage[0].b.resp = slv0_b_resp_q;
            slv_stage[0].b_valid = 1'b1;
        end
        if (slv1_b_valid_q) begin
            slv_stage[1].b.resp = slv1_b_resp_q;
            slv_stage[1].b_valid = 1'b1;
        end
        if (slv0_r_valid_q) begin
            slv_stage[0].r.data = slv0_r_data_q;
            slv_stage[0].r.resp = slv0_r_resp_q;
            slv_stage[0].r_valid = 1'b1;
        end
        if (slv1_r_valid_q) begin
            slv_stage[1].r.data = slv1_r_data_q;
            slv_stage[1].r.resp = slv1_r_resp_q;
            slv_stage[1].r_valid = 1'b1;
        end

        if (wr_busy0_q) begin
            if (wr_owner0_q == 1'b0) begin
                slv_stage[0].b = mst_ports_resp_i[0].b;
                slv_stage[0].b_valid = mst_ports_resp_i[0].b_valid;
                mst_stage[0].b_ready = slv_ports_req_i[0].b_ready;
            end else begin
                slv_stage[1].b = mst_ports_resp_i[0].b;
                slv_stage[1].b_valid = mst_ports_resp_i[0].b_valid;
                mst_stage[0].b_ready = slv_ports_req_i[1].b_ready;
            end
        end

        if (wr_busy1_q) begin
            if (wr_owner1_q == 1'b0) begin
                slv_stage[0].b = mst_ports_resp_i[1].b;
                slv_stage[0].b_valid = mst_ports_resp_i[1].b_valid;
                mst_stage[1].b_ready = slv_ports_req_i[0].b_ready;
            end else begin
                slv_stage[1].b = mst_ports_resp_i[1].b;
                slv_stage[1].b_valid = mst_ports_resp_i[1].b_valid;
                mst_stage[1].b_ready = slv_ports_req_i[1].b_ready;
            end
        end

        if (rd_busy0_q) begin
            if (rd_owner0_q == 1'b0) begin
                slv_stage[0].r = mst_ports_resp_i[0].r;
                slv_stage[0].r_valid = mst_ports_resp_i[0].r_valid;
                mst_stage[0].r_ready = slv_ports_req_i[0].r_ready;
            end else begin
                slv_stage[1].r = mst_ports_resp_i[0].r;
                slv_stage[1].r_valid = mst_ports_resp_i[0].r_valid;
                mst_stage[0].r_ready = slv_ports_req_i[1].r_ready;
            end
        end

        if (rd_busy1_q) begin
            if (rd_owner1_q == 1'b0) begin
                slv_stage[0].r = mst_ports_resp_i[1].r;
                slv_stage[0].r_valid = mst_ports_resp_i[1].r_valid;
                mst_stage[1].r_ready = slv_ports_req_i[0].r_ready;
            end else begin
                slv_stage[1].r = mst_ports_resp_i[1].r;
                slv_stage[1].r_valid = mst_ports_resp_i[1].r_valid;
                mst_stage[1].r_ready = slv_ports_req_i[1].r_ready;
            end
        end

        if (grant_slv0_wr_t0) begin
            slv_stage[0].aw_ready = 1'b1;
            slv_stage[0].w_ready = 1'b1;
            mst_stage[0].aw = slv_ports_req_i[0].aw;
            mst_stage[0].aw_valid = 1'b1;
            mst_stage[0].w = slv_ports_req_i[0].w;
            mst_stage[0].w_valid = 1'b1;
        end
        if (grant_slv1_wr_t0) begin
            slv_stage[1].aw_ready = 1'b1;
            slv_stage[1].w_ready = 1'b1;
            mst_stage[0].aw = slv_ports_req_i[1].aw;
            mst_stage[0].aw_valid = 1'b1;
            mst_stage[0].w = slv_ports_req_i[1].w;
            mst_stage[0].w_valid = 1'b1;
        end
        if (grant_slv0_wr_t1) begin
            slv_stage[0].aw_ready = 1'b1;
            slv_stage[0].w_ready = 1'b1;
            mst_stage[1].aw = slv_ports_req_i[0].aw;
            mst_stage[1].aw_valid = 1'b1;
            mst_stage[1].w = slv_ports_req_i[0].w;
            mst_stage[1].w_valid = 1'b1;
        end
        if (grant_slv1_wr_t1) begin
            slv_stage[1].aw_ready = 1'b1;
            slv_stage[1].w_ready = 1'b1;
            mst_stage[1].aw = slv_ports_req_i[1].aw;
            mst_stage[1].aw_valid = 1'b1;
            mst_stage[1].w = slv_ports_req_i[1].w;
            mst_stage[1].w_valid = 1'b1;
        end
        if (grant_slv0_wr_err) begin
            slv_stage[0].aw_ready = 1'b1;
            slv_stage[0].w_ready = 1'b1;
        end
        if (grant_slv1_wr_err) begin
            slv_stage[1].aw_ready = 1'b1;
            slv_stage[1].w_ready = 1'b1;
        end

        if (grant_slv0_rd_t0) begin
            slv_stage[0].ar_ready = 1'b1;
            mst_stage[0].ar = slv_ports_req_i[0].ar;
            mst_stage[0].ar_valid = 1'b1;
        end
        if (grant_slv1_rd_t0) begin
            slv_stage[1].ar_ready = 1'b1;
            mst_stage[0].ar = slv_ports_req_i[1].ar;
            mst_stage[0].ar_valid = 1'b1;
        end
        if (grant_slv0_rd_t1) begin
            slv_stage[0].ar_ready = 1'b1;
            mst_stage[1].ar = slv_ports_req_i[0].ar;
            mst_stage[1].ar_valid = 1'b1;
        end
        if (grant_slv1_rd_t1) begin
            slv_stage[1].ar_ready = 1'b1;
            mst_stage[1].ar = slv_ports_req_i[1].ar;
            mst_stage[1].ar_valid = 1'b1;
        end
        if (grant_slv0_rd_err) begin
            slv_stage[0].ar_ready = 1'b1;
        end
        if (grant_slv1_rd_err) begin
            slv_stage[1].ar_ready = 1'b1;
        end
    end

    always_ff @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            wr_busy0_q <= 1'b0;
            wr_owner0_q <= 1'b0;
            wr_busy1_q <= 1'b0;
            wr_owner1_q <= 1'b0;
            rd_busy0_q <= 1'b0;
            rd_owner0_q <= 1'b0;
            rd_busy1_q <= 1'b0;
            rd_owner1_q <= 1'b0;
            slv0_b_resp_q <= RESP_OKAY;
            slv0_b_valid_q <= 1'b0;
            slv0_r_data_q <= 32'h0;
            slv0_r_resp_q <= RESP_OKAY;
            slv0_r_valid_q <= 1'b0;
            slv1_b_resp_q <= RESP_OKAY;
            slv1_b_valid_q <= 1'b0;
            slv1_r_data_q <= 32'h0;
            slv1_r_resp_q <= RESP_OKAY;
            slv1_r_valid_q <= 1'b0;
        end else begin
            if (slv0_b_valid_q && slv_ports_req_i[0].b_ready) begin
                slv0_b_valid_q <= 1'b0;
            end
            if (slv1_b_valid_q && slv_ports_req_i[1].b_ready) begin
                slv1_b_valid_q <= 1'b0;
            end
            if (slv0_r_valid_q && slv_ports_req_i[0].r_ready) begin
                slv0_r_valid_q <= 1'b0;
            end
            if (slv1_r_valid_q && slv_ports_req_i[1].r_ready) begin
                slv1_r_valid_q <= 1'b0;
            end

            if (grant_slv0_wr_t0) begin
                wr_busy0_q <= 1'b1;
                wr_owner0_q <= 1'b0;
            end
            if (grant_slv1_wr_t0) begin
                wr_busy0_q <= 1'b1;
                wr_owner0_q <= 1'b1;
            end
            if (grant_slv0_wr_t1) begin
                wr_busy1_q <= 1'b1;
                wr_owner1_q <= 1'b0;
            end
            if (grant_slv1_wr_t1) begin
                wr_busy1_q <= 1'b1;
                wr_owner1_q <= 1'b1;
            end

            if (wr_busy0_q) begin
                if ((!wr_owner0_q && mst_ports_resp_i[0].b_valid && slv_ports_req_i[0].b_ready)
                    || (wr_owner0_q && mst_ports_resp_i[0].b_valid && slv_ports_req_i[1].b_ready)) begin
                    wr_busy0_q <= 1'b0;
                end
            end
            if (wr_busy1_q) begin
                if ((!wr_owner1_q && mst_ports_resp_i[1].b_valid && slv_ports_req_i[0].b_ready)
                    || (wr_owner1_q && mst_ports_resp_i[1].b_valid && slv_ports_req_i[1].b_ready)) begin
                    wr_busy1_q <= 1'b0;
                end
            end

            if (grant_slv0_wr_err) begin
                slv0_b_resp_q <= RESP_DECERR;
                slv0_b_valid_q <= 1'b1;
            end
            if (grant_slv1_wr_err) begin
                slv1_b_resp_q <= RESP_DECERR;
                slv1_b_valid_q <= 1'b1;
            end

            if (grant_slv0_rd_t0) begin
                rd_busy0_q <= 1'b1;
                rd_owner0_q <= 1'b0;
            end
            if (grant_slv1_rd_t0) begin
                rd_busy0_q <= 1'b1;
                rd_owner0_q <= 1'b1;
            end
            if (grant_slv0_rd_t1) begin
                rd_busy1_q <= 1'b1;
                rd_owner1_q <= 1'b0;
            end
            if (grant_slv1_rd_t1) begin
                rd_busy1_q <= 1'b1;
                rd_owner1_q <= 1'b1;
            end

            if (rd_busy0_q) begin
                if ((!rd_owner0_q && mst_ports_resp_i[0].r_valid && slv_ports_req_i[0].r_ready)
                    || (rd_owner0_q && mst_ports_resp_i[0].r_valid && slv_ports_req_i[1].r_ready)) begin
                    rd_busy0_q <= 1'b0;
                end
            end
            if (rd_busy1_q) begin
                if ((!rd_owner1_q && mst_ports_resp_i[1].r_valid && slv_ports_req_i[0].r_ready)
                    || (rd_owner1_q && mst_ports_resp_i[1].r_valid && slv_ports_req_i[1].r_ready)) begin
                    rd_busy1_q <= 1'b0;
                end
            end

            if (grant_slv0_rd_err) begin
                slv0_r_data_q <= DECERR_DATA;
                slv0_r_resp_q <= RESP_DECERR;
                slv0_r_valid_q <= 1'b1;
            end
            if (grant_slv1_rd_err) begin
                slv1_r_data_q <= DECERR_DATA;
                slv1_r_resp_q <= RESP_DECERR;
                slv1_r_valid_q <= 1'b1;
            end
        end
    end

    assign slv_ports_resp_o = slv_stage;
    assign mst_ports_req_o = mst_stage;

    logic _unused_signals;
    assign _unused_signals = test_i
        ^ (^Cfg.NoSlvPorts)
        ^ (^Cfg.NoMstPorts)
        ^ (^Cfg.MaxMstTrans)
        ^ (^Cfg.MaxSlvTrans)
        ^ Cfg.FallThrough
        ^ (^Cfg.LatencyMode)
        ^ (^Cfg.AxiIdWidthSlvPorts)
        ^ (^Cfg.AxiIdUsedSlvPorts)
        ^ (^Cfg.AxiAddrWidth)
        ^ (^Cfg.AxiDataWidth)
        ^ (^Cfg.NoAddrRules)
        ^ (^en_default_mst_port_i)
        ^ (^default_mst_port_i)
        ^ (^MstIdxWidth);
endmodule
