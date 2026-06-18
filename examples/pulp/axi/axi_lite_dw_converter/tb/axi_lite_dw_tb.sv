module axi_lite_dw_typed_core #(
    parameter int unsigned SLV_DW = 32,
    parameter int unsigned MST_DW = 32
) (
    input  logic                clk,
    input  logic                rst_n,
    input  logic [31:0]         slv_aw_addr,
    input  logic                slv_aw_valid,
    output logic                slv_aw_ready,
    input  logic [SLV_DW-1:0]   slv_w_data,
    input  logic [SLV_DW/8-1:0] slv_w_strb,
    input  logic                slv_w_valid,
    output logic                slv_w_ready,
    output logic [1:0]          slv_b_resp,
    output logic                slv_b_valid,
    input  logic                slv_b_ready,
    input  logic [31:0]         slv_ar_addr,
    input  logic                slv_ar_valid,
    output logic                slv_ar_ready,
    output logic [SLV_DW-1:0]   slv_r_data,
    output logic [1:0]          slv_r_resp,
    output logic                slv_r_valid,
    input  logic                slv_r_ready,
    output logic [31:0]         mst_aw_addr,
    output logic                mst_aw_valid,
    input  logic                mst_aw_ready,
    output logic [MST_DW-1:0]   mst_w_data,
    output logic [MST_DW/8-1:0] mst_w_strb,
    output logic                mst_w_valid,
    input  logic                mst_w_ready,
    input  logic [1:0]          mst_b_resp,
    input  logic                mst_b_valid,
    output logic                mst_b_ready,
    output logic [31:0]         mst_ar_addr,
    output logic                mst_ar_valid,
    input  logic                mst_ar_ready,
    input  logic [MST_DW-1:0]   mst_r_data,
    input  logic [1:0]          mst_r_resp,
    input  logic                mst_r_valid,
    output logic                mst_r_ready
);
    import axi_pkg::*;

    typedef logic [31:0] addr_t;
    typedef logic [SLV_DW-1:0] slv_data_t;
    typedef logic [SLV_DW/8-1:0] slv_strb_t;
    typedef logic [MST_DW-1:0] mst_data_t;
    typedef logic [MST_DW/8-1:0] mst_strb_t;

    typedef struct packed {
        addr_t          addr;
        prot_t          prot;
    } aw_chan_t;

    typedef struct packed {
        slv_data_t data;
        slv_strb_t strb;
    } w_chan_slv_t;

    typedef struct packed {
        mst_data_t data;
        mst_strb_t strb;
    } w_chan_mst_t;

    typedef struct packed {
        resp_t resp;
    } b_chan_t;

    typedef struct packed {
        addr_t          addr;
        prot_t          prot;
    } ar_chan_t;

    typedef struct packed {
        slv_data_t      data;
        resp_t          resp;
    } r_chan_slv_t;

    typedef struct packed {
        mst_data_t      data;
        resp_t          resp;
    } r_chan_mst_t;

    typedef struct packed {
        aw_chan_t    aw;
        logic        aw_valid;
        w_chan_slv_t w;
        logic        w_valid;
        logic        b_ready;
        ar_chan_t    ar;
        logic        ar_valid;
        logic        r_ready;
    } req_lite_slv_t;

    typedef struct packed {
        logic        aw_ready;
        logic        w_ready;
        b_chan_t     b;
        logic        b_valid;
        logic        ar_ready;
        r_chan_slv_t r;
        logic        r_valid;
    } res_lite_slv_t;

    typedef struct packed {
        aw_chan_t    aw;
        logic        aw_valid;
        w_chan_mst_t w;
        logic        w_valid;
        logic        b_ready;
        ar_chan_t    ar;
        logic        ar_valid;
        logic        r_ready;
    } req_lite_mst_t;

    typedef struct packed {
        logic        aw_ready;
        logic        w_ready;
        b_chan_t     b;
        logic        b_valid;
        logic        ar_ready;
        r_chan_mst_t r;
        logic        r_valid;
    } res_lite_mst_t;

    req_lite_slv_t slv_req;
    res_lite_slv_t slv_res;
    req_lite_mst_t mst_req;
    res_lite_mst_t mst_res;

    assign slv_req = {
        slv_aw_addr,
        3'b000,
        slv_aw_valid,
        slv_w_data,
        slv_w_strb,
        slv_w_valid,
        slv_b_ready,
        slv_ar_addr,
        3'b000,
        slv_ar_valid,
        slv_r_ready
    };

    assign slv_aw_ready = slv_res.aw_ready;
    assign slv_w_ready = slv_res.w_ready;
    assign slv_b_resp = slv_res.b.resp;
    assign slv_b_valid = slv_res.b_valid;
    assign slv_ar_ready = slv_res.ar_ready;
    assign slv_r_data = slv_res.r.data;
    assign slv_r_resp = slv_res.r.resp;
    assign slv_r_valid = slv_res.r_valid;

    assign mst_aw_addr = mst_req.aw.addr;
    assign mst_aw_valid = mst_req.aw_valid;
    assign mst_w_data = mst_req.w.data;
    assign mst_w_strb = mst_req.w.strb;
    assign mst_w_valid = mst_req.w_valid;
    assign mst_b_ready = mst_req.b_ready;
    assign mst_ar_addr = mst_req.ar.addr;
    assign mst_ar_valid = mst_req.ar_valid;
    assign mst_r_ready = mst_req.r_ready;

    assign mst_res.aw_ready = mst_aw_ready;
    assign mst_res.w_ready = mst_w_ready;
    assign mst_res.b.resp = mst_b_resp;
    assign mst_res.b_valid = mst_b_valid;
    assign mst_res.ar_ready = mst_ar_ready;
    assign mst_res.r.data = mst_r_data;
    assign mst_res.r.resp = mst_r_resp;
    assign mst_res.r_valid = mst_r_valid;

    axi_lite_dw_converter #(
        .AxiAddrWidth(32),
        .AxiSlvPortDataWidth(SLV_DW),
        .AxiMstPortDataWidth(MST_DW),
        .axi_lite_aw_t(aw_chan_t),
        .axi_lite_slv_w_t(w_chan_slv_t),
        .axi_lite_mst_w_t(w_chan_mst_t),
        .axi_lite_b_t(b_chan_t),
        .axi_lite_ar_t(ar_chan_t),
        .axi_lite_slv_r_t(r_chan_slv_t),
        .axi_lite_mst_r_t(r_chan_mst_t),
        .axi_lite_slv_req_t(req_lite_slv_t),
        .axi_lite_slv_res_t(res_lite_slv_t),
        .axi_lite_mst_req_t(req_lite_mst_t),
        .axi_lite_mst_res_t(res_lite_mst_t)
    ) i_dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .slv_req_i(slv_req),
        .slv_res_o(slv_res),
        .mst_req_o(mst_req),
        .mst_res_i(mst_res)
    );

