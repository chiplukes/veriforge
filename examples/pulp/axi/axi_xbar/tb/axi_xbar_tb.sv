module axi_xbar_exec_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [1:0]  slv0_aw_id,
    input  logic [31:0] slv0_aw_addr,
    input  logic [2:0]  slv0_aw_prot,
    input  logic [7:0]  slv0_aw_len,
    input  logic        slv0_aw_valid,
    output logic        slv0_aw_ready,
    input  logic [31:0] slv0_w_data,
    input  logic [3:0]  slv0_w_strb,
    input  logic        slv0_w_last,
    input  logic        slv0_w_valid,
    output logic        slv0_w_ready,
    output logic [1:0]  slv0_b_id,
    output logic [1:0]  slv0_b_resp,
    output logic        slv0_b_valid,
    input  logic        slv0_b_ready,
    input  logic [1:0]  slv0_ar_id,
    input  logic [31:0] slv0_ar_addr,
    input  logic [2:0]  slv0_ar_prot,
    input  logic [7:0]  slv0_ar_len,
    input  logic        slv0_ar_valid,
    output logic        slv0_ar_ready,
    output logic [1:0]  slv0_r_id,
    output logic [31:0] slv0_r_data,
    output logic [1:0]  slv0_r_resp,
    output logic        slv0_r_last,
    output logic        slv0_r_valid,
    input  logic        slv0_r_ready,
    input  logic [1:0]  slv1_aw_id,
    input  logic [31:0] slv1_aw_addr,
    input  logic [2:0]  slv1_aw_prot,
    input  logic [7:0]  slv1_aw_len,
    input  logic        slv1_aw_valid,
    output logic        slv1_aw_ready,
    input  logic [31:0] slv1_w_data,
    input  logic [3:0]  slv1_w_strb,
    input  logic        slv1_w_last,
    input  logic        slv1_w_valid,
    output logic        slv1_w_ready,
    output logic [1:0]  slv1_b_id,
    output logic [1:0]  slv1_b_resp,
    output logic        slv1_b_valid,
    input  logic        slv1_b_ready,
    input  logic [1:0]  slv1_ar_id,
    input  logic [31:0] slv1_ar_addr,
    input  logic [2:0]  slv1_ar_prot,
    input  logic [7:0]  slv1_ar_len,
    input  logic        slv1_ar_valid,
    output logic        slv1_ar_ready,
    output logic [1:0]  slv1_r_id,
    output logic [31:0] slv1_r_data,
    output logic [1:0]  slv1_r_resp,
    output logic        slv1_r_last,
    output logic        slv1_r_valid,
    input  logic        slv1_r_ready,
    output logic [31:0] target0_data,
    output logic [31:0] target1_data,
    output logic [2:0]  mst0_last_aw_id,
    output logic [2:0]  mst1_last_aw_id,
    output logic [2:0]  mst0_last_ar_id,
    output logic [2:0]  mst1_last_ar_id
);
    axi_xbar_typed_exec_tb i_typed_exec_tb (
        .clk(clk),
        .rst_n(rst_n),
        .slv0_aw_id(slv0_aw_id),
        .slv0_aw_addr(slv0_aw_addr),
        .slv0_aw_prot(slv0_aw_prot),
        .slv0_aw_len(slv0_aw_len),
        .slv0_aw_valid(slv0_aw_valid),
        .slv0_aw_ready(slv0_aw_ready),
        .slv0_w_data(slv0_w_data),
        .slv0_w_strb(slv0_w_strb),
        .slv0_w_last(slv0_w_last),
        .slv0_w_valid(slv0_w_valid),
        .slv0_w_ready(slv0_w_ready),
        .slv0_b_id(slv0_b_id),
        .slv0_b_resp(slv0_b_resp),
        .slv0_b_valid(slv0_b_valid),
        .slv0_b_ready(slv0_b_ready),
        .slv0_ar_id(slv0_ar_id),
        .slv0_ar_addr(slv0_ar_addr),
        .slv0_ar_prot(slv0_ar_prot),
        .slv0_ar_len(slv0_ar_len),
        .slv0_ar_valid(slv0_ar_valid),
        .slv0_ar_ready(slv0_ar_ready),
        .slv0_r_id(slv0_r_id),
        .slv0_r_data(slv0_r_data),
        .slv0_r_resp(slv0_r_resp),
        .slv0_r_last(slv0_r_last),
        .slv0_r_valid(slv0_r_valid),
        .slv0_r_ready(slv0_r_ready),
        .slv1_aw_id(slv1_aw_id),
        .slv1_aw_addr(slv1_aw_addr),
        .slv1_aw_prot(slv1_aw_prot),
        .slv1_aw_len(slv1_aw_len),
        .slv1_aw_valid(slv1_aw_valid),
        .slv1_aw_ready(slv1_aw_ready),
        .slv1_w_data(slv1_w_data),
        .slv1_w_strb(slv1_w_strb),
        .slv1_w_last(slv1_w_last),
        .slv1_w_valid(slv1_w_valid),
        .slv1_w_ready(slv1_w_ready),
        .slv1_b_id(slv1_b_id),
        .slv1_b_resp(slv1_b_resp),
        .slv1_b_valid(slv1_b_valid),
        .slv1_b_ready(slv1_b_ready),
        .slv1_ar_id(slv1_ar_id),
        .slv1_ar_addr(slv1_ar_addr),
        .slv1_ar_prot(slv1_ar_prot),
        .slv1_ar_len(slv1_ar_len),
        .slv1_ar_valid(slv1_ar_valid),
        .slv1_ar_ready(slv1_ar_ready),
        .slv1_r_id(slv1_r_id),
        .slv1_r_data(slv1_r_data),
        .slv1_r_resp(slv1_r_resp),
        .slv1_r_last(slv1_r_last),
        .slv1_r_valid(slv1_r_valid),
        .slv1_r_ready(slv1_r_ready),
        .target0_data(target0_data),
        .target1_data(target1_data),
        .mst0_last_aw_id(mst0_last_aw_id),
        .mst1_last_aw_id(mst1_last_aw_id),
        .mst0_last_ar_id(mst0_last_ar_id),
        .mst1_last_ar_id(mst1_last_ar_id)
    );
