module axi_cdc_exec_tb (
    input  logic        src_clk_i,
    input  logic        src_rst_ni,
    input  logic [1:0]  src_aw_id,
    input  logic [31:0] src_aw_addr,
    input  logic [2:0]  src_aw_prot,
    input  logic [7:0]  src_aw_len,
    input  logic        src_aw_valid,
    output logic        src_aw_ready,
    input  logic [31:0] src_w_data,
    input  logic [3:0]  src_w_strb,
    input  logic        src_w_last,
    input  logic        src_w_valid,
    output logic        src_w_ready,
    output logic [1:0]  src_b_id,
    output logic [1:0]  src_b_resp,
    output logic        src_b_valid,
    input  logic        src_b_ready,
    input  logic [1:0]  src_ar_id,
    input  logic [31:0] src_ar_addr,
    input  logic [2:0]  src_ar_prot,
    input  logic [7:0]  src_ar_len,
    input  logic        src_ar_valid,
    output logic        src_ar_ready,
    output logic [1:0]  src_r_id,
    output logic [31:0] src_r_data,
    output logic [1:0]  src_r_resp,
    output logic        src_r_last,
    output logic        src_r_valid,
    input  logic        src_r_ready,
    input  logic        dst_clk_i,
    input  logic        dst_rst_ni,
    output logic [1:0]  dst_aw_id,
    output logic [31:0] dst_aw_addr,
    output logic [2:0]  dst_aw_prot,
    output logic [7:0]  dst_aw_len,
    output logic        dst_aw_valid,
    input  logic        dst_aw_ready,
    output logic [31:0] dst_w_data,
    output logic [3:0]  dst_w_strb,
    output logic        dst_w_last,
    output logic        dst_w_valid,
    input  logic        dst_w_ready,
    input  logic [1:0]  dst_b_id,
    input  logic [1:0]  dst_b_resp,
    input  logic        dst_b_valid,
    output logic        dst_b_ready,
    output logic [1:0]  dst_ar_id,
    output logic [31:0] dst_ar_addr,
    output logic [2:0]  dst_ar_prot,
    output logic [7:0]  dst_ar_len,
    output logic        dst_ar_valid,
    input  logic        dst_ar_ready,
    input  logic [1:0]  dst_r_id,
    input  logic [31:0] dst_r_data,
    input  logic [1:0]  dst_r_resp,
    input  logic        dst_r_last,
    input  logic        dst_r_valid,
    output logic        dst_r_ready
);
    localparam int unsigned LOG_DEPTH = 1;

    typedef logic [31:0] addr_t;
    typedef logic [31:0] data_t;
    typedef logic [3:0] strb_t;
    typedef logic [2:0] prot_t;
    typedef logic [1:0] resp_t;
    typedef logic [7:0] len_t;
    typedef logic [1:0] id_t;

    typedef struct packed {
        id_t   id;
        addr_t addr;
        prot_t prot;
        len_t  len;
    } aw_chan_t;

    typedef struct packed {
        data_t data;
        strb_t strb;
        logic  last;
    } w_chan_t;

    typedef struct packed {
        id_t   id;
        resp_t resp;
    } b_chan_t;

    typedef struct packed {
        id_t   id;
        addr_t addr;
        prot_t prot;
        len_t  len;
    } ar_chan_t;

    typedef struct packed {
        id_t   id;
        data_t data;
        resp_t resp;
        logic  last;
    } r_chan_t;

    typedef struct packed {
        aw_chan_t aw;
        logic     aw_valid;
        w_chan_t  w;
        logic     w_valid;
        logic     b_ready;
        aw_chan_t ar;
        logic     ar_valid;
        logic     r_ready;
    } req_axi_t;

    typedef struct packed {
        logic    aw_ready;
        logic    w_ready;
        b_chan_t b;
        logic    b_valid;
        logic    ar_ready;
        r_chan_t r;
        logic    r_valid;
    } resp_axi_t;

    req_axi_t src_req;
    resp_axi_t src_resp;
    req_axi_t dst_req;
    resp_axi_t dst_resp;

    assign src_req = {
        src_aw_id,
        src_aw_addr,
        src_aw_prot,
        src_aw_len,
        src_aw_valid,
        src_w_data,
        src_w_strb,
        src_w_last,
        src_w_valid,
        src_b_ready,
        src_ar_id,
        src_ar_addr,
        src_ar_prot,
        src_ar_len,
        src_ar_valid,
        src_r_ready
    };

    assign src_aw_ready = src_resp.aw_ready;
    assign src_w_ready = src_resp.w_ready;
    assign src_b_id = src_resp.b.id;
    assign src_b_resp = src_resp.b.resp;
    assign src_b_valid = src_resp.b_valid;
    assign src_ar_ready = src_resp.ar_ready;
    assign src_r_id = src_resp.r.id;
    assign src_r_data = src_resp.r.data;
    assign src_r_resp = src_resp.r.resp;
    assign src_r_last = src_resp.r.last;
    assign src_r_valid = src_resp.r_valid;

    assign dst_aw_id = dst_req.aw.id;
    assign dst_aw_addr = dst_req.aw.addr;
    assign dst_aw_prot = dst_req.aw.prot;
    assign dst_aw_len = dst_req.aw.len;
    assign dst_aw_valid = dst_req.aw_valid;
    assign dst_w_data = dst_req.w.data;
    assign dst_w_strb = dst_req.w.strb;
    assign dst_w_last = dst_req.w.last;
    assign dst_w_valid = dst_req.w_valid;
    assign dst_b_ready = dst_req.b_ready;
    assign dst_ar_id = dst_req.ar.id;
    assign dst_ar_addr = dst_req.ar.addr;
    assign dst_ar_prot = dst_req.ar.prot;
    assign dst_ar_len = dst_req.ar.len;
    assign dst_ar_valid = dst_req.ar_valid;
    assign dst_r_ready = dst_req.r_ready;

    assign dst_resp = {
        dst_aw_ready,
        dst_w_ready,
        dst_b_id,
        dst_b_resp,
        dst_b_valid,
        dst_ar_ready,
        dst_r_id,
        dst_r_data,
        dst_r_resp,
        dst_r_last,
        dst_r_valid
    };

    axi_cdc #(
        .aw_chan_t(aw_chan_t),
        .w_chan_t(w_chan_t),
        .b_chan_t(b_chan_t),
        .ar_chan_t(ar_chan_t),
        .r_chan_t(r_chan_t),
        .axi_req_t(req_axi_t),
        .axi_resp_t(resp_axi_t),
        .LogDepth(LOG_DEPTH)
    ) i_dut (
        .src_clk_i(src_clk_i),
        .src_rst_ni(src_rst_ni),
        .src_req_i(src_req),
        .src_resp_o(src_resp),
        .dst_clk_i(dst_clk_i),
        .dst_rst_ni(dst_rst_ni),
        .dst_req_o(dst_req),
        .dst_resp_i(dst_resp)
    );