endmodule

module axi_lite_dw_down_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] slv_aw_addr,
    input  logic        slv_aw_valid,
    output logic        slv_aw_ready,
    input  logic [31:0] slv_w_data,
    input  logic [3:0]  slv_w_strb,
    input  logic        slv_w_valid,
    output logic        slv_w_ready,
    output logic [1:0]  slv_b_resp,
    output logic        slv_b_valid,
    input  logic        slv_b_ready,
    input  logic [31:0] slv_ar_addr,
    input  logic        slv_ar_valid,
    output logic        slv_ar_ready,
    output logic [31:0] slv_r_data,
    output logic [1:0]  slv_r_resp,
    output logic        slv_r_valid,
    input  logic        slv_r_ready,
    output logic [31:0] mst_aw_addr,
    output logic        mst_aw_valid,
    input  logic        mst_aw_ready,
    output logic [15:0] mst_w_data,
    output logic [1:0]  mst_w_strb,
    output logic        mst_w_valid,
    input  logic        mst_w_ready,
    input  logic [1:0]  mst_b_resp,
    input  logic        mst_b_valid,
    output logic        mst_b_ready,
    output logic [31:0] mst_ar_addr,
    output logic        mst_ar_valid,
    input  logic        mst_ar_ready,
    input  logic [15:0] mst_r_data,
    input  logic [1:0]  mst_r_resp,
    input  logic        mst_r_valid,
    output logic        mst_r_ready
);
    axi_lite_dw_typed_core #(
        .SLV_DW(32),
        .MST_DW(16)
    ) i_core (
        .clk(clk),
        .rst_n(rst_n),
        .slv_aw_addr(slv_aw_addr),
        .slv_aw_valid(slv_aw_valid),
        .slv_aw_ready(slv_aw_ready),
        .slv_w_data(slv_w_data),
        .slv_w_strb(slv_w_strb),
        .slv_w_valid(slv_w_valid),
        .slv_w_ready(slv_w_ready),
        .slv_b_resp(slv_b_resp),
        .slv_b_valid(slv_b_valid),
        .slv_b_ready(slv_b_ready),
        .slv_ar_addr(slv_ar_addr),
        .slv_ar_valid(slv_ar_valid),
        .slv_ar_ready(slv_ar_ready),
        .slv_r_data(slv_r_data),
        .slv_r_resp(slv_r_resp),
        .slv_r_valid(slv_r_valid),
        .slv_r_ready(slv_r_ready),
        .mst_aw_addr(mst_aw_addr),
        .mst_aw_valid(mst_aw_valid),
        .mst_aw_ready(mst_aw_ready),
        .mst_w_data(mst_w_data),
        .mst_w_strb(mst_w_strb),
        .mst_w_valid(mst_w_valid),
        .mst_w_ready(mst_w_ready),
        .mst_b_resp(mst_b_resp),
        .mst_b_valid(mst_b_valid),
        .mst_b_ready(mst_b_ready),
        .mst_ar_addr(mst_ar_addr),
        .mst_ar_valid(mst_ar_valid),
        .mst_ar_ready(mst_ar_ready),
        .mst_r_data(mst_r_data),
        .mst_r_resp(mst_r_resp),
        .mst_r_valid(mst_r_valid),
        .mst_r_ready(mst_r_ready)
    );
