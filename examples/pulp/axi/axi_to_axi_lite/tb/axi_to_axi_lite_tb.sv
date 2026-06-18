module axi_to_axi_lite_exec_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [1:0]  slv_aw_id,
    input  logic [31:0] slv_aw_addr,
    input  logic [2:0]  slv_aw_prot,
    input  logic [7:0]  slv_aw_len,
    input  logic [5:0]  slv_aw_atop,
    input  logic        slv_aw_valid,
    output logic        slv_aw_ready,
    input  logic [31:0] slv_w_data,
    input  logic [3:0]  slv_w_strb,
    input  logic        slv_w_last,
    input  logic        slv_w_valid,
    output logic        slv_w_ready,
    output logic [1:0]  slv_b_id,
    output logic [1:0]  slv_b_resp,
    output logic        slv_b_valid,
    input  logic        slv_b_ready,
    input  logic [1:0]  slv_ar_id,
    input  logic [31:0] slv_ar_addr,
    input  logic [2:0]  slv_ar_prot,
    input  logic [7:0]  slv_ar_len,
    input  logic        slv_ar_valid,
    output logic        slv_ar_ready,
    output logic [1:0]  slv_r_id,
    output logic [31:0] slv_r_data,
    output logic [1:0]  slv_r_resp,
    output logic        slv_r_last,
    output logic        slv_r_valid,
    input  logic        slv_r_ready,
    output logic [31:0] mst_aw_addr,
    output logic [2:0]  mst_aw_prot,
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
    output logic [2:0]  mst_ar_prot,
    output logic        mst_ar_valid,
    input  logic        mst_ar_ready,
    input  logic [31:0] mst_r_data,
    input  logic [1:0]  mst_r_resp,
    input  logic        mst_r_valid,
    output logic        mst_r_ready
);
    axi_to_axi_lite_typed_exec_tb i_typed_exec_tb (
        .clk(clk),
        .rst_n(rst_n),
        .slv_aw_id(slv_aw_id),
        .slv_aw_addr(slv_aw_addr),
        .slv_aw_prot(slv_aw_prot),
        .slv_aw_len(slv_aw_len),
        .slv_aw_atop(slv_aw_atop),
        .slv_aw_valid(slv_aw_valid),
        .slv_aw_ready(slv_aw_ready),
        .slv_w_data(slv_w_data),
        .slv_w_strb(slv_w_strb),
        .slv_w_last(slv_w_last),
        .slv_w_valid(slv_w_valid),
        .slv_w_ready(slv_w_ready),
        .slv_b_id(slv_b_id),
        .slv_b_resp(slv_b_resp),
        .slv_b_valid(slv_b_valid),
        .slv_b_ready(slv_b_ready),
        .slv_ar_id(slv_ar_id),
        .slv_ar_addr(slv_ar_addr),
        .slv_ar_prot(slv_ar_prot),
        .slv_ar_len(slv_ar_len),
        .slv_ar_valid(slv_ar_valid),
        .slv_ar_ready(slv_ar_ready),
        .slv_r_id(slv_r_id),
        .slv_r_data(slv_r_data),
        .slv_r_resp(slv_r_resp),
        .slv_r_last(slv_r_last),
        .slv_r_valid(slv_r_valid),
        .slv_r_ready(slv_r_ready),
        .mst_aw_addr(mst_aw_addr),
        .mst_aw_prot(mst_aw_prot),
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
        .mst_ar_prot(mst_ar_prot),
        .mst_ar_valid(mst_ar_valid),
        .mst_ar_ready(mst_ar_ready),
        .mst_r_data(mst_r_data),
        .mst_r_resp(mst_r_resp),
        .mst_r_valid(mst_r_valid),
        .mst_r_ready(mst_r_ready)
    );
endmodule