endmodule

module axi_cdc_req_fifo_exec_tb (
    input  logic        src_clk_i,
    input  logic        src_rst_ni,
    input  logic        src_push_i,
    output logic        src_ready_o,
    input  logic [1:0]  src_aw_id,
    input  logic [31:0] src_aw_addr,
    input  logic [2:0]  src_aw_prot,
    input  logic [7:0]  src_aw_len,
    input  logic        src_aw_valid,
    input  logic [31:0] src_w_data,
    input  logic [3:0]  src_w_strb,
    input  logic        src_w_last,
    input  logic        src_w_valid,
    input  logic        src_b_ready,
    input  logic [1:0]  src_ar_id,
    input  logic [31:0] src_ar_addr,
    input  logic [2:0]  src_ar_prot,
    input  logic [7:0]  src_ar_len,
    input  logic        src_ar_valid,
    input  logic        src_r_ready,
    input  logic        dst_clk_i,
    input  logic        dst_rst_ni,
    output logic        dst_valid_o,
    input  logic        dst_ready_i,
    output logic [1:0]  dst_aw_id,
    output logic [31:0] dst_aw_addr,
    output logic [2:0]  dst_aw_prot,
    output logic [7:0]  dst_aw_len,
    output logic        dst_aw_valid,
    output logic [31:0] dst_w_data,
    output logic [3:0]  dst_w_strb,
    output logic        dst_w_last,
    output logic        dst_w_valid,
    output logic        dst_b_ready,
    output logic [1:0]  dst_ar_id,
    output logic [31:0] dst_ar_addr,
    output logic [2:0]  dst_ar_prot,
    output logic [7:0]  dst_ar_len,
    output logic        dst_ar_valid,
    output logic        dst_r_ready
);
    localparam int unsigned LOG_DEPTH = 1;

    typedef logic [31:0] addr_t;
    typedef logic [31:0] data_t;
    typedef logic [3:0] strb_t;
    typedef logic [2:0] prot_t;
    typedef logic [7:0] len_t;
    typedef logic [1:0] id_t;

    typedef struct packed {
        id_t   id;
        addr_t addr;
        prot_t prot;
        len_t  len;
    } aw_chan_t;

    typedef struct packed {
        data_t data;
        strb_t strb;
        logic  last;
    } w_chan_t;

    typedef struct packed {
        aw_chan_t aw;
        logic     aw_valid;
        w_chan_t  w;
        logic     w_valid;
        logic     b_ready;
        aw_chan_t ar;
        logic     ar_valid;
        logic     r_ready;
    } req_axi_t;

    logic [$bits(req_axi_t)-1:0] src_data;
    logic [$bits(req_axi_t)-1:0] dst_data;

    assign src_data = {
        src_aw_id,
        src_aw_addr,
        src_aw_prot,
        src_aw_len,
        src_aw_valid,
        src_w_data,
        src_w_strb,
        src_w_last,
        src_w_valid,
        src_b_ready,
        src_ar_id,
        src_ar_addr,
        src_ar_prot,
        src_ar_len,
        src_ar_valid,
        src_r_ready
    };

    assign {
        dst_aw_id,
        dst_aw_addr,
        dst_aw_prot,
        dst_aw_len,
        dst_aw_valid,
        dst_w_data,
        dst_w_strb,
        dst_w_last,
        dst_w_valid,
        dst_b_ready,
        dst_ar_id,
        dst_ar_addr,
        dst_ar_prot,
        dst_ar_len,
        dst_ar_valid,
        dst_r_ready
    } = dst_data;

    cdc_fifo_2phase #(
        .WIDTH($bits(req_axi_t)),
        .LOG_DEPTH(LOG_DEPTH)
    ) i_req_cdc (
        .src_rst_ni(src_rst_ni),
        .src_clk_i(src_clk_i),
        .src_data_i(src_data),
        .src_valid_i(src_push_i),
        .src_ready_o(src_ready_o),
        .dst_rst_ni(dst_rst_ni),
        .dst_clk_i(dst_clk_i),
        .dst_data_o(dst_data),
        .dst_valid_o(dst_valid_o),
        .dst_ready_i(dst_ready_i)
    );