endmodule

module axi_lite_dw_up_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] slv_aw_addr,
    input  logic        slv_aw_valid,
    output logic        slv_aw_ready,
    input  logic [15:0] slv_w_data,
    input  logic [1:0]  slv_w_strb,
    input  logic        slv_w_valid,
    output logic        slv_w_ready,
    output logic [1:0]  slv_b_resp,
    output logic        slv_b_valid,
    input  logic        slv_b_ready,
    input  logic [31:0] slv_ar_addr,
    input  logic        slv_ar_valid,
    output logic        slv_ar_ready,
    output logic [15:0] slv_r_data,
    output logic [1:0]  slv_r_resp,
    output logic        slv_r_valid,
    input  logic        slv_r_ready,
    output logic [31:0] mst_aw_addr,
    output logic        mst_aw_valid,
    input  logic        mst_aw_ready,
    output logic [31:0] mst_w_data,
    output logic [3:0]  mst_w_strb,
    output logic        mst_w_valid,
    input  logic        mst_w_ready,
    input  logic [1:0]  mst_b_resp,
    input  logic        mst_b_valid,
    output logic        mst_b_ready,
    output logic [31:0] mst_ar_addr,
    output logic        mst_ar_valid,
    input  logic        mst_ar_ready,
    input  logic [31:0] mst_r_data,
    input  logic [1:0]  mst_r_resp,
    input  logic        mst_r_valid,
    output logic        mst_r_ready
);
    axi_lite_dw_typed_core #(
        .SLV_DW(16),
        .MST_DW(32)
    ) i_core (
        .clk(clk),
        .rst_n(rst_n),
        .slv_aw_addr(slv_aw_addr),
        .slv_aw_valid(slv_aw_valid),
        .slv_aw_ready(slv_aw_ready),
        .slv_w_data(slv_w_data),
        .slv_w_strb(slv_w_strb),
        .slv_w_valid(slv_w_valid),
        .slv_w_ready(slv_w_ready),
        .slv_b_resp(slv_b_resp),
        .slv_b_valid(slv_b_valid),
        .slv_b_ready(slv_b_ready),
        .slv_ar_addr(slv_ar_addr),
        .slv_ar_valid(slv_ar_valid),
        .slv_ar_ready(slv_ar_ready),
        .slv_r_data(slv_r_data),
        .slv_r_resp(slv_r_resp),
        .slv_r_valid(slv_r_valid),
        .slv_r_ready(slv_r_ready),
        .mst_aw_addr(mst_aw_addr),
        .mst_aw_valid(mst_aw_valid),
        .mst_aw_ready(mst_aw_ready),
        .mst_w_data(mst_w_data),
        .mst_w_strb(mst_w_strb),
        .mst_w_valid(mst_w_valid),
        .mst_w_ready(mst_w_ready),
        .mst_b_resp(mst_b_resp),
        .mst_b_valid(mst_b_valid),
        .mst_b_ready(mst_b_ready),
        .mst_ar_addr(mst_ar_addr),
        .mst_ar_valid(mst_ar_valid),
        .mst_ar_ready(mst_ar_ready),
        .mst_r_data(mst_r_data),
        .mst_r_resp(mst_r_resp),
        .mst_r_valid(mst_r_valid),
        .mst_r_ready(mst_r_ready)
    );
endmodule