module axi_to_axi_lite_typed_exec_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [1:0]  slv_aw_id,
    input  logic [31:0] slv_aw_addr,
    input  logic [2:0]  slv_aw_prot,
    input  logic [7:0]  slv_aw_len,
    input  logic [5:0]  slv_aw_atop,
    input  logic        slv_aw_valid,
    output logic        slv_aw_ready,
    input  logic [31:0] slv_w_data,
    input  logic [3:0]  slv_w_strb,
    input  logic        slv_w_last,
    input  logic        slv_w_valid,
    output logic        slv_w_ready,
    output logic [1:0]  slv_b_id,
    output logic [1:0]  slv_b_resp,
    output logic        slv_b_valid,
    input  logic        slv_b_ready,
    input  logic [1:0]  slv_ar_id,
    input  logic [31:0] slv_ar_addr,
    input  logic [2:0]  slv_ar_prot,
    input  logic [7:0]  slv_ar_len,
    input  logic        slv_ar_valid,
    output logic        slv_ar_ready,
    output logic [1:0]  slv_r_id,
    output logic [31:0] slv_r_data,
    output logic [1:0]  slv_r_resp,
    output logic        slv_r_last,
    output logic        slv_r_valid,
    input  logic        slv_r_ready,
    output logic [31:0] mst_aw_addr,
    output logic [2:0]  mst_aw_prot,
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
    output logic [2:0]  mst_ar_prot,
    output logic        mst_ar_valid,
    input  logic        mst_ar_ready,
    input  logic [31:0] mst_r_data,
    input  logic [1:0]  mst_r_resp,
    input  logic        mst_r_valid,
    output logic        mst_r_ready
);
    localparam int unsigned AxiAddrWidth = 32;
    localparam int unsigned AxiDataWidth = 32;
    localparam int unsigned AxiIdWidth = 2;

    typedef logic [AxiAddrWidth-1:0] addr_t;
    typedef logic [AxiDataWidth-1:0] data_t;
    typedef logic [AxiIdWidth-1:0] id_t;
    typedef logic [2:0] prot_t;
    typedef logic [1:0] resp_t;
    typedef logic [7:0] len_t;
    typedef logic [5:0] atop_t;
    typedef logic [AxiDataWidth/8-1:0] strb_t;

    typedef struct packed {
        id_t   id;
        addr_t addr;
        prot_t prot;
        len_t  len;
        atop_t atop;
    } full_aw_chan_t;

    typedef struct packed {
        data_t data;
        strb_t strb;
        logic  last;
    } full_w_chan_t;

    typedef struct packed {
        id_t   id;
        resp_t resp;
    } full_b_chan_t;

    typedef struct packed {
        id_t   id;
        addr_t addr;
        prot_t prot;
        len_t  len;
    } full_ar_chan_t;

    typedef struct packed {
        id_t   id;
        data_t data;
        resp_t resp;
        logic  last;
    } full_r_chan_t;

    typedef struct packed {
        full_aw_chan_t aw;
        logic          aw_valid;
        full_w_chan_t  w;
        logic          w_valid;
        logic          b_ready;
        full_ar_chan_t ar;
        logic          ar_valid;
        logic          r_ready;
    } full_req_t;

    typedef struct packed {
        logic         aw_ready;
        logic         w_ready;
        full_b_chan_t b;
        logic         b_valid;
        logic         ar_ready;
        full_r_chan_t r;
        logic         r_valid;
    } full_resp_t;

    typedef struct packed {
        addr_t addr;
        prot_t prot;
    } lite_aw_chan_t;

    typedef struct packed {
        data_t data;
        strb_t strb;
    } lite_w_chan_t;

    typedef struct packed {
        resp_t resp;
    } lite_b_chan_t;

    typedef struct packed {
        addr_t addr;
        prot_t prot;
    } lite_ar_chan_t;

    typedef struct packed {
        data_t data;
        resp_t resp;
    } lite_r_chan_t;

    typedef struct packed {
        lite_aw_chan_t aw;
        logic          aw_valid;
        lite_w_chan_t  w;
        logic          w_valid;
        logic          b_ready;
        lite_ar_chan_t ar;
        logic          ar_valid;
        logic          r_ready;
    } lite_req_t;

    typedef struct packed {
        logic         aw_ready;
        logic         w_ready;
        lite_b_chan_t b;
        logic         b_valid;
        logic         ar_ready;
        lite_r_chan_t r;
        logic         r_valid;
    } lite_resp_t;

    full_req_t  slv_req;
    full_resp_t slv_resp;
    lite_req_t  mst_req;
    lite_resp_t mst_resp;

    assign slv_req.aw.id = slv_aw_id;
    assign slv_req.aw.addr = slv_aw_addr;
    assign slv_req.aw.prot = slv_aw_prot;
    assign slv_req.aw.len = slv_aw_len;
    assign slv_req.aw.atop = slv_aw_atop;
    assign slv_req.aw_valid = slv_aw_valid;
    assign slv_req.w.data = slv_w_data;
    assign slv_req.w.strb = slv_w_strb;
    assign slv_req.w.last = slv_w_last;
    assign slv_req.w_valid = slv_w_valid;
    assign slv_req.b_ready = slv_b_ready;
    assign slv_req.ar.id = slv_ar_id;
    assign slv_req.ar.addr = slv_ar_addr;
    assign slv_req.ar.prot = slv_ar_prot;
    assign slv_req.ar.len = slv_ar_len;
    assign slv_req.ar_valid = slv_ar_valid;
    assign slv_req.r_ready = slv_r_ready;

    assign slv_aw_ready = slv_resp.aw_ready;
    assign slv_w_ready = slv_resp.w_ready;
    assign slv_b_id = slv_resp.b.id;
    assign slv_b_resp = slv_resp.b.resp;
    assign slv_b_valid = slv_resp.b_valid;
    assign slv_ar_ready = slv_resp.ar_ready;
    assign slv_r_id = slv_resp.r.id;
    assign slv_r_data = slv_resp.r.data;
    assign slv_r_resp = slv_resp.r.resp;
    assign slv_r_last = slv_resp.r.last;
    assign slv_r_valid = slv_resp.r_valid;

    assign mst_aw_addr = mst_req.aw.addr;
    assign mst_aw_prot = mst_req.aw.prot;
    assign mst_aw_valid = mst_req.aw_valid;
    assign mst_w_data = mst_req.w.data;
    assign mst_w_strb = mst_req.w.strb;
    assign mst_w_valid = mst_req.w_valid;
    assign mst_b_ready = mst_req.b_ready;
    assign mst_ar_addr = mst_req.ar.addr;
    assign mst_ar_prot = mst_req.ar.prot;
    assign mst_ar_valid = mst_req.ar_valid;
    assign mst_r_ready = mst_req.r_ready;

    assign mst_resp.aw_ready = mst_aw_ready;
    assign mst_resp.w_ready = mst_w_ready;
    assign mst_resp.b.resp = mst_b_resp;
    assign mst_resp.b_valid = mst_b_valid;
    assign mst_resp.ar_ready = mst_ar_ready;
    assign mst_resp.r.data = mst_r_data;
    assign mst_resp.r.resp = mst_r_resp;
    assign mst_resp.r_valid = mst_r_valid;

    axi_to_axi_lite #(
        .AxiAddrWidth(AxiAddrWidth),
        .AxiDataWidth(AxiDataWidth),
        .AxiIdWidth(AxiIdWidth),
        .AxiUserWidth(0),
        .AxiMaxWriteTxns(1),
        .AxiMaxReadTxns(1),
        .FullBW(1'b0),
        .FallThrough(1'b1),
        .full_req_t(full_req_t),
        .full_resp_t(full_resp_t),
        .lite_req_t(lite_req_t),
        .lite_resp_t(lite_resp_t)
    ) i_dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .test_i(1'b0),
        .slv_req_i(slv_req),
        .slv_resp_o(slv_resp),
        .mst_req_o(mst_req),
        .mst_resp_i(mst_resp)
    );
endmodule
