module axi_lite_mailbox_slave_typed_exec_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] slv_aw_addr,
    input  logic [2:0]  slv_aw_prot,
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
    input  logic        slv_ar_valid,
    output logic        slv_ar_ready,
    output logic [31:0] slv_r_data,
    output logic [1:0]  slv_r_resp,
    output logic        slv_r_valid,
    input  logic        slv_r_ready,
    output logic [31:0] mbox_w_data,
    input  logic        mbox_w_full,
    input  logic [1:0]  mbox_w_usage,
    output logic        mbox_w_push,
    output logic        mbox_w_flush,
    input  logic [31:0] mbox_r_data,
    input  logic        mbox_r_empty,
    input  logic [1:0]  mbox_r_usage,
    output logic        mbox_r_pop,
    output logic        mbox_r_flush,
    output logic        irq,
    output logic        clear_irq
);
    typedef logic [31:0] addr_t;
    typedef logic [31:0] data_t;
    typedef logic [3:0] strb_t;
    typedef logic [2:0] prot_t;
    typedef logic [1:0] resp_t;
    typedef logic [1:0] usage_t;

    typedef struct packed {
        addr_t addr;
        prot_t prot;
    } aw_chan_t;

    typedef struct packed {
        data_t data;
        strb_t strb;
    } w_chan_t;

    typedef struct packed {
        resp_t resp;
    } b_chan_t;

    typedef struct packed {
        addr_t addr;
        prot_t prot;
    } ar_chan_t;

    typedef struct packed {
        data_t data;
        resp_t resp;
    } r_chan_t;

    typedef struct packed {
        aw_chan_t aw;
        logic     aw_valid;
        w_chan_t  w;
        logic     w_valid;
        logic     b_ready;
        ar_chan_t ar;
        logic     ar_valid;
        logic     r_ready;
    } req_lite_t;

    typedef struct packed {
        logic    aw_ready;
        logic    w_ready;
        b_chan_t b;
        logic    b_valid;
        logic    ar_ready;
        r_chan_t r;
        logic    r_valid;
    } resp_lite_t;

    req_lite_t slv_req;
    resp_lite_t slv_resp;
    addr_t base_addr;

    assign slv_req = {
        slv_aw_addr,
        slv_aw_prot,
        slv_aw_valid,
        slv_w_data,
        slv_w_strb,
        slv_w_valid,
        slv_b_ready,
        slv_ar_addr,
        slv_ar_prot,
        slv_ar_valid,
        slv_r_ready
    };

    assign slv_aw_ready = slv_resp.aw_ready;
    assign slv_w_ready = slv_resp.w_ready;
    assign slv_b_resp = slv_resp.b.resp;
    assign slv_b_valid = slv_resp.b_valid;
    assign slv_ar_ready = slv_resp.ar_ready;
    assign slv_r_data = slv_resp.r.data;
    assign slv_r_resp = slv_resp.r.resp;
    assign slv_r_valid = slv_resp.r_valid;
    assign base_addr = 32'h00000100;

    axi_lite_mailbox_slave #(
        .MailboxDepth(2),
        .AxiAddrWidth(32),
        .AxiDataWidth(32),
        .req_lite_t(req_lite_t),
        .resp_lite_t(resp_lite_t),
        .addr_t(addr_t),
        .data_t(data_t),
        .usage_t(usage_t)
    ) i_dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .slv_req_i(slv_req),
        .slv_resp_o(slv_resp),
        .base_addr_i(base_addr),
        .mbox_w_data_o(mbox_w_data),
        .mbox_w_full_i(mbox_w_full),
        .mbox_w_push_o(mbox_w_push),
        .mbox_w_flush_o(mbox_w_flush),
        .mbox_w_usage_i(mbox_w_usage),
        .mbox_r_data_i(mbox_r_data),
        .mbox_r_empty_i(mbox_r_empty),
        .mbox_r_pop_o(mbox_r_pop),
        .mbox_r_flush_o(mbox_r_flush),
        .mbox_r_usage_i(mbox_r_usage),
        .irq_o(irq),
        .clear_irq_o(clear_irq)
    );
endmodule