module axi_lite_dw_same_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] slv_aw_addr,
    input  logic        slv_aw_valid,
    output logic        slv_aw_ready,
    input  logic [31:0] slv_w_data,
    input  logic [3:0]  slv_w_strb,
    input  logic        slv_w_valid,
    output logic        slv_w_ready,
    output logic [1:0]  slv_b_resp,
    output logic        slv_b_valid,
    input  logic        slv_b_ready,
    input  logic [31:0] slv_ar_addr,
    input  logic        slv_ar_valid,
    output logic        slv_ar_ready,
    output logic [31:0] slv_r_data,
    output logic [1:0]  slv_r_resp,
    output logic        slv_r_valid,
    input  logic        slv_r_ready,
    output logic [31:0] mst_aw_addr,
    output logic        mst_aw_valid,
    input  logic        mst_aw_ready,
    output logic [31:0] mst_w_data,
    output logic [3:0]  mst_w_strb,
    output logic        mst_w_valid,
    input  logic        mst_w_ready,
    input  logic [1:0]  mst_b_resp,
    input  logic        mst_b_valid,
    output logic        mst_b_ready,
    output logic [31:0] mst_ar_addr,
    output logic        mst_ar_valid,
    input  logic        mst_ar_ready,
    input  logic [31:0] mst_r_data,
    input  logic [1:0]  mst_r_resp,
    input  logic        mst_r_valid,
    output logic        mst_r_ready
);
    axi_lite_dw_typed_core #(
        .SLV_DW(32),
        .MST_DW(32)
    ) i_core (
        .clk(clk),
        .rst_n(rst_n),
        .slv_aw_addr(slv_aw_addr),
        .slv_aw_valid(slv_aw_valid),
        .slv_aw_ready(slv_aw_ready),
        .slv_w_data(slv_w_data),
        .slv_w_strb(slv_w_strb),
        .slv_w_valid(slv_w_valid),
        .slv_w_ready(slv_w_ready),
        .slv_b_resp(slv_b_resp),
        .slv_b_valid(slv_b_valid),
        .slv_b_ready(slv_b_ready),
        .slv_ar_addr(slv_ar_addr),
        .slv_ar_valid(slv_ar_valid),
        .slv_ar_ready(slv_ar_ready),
        .slv_r_data(slv_r_data),
        .slv_r_resp(slv_r_resp),
        .slv_r_valid(slv_r_valid),
        .slv_r_ready(slv_r_ready),
        .mst_aw_addr(mst_aw_addr),
        .mst_aw_valid(mst_aw_valid),
        .mst_aw_ready(mst_aw_ready),
        .mst_w_data(mst_w_data),
        .mst_w_strb(mst_w_strb),
        .mst_w_valid(mst_w_valid),
        .mst_w_ready(mst_w_ready),
        .mst_b_resp(mst_b_resp),
        .mst_b_valid(mst_b_valid),
        .mst_b_ready(mst_b_ready),
        .mst_ar_addr(mst_ar_addr),
        .mst_ar_valid(mst_ar_valid),
        .mst_ar_ready(mst_ar_ready),
        .mst_r_data(mst_r_data),
        .mst_r_resp(mst_r_resp),
        .mst_r_valid(mst_r_valid),
        .mst_r_ready(mst_r_ready)
    );
endmodule

module axi_lite_dw_typed_down32_16_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] slv_aw_addr,
    input  logic        slv_aw_valid,
    output logic        slv_aw_ready,
    input  logic [31:0] slv_w_data,
    input  logic [3:0]  slv_w_strb,
    input  logic        slv_w_valid,
    output logic        slv_w_ready,
    output logic [1:0]  slv_b_resp,
    output logic        slv_b_valid,
    input  logic        slv_b_ready,
    input  logic [31:0] slv_ar_addr,
    input  logic        slv_ar_valid,
    output logic        slv_ar_ready,
    output logic [31:0] slv_r_data,
    output logic [1:0]  slv_r_resp,
    output logic        slv_r_valid,
    input  logic        slv_r_ready,
    output logic [31:0] mst_aw_addr,
    output logic        mst_aw_valid,
    input  logic        mst_aw_ready,
    output logic [15:0] mst_w_data,
    output logic [1:0]  mst_w_strb,
    output logic        mst_w_valid,
    input  logic        mst_w_ready,
    input  logic [1:0]  mst_b_resp,
    input  logic        mst_b_valid,
    output logic        mst_b_ready,
    output logic [31:0] mst_ar_addr,
    output logic        mst_ar_valid,
    input  logic        mst_ar_ready,
    input  logic [15:0] mst_r_data,
    input  logic [1:0]  mst_r_resp,
    input  logic        mst_r_valid,
    output logic        mst_r_ready
);
    axi_lite_dw_typed_core #(
        .SLV_DW(32),
        .MST_DW(16)
    ) i_core (
        .clk(clk),
        .rst_n(rst_n),
        .slv_aw_addr(slv_aw_addr),
        .slv_aw_valid(slv_aw_valid),
        .slv_aw_ready(slv_aw_ready),
        .slv_w_data(slv_w_data),
        .slv_w_strb(slv_w_strb),
        .slv_w_valid(slv_w_valid),
        .slv_w_ready(slv_w_ready),
        .slv_b_resp(slv_b_resp),
        .slv_b_valid(slv_b_valid),
        .slv_b_ready(slv_b_ready),
        .slv_ar_addr(slv_ar_addr),
        .slv_ar_valid(slv_ar_valid),
        .slv_ar_ready(slv_ar_ready),
        .slv_r_data(slv_r_data),
        .slv_r_resp(slv_r_resp),
        .slv_r_valid(slv_r_valid),
        .slv_r_ready(slv_r_ready),
        .mst_aw_addr(mst_aw_addr),
        .mst_aw_valid(mst_aw_valid),
        .mst_aw_ready(mst_aw_ready),
        .mst_w_data(mst_w_data),
        .mst_w_strb(mst_w_strb),
        .mst_w_valid(mst_w_valid),
        .mst_w_ready(mst_w_ready),
        .mst_b_resp(mst_b_resp),
        .mst_b_valid(mst_b_valid),
        .mst_b_ready(mst_b_ready),
        .mst_ar_addr(mst_ar_addr),
        .mst_ar_valid(mst_ar_valid),
        .mst_ar_ready(mst_ar_ready),
        .mst_r_data(mst_r_data),
        .mst_r_resp(mst_r_resp),
        .mst_r_valid(mst_r_valid),
        .mst_r_ready(mst_r_ready)
    );
