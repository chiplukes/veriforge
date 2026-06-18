module axi_lite_dw_converter #(
    parameter int unsigned AxiAddrWidth = 32'd0,
    parameter int unsigned AxiSlvPortDataWidth = 32'd0,
    parameter int unsigned AxiMstPortDataWidth = 32'd0,
    parameter type axi_lite_aw_t = logic,
    parameter type axi_lite_slv_w_t = logic,
    parameter type axi_lite_mst_w_t = logic,
    parameter type axi_lite_b_t = logic,
    parameter type axi_lite_ar_t = logic,
    parameter type axi_lite_slv_r_t = logic,
    parameter type axi_lite_mst_r_t = logic,
    parameter type axi_lite_slv_req_t = logic,
    parameter type axi_lite_slv_res_t = logic,
    parameter type axi_lite_mst_req_t = logic,
    parameter type axi_lite_mst_res_t = logic
) (
    input  logic                  clk_i,
    input  logic                  rst_ni,
    input  axi_lite_slv_req_t     slv_req_i,
    output axi_lite_slv_res_t      slv_res_o,
    output axi_lite_mst_req_t      mst_req_o,
    input  axi_lite_mst_res_t     mst_res_i
);

    localparam int unsigned AxiSlvPortStrbWidth = AxiSlvPortDataWidth / 32'd8;
    localparam int unsigned AxiMstPortStrbWidth = AxiMstPortDataWidth / 32'd8;
    typedef logic [AxiAddrWidth-1:0] addr_t;
    logic slv_aw_ready_int;
    logic slv_w_ready_int;
    axi_pkg::resp_t slv_b_resp_int;
    logic slv_b_valid_int;
    logic slv_ar_ready_int;
    logic [AxiSlvPortDataWidth-1:0] slv_r_data_int;
    axi_pkg::resp_t slv_r_resp_int;
    logic slv_r_valid_int;
    addr_t mst_aw_addr_int;
    axi_pkg::prot_t mst_aw_prot_int;
    logic mst_aw_valid_int;
    logic [AxiMstPortDataWidth-1:0] mst_w_data_int;
    logic [AxiMstPortStrbWidth-1:0] mst_w_strb_int;
    logic mst_w_valid_int;
    logic mst_b_ready_int;
    addr_t mst_ar_addr_int;
    axi_pkg::prot_t mst_ar_prot_int;
    logic mst_ar_valid_int;
    logic mst_r_ready_int;

    assign slv_res_o = {
        slv_aw_ready_int,
        slv_w_ready_int,
        slv_b_resp_int,
        slv_b_valid_int,
        slv_ar_ready_int,
        slv_r_data_int,
        slv_r_resp_int,
        slv_r_valid_int
    };
    assign mst_req_o = {
        mst_aw_addr_int,
        mst_aw_prot_int,
        mst_aw_valid_int,
        mst_w_data_int,
        mst_w_strb_int,
        mst_w_valid_int,
        mst_b_ready_int,
        mst_ar_addr_int,
        mst_ar_prot_int,
        mst_ar_valid_int,
        mst_r_ready_int
    };

    if (AxiSlvPortDataWidth > AxiMstPortDataWidth) begin : gen_downsizer
        localparam int unsigned DownsizeFactor = AxiSlvPortDataWidth / AxiMstPortDataWidth;
        localparam int unsigned SelWidth = $clog2(DownsizeFactor);
        localparam int unsigned SelOffset = $clog2(AxiMstPortStrbWidth);

        typedef logic [SelWidth-1:0] sel_t;
        localparam logic [1:0] WrIdle = 2'd0;
        localparam logic [1:0] WrSend = 2'd1;
        localparam logic [1:0] WrWaitB = 2'd2;
        localparam logic [1:0] WrResp = 2'd3;
        localparam logic [1:0] RdIdle = 2'd0;
        localparam logic [1:0] RdSend = 2'd1;
        localparam logic [1:0] RdWaitR = 2'd2;
        localparam logic [1:0] RdResp = 2'd3;

        axi_lite_aw_t wr_aw_q;
        axi_lite_slv_w_t wr_w_q;
        axi_lite_ar_t rd_ar_q;
        logic wr_aw_valid_q;
        logic wr_w_valid_q;
        logic rd_ar_valid_q;
        logic [1:0] wr_state_q;
        logic [1:0] rd_state_q;
        sel_t wr_sel_q;
        sel_t rd_sel_q;
        axi_pkg::resp_t wr_resp_q;
        axi_pkg::resp_t rd_resp_q;
        logic [AxiSlvPortDataWidth-1:0] rd_data_q;

        initial begin
            wr_aw_q = '0;
            wr_w_q = '0;
            rd_ar_q = '0;
            wr_aw_valid_q = 1'b0;
            wr_w_valid_q = 1'b0;
            rd_ar_valid_q = 1'b0;
            wr_state_q = WrIdle;
            rd_state_q = RdIdle;
            wr_sel_q = '0;
            rd_sel_q = '0;
            wr_resp_q = '0;
            rd_resp_q = '0;
            rd_data_q = '0;
        end

        wire capture_wr_aw = slv_req_i.aw_valid && !wr_aw_valid_q;
        wire capture_wr_w = slv_req_i.w_valid && !wr_w_valid_q;
        wire capture_rd_ar = slv_req_i.ar_valid && !rd_ar_valid_q;
        wire have_wr_payload = (wr_aw_valid_q || capture_wr_aw) && (wr_w_valid_q || capture_wr_w);

        always_comb begin
            slv_aw_ready_int = !wr_aw_valid_q;
            slv_w_ready_int = !wr_w_valid_q;
            slv_b_resp_int = '0;
            slv_b_valid_int = 1'b0;
            slv_ar_ready_int = !rd_ar_valid_q;
            slv_r_data_int = '0;
            slv_r_resp_int = '0;
            slv_r_valid_int = 1'b0;
            mst_aw_addr_int = '0;
            mst_aw_prot_int = '0;
            mst_aw_valid_int = 1'b0;
            mst_w_data_int = '0;
            mst_w_strb_int = '0;
            mst_w_valid_int = 1'b0;
            mst_b_ready_int = 1'b0;
            mst_ar_addr_int = '0;
            mst_ar_prot_int = '0;
            mst_ar_valid_int = 1'b0;
            mst_r_ready_int = 1'b0;

            case (wr_state_q)
                WrSend: begin
                    mst_aw_addr_int = wr_aw_q.addr;
                    mst_aw_addr_int[SelOffset+:SelWidth] = wr_sel_q;
                    mst_aw_addr_int[SelOffset-1:0] = '0;
                    mst_aw_prot_int = wr_aw_q.prot;
                    mst_w_data_int = wr_w_q.data[wr_sel_q*AxiMstPortDataWidth+:AxiMstPortDataWidth];
                    mst_w_strb_int = wr_w_q.strb[wr_sel_q*AxiMstPortStrbWidth+:AxiMstPortStrbWidth];
                    mst_aw_valid_int = wr_aw_valid_q && wr_w_valid_q;
                    mst_w_valid_int = wr_aw_valid_q && wr_w_valid_q;
                end
                WrWaitB: begin
                    mst_b_ready_int = 1'b1;
                end
                WrResp: begin
                    slv_b_resp_int = wr_resp_q;
                    slv_b_valid_int = 1'b1;
                end
                default: begin
                end
            endcase

            case (rd_state_q)
                RdSend: begin
                    mst_ar_addr_int = rd_ar_q.addr;
                    mst_ar_addr_int[SelOffset+:SelWidth] = rd_sel_q;
                    mst_ar_addr_int[SelOffset-1:0] = '0;
                    mst_ar_prot_int = rd_ar_q.prot;
                    mst_ar_valid_int = rd_ar_valid_q;
                end
                RdWaitR: begin
                    mst_r_ready_int = 1'b1;
                end
                RdResp: begin
                    slv_r_data_int = rd_data_q;
                    slv_r_resp_int = rd_resp_q;
                    slv_r_valid_int = 1'b1;
                end
                default: begin
                end
            endcase
        end

        always_ff @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                wr_aw_q <= '0;
                wr_w_q <= '0;
                rd_ar_q <= '0;
                wr_aw_valid_q <= 1'b0;
                wr_w_valid_q <= 1'b0;
                rd_ar_valid_q <= 1'b0;
                wr_state_q <= WrIdle;
                rd_state_q <= RdIdle;
                wr_sel_q <= '0;
                rd_sel_q <= '0;
                wr_resp_q <= '0;
                rd_resp_q <= '0;
                rd_data_q <= '0;
            end else begin
                if (capture_wr_aw) begin
                    wr_aw_q <= slv_req_i.aw;
                    wr_aw_valid_q <= 1'b1;
                end
                if (capture_wr_w) begin
                    wr_w_q <= slv_req_i.w;
                    wr_w_valid_q <= 1'b1;
                end
                if (capture_rd_ar) begin
                    rd_ar_q <= slv_req_i.ar;
                    rd_ar_valid_q <= 1'b1;
                end

                case (wr_state_q)
                    WrIdle: begin
                        if (have_wr_payload) begin
                            wr_state_q <= WrSend;
                            wr_sel_q <= '0;
                            wr_resp_q <= '0;
                        end
                    end
                    WrSend: begin
                        if ((wr_aw_valid_q && wr_w_valid_q) && mst_res_i.aw_ready && mst_res_i.w_ready) begin
                            wr_state_q <= WrWaitB;
                        end
                    end
                    WrWaitB: begin
                        if (mst_res_i.b_valid) begin
                            wr_resp_q <= wr_resp_q | mst_res_i.b.resp;
                            if (wr_sel_q == sel_t'(DownsizeFactor-1)) begin
                                wr_state_q <= WrResp;
                            end else begin
                                wr_sel_q <= sel_t'(wr_sel_q + 1'b1);
                                wr_state_q <= WrSend;
                            end
                        end
                    end
                    WrResp: begin
                        if (slv_req_i.b_ready) begin
                            wr_aw_valid_q <= 1'b0;
                            wr_w_valid_q <= 1'b0;
                            wr_state_q <= WrIdle;
                            wr_resp_q <= '0;
                        end
                    end
                    default: begin
                        wr_state_q <= WrIdle;
                    end
                endcase

                case (rd_state_q)
                    RdIdle: begin
                        if (rd_ar_valid_q) begin
                            rd_state_q <= RdSend;
                            rd_sel_q <= '0;
                            rd_resp_q <= '0;
                            rd_data_q <= '0;
                        end
                    end
                    RdSend: begin
                        if (rd_ar_valid_q && mst_res_i.ar_ready) begin
                            rd_state_q <= RdWaitR;
                        end
                    end
                    RdWaitR: begin
                        if (mst_res_i.r_valid) begin
                            rd_data_q[rd_sel_q*AxiMstPortDataWidth+:AxiMstPortDataWidth] <= mst_res_i.r.data;
                            rd_resp_q <= rd_resp_q | mst_res_i.r.resp;
                            if (rd_sel_q == sel_t'(DownsizeFactor-1)) begin
                                rd_state_q <= RdResp;
                            end else begin
                                rd_sel_q <= sel_t'(rd_sel_q + 1'b1);
                                rd_state_q <= RdSend;
                            end
                        end
                    end
                    RdResp: begin
                        if (slv_req_i.r_ready) begin
                            rd_ar_valid_q <= 1'b0;
                            rd_state_q <= RdIdle;
                            rd_resp_q <= '0;
                        end
                    end
                    default: begin
                        rd_state_q <= RdIdle;
                    end
                endcase
            end
        end
    end else if (AxiMstPortDataWidth > AxiSlvPortDataWidth) begin : gen_upsizer
        localparam int unsigned UpsizeFactor = AxiMstPortDataWidth / AxiSlvPortDataWidth;
        localparam int unsigned SelOffset = $clog2(AxiSlvPortStrbWidth);
        localparam int unsigned SelWidth = $clog2(UpsizeFactor);

        typedef logic [SelWidth-1:0] sel_t;
        localparam logic [1:0] WrIdle = 2'd0;
        localparam logic [1:0] WrSend = 2'd1;
        localparam logic [1:0] WrWaitB = 2'd2;
        localparam logic [1:0] WrResp = 2'd3;
        localparam logic [1:0] RdIdle = 2'd0;
        localparam logic [1:0] RdSend = 2'd1;
        localparam logic [1:0] RdWaitR = 2'd2;
        localparam logic [1:0] RdResp = 2'd3;

        axi_lite_aw_t wr_aw_q;
        axi_lite_slv_w_t wr_w_q;
        axi_lite_ar_t rd_ar_q;
        logic wr_aw_valid_q;
        logic wr_w_valid_q;
        logic rd_ar_valid_q;
        logic [1:0] wr_state_q;
        logic [1:0] rd_state_q;
        sel_t wr_sel_q;
        sel_t rd_sel_q;
        axi_pkg::resp_t wr_resp_q;
        axi_lite_slv_r_t rd_resp_chan_q;
        logic [AxiMstPortStrbWidth-1:0] wide_strb;

        initial begin
            wr_aw_q = '0;
            wr_w_q = '0;
            rd_ar_q = '0;
            wr_aw_valid_q = 1'b0;
            wr_w_valid_q = 1'b0;
            rd_ar_valid_q = 1'b0;
            wr_state_q = WrIdle;
            rd_state_q = RdIdle;
            wr_sel_q = '0;
            rd_sel_q = '0;
            wr_resp_q = '0;
            rd_resp_chan_q = '0;
            wide_strb = '0;
        end

        wire capture_wr_aw = slv_req_i.aw_valid && !wr_aw_valid_q;
        wire capture_wr_w = slv_req_i.w_valid && !wr_w_valid_q;
        wire capture_rd_ar = slv_req_i.ar_valid && !rd_ar_valid_q;
        wire have_wr_payload = (wr_aw_valid_q || capture_wr_aw) && (wr_w_valid_q || capture_wr_w);

        always_comb begin
            slv_aw_ready_int = !wr_aw_valid_q;
            slv_w_ready_int = !wr_w_valid_q;
            slv_b_resp_int = '0;
            slv_b_valid_int = 1'b0;
            slv_ar_ready_int = !rd_ar_valid_q;
            slv_r_data_int = '0;
            slv_r_resp_int = '0;
            slv_r_valid_int = 1'b0;
            mst_aw_addr_int = '0;
            mst_aw_prot_int = '0;
            mst_aw_valid_int = 1'b0;
            mst_w_data_int = '0;
            mst_w_strb_int = '0;
            mst_w_valid_int = 1'b0;
            mst_b_ready_int = 1'b0;
            mst_ar_addr_int = '0;
            mst_ar_prot_int = '0;
            mst_ar_valid_int = 1'b0;
            mst_r_ready_int = 1'b0;
            wide_strb = '0;

            case (wr_state_q)
                WrSend: begin
                    wide_strb = {{(AxiMstPortStrbWidth-AxiSlvPortStrbWidth){1'b0}}, wr_w_q.strb};
                    wide_strb = wide_strb << (wr_sel_q * AxiSlvPortStrbWidth);
                    mst_aw_addr_int = wr_aw_q.addr;
                    mst_aw_prot_int = wr_aw_q.prot;
                    mst_w_data_int = {UpsizeFactor{wr_w_q.data}};
                    mst_w_strb_int = wide_strb;
                    mst_aw_valid_int = wr_aw_valid_q && wr_w_valid_q;
                    mst_w_valid_int = wr_aw_valid_q && wr_w_valid_q;
                end
                WrWaitB: begin
                    mst_b_ready_int = 1'b1;
                end
                WrResp: begin
                    slv_b_resp_int = wr_resp_q;
                    slv_b_valid_int = 1'b1;
                end
                default: begin
                end
            endcase

            case (rd_state_q)
                RdSend: begin
                    mst_ar_addr_int = rd_ar_q.addr;
                    mst_ar_prot_int = rd_ar_q.prot;
                    mst_ar_valid_int = rd_ar_valid_q;
                end
                RdWaitR: begin
                    mst_r_ready_int = 1'b1;
                end
                RdResp: begin
                    slv_r_data_int = rd_resp_chan_q.data;
                    slv_r_resp_int = rd_resp_chan_q.resp;
                    slv_r_valid_int = 1'b1;
                end
                default: begin
                end
            endcase
        end

        always_ff @(posedge clk_i or negedge rst_ni) begin
            if (!rst_ni) begin
                wr_aw_q <= '0;
                wr_w_q <= '0;
                rd_ar_q <= '0;
                wr_aw_valid_q <= 1'b0;
                wr_w_valid_q <= 1'b0;
                rd_ar_valid_q <= 1'b0;
                wr_state_q <= WrIdle;
                rd_state_q <= RdIdle;
                wr_sel_q <= '0;
                rd_sel_q <= '0;
                wr_resp_q <= '0;
                rd_resp_chan_q <= '0;
            end else begin
                if (capture_wr_aw) begin
                    wr_aw_q <= slv_req_i.aw;
                    wr_aw_valid_q <= 1'b1;
                end
                if (capture_wr_w) begin
                    wr_w_q <= slv_req_i.w;
                    wr_w_valid_q <= 1'b1;
                end
                if (capture_rd_ar) begin
                    rd_ar_q <= slv_req_i.ar;
                    rd_ar_valid_q <= 1'b1;
                end

                case (wr_state_q)
                    WrIdle: begin
                        if (have_wr_payload) begin
                            wr_sel_q <= sel_t'((capture_wr_aw ? slv_req_i.aw.addr : wr_aw_q.addr) >> SelOffset);
                            wr_resp_q <= '0;
                            wr_state_q <= WrSend;
                        end
                    end
                    WrSend: begin
                        if ((wr_aw_valid_q && wr_w_valid_q) && mst_res_i.aw_ready && mst_res_i.w_ready) begin
                            wr_state_q <= WrWaitB;
                        end
                    end
                    WrWaitB: begin
                        if (mst_res_i.b_valid) begin
                            wr_resp_q <= mst_res_i.b.resp;
                            wr_state_q <= WrResp;
                        end
                    end
                    WrResp: begin
                        if (slv_req_i.b_ready) begin
                            wr_aw_valid_q <= 1'b0;
                            wr_w_valid_q <= 1'b0;
                            wr_state_q <= WrIdle;
                        end
                    end
                    default: begin
                        wr_state_q <= WrIdle;
                    end
                endcase

                case (rd_state_q)
                    RdIdle: begin
                        if (rd_ar_valid_q) begin
                            rd_sel_q <= sel_t'((capture_rd_ar ? slv_req_i.ar.addr : rd_ar_q.addr) >> SelOffset);
                            rd_state_q <= RdSend;
                        end
                    end
                    RdSend: begin
                        if (rd_ar_valid_q && mst_res_i.ar_ready) begin
                            rd_state_q <= RdWaitR;
                        end
                    end
                    RdWaitR: begin
                        if (mst_res_i.r_valid) begin
                            rd_resp_chan_q.data <= mst_res_i.r.data[rd_sel_q*AxiSlvPortDataWidth+:AxiSlvPortDataWidth];
                            rd_resp_chan_q.resp <= mst_res_i.r.resp;
                            rd_state_q <= RdResp;
                        end
                    end
                    RdResp: begin
                        if (slv_req_i.r_ready) begin
                            rd_ar_valid_q <= 1'b0;
                            rd_state_q <= RdIdle;
                        end
                    end
                    default: begin
                        rd_state_q <= RdIdle;
                    end
                endcase
            end
        end
    end else begin : gen_passthrough
        assign slv_aw_ready_int = mst_res_i.aw_ready;
        assign slv_w_ready_int = mst_res_i.w_ready;
        assign slv_b_resp_int = mst_res_i.b.resp;
        assign slv_b_valid_int = mst_res_i.b_valid;
        assign slv_ar_ready_int = mst_res_i.ar_ready;
        assign slv_r_data_int = mst_res_i.r.data;
        assign slv_r_resp_int = mst_res_i.r.resp;
        assign slv_r_valid_int = mst_res_i.r_valid;
        assign mst_aw_addr_int = slv_req_i.aw.addr;
        assign mst_aw_prot_int = slv_req_i.aw.prot;
        assign mst_aw_valid_int = slv_req_i.aw_valid;
        assign mst_w_data_int = slv_req_i.w.data;
        assign mst_w_strb_int = slv_req_i.w.strb;
        assign mst_w_valid_int = slv_req_i.w_valid;
        assign mst_b_ready_int = slv_req_i.b_ready;
        assign mst_ar_addr_int = slv_req_i.ar.addr;
        assign mst_ar_prot_int = slv_req_i.ar.prot;
        assign mst_ar_valid_int = slv_req_i.ar_valid;
        assign mst_r_ready_int = slv_req_i.r_ready;
    end

endmodule