module axi_lite_mailbox_typed_exec_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] slv0_aw_addr,
    input  logic [2:0]  slv0_aw_prot,
    input  logic        slv0_aw_valid,
    output logic        slv0_aw_ready,
    input  logic [31:0] slv0_w_data,
    input  logic [3:0]  slv0_w_strb,
    input  logic        slv0_w_valid,
    output logic        slv0_w_ready,
    output logic [1:0]  slv0_b_resp,
    output logic        slv0_b_valid,
    input  logic        slv0_b_ready,
    input  logic [31:0] slv0_ar_addr,
    input  logic [2:0]  slv0_ar_prot,
    input  logic        slv0_ar_valid,
    output logic        slv0_ar_ready,
    output logic [31:0] slv0_r_data,
    output logic [1:0]  slv0_r_resp,
    output logic        slv0_r_valid,
    input  logic        slv0_r_ready,
    input  logic [31:0] slv1_aw_addr,
    input  logic [2:0]  slv1_aw_prot,
    input  logic        slv1_aw_valid,
    output logic        slv1_aw_ready,
    input  logic [31:0] slv1_w_data,
    input  logic [3:0]  slv1_w_strb,
    input  logic        slv1_w_valid,
    output logic        slv1_w_ready,
    output logic [1:0]  slv1_b_resp,
    output logic        slv1_b_valid,
    input  logic        slv1_b_ready,
    input  logic [31:0] slv1_ar_addr,
    input  logic [2:0]  slv1_ar_prot,
    input  logic        slv1_ar_valid,
    output logic        slv1_ar_ready,
    output logic [31:0] slv1_r_data,
    output logic [1:0]  slv1_r_resp,
    output logic        slv1_r_valid,
    input  logic        slv1_r_ready,
    output logic        irq0,
    output logic        irq1
);
    typedef logic [31:0] addr_t;
    typedef logic [31:0] data_t;
    typedef logic [3:0] strb_t;
    typedef logic [2:0] prot_t;
    typedef logic [1:0] resp_t;

    typedef struct packed {
        addr_t addr;
        prot_t prot;
    } aw_chan_t;

    typedef struct packed {
        data_t data;
        strb_t strb;
    } w_chan_t;

    typedef struct packed {
        resp_t resp;
    } b_chan_t;

    typedef struct packed {
        addr_t addr;
        prot_t prot;
    } ar_chan_t;

    typedef struct packed {
        data_t data;
        resp_t resp;
    } r_chan_t;

    typedef struct packed {
        aw_chan_t aw;
        logic     aw_valid;
        w_chan_t  w;
        logic     w_valid;
        logic     b_ready;
        ar_chan_t ar;
        logic     ar_valid;
        logic     r_ready;
    } req_lite_t;

    typedef struct packed {
        logic    aw_ready;
        logic    w_ready;
        b_chan_t b;
        logic    b_valid;
        logic    ar_ready;
        r_chan_t r;
        logic    r_valid;
    } resp_lite_t;

    req_lite_t [1:0] slv_reqs;
    resp_lite_t [1:0] slv_resps;
    logic [1:0] irqs;
    addr_t [1:0] base_addr;

    assign slv_reqs[0] = {
        slv0_aw_addr,
        slv0_aw_prot,
        slv0_aw_valid,
        slv0_w_data,
        slv0_w_strb,
        slv0_w_valid,
        slv0_b_ready,
        slv0_ar_addr,
        slv0_ar_prot,
        slv0_ar_valid,
        slv0_r_ready
    };
    assign slv_reqs[1] = {
        slv1_aw_addr,
        slv1_aw_prot,
        slv1_aw_valid,
        slv1_w_data,
        slv1_w_strb,
        slv1_w_valid,
        slv1_b_ready,
        slv1_ar_addr,
        slv1_ar_prot,
        slv1_ar_valid,
        slv1_r_ready
    };

    assign slv0_aw_ready = slv_resps[0].aw_ready;
    assign slv0_w_ready = slv_resps[0].w_ready;
    assign slv0_b_resp = slv_resps[0].b.resp;
    assign slv0_b_valid = slv_resps[0].b_valid;
    assign slv0_ar_ready = slv_resps[0].ar_ready;
    assign slv0_r_data = slv_resps[0].r.data;
    assign slv0_r_resp = slv_resps[0].r.resp;
    assign slv0_r_valid = slv_resps[0].r_valid;

    assign slv1_aw_ready = slv_resps[1].aw_ready;
    assign slv1_w_ready = slv_resps[1].w_ready;
    assign slv1_b_resp = slv_resps[1].b.resp;
    assign slv1_b_valid = slv_resps[1].b_valid;
    assign slv1_ar_ready = slv_resps[1].ar_ready;
    assign slv1_r_data = slv_resps[1].r.data;
    assign slv1_r_resp = slv_resps[1].r.resp;
    assign slv1_r_valid = slv_resps[1].r_valid;

    assign irq0 = irqs[0];
    assign irq1 = irqs[1];
    assign base_addr[0] = 32'h00000000;
    assign base_addr[1] = 32'h00000100;

    axi_lite_mailbox #(
        .MailboxDepth(2),
        .AxiAddrWidth(32),
        .AxiDataWidth(32),
        .req_lite_t(req_lite_t),
        .resp_lite_t(resp_lite_t),
        .addr_t(addr_t)
    ) i_dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .test_i(1'b0),
        .slv_reqs_i(slv_reqs),
        .slv_resps_o(slv_resps),
        .irq_o(irqs),
        .base_addr_i(base_addr)
    );