endmodule

module axi_lite_dw_typed_up16_32_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] slv_aw_addr,
    input  logic        slv_aw_valid,
    output logic        slv_aw_ready,
    input  logic [15:0] slv_w_data,
    input  logic [1:0]  slv_w_strb,
    input  logic        slv_w_valid,
    output logic        slv_w_ready,
    output logic [1:0]  slv_b_resp,
    output logic        slv_b_valid,
    input  logic        slv_b_ready,
    input  logic [31:0] slv_ar_addr,
    input  logic        slv_ar_valid,
    output logic        slv_ar_ready,
    output logic [15:0] slv_r_data,
    output logic [1:0]  slv_r_resp,
    output logic        slv_r_valid,
    input  logic        slv_r_ready,
    output logic [31:0] mst_aw_addr,
    output logic        mst_aw_valid,
    input  logic        mst_aw_ready,
    output logic [31:0] mst_w_data,
    output logic [3:0]  mst_w_strb,
    output logic        mst_w_valid,
    input  logic        mst_w_ready,
    input  logic [1:0]  mst_b_resp,
    input  logic        mst_b_valid,
    output logic        mst_b_ready,
    output logic [31:0] mst_ar_addr,
    output logic        mst_ar_valid,
    input  logic        mst_ar_ready,
    input  logic [31:0] mst_r_data,
    input  logic [1:0]  mst_r_resp,
    input  logic        mst_r_valid,
    output logic        mst_r_ready
);
    axi_lite_dw_typed_core #(
        .SLV_DW(16),
        .MST_DW(32)
    ) i_core (
        .clk(clk),
        .rst_n(rst_n),
        .slv_aw_addr(slv_aw_addr),
        .slv_aw_valid(slv_aw_valid),
        .slv_aw_ready(slv_aw_ready),
        .slv_w_data(slv_w_data),
        .slv_w_strb(slv_w_strb),
        .slv_w_valid(slv_w_valid),
        .slv_w_ready(slv_w_ready),
        .slv_b_resp(slv_b_resp),
        .slv_b_valid(slv_b_valid),
        .slv_b_ready(slv_b_ready),
        .slv_ar_addr(slv_ar_addr),
        .slv_ar_valid(slv_ar_valid),
        .slv_ar_ready(slv_ar_ready),
        .slv_r_data(slv_r_data),
        .slv_r_resp(slv_r_resp),
        .slv_r_valid(slv_r_valid),
        .slv_r_ready(slv_r_ready),
        .mst_aw_addr(mst_aw_addr),
        .mst_aw_valid(mst_aw_valid),
        .mst_aw_ready(mst_aw_ready),
        .mst_w_data(mst_w_data),
        .mst_w_strb(mst_w_strb),
        .mst_w_valid(mst_w_valid),
        .mst_w_ready(mst_w_ready),
        .mst_b_resp(mst_b_resp),
        .mst_b_valid(mst_b_valid),
        .mst_b_ready(mst_b_ready),
        .mst_ar_addr(mst_ar_addr),
        .mst_ar_valid(mst_ar_valid),
        .mst_ar_ready(mst_ar_ready),
        .mst_r_data(mst_r_data),
        .mst_r_resp(mst_r_resp),
        .mst_r_valid(mst_r_valid),
        .mst_r_ready(mst_r_ready)
    );
