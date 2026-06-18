module axi_lite_to_axi_exec_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] slv_aw_addr,
    input  logic [2:0]  slv_aw_prot,
    input  logic [3:0]  slv_aw_cache,
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
    input  logic [2:0]  slv_ar_prot,
    input  logic [3:0]  slv_ar_cache,
    input  logic        slv_ar_valid,
    output logic        slv_ar_ready,
    output logic [31:0] slv_r_data,
    output logic [1:0]  slv_r_resp,
    output logic        slv_r_valid,
    input  logic        slv_r_ready,
    output logic [31:0] mst_aw_addr,
    output logic [2:0]  mst_aw_prot,
    output logic [2:0]  mst_aw_size,
    output logic [1:0]  mst_aw_burst,
    output logic [3:0]  mst_aw_cache,
    output logic        mst_aw_valid,
    input  logic        mst_aw_ready,
    output logic [31:0] mst_w_data,
    output logic [3:0]  mst_w_strb,
    output logic        mst_w_last,
    output logic        mst_w_valid,
    input  logic        mst_w_ready,
    input  logic [1:0]  mst_b_resp,
    input  logic        mst_b_valid,
    output logic        mst_b_ready,
    output logic [31:0] mst_ar_addr,
    output logic [2:0]  mst_ar_prot,
    output logic [2:0]  mst_ar_size,
    output logic [1:0]  mst_ar_burst,
    output logic [3:0]  mst_ar_cache,
    output logic        mst_ar_valid,
    input  logic        mst_ar_ready,
    input  logic [31:0] mst_r_data,
    input  logic [1:0]  mst_r_resp,
    input  logic        mst_r_valid,
    output logic        mst_r_ready
);
    axi_lite_to_axi_typed_exec_tb i_typed_exec_tb (
        .clk(clk),
        .rst_n(rst_n),
        .slv_aw_addr(slv_aw_addr),
        .slv_aw_prot(slv_aw_prot),
        .slv_aw_cache(slv_aw_cache),
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
        .slv_ar_prot(slv_ar_prot),
        .slv_ar_cache(slv_ar_cache),
        .slv_ar_valid(slv_ar_valid),
        .slv_ar_ready(slv_ar_ready),
        .slv_r_data(slv_r_data),
        .slv_r_resp(slv_r_resp),
        .slv_r_valid(slv_r_valid),
        .slv_r_ready(slv_r_ready),
        .mst_aw_addr(mst_aw_addr),
        .mst_aw_prot(mst_aw_prot),
        .mst_aw_size(mst_aw_size),
        .mst_aw_burst(mst_aw_burst),
        .mst_aw_cache(mst_aw_cache),
        .mst_aw_valid(mst_aw_valid),
        .mst_aw_ready(mst_aw_ready),
        .mst_w_data(mst_w_data),
        .mst_w_strb(mst_w_strb),
        .mst_w_last(mst_w_last),
        .mst_w_valid(mst_w_valid),
        .mst_w_ready(mst_w_ready),
        .mst_b_resp(mst_b_resp),
        .mst_b_valid(mst_b_valid),
        .mst_b_ready(mst_b_ready),
        .mst_ar_addr(mst_ar_addr),
        .mst_ar_prot(mst_ar_prot),
        .mst_ar_size(mst_ar_size),
        .mst_ar_burst(mst_ar_burst),
        .mst_ar_cache(mst_ar_cache),
        .mst_ar_valid(mst_ar_valid),
        .mst_ar_ready(mst_ar_ready),
        .mst_r_data(mst_r_data),
        .mst_r_resp(mst_r_resp),
        .mst_r_valid(mst_r_valid),
        .mst_r_ready(mst_r_ready)
    );
endmodule


