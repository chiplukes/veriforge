module axi_xbar #(
    parameter axi_pkg::xbar_cfg_t Cfg = '0,
    parameter bit ATOPs = 1'b1,
    parameter bit Connectivity = 1'b1,
    parameter type slv_aw_chan_t = logic,
    parameter type mst_aw_chan_t = logic,
    parameter type w_chan_t = logic,
    parameter type slv_b_chan_t = logic,
    parameter type mst_b_chan_t = logic,
    parameter type slv_ar_chan_t = logic,
    parameter type mst_ar_chan_t = logic,
    parameter type slv_r_chan_t = logic,
    parameter type mst_r_chan_t = logic,
    parameter type slv_req_t = logic,
    parameter type slv_resp_t = logic,
    parameter type mst_req_t = logic,
    parameter type mst_resp_t = logic,
    parameter type rule_t = axi_pkg::xbar_rule_64_t
) (
    input logic clk_i,
    input logic rst_ni,
    input logic test_i,
    input slv_req_t [1:0] slv_ports_req_i,
    output slv_resp_t [1:0] slv_ports_resp_o,
    output mst_req_t [1:0] mst_ports_req_o,
    input mst_resp_t [1:0] mst_ports_resp_i,
    input rule_t [1:0] addr_map_i,
    input logic [1:0] en_default_mst_port_i,
    input logic [1:0] default_mst_port_i
);
    localparam axi_pkg::resp_t RESP_OKAY = axi_pkg::RESP_OKAY;
    localparam axi_pkg::resp_t RESP_DECERR = axi_pkg::RESP_DECERR;
    localparam logic [31:0] DECERR_DATA = 32'hBADC_AB1E;

    logic wr_busy0_q;
    logic wr_owner0_q;
    logic wr_busy1_q;
    logic wr_owner1_q;
    logic rd_busy0_q;
    logic rd_owner0_q;
    logic rd_busy1_q;
    logic rd_owner1_q;

    slv_b_chan_t [1:0] slv_b_chan_q;
    logic [1:0] slv_b_valid_q;
    slv_r_chan_t [1:0] slv_r_chan_q;
    logic [1:0] slv_r_valid_q;

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
    logic slv0_write_subset_ok;
    logic slv1_write_subset_ok;
    logic slv0_read_subset_ok;
    logic slv1_read_subset_ok;

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

    assign slv0_write_subset_ok = (slv_ports_req_i[0].aw.len == 8'd0) && slv_ports_req_i[0].w.last;
    assign slv1_write_subset_ok = (slv_ports_req_i[1].aw.len == 8'd0) && slv_ports_req_i[1].w.last;
    assign slv0_read_subset_ok = (slv_ports_req_i[0].ar.len == 8'd0);
    assign slv1_read_subset_ok = (slv_ports_req_i[1].ar.len == 8'd0);

    assign slv0_write_req = slv_ports_req_i[0].aw_valid && slv_ports_req_i[0].w_valid
        && !slv_b_valid_q[0] && slv0_write_subset_ok;
    assign slv1_write_req = slv_ports_req_i[1].aw_valid && slv_ports_req_i[1].w_valid
        && !slv_b_valid_q[1] && slv1_write_subset_ok;
    assign slv0_read_req = slv_ports_req_i[0].ar_valid && !slv_r_valid_q[0] && slv0_read_subset_ok;
    assign slv1_read_req = slv_ports_req_i[1].ar_valid && !slv_r_valid_q[1] && slv1_read_subset_ok;

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
        slv_ports_resp_o = '0;
        mst_ports_req_o = '0;

        if (slv_b_valid_q[0]) begin
            slv_ports_resp_o[0].b = slv_b_chan_q[0];
            slv_ports_resp_o[0].b_valid = 1'b1;
        end
        if (slv_b_valid_q[1]) begin
            slv_ports_resp_o[1].b = slv_b_chan_q[1];
            slv_ports_resp_o[1].b_valid = 1'b1;
        end
        if (slv_r_valid_q[0]) begin
            slv_ports_resp_o[0].r = slv_r_chan_q[0];
            slv_ports_resp_o[0].r_valid = 1'b1;
        end
        if (slv_r_valid_q[1]) begin
            slv_ports_resp_o[1].r = slv_r_chan_q[1];
            slv_ports_resp_o[1].r_valid = 1'b1;
        end

        if (wr_busy0_q) begin
            if (wr_owner0_q == 1'b0) begin
                slv_ports_resp_o[0].b.id = mst_ports_resp_i[0].b.id[1:0];
                slv_ports_resp_o[0].b.resp = mst_ports_resp_i[0].b.resp;
                slv_ports_resp_o[0].b_valid = mst_ports_resp_i[0].b_valid;
                mst_ports_req_o[0].b_ready = slv_ports_req_i[0].b_ready;
            end else begin
                slv_ports_resp_o[1].b.id = mst_ports_resp_i[0].b.id[1:0];
                slv_ports_resp_o[1].b.resp = mst_ports_resp_i[0].b.resp;
                slv_ports_resp_o[1].b_valid = mst_ports_resp_i[0].b_valid;
                mst_ports_req_o[0].b_ready = slv_ports_req_i[1].b_ready;
            end
        end
        if (wr_busy1_q) begin
            if (wr_owner1_q == 1'b0) begin
                slv_ports_resp_o[0].b.id = mst_ports_resp_i[1].b.id[1:0];
                slv_ports_resp_o[0].b.resp = mst_ports_resp_i[1].b.resp;
                slv_ports_resp_o[0].b_valid = mst_ports_resp_i[1].b_valid;
                mst_ports_req_o[1].b_ready = slv_ports_req_i[0].b_ready;
            end else begin
                slv_ports_resp_o[1].b.id = mst_ports_resp_i[1].b.id[1:0];
                slv_ports_resp_o[1].b.resp = mst_ports_resp_i[1].b.resp;
                slv_ports_resp_o[1].b_valid = mst_ports_resp_i[1].b_valid;
                mst_ports_req_o[1].b_ready = slv_ports_req_i[1].b_ready;
            end
        end

        if (rd_busy0_q) begin
            if (rd_owner0_q == 1'b0) begin
                slv_ports_resp_o[0].r.id = mst_ports_resp_i[0].r.id[1:0];
                slv_ports_resp_o[0].r.data = mst_ports_resp_i[0].r.data;
                slv_ports_resp_o[0].r.resp = mst_ports_resp_i[0].r.resp;
                slv_ports_resp_o[0].r.last = mst_ports_resp_i[0].r.last;
                slv_ports_resp_o[0].r_valid = mst_ports_resp_i[0].r_valid;
                mst_ports_req_o[0].r_ready = slv_ports_req_i[0].r_ready;
            end else begin
                slv_ports_resp_o[1].r.id = mst_ports_resp_i[0].r.id[1:0];
                slv_ports_resp_o[1].r.data = mst_ports_resp_i[0].r.data;
                slv_ports_resp_o[1].r.resp = mst_ports_resp_i[0].r.resp;
                slv_ports_resp_o[1].r.last = mst_ports_resp_i[0].r.last;
                slv_ports_resp_o[1].r_valid = mst_ports_resp_i[0].r_valid;
                mst_ports_req_o[0].r_ready = slv_ports_req_i[1].r_ready;
            end
        end
        if (rd_busy1_q) begin
            if (rd_owner1_q == 1'b0) begin
                slv_ports_resp_o[0].r.id = mst_ports_resp_i[1].r.id[1:0];
                slv_ports_resp_o[0].r.data = mst_ports_resp_i[1].r.data;
                slv_ports_resp_o[0].r.resp = mst_ports_resp_i[1].r.resp;
                slv_ports_resp_o[0].r.last = mst_ports_resp_i[1].r.last;
                slv_ports_resp_o[0].r_valid = mst_ports_resp_i[1].r_valid;
                mst_ports_req_o[1].r_ready = slv_ports_req_i[0].r_ready;
            end else begin
                slv_ports_resp_o[1].r.id = mst_ports_resp_i[1].r.id[1:0];
                slv_ports_resp_o[1].r.data = mst_ports_resp_i[1].r.data;
                slv_ports_resp_o[1].r.resp = mst_ports_resp_i[1].r.resp;
                slv_ports_resp_o[1].r.last = mst_ports_resp_i[1].r.last;
                slv_ports_resp_o[1].r_valid = mst_ports_resp_i[1].r_valid;
                mst_ports_req_o[1].r_ready = slv_ports_req_i[1].r_ready;
            end
        end

        if (grant_slv0_wr_t0) begin
            slv_ports_resp_o[0].aw_ready = 1'b1;
            slv_ports_resp_o[0].w_ready = 1'b1;
            mst_ports_req_o[0].aw.id = {1'b0, slv_ports_req_i[0].aw.id};
            mst_ports_req_o[0].aw.addr = slv_ports_req_i[0].aw.addr;
            mst_ports_req_o[0].aw.prot = slv_ports_req_i[0].aw.prot;
            mst_ports_req_o[0].aw.len = slv_ports_req_i[0].aw.len;
            mst_ports_req_o[0].aw_valid = 1'b1;
            mst_ports_req_o[0].w = slv_ports_req_i[0].w;
            mst_ports_req_o[0].w_valid = 1'b1;
        end
        if (grant_slv1_wr_t0) begin
            slv_ports_resp_o[1].aw_ready = 1'b1;
            slv_ports_resp_o[1].w_ready = 1'b1;
            mst_ports_req_o[0].aw.id = {1'b1, slv_ports_req_i[1].aw.id};
            mst_ports_req_o[0].aw.addr = slv_ports_req_i[1].aw.addr;
            mst_ports_req_o[0].aw.prot = slv_ports_req_i[1].aw.prot;
            mst_ports_req_o[0].aw.len = slv_ports_req_i[1].aw.len;
            mst_ports_req_o[0].aw_valid = 1'b1;
            mst_ports_req_o[0].w = slv_ports_req_i[1].w;
            mst_ports_req_o[0].w_valid = 1'b1;
        end
        if (grant_slv0_wr_t1) begin
            slv_ports_resp_o[0].aw_ready = 1'b1;
            slv_ports_resp_o[0].w_ready = 1'b1;
            mst_ports_req_o[1].aw.id = {1'b0, slv_ports_req_i[0].aw.id};
            mst_ports_req_o[1].aw.addr = slv_ports_req_i[0].aw.addr;
            mst_ports_req_o[1].aw.prot = slv_ports_req_i[0].aw.prot;
            mst_ports_req_o[1].aw.len = slv_ports_req_i[0].aw.len;
            mst_ports_req_o[1].aw_valid = 1'b1;
            mst_ports_req_o[1].w = slv_ports_req_i[0].w;
            mst_ports_req_o[1].w_valid = 1'b1;
        end
        if (grant_slv1_wr_t1) begin
            slv_ports_resp_o[1].aw_ready = 1'b1;
            slv_ports_resp_o[1].w_ready = 1'b1;
            mst_ports_req_o[1].aw.id = {1'b1, slv_ports_req_i[1].aw.id};
            mst_ports_req_o[1].aw.addr = slv_ports_req_i[1].aw.addr;
            mst_ports_req_o[1].aw.prot = slv_ports_req_i[1].aw.prot;
            mst_ports_req_o[1].aw.len = slv_ports_req_i[1].aw.len;
            mst_ports_req_o[1].aw_valid = 1'b1;
            mst_ports_req_o[1].w = slv_ports_req_i[1].w;
            mst_ports_req_o[1].w_valid = 1'b1;
        end
        if (grant_slv0_wr_err) begin
            slv_ports_resp_o[0].aw_ready = 1'b1;
            slv_ports_resp_o[0].w_ready = 1'b1;
        end
        if (grant_slv1_wr_err) begin
            slv_ports_resp_o[1].aw_ready = 1'b1;
            slv_ports_resp_o[1].w_ready = 1'b1;
        end

        if (grant_slv0_rd_t0) begin
            slv_ports_resp_o[0].ar_ready = 1'b1;
            mst_ports_req_o[0].ar.id = {1'b0, slv_ports_req_i[0].ar.id};
            mst_ports_req_o[0].ar.addr = slv_ports_req_i[0].ar.addr;
            mst_ports_req_o[0].ar.prot = slv_ports_req_i[0].ar.prot;
            mst_ports_req_o[0].ar.len = slv_ports_req_i[0].ar.len;
            mst_ports_req_o[0].ar_valid = 1'b1;
        end
        if (grant_slv1_rd_t0) begin
            slv_ports_resp_o[1].ar_ready = 1'b1;
            mst_ports_req_o[0].ar.id = {1'b1, slv_ports_req_i[1].ar.id};
            mst_ports_req_o[0].ar.addr = slv_ports_req_i[1].ar.addr;
            mst_ports_req_o[0].ar.prot = slv_ports_req_i[1].ar.prot;
            mst_ports_req_o[0].ar.len = slv_ports_req_i[1].ar.len;
            mst_ports_req_o[0].ar_valid = 1'b1;
        end
        if (grant_slv0_rd_t1) begin
            slv_ports_resp_o[0].ar_ready = 1'b1;
            mst_ports_req_o[1].ar.id = {1'b0, slv_ports_req_i[0].ar.id};
            mst_ports_req_o[1].ar.addr = slv_ports_req_i[0].ar.addr;
            mst_ports_req_o[1].ar.prot = slv_ports_req_i[0].ar.prot;
            mst_ports_req_o[1].ar.len = slv_ports_req_i[0].ar.len;
            mst_ports_req_o[1].ar_valid = 1'b1;
        end
        if (grant_slv1_rd_t1) begin
            slv_ports_resp_o[1].ar_ready = 1'b1;
            mst_ports_req_o[1].ar.id = {1'b1, slv_ports_req_i[1].ar.id};
            mst_ports_req_o[1].ar.addr = slv_ports_req_i[1].ar.addr;
            mst_ports_req_o[1].ar.prot = slv_ports_req_i[1].ar.prot;
            mst_ports_req_o[1].ar.len = slv_ports_req_i[1].ar.len;
            mst_ports_req_o[1].ar_valid = 1'b1;
        end
        if (grant_slv0_rd_err) begin
            slv_ports_resp_o[0].ar_ready = 1'b1;
        end
        if (grant_slv1_rd_err) begin
            slv_ports_resp_o[1].ar_ready = 1'b1;
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
            slv_b_chan_q[0] <= '0;
            slv_b_chan_q[1] <= '0;
            slv_b_valid_q <= '0;
            slv_r_chan_q[0] <= '0;
            slv_r_chan_q[1] <= '0;
            slv_r_valid_q <= '0;
        end else begin
            if (slv_b_valid_q[0] && slv_ports_req_i[0].b_ready) begin
                slv_b_valid_q[0] <= 1'b0;
            end
            if (slv_b_valid_q[1] && slv_ports_req_i[1].b_ready) begin
                slv_b_valid_q[1] <= 1'b0;
            end
            if (slv_r_valid_q[0] && slv_ports_req_i[0].r_ready) begin
                slv_r_valid_q[0] <= 1'b0;
            end
            if (slv_r_valid_q[1] && slv_ports_req_i[1].r_ready) begin
                slv_r_valid_q[1] <= 1'b0;
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
                slv_b_chan_q[0].id <= slv_ports_req_i[0].aw.id;
                slv_b_chan_q[0].resp <= RESP_DECERR;
                slv_b_valid_q[0] <= 1'b1;
            end
            if (grant_slv1_wr_err) begin
                slv_b_chan_q[1].id <= slv_ports_req_i[1].aw.id;
                slv_b_chan_q[1].resp <= RESP_DECERR;
                slv_b_valid_q[1] <= 1'b1;
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
                slv_r_chan_q[0].id <= slv_ports_req_i[0].ar.id;
                slv_r_chan_q[0].data <= DECERR_DATA;
                slv_r_chan_q[0].resp <= RESP_DECERR;
                slv_r_chan_q[0].last <= 1'b1;
                slv_r_valid_q[0] <= 1'b1;
            end
            if (grant_slv1_rd_err) begin
                slv_r_chan_q[1].id <= slv_ports_req_i[1].ar.id;
                slv_r_chan_q[1].data <= DECERR_DATA;
                slv_r_chan_q[1].resp <= RESP_DECERR;
                slv_r_chan_q[1].last <= 1'b1;
                slv_r_valid_q[1] <= 1'b1;
            end
        end
    end

    logic _unused_inputs;
    assign _unused_inputs = test_i | ATOPs | Connectivity | Cfg.FallThrough
        | en_default_mst_port_i[0] | en_default_mst_port_i[1]
        | default_mst_port_i[0] | default_mst_port_i[1];
endmodule