endmodule

module axi_lite_dw_typed_same32_32_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] slv_aw_addr,
    input  logic        slv_aw_valid,
    output logic        slv_aw_ready,
    input  logic [31:0] slv_w_data,
    input  logic [3:0]  slv_w_strb,
    input  logic        slv_w_valid,
    output logic        slv_w_ready,
    output logic [1:0]  slv_b_resp,
    output logic        slv_b_valid,
    input  logic        slv_b_ready,
    input  logic [31:0] slv_ar_addr,
    input  logic        slv_ar_valid,
    output logic        slv_ar_ready,
    output logic [31:0] slv_r_data,
    output logic [1:0]  slv_r_resp,
    output logic        slv_r_valid,
    input  logic        slv_r_ready,
    output logic [31:0] mst_aw_addr,
    output logic        mst_aw_valid,
    input  logic        mst_aw_ready,
    output logic [31:0] mst_w_data,
    output logic [3:0]  mst_w_strb,
    output logic        mst_w_valid,
    input  logic        mst_w_ready,
    input  logic [1:0]  mst_b_resp,
    input  logic        mst_b_valid,
    output logic        mst_b_ready,
    output logic [31:0] mst_ar_addr,
    output logic        mst_ar_valid,
    input  logic        mst_ar_ready,
    input  logic [31:0] mst_r_data,
    input  logic [1:0]  mst_r_resp,
    input  logic        mst_r_valid,
    output logic        mst_r_ready
);
    axi_lite_dw_typed_core #(
        .SLV_DW(32),
        .MST_DW(32)
    ) i_core (
        .clk(clk),
        .rst_n(rst_n),
        .slv_aw_addr(slv_aw_addr),
        .slv_aw_valid(slv_aw_valid),
        .slv_aw_ready(slv_aw_ready),
        .slv_w_data(slv_w_data),
        .slv_w_strb(slv_w_strb),
        .slv_w_valid(slv_w_valid),
        .slv_w_ready(slv_w_ready),
        .slv_b_resp(slv_b_resp),
        .slv_b_valid(slv_b_valid),
        .slv_b_ready(slv_b_ready),
        .slv_ar_addr(slv_ar_addr),
        .slv_ar_valid(slv_ar_valid),
        .slv_ar_ready(slv_ar_ready),
        .slv_r_data(slv_r_data),
        .slv_r_resp(slv_r_resp),
        .slv_r_valid(slv_r_valid),
        .slv_r_ready(slv_r_ready),
        .mst_aw_addr(mst_aw_addr),
        .mst_aw_valid(mst_aw_valid),
        .mst_aw_ready(mst_aw_ready),
        .mst_w_data(mst_w_data),
        .mst_w_strb(mst_w_strb),
        .mst_w_valid(mst_w_valid),
        .mst_w_ready(mst_w_ready),
        .mst_b_resp(mst_b_resp),
        .mst_b_valid(mst_b_valid),
        .mst_b_ready(mst_b_ready),
        .mst_ar_addr(mst_ar_addr),
        .mst_ar_valid(mst_ar_valid),
        .mst_ar_ready(mst_ar_ready),
        .mst_r_data(mst_r_data),
        .mst_r_resp(mst_r_resp),
        .mst_r_valid(mst_r_valid),
        .mst_r_ready(mst_r_ready)
    );
endmodule