endmodule


module axi_lite_mailbox_exec_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic [31:0] slv0_aw_addr,
    input  logic [2:0]  slv0_aw_prot,
    input  logic        slv0_aw_valid,
    output logic        slv0_aw_ready,
    input  logic [31:0] slv0_w_data,
    input  logic [3:0]  slv0_w_strb,
    input  logic        slv0_w_valid,
    output logic        slv0_w_ready,
    output logic [1:0]  slv0_b_resp,
    output logic        slv0_b_valid,
    input  logic        slv0_b_ready,
    input  logic [31:0] slv0_ar_addr,
    input  logic [2:0]  slv0_ar_prot,
    input  logic        slv0_ar_valid,
    output logic        slv0_ar_ready,
    output logic [31:0] slv0_r_data,
    output logic [1:0]  slv0_r_resp,
    output logic        slv0_r_valid,
    input  logic        slv0_r_ready,
    input  logic [31:0] slv1_aw_addr,
    input  logic [2:0]  slv1_aw_prot,
    input  logic        slv1_aw_valid,
    output logic        slv1_aw_ready,
    input  logic [31:0] slv1_w_data,
    input  logic [3:0]  slv1_w_strb,
    input  logic        slv1_w_valid,
    output logic        slv1_w_ready,
    output logic [1:0]  slv1_b_resp,
    output logic        slv1_b_valid,
    input  logic        slv1_b_ready,
    input  logic [31:0] slv1_ar_addr,
    input  logic [2:0]  slv1_ar_prot,
    input  logic        slv1_ar_valid,
    output logic        slv1_ar_ready,
    output logic [31:0] slv1_r_data,
    output logic [1:0]  slv1_r_resp,
    output logic        slv1_r_valid,
    input  logic        slv1_r_ready,
    output logic        irq0,
    output logic        irq1
);
    axi_lite_mailbox_typed_exec_tb i_core (
        .clk(clk),
        .rst_n(rst_n),
        .slv0_aw_addr(slv0_aw_addr),
        .slv0_aw_prot(slv0_aw_prot),
        .slv0_aw_valid(slv0_aw_valid),
        .slv0_aw_ready(slv0_aw_ready),
        .slv0_w_data(slv0_w_data),
        .slv0_w_strb(slv0_w_strb),
        .slv0_w_valid(slv0_w_valid),
        .slv0_w_ready(slv0_w_ready),
        .slv0_b_resp(slv0_b_resp),
        .slv0_b_valid(slv0_b_valid),
        .slv0_b_ready(slv0_b_ready),
        .slv0_ar_addr(slv0_ar_addr),
        .slv0_ar_prot(slv0_ar_prot),
        .slv0_ar_valid(slv0_ar_valid),
        .slv0_ar_ready(slv0_ar_ready),
        .slv0_r_data(slv0_r_data),
        .slv0_r_resp(slv0_r_resp),
        .slv0_r_valid(slv0_r_valid),
        .slv0_r_ready(slv0_r_ready),
        .slv1_aw_addr(slv1_aw_addr),
        .slv1_aw_prot(slv1_aw_prot),
        .slv1_aw_valid(slv1_aw_valid),
        .slv1_aw_ready(slv1_aw_ready),
        .slv1_w_data(slv1_w_data),
        .slv1_w_strb(slv1_w_strb),
        .slv1_w_valid(slv1_w_valid),
        .slv1_w_ready(slv1_w_ready),
        .slv1_b_resp(slv1_b_resp),
        .slv1_b_valid(slv1_b_valid),
        .slv1_b_ready(slv1_b_ready),
        .slv1_ar_addr(slv1_ar_addr),
        .slv1_ar_prot(slv1_ar_prot),
        .slv1_ar_valid(slv1_ar_valid),
        .slv1_ar_ready(slv1_ar_ready),
        .slv1_r_data(slv1_r_data),
        .slv1_r_resp(slv1_r_resp),
        .slv1_r_valid(slv1_r_valid),
        .slv1_r_ready(slv1_r_ready),
        .irq0(irq0),
        .irq1(irq1)
    );
endmodule