module axi_lite_to_axi_typed_exec_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] slv_aw_addr,
    input  logic [2:0]  slv_aw_prot,
    input  logic [3:0]  slv_aw_cache,
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
    input  logic [2:0]  slv_ar_prot,
    input  logic [3:0]  slv_ar_cache,
    input  logic        slv_ar_valid,
    output logic        slv_ar_ready,
    output logic [31:0] slv_r_data,
    output logic [1:0]  slv_r_resp,
    output logic        slv_r_valid,
    input  logic        slv_r_ready,
    output logic [31:0] mst_aw_addr,
    output logic [2:0]  mst_aw_prot,
    output logic [2:0]  mst_aw_size,
    output logic [1:0]  mst_aw_burst,
    output logic [3:0]  mst_aw_cache,
    output logic        mst_aw_valid,
    input  logic        mst_aw_ready,
    output logic [31:0] mst_w_data,
    output logic [3:0]  mst_w_strb,
    output logic        mst_w_last,
    output logic        mst_w_valid,
    input  logic        mst_w_ready,
    input  logic [1:0]  mst_b_resp,
    input  logic        mst_b_valid,
    output logic        mst_b_ready,
    output logic [31:0] mst_ar_addr,
    output logic [2:0]  mst_ar_prot,
    output logic [2:0]  mst_ar_size,
    output logic [1:0]  mst_ar_burst,
    output logic [3:0]  mst_ar_cache,
    output logic        mst_ar_valid,
    input  logic        mst_ar_ready,
    input  logic [31:0] mst_r_data,
    input  logic [1:0]  mst_r_resp,
    input  logic        mst_r_valid,
    output logic        mst_r_ready
);
    typedef struct packed {
        logic [31:0] addr;
        logic [2:0]  prot;
    } lite_aw_chan_t;

    typedef struct packed {
        logic [31:0] data;
        logic [3:0]  strb;
    } lite_w_chan_t;

    typedef struct packed {
        logic [1:0] resp;
    } lite_b_chan_t;

    typedef struct packed {
        logic [31:0] addr;
        logic [2:0]  prot;
    } lite_ar_chan_t;

    typedef struct packed {
        logic [31:0] data;
        logic [1:0]  resp;
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
    } req_lite_t;

    typedef struct packed {
        logic         aw_ready;
        logic         w_ready;
        lite_b_chan_t b;
        logic         b_valid;
        logic         ar_ready;
        lite_r_chan_t r;
        logic         r_valid;
    } resp_lite_t;

    typedef struct packed {
        logic [31:0] addr;
        logic [2:0]  prot;
        logic [2:0]  size;
        logic [1:0]  burst;
        logic [3:0]  cache;
    } axi_aw_chan_t;

    typedef struct packed {
        logic [31:0] data;
        logic [3:0]  strb;
        logic  last;
    } axi_w_chan_t;

    typedef struct packed {
        logic [1:0] resp;
    } axi_b_chan_t;

    typedef struct packed {
        logic [31:0] addr;
        logic [2:0]  prot;
        logic [2:0]  size;
        logic [1:0]  burst;
        logic [3:0]  cache;
    } axi_ar_chan_t;

    typedef struct packed {
        logic [31:0] data;
        logic [1:0]  resp;
    } axi_r_chan_t;

    typedef struct packed {
        axi_aw_chan_t aw;
        logic         aw_valid;
        axi_w_chan_t  w;
        logic         w_valid;
        logic         b_ready;
        axi_ar_chan_t ar;
        logic         ar_valid;
        logic         r_ready;
    } axi_req_t;

    typedef struct packed {
        logic       aw_ready;
        logic       w_ready;
        axi_b_chan_t b;
        logic       b_valid;
        logic       ar_ready;
        axi_r_chan_t r;
        logic       r_valid;
    } axi_resp_t;

    logic _unused_clk;
    logic _unused_rst_n;
    req_lite_t slv_req;
    resp_lite_t slv_resp;
    axi_req_t mst_req;
    axi_resp_t mst_resp;

    assign _unused_clk = clk;
    assign _unused_rst_n = rst_n;

    assign slv_req.aw.addr = slv_aw_addr;
    assign slv_req.aw.prot = slv_aw_prot;
    assign slv_req.aw_valid = slv_aw_valid;
    assign slv_req.w.data = slv_w_data;
    assign slv_req.w.strb = slv_w_strb;
    assign slv_req.w_valid = slv_w_valid;
    assign slv_req.b_ready = slv_b_ready;
    assign slv_req.ar.addr = slv_ar_addr;
    assign slv_req.ar.prot = slv_ar_prot;
    assign slv_req.ar_valid = slv_ar_valid;
    assign slv_req.r_ready = slv_r_ready;

    assign slv_aw_ready = slv_resp.aw_ready;
    assign slv_w_ready = slv_resp.w_ready;
    assign slv_b_resp = slv_resp.b.resp;
    assign slv_b_valid = slv_resp.b_valid;
    assign slv_ar_ready = slv_resp.ar_ready;
    assign slv_r_data = slv_resp.r.data;
    assign slv_r_resp = slv_resp.r.resp;
    assign slv_r_valid = slv_resp.r_valid;

    assign mst_aw_addr = mst_req.aw.addr;
    assign mst_aw_prot = mst_req.aw.prot;
    assign mst_aw_size = mst_req.aw.size;
    assign mst_aw_burst = mst_req.aw.burst;
    assign mst_aw_cache = mst_req.aw.cache;
    assign mst_aw_valid = mst_req.aw_valid;
    assign mst_w_data = mst_req.w.data;
    assign mst_w_strb = mst_req.w.strb;
    assign mst_w_last = mst_req.w.last;
    assign mst_w_valid = mst_req.w_valid;
    assign mst_b_ready = mst_req.b_ready;
    assign mst_ar_addr = mst_req.ar.addr;
    assign mst_ar_prot = mst_req.ar.prot;
    assign mst_ar_size = mst_req.ar.size;
    assign mst_ar_burst = mst_req.ar.burst;
    assign mst_ar_cache = mst_req.ar.cache;
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

    axi_lite_to_axi #(
        .AxiDataWidth(32),
        .req_lite_t(req_lite_t),
        .resp_lite_t(resp_lite_t),
        .axi_req_t(axi_req_t),
        .axi_resp_t(axi_resp_t)
    ) i_dut (
        .slv_req_lite_i(slv_req),
        .slv_resp_lite_o(slv_resp),
        .slv_aw_cache_i(slv_aw_cache),
        .slv_ar_cache_i(slv_ar_cache),
        .mst_req_o(mst_req),
        .mst_resp_i(mst_resp)
    );
endmodule