module axi_lite_dw_typed_up32_128_tb (
    input  logic         clk,
    input  logic         rst_n,
    input  logic [31:0]  slv_aw_addr,
    input  logic         slv_aw_valid,
    output logic         slv_aw_ready,
    input  logic [31:0]  slv_w_data,
    input  logic [3:0]   slv_w_strb,
    input  logic         slv_w_valid,
    output logic         slv_w_ready,
    output logic [1:0]   slv_b_resp,
    output logic         slv_b_valid,
    input  logic         slv_b_ready,
    input  logic [31:0]  slv_ar_addr,
    input  logic         slv_ar_valid,
    output logic         slv_ar_ready,
    output logic [31:0]  slv_r_data,
    output logic [1:0]   slv_r_resp,
    output logic         slv_r_valid,
    input  logic         slv_r_ready,
    output logic [31:0]  mst_aw_addr,
    output logic         mst_aw_valid,
    input  logic         mst_aw_ready,
    output logic [127:0] mst_w_data,
    output logic [15:0]  mst_w_strb,
    output logic         mst_w_valid,
    input  logic         mst_w_ready,
    input  logic [1:0]   mst_b_resp,
    input  logic         mst_b_valid,
    output logic         mst_b_ready,
    output logic [31:0]  mst_ar_addr,
    output logic         mst_ar_valid,
    input  logic         mst_ar_ready,
    input  logic [127:0] mst_r_data,
    input  logic [1:0]   mst_r_resp,
    input  logic         mst_r_valid,
    output logic         mst_r_ready
);
    axi_lite_dw_typed_core #(
        .SLV_DW(32),
        .MST_DW(128)
    ) i_core (
        .clk(clk),
        .rst_n(rst_n),
        .slv_aw_addr(slv_aw_addr),
        .slv_aw_valid(slv_aw_valid),
        .slv_aw_ready(slv_aw_ready),
        .slv_w_data(slv_w_data),
        .slv_w_strb(slv_w_strb),
        .slv_w_valid(slv_w_valid),
        .slv_w_ready(slv_w_ready),
        .slv_b_resp(slv_b_resp),
        .slv_b_valid(slv_b_valid),
        .slv_b_ready(slv_b_ready),
        .slv_ar_addr(slv_ar_addr),
        .slv_ar_valid(slv_ar_valid),
        .slv_ar_ready(slv_ar_ready),
        .slv_r_data(slv_r_data),
        .slv_r_resp(slv_r_resp),
        .slv_r_valid(slv_r_valid),
        .slv_r_ready(slv_r_ready),
        .mst_aw_addr(mst_aw_addr),
        .mst_aw_valid(mst_aw_valid),
        .mst_aw_ready(mst_aw_ready),
        .mst_w_data(mst_w_data),
        .mst_w_strb(mst_w_strb),
        .mst_w_valid(mst_w_valid),
        .mst_w_ready(mst_w_ready),
        .mst_b_resp(mst_b_resp),
        .mst_b_valid(mst_b_valid),
        .mst_b_ready(mst_b_ready),
        .mst_ar_addr(mst_ar_addr),
        .mst_ar_valid(mst_ar_valid),
        .mst_ar_ready(mst_ar_ready),
        .mst_r_data(mst_r_data),
        .mst_r_resp(mst_r_resp),
        .mst_r_valid(mst_r_valid),
        .mst_r_ready(mst_r_ready)
    );
endmodule

module axi_lite_dw_typed_up64_128_tb (
    input  logic         clk,
    input  logic         rst_n,
    input  logic [31:0]  slv_aw_addr,
    input  logic         slv_aw_valid,
    output logic         slv_aw_ready,
    input  logic [63:0]  slv_w_data,
    input  logic [7:0]   slv_w_strb,
    input  logic         slv_w_valid,
    output logic         slv_w_ready,
    output logic [1:0]   slv_b_resp,
    output logic         slv_b_valid,
    input  logic         slv_b_ready,
    input  logic [31:0]  slv_ar_addr,
    input  logic         slv_ar_valid,
    output logic         slv_ar_ready,
    output logic [63:0]  slv_r_data,
    output logic [1:0]   slv_r_resp,
    output logic         slv_r_valid,
    input  logic         slv_r_ready,
    output logic [31:0]  mst_aw_addr,
    output logic         mst_aw_valid,
    input  logic         mst_aw_ready,
    output logic [127:0] mst_w_data,
    output logic [15:0]  mst_w_strb,
    output logic         mst_w_valid,
    input  logic         mst_w_ready,
    input  logic [1:0]   mst_b_resp,
    input  logic         mst_b_valid,
    output logic         mst_b_ready,
    output logic [31:0]  mst_ar_addr,
    output logic         mst_ar_valid,
    input  logic         mst_ar_ready,
    input  logic [127:0] mst_r_data,
    input  logic [1:0]   mst_r_resp,
    input  logic         mst_r_valid,
    output logic         mst_r_ready
);
    axi_lite_dw_typed_core #(
        .SLV_DW(64),
        .MST_DW(128)
    ) i_core (
        .clk(clk),
        .rst_n(rst_n),
        .slv_aw_addr(slv_aw_addr),
        .slv_aw_valid(slv_aw_valid),
        .slv_aw_ready(slv_aw_ready),
        .slv_w_data(slv_w_data),
        .slv_w_strb(slv_w_strb),
        .slv_w_valid(slv_w_valid),
        .slv_w_ready(slv_w_ready),
        .slv_b_resp(slv_b_resp),
        .slv_b_valid(slv_b_valid),
        .slv_b_ready(slv_b_ready),
        .slv_ar_addr(slv_ar_addr),
        .slv_ar_valid(slv_ar_valid),
        .slv_ar_ready(slv_ar_ready),
        .slv_r_data(slv_r_data),
        .slv_r_resp(slv_r_resp),
        .slv_r_valid(slv_r_valid),
        .slv_r_ready(slv_r_ready),
        .mst_aw_addr(mst_aw_addr),
        .mst_aw_valid(mst_aw_valid),
        .mst_aw_ready(mst_aw_ready),
        .mst_w_data(mst_w_data),
        .mst_w_strb(mst_w_strb),
        .mst_w_valid(mst_w_valid),
        .mst_w_ready(mst_w_ready),
        .mst_b_resp(mst_b_resp),
        .mst_b_valid(mst_b_valid),
        .mst_b_ready(mst_b_ready),
        .mst_ar_addr(mst_ar_addr),
        .mst_ar_valid(mst_ar_valid),
        .mst_ar_ready(mst_ar_ready),
        .mst_r_data(mst_r_data),
        .mst_r_resp(mst_r_resp),
        .mst_r_valid(mst_r_valid),
        .mst_r_ready(mst_r_ready)
    );