endmodule


module axi_cdc_resp_fifo_exec_tb (
    input  logic        src_clk_i,
    input  logic        src_rst_ni,
    output logic        src_valid_o,
    input  logic        src_ready_i,
    output logic        src_aw_ready,
    output logic        src_w_ready,
    output logic [1:0]  src_b_id,
    output logic [1:0]  src_b_resp,
    output logic        src_b_valid,
    output logic        src_ar_ready,
    output logic [1:0]  src_r_id,
    output logic [31:0] src_r_data,
    output logic [1:0]  src_r_resp,
    output logic        src_r_last,
    output logic        src_r_valid,
    input  logic        dst_clk_i,
    input  logic        dst_rst_ni,
    input  logic        dst_push_i,
    output logic        dst_ready_o,
    input  logic        dst_aw_ready,
    input  logic        dst_w_ready,
    input  logic [1:0]  dst_b_id,
    input  logic [1:0]  dst_b_resp,
    input  logic        dst_b_valid,
    input  logic        dst_ar_ready,
    input  logic [1:0]  dst_r_id,
    input  logic [31:0] dst_r_data,
    input  logic [1:0]  dst_r_resp,
    input  logic        dst_r_last,
    input  logic        dst_r_valid
);
    localparam int unsigned LOG_DEPTH = 1;

    typedef logic [31:0] data_t;
    typedef logic [1:0] resp_t;
    typedef logic [1:0] id_t;

    typedef struct packed {
        id_t  id;
        resp_t resp;
    } b_chan_t;

    typedef struct packed {
        id_t   id;
        data_t data;
        resp_t resp;
        logic  last;
    } r_chan_t;

    typedef struct packed {
        logic    aw_ready;
        logic    w_ready;
        b_chan_t b;
        logic    b_valid;
        logic    ar_ready;
        r_chan_t r;
        logic    r_valid;
    } resp_axi_t;

    logic [$bits(resp_axi_t)-1:0] dst_data;
    logic [$bits(resp_axi_t)-1:0] src_data;

    assign dst_data = {
        dst_aw_ready,
        dst_w_ready,
        dst_b_id,
        dst_b_resp,
        dst_b_valid,
        dst_ar_ready,
        dst_r_id,
        dst_r_data,
        dst_r_resp,
        dst_r_last,
        dst_r_valid
    };

    assign {
        src_aw_ready,
        src_w_ready,
        src_b_id,
        src_b_resp,
        src_b_valid,
        src_ar_ready,
        src_r_id,
        src_r_data,
        src_r_resp,
        src_r_last,
        src_r_valid
    } = src_data;

    cdc_fifo_2phase #(
        .WIDTH($bits(resp_axi_t)),
        .LOG_DEPTH(LOG_DEPTH)
    ) i_resp_cdc (
        .src_rst_ni(dst_rst_ni),
        .src_clk_i(dst_clk_i),
        .src_data_i(dst_data),
        .src_valid_i(dst_push_i),
        .src_ready_o(dst_ready_o),
        .dst_rst_ni(src_rst_ni),
        .dst_clk_i(src_clk_i),
        .dst_data_o(src_data),
        .dst_valid_o(src_valid_o),
        .dst_ready_i(src_ready_i)
    );
endmodule