endmodule

module axi_xbar_typed_exec_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [1:0]  slv0_aw_id,
    input  logic [31:0] slv0_aw_addr,
    input  logic [2:0]  slv0_aw_prot,
    input  logic [7:0]  slv0_aw_len,
    input  logic        slv0_aw_valid,
    output logic        slv0_aw_ready,
    input  logic [31:0] slv0_w_data,
    input  logic [3:0]  slv0_w_strb,
    input  logic        slv0_w_last,
    input  logic        slv0_w_valid,
    output logic        slv0_w_ready,
    output logic [1:0]  slv0_b_id,
    output logic [1:0]  slv0_b_resp,
    output logic        slv0_b_valid,
    input  logic        slv0_b_ready,
    input  logic [1:0]  slv0_ar_id,
    input  logic [31:0] slv0_ar_addr,
    input  logic [2:0]  slv0_ar_prot,
    input  logic [7:0]  slv0_ar_len,
    input  logic        slv0_ar_valid,
    output logic        slv0_ar_ready,
    output logic [1:0]  slv0_r_id,
    output logic [31:0] slv0_r_data,
    output logic [1:0]  slv0_r_resp,
    output logic        slv0_r_last,
    output logic        slv0_r_valid,
    input  logic        slv0_r_ready,
    input  logic [1:0]  slv1_aw_id,
    input  logic [31:0] slv1_aw_addr,
    input  logic [2:0]  slv1_aw_prot,
    input  logic [7:0]  slv1_aw_len,
    input  logic        slv1_aw_valid,
    output logic        slv1_aw_ready,
    input  logic [31:0] slv1_w_data,
    input  logic [3:0]  slv1_w_strb,
    input  logic        slv1_w_last,
    input  logic        slv1_w_valid,
    output logic        slv1_w_ready,
    output logic [1:0]  slv1_b_id,
    output logic [1:0]  slv1_b_resp,
    output logic        slv1_b_valid,
    input  logic        slv1_b_ready,
    input  logic [1:0]  slv1_ar_id,
    input  logic [31:0] slv1_ar_addr,
    input  logic [2:0]  slv1_ar_prot,
    input  logic [7:0]  slv1_ar_len,
    input  logic        slv1_ar_valid,
    output logic        slv1_ar_ready,
    output logic [1:0]  slv1_r_id,
    output logic [31:0] slv1_r_data,
    output logic [1:0]  slv1_r_resp,
    output logic        slv1_r_last,
    output logic        slv1_r_valid,
    input  logic        slv1_r_ready,
    output logic [31:0] target0_data,
    output logic [31:0] target1_data,
    output logic [2:0]  mst0_last_aw_id,
    output logic [2:0]  mst1_last_aw_id,
    output logic [2:0]  mst0_last_ar_id,
    output logic [2:0]  mst1_last_ar_id
);
    import axi_pkg::*;

    localparam logic [31:0] TARGET0_BASE = 32'h0000_0000;
    localparam logic [31:0] TARGET1_BASE = 32'h0000_0100;
    localparam logic [31:0] TARGET2_BASE = 32'h0000_0200;
    localparam logic [31:0] TARGET0_INIT = 32'h1111_1111;
    localparam logic [31:0] TARGET1_INIT = 32'h2222_2222;

    typedef logic [31:0] addr_t;
    typedef logic [31:0] data_t;
    typedef logic [3:0] strb_t;
    typedef logic [7:0] len_t;
    typedef logic [1:0] slv_id_t;
    typedef logic [2:0] mst_id_t;

    typedef struct packed {
        slv_id_t id;
        addr_t   addr;
        prot_t   prot;
        len_t    len;
    } slv_aw_chan_t;

    typedef struct packed {
        mst_id_t id;
        addr_t   addr;
        prot_t   prot;
        len_t    len;
    } mst_aw_chan_t;

    typedef struct packed {
        data_t data;
        strb_t strb;
        logic  last;
    } w_chan_t;

    typedef struct packed {
        slv_id_t id;
        resp_t   resp;
    } slv_b_chan_t;

    typedef struct packed {
        mst_id_t id;
        resp_t   resp;
    } mst_b_chan_t;

    typedef struct packed {
        slv_id_t id;
        addr_t   addr;
        prot_t   prot;
        len_t    len;
    } slv_ar_chan_t;

    typedef struct packed {
        mst_id_t id;
        addr_t   addr;
        prot_t   prot;
        len_t    len;
    } mst_ar_chan_t;

    typedef struct packed {
        slv_id_t id;
        data_t   data;
        resp_t   resp;
        logic    last;
    } slv_r_chan_t;

    typedef struct packed {
        mst_id_t id;
        data_t   data;
        resp_t   resp;
        logic    last;
    } mst_r_chan_t;

    typedef struct packed {
        slv_aw_chan_t aw;
        logic         aw_valid;
        w_chan_t      w;
        logic         w_valid;
        logic         b_ready;
        slv_ar_chan_t ar;
        logic         ar_valid;
        logic         r_ready;
    } slv_req_t;

    typedef struct packed {
        logic        aw_ready;
        logic        w_ready;
        slv_b_chan_t b;
        logic        b_valid;
        logic        ar_ready;
        slv_r_chan_t r;
        logic        r_valid;
    } slv_resp_t;

    typedef struct packed {
        mst_aw_chan_t aw;
        logic         aw_valid;
        w_chan_t      w;
        logic         w_valid;
        logic         b_ready;
        mst_ar_chan_t ar;
        logic         ar_valid;
        logic         r_ready;
    } mst_req_t;

    typedef struct packed {
        logic        aw_ready;
        logic        w_ready;
        mst_b_chan_t b;
        logic        b_valid;
        logic        ar_ready;
        mst_r_chan_t r;
        logic        r_valid;
    } mst_resp_t;

    slv_req_t [1:0] slv_ports_req;
    slv_resp_t [1:0] slv_ports_resp;
    mst_req_t [1:0] mst_ports_req;
    mst_resp_t [1:0] mst_ports_resp;
    axi_pkg::xbar_rule_64_t [1:0] addr_map;
    logic [1:0] en_default_mst_port;
    logic [1:0] default_mst_port;

    data_t target0_data_q;
    data_t target1_data_q;
    mst_id_t target0_b_id_q;
    mst_id_t target1_b_id_q;
    logic target0_b_valid_q;
    logic target1_b_valid_q;
    mst_id_t target0_r_id_q;
    mst_id_t target1_r_id_q;
    data_t target0_r_data_q;
    data_t target1_r_data_q;
    resp_t target0_r_resp_q;
    resp_t target1_r_resp_q;
    logic target0_r_valid_q;
    logic target1_r_valid_q;

    logic [2:0] mst0_last_aw_id_q;
    logic [2:0] mst1_last_aw_id_q;
    logic [2:0] mst0_last_ar_id_q;
    logic [2:0] mst1_last_ar_id_q;

    assign slv_ports_req[0] = {
        slv0_aw_id,
        slv0_aw_addr,
        slv0_aw_prot,
        slv0_aw_len,
        slv0_aw_valid,
        slv0_w_data,
        slv0_w_strb,
        slv0_w_last,
        slv0_w_valid,
        slv0_b_ready,
        slv0_ar_id,
        slv0_ar_addr,
        slv0_ar_prot,
        slv0_ar_len,
        slv0_ar_valid,
        slv0_r_ready
    };
    assign slv_ports_req[1] = {
        slv1_aw_id,
        slv1_aw_addr,
        slv1_aw_prot,
        slv1_aw_len,
        slv1_aw_valid,
        slv1_w_data,
        slv1_w_strb,
        slv1_w_last,
        slv1_w_valid,
        slv1_b_ready,
        slv1_ar_id,
        slv1_ar_addr,
        slv1_ar_prot,
        slv1_ar_len,
        slv1_ar_valid,
        slv1_r_ready
    };

    assign slv0_aw_ready = slv_ports_resp[0].aw_ready;
    assign slv0_w_ready = slv_ports_resp[0].w_ready;
    assign slv0_b_id = slv_ports_resp[0].b.id;
    assign slv0_b_resp = slv_ports_resp[0].b.resp;
    assign slv0_b_valid = slv_ports_resp[0].b_valid;
    assign slv0_ar_ready = slv_ports_resp[0].ar_ready;
    assign slv0_r_id = slv_ports_resp[0].r.id;
    assign slv0_r_data = slv_ports_resp[0].r.data;
    assign slv0_r_resp = slv_ports_resp[0].r.resp;
    assign slv0_r_last = slv_ports_resp[0].r.last;
    assign slv0_r_valid = slv_ports_resp[0].r_valid;

    assign slv1_aw_ready = slv_ports_resp[1].aw_ready;
    assign slv1_w_ready = slv_ports_resp[1].w_ready;
    assign slv1_b_id = slv_ports_resp[1].b.id;
    assign slv1_b_resp = slv_ports_resp[1].b.resp;
    assign slv1_b_valid = slv_ports_resp[1].b_valid;
    assign slv1_ar_ready = slv_ports_resp[1].ar_ready;
    assign slv1_r_id = slv_ports_resp[1].r.id;
    assign slv1_r_data = slv_ports_resp[1].r.data;
    assign slv1_r_resp = slv_ports_resp[1].r.resp;
    assign slv1_r_last = slv_ports_resp[1].r.last;
    assign slv1_r_valid = slv_ports_resp[1].r_valid;

    assign mst_ports_resp[0] = {
        !target0_b_valid_q,
        !target0_b_valid_q,
        target0_b_id_q,
        RESP_OKAY,
        target0_b_valid_q,
        !target0_r_valid_q,
        target0_r_id_q,
        target0_r_data_q,
        target0_r_resp_q,
        1'b1,
        target0_r_valid_q
    };
    assign mst_ports_resp[1] = {
        !target1_b_valid_q,
        !target1_b_valid_q,
        target1_b_id_q,
        RESP_OKAY,
        target1_b_valid_q,
        !target1_r_valid_q,
        target1_r_id_q,
        target1_r_data_q,
        target1_r_resp_q,
        1'b1,
        target1_r_valid_q
    };

    assign target0_data = target0_data_q;
    assign target1_data = target1_data_q;
    assign mst0_last_aw_id = mst0_last_aw_id_q;
    assign mst1_last_aw_id = mst1_last_aw_id_q;
    assign mst0_last_ar_id = mst0_last_ar_id_q;
    assign mst1_last_ar_id = mst1_last_ar_id_q;

    assign en_default_mst_port = '0;
    assign default_mst_port = '0;

    initial begin
        addr_map[0].idx = 32'd0;
        addr_map[0].start_addr = 64'(TARGET0_BASE);
        addr_map[0].end_addr = 64'(TARGET1_BASE);
        addr_map[1].idx = 32'd1;
        addr_map[1].start_addr = 64'(TARGET1_BASE);
        addr_map[1].end_addr = 64'(TARGET2_BASE);
    end

    axi_xbar #(
        .slv_aw_chan_t(slv_aw_chan_t),
        .mst_aw_chan_t(mst_aw_chan_t),
        .w_chan_t(w_chan_t),
        .slv_b_chan_t(slv_b_chan_t),
        .mst_b_chan_t(mst_b_chan_t),
        .slv_ar_chan_t(slv_ar_chan_t),
        .mst_ar_chan_t(mst_ar_chan_t),
        .slv_r_chan_t(slv_r_chan_t),
        .mst_r_chan_t(mst_r_chan_t),
        .slv_req_t(slv_req_t),
        .slv_resp_t(slv_resp_t),
        .mst_req_t(mst_req_t),
        .mst_resp_t(mst_resp_t),
        .rule_t(axi_pkg::xbar_rule_64_t)
    ) i_dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .test_i(1'b0),
        .slv_ports_req_i(slv_ports_req),
        .slv_ports_resp_o(slv_ports_resp),
        .mst_ports_req_o(mst_ports_req),
        .mst_ports_resp_i(mst_ports_resp),
        .addr_map_i(addr_map),
        .en_default_mst_port_i(en_default_mst_port),
        .default_mst_port_i(default_mst_port)
    );

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            target0_data_q <= TARGET0_INIT;
            target1_data_q <= TARGET1_INIT;
            target0_b_id_q <= '0;
            target1_b_id_q <= '0;
            target0_b_valid_q <= 1'b0;
            target1_b_valid_q <= 1'b0;
            target0_r_id_q <= '0;
            target1_r_id_q <= '0;
            target0_r_data_q <= '0;
            target1_r_data_q <= '0;
            target0_r_resp_q <= RESP_OKAY;
            target1_r_resp_q <= RESP_OKAY;
            target0_r_valid_q <= 1'b0;
            target1_r_valid_q <= 1'b0;
            mst0_last_aw_id_q <= '0;
            mst1_last_aw_id_q <= '0;
            mst0_last_ar_id_q <= '0;
            mst1_last_ar_id_q <= '0;
        end else begin
            if (target0_b_valid_q && mst_ports_req[0].b_ready) begin
                target0_b_valid_q <= 1'b0;
            end
            if (target1_b_valid_q && mst_ports_req[1].b_ready) begin
                target1_b_valid_q <= 1'b0;
            end
            if (target0_r_valid_q && mst_ports_req[0].r_ready) begin
                target0_r_valid_q <= 1'b0;
            end
            if (target1_r_valid_q && mst_ports_req[1].r_ready) begin
                target1_r_valid_q <= 1'b0;
            end

            if (mst_ports_req[0].aw_valid && mst_ports_req[0].w_valid && !target0_b_valid_q) begin
                if (mst_ports_req[0].w.strb[0]) target0_data_q[7:0] <= mst_ports_req[0].w.data[7:0];
                if (mst_ports_req[0].w.strb[1]) target0_data_q[15:8] <= mst_ports_req[0].w.data[15:8];
                if (mst_ports_req[0].w.strb[2]) target0_data_q[23:16] <= mst_ports_req[0].w.data[23:16];
                if (mst_ports_req[0].w.strb[3]) target0_data_q[31:24] <= mst_ports_req[0].w.data[31:24];
                target0_b_id_q <= mst_ports_req[0].aw.id;
                target0_b_valid_q <= 1'b1;
                mst0_last_aw_id_q <= mst_ports_req[0].aw.id;
            end
            if (mst_ports_req[1].aw_valid && mst_ports_req[1].w_valid && !target1_b_valid_q) begin
                if (mst_ports_req[1].w.strb[0]) target1_data_q[7:0] <= mst_ports_req[1].w.data[7:0];
                if (mst_ports_req[1].w.strb[1]) target1_data_q[15:8] <= mst_ports_req[1].w.data[15:8];
                if (mst_ports_req[1].w.strb[2]) target1_data_q[23:16] <= mst_ports_req[1].w.data[23:16];
                if (mst_ports_req[1].w.strb[3]) target1_data_q[31:24] <= mst_ports_req[1].w.data[31:24];
                target1_b_id_q <= mst_ports_req[1].aw.id;
                target1_b_valid_q <= 1'b1;
                mst1_last_aw_id_q <= mst_ports_req[1].aw.id;
            end

            if (mst_ports_req[0].ar_valid && !target0_r_valid_q) begin
                target0_r_id_q <= mst_ports_req[0].ar.id;
                target0_r_data_q <= target0_data_q;
                target0_r_resp_q <= RESP_OKAY;
                target0_r_valid_q <= 1'b1;
                mst0_last_ar_id_q <= mst_ports_req[0].ar.id;
            end
            if (mst_ports_req[1].ar_valid && !target1_r_valid_q) begin
                target1_r_id_q <= mst_ports_req[1].ar.id;
                target1_r_data_q <= target1_data_q;
                target1_r_resp_q <= RESP_OKAY;
                target1_r_valid_q <= 1'b1;
                mst1_last_ar_id_q <= mst_ports_req[1].ar.id;
            end
        end
    end
endmodule