endmodule

module axi_lite_dw_typed_up128_256_tb (
    input  logic         clk,
    input  logic         rst_n,
    input  logic [31:0]  slv_aw_addr,
    input  logic         slv_aw_valid,
    output logic         slv_aw_ready,
    input  logic [127:0] slv_w_data,
    input  logic [15:0]  slv_w_strb,
    input  logic         slv_w_valid,
    output logic         slv_w_ready,
    output logic [1:0]   slv_b_resp,
    output logic         slv_b_valid,
    input  logic         slv_b_ready,
    input  logic [31:0]  slv_ar_addr,
    input  logic         slv_ar_valid,
    output logic         slv_ar_ready,
    output logic [127:0] slv_r_data,
    output logic [1:0]   slv_r_resp,
    output logic         slv_r_valid,
    input  logic         slv_r_ready,
    output logic [31:0]  mst_aw_addr,
    output logic         mst_aw_valid,
    input  logic         mst_aw_ready,
    output logic [255:0] mst_w_data,
    output logic [31:0]  mst_w_strb,
    output logic         mst_w_valid,
    input  logic         mst_w_ready,
    input  logic [1:0]   mst_b_resp,
    input  logic         mst_b_valid,
    output logic         mst_b_ready,
    output logic [31:0]  mst_ar_addr,
    output logic         mst_ar_valid,
    input  logic         mst_ar_ready,
    input  logic [255:0] mst_r_data,
    input  logic [1:0]   mst_r_resp,
    input  logic         mst_r_valid,
    output logic         mst_r_ready
);
    axi_lite_dw_typed_core #(
        .SLV_DW(128),
        .MST_DW(256)
    ) i_core (
        .clk(clk),
        .rst_n(rst_n),
        .slv_aw_addr(slv_aw_addr),
        .slv_aw_valid(slv_aw_valid),
        .slv_aw_ready(slv_aw_ready),
        .slv_w_data(slv_w_data),
        .slv_w_strb(slv_w_strb),
        .slv_w_valid(slv_w_valid),
        .slv_w_ready(slv_w_ready),
        .slv_b_resp(slv_b_resp),
        .slv_b_valid(slv_b_valid),
        .slv_b_ready(slv_b_ready),
        .slv_ar_addr(slv_ar_addr),
        .slv_ar_valid(slv_ar_valid),
        .slv_ar_ready(slv_ar_ready),
        .slv_r_data(slv_r_data),
        .slv_r_resp(slv_r_resp),
        .slv_r_valid(slv_r_valid),
        .slv_r_ready(slv_r_ready),
        .mst_aw_addr(mst_aw_addr),
        .mst_aw_valid(mst_aw_valid),
        .mst_aw_ready(mst_aw_ready),
        .mst_w_data(mst_w_data),
        .mst_w_strb(mst_w_strb),
        .mst_w_valid(mst_w_valid),
        .mst_w_ready(mst_w_ready),
        .mst_b_resp(mst_b_resp),
        .mst_b_valid(mst_b_valid),
        .mst_b_ready(mst_b_ready),
        .mst_ar_addr(mst_ar_addr),
        .mst_ar_valid(mst_ar_valid),
        .mst_ar_ready(mst_ar_ready),
        .mst_r_data(mst_r_data),
        .mst_r_resp(mst_r_resp),
        .mst_r_valid(mst_r_valid),
        .mst_r_ready(mst_r_ready)
    );
endmodule
