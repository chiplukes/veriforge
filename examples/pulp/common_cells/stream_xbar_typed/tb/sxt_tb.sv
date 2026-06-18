module sxt0_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        flush,
    input  logic [35:0] data_i,
    input  logic [2:0]  sel_i,
    input  logic [2:0]  valid_i,
    output logic [2:0]  ready_o,
    output logic [23:0] data_o,
    output logic [3:0]  idx_o,
    output logic [1:0]  valid_o,
    input  logic [1:0]  ready_i
);

    typedef logic [0:0] sel_t;
    typedef logic [1:0] idx_t;
    typedef struct packed {
        logic [7:0] payload;
        logic [3:0] meta;
    } payload_t;

    payload_t inp_data [2:0];
    sel_t out_sel [2:0];
    payload_t out_data [1:0];
    idx_t rr [1:0];
    idx_t out_idx [1:0];

    assign inp_data[0] = data_i[11:0];
    assign inp_data[1] = data_i[23:12];
    assign inp_data[2] = data_i[35:24];
    assign out_sel[0] = sel_i[0];
    assign out_sel[1] = sel_i[1];
    assign out_sel[2] = sel_i[2];
    assign rr[0] = '0;
    assign rr[1] = '0;
    assign data_o[11:0] = out_data[0];
    assign data_o[23:12] = out_data[1];
    assign idx_o[1:0] = out_idx[0];
    assign idx_o[3:2] = out_idx[1];

    stream_xbar #(
        .NumInp(3),
        .NumOut(2),
        .payload_t(payload_t),
        .idx_inp_t(idx_t),
        .sel_oup_t(sel_t),
        .OutSpillReg(1'b0),
        .ExtPrio(1'b0),
        .AxiVldRdy(1'b1),
        .LockIn(1'b1)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .rr_i(rr),
        .data_i(inp_data),
        .sel_i(out_sel),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_o(out_data),
        .idx_o(out_idx),
        .valid_o(valid_o),
        .ready_i(ready_i)
    );

endmodule

module sxt1_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        flush,
    input  logic [35:0] data_i,
    input  logic [2:0]  sel_i,
    input  logic [2:0]  valid_i,
    output logic [2:0]  ready_o,
    output logic [23:0] data_o,
    output logic [3:0]  idx_o,
    output logic [1:0]  valid_o,
    input  logic [1:0]  ready_i
);

    typedef logic [0:0] sel_t;
    typedef logic [1:0] idx_t;
    typedef struct packed {
        logic [7:0] payload;
        logic [3:0] meta;
    } payload_t;

    payload_t inp_data [2:0];
    sel_t out_sel [2:0];
    payload_t out_data [1:0];
    idx_t rr [1:0];
    idx_t out_idx [1:0];

    assign inp_data[0] = data_i[11:0];
    assign inp_data[1] = data_i[23:12];
    assign inp_data[2] = data_i[35:24];
    assign out_sel[0] = sel_i[0];
    assign out_sel[1] = sel_i[1];
    assign out_sel[2] = sel_i[2];
    assign rr[0] = '0;
    assign rr[1] = '0;
    assign data_o[11:0] = out_data[0];
    assign data_o[23:12] = out_data[1];
    assign idx_o[1:0] = out_idx[0];
    assign idx_o[3:2] = out_idx[1];

    stream_xbar #(
        .NumInp(3),
        .NumOut(2),
        .payload_t(payload_t),
        .idx_inp_t(idx_t),
        .sel_oup_t(sel_t),
        .OutSpillReg(1'b1),
        .ExtPrio(1'b0),
        .AxiVldRdy(1'b1),
        .LockIn(1'b1)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .rr_i(rr),
        .data_i(inp_data),
        .sel_i(out_sel),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_o(out_data),
        .idx_o(out_idx),
        .valid_o(valid_o),
        .ready_i(ready_i)
    );

endmodule

module rrt0_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        flush,
    input  logic [1:0]  rr_i,
    input  logic [2:0]  req_i,
    output logic [2:0]  gnt_o,
    input  logic [35:0] data_i,
    output logic        req_o,
    input  logic        gnt_i,
    output logic [11:0] data_o,
    output logic [1:0]  idx_o
);

    typedef logic [1:0] idx_t;
    typedef struct packed {
        logic [7:0] payload;
        logic [3:0] meta;
    } payload_t;

    payload_t inp_data [2:0];
    payload_t out_data;

    assign inp_data[0] = data_i[11:0];
    assign inp_data[1] = data_i[23:12];
    assign inp_data[2] = data_i[35:24];
    assign data_o = out_data;

    rr_arb_tree #(
        .NumIn(3),
        .DataType(payload_t),
        .ExtPrio(1'b0),
        .AxiVldRdy(1'b1),
        .LockIn(1'b1),
        .FairArb(1'b1),
        .idx_t(idx_t)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .rr_i(rr_i),
        .req_i(req_i),
        .gnt_o(gnt_o),
        .data_i(inp_data),
        .req_o(req_o),
        .gnt_i(gnt_i),
        .data_o(out_data),
        .idx_o(idx_o)
    );

endmodule

module rrt0_tb_local;

    typedef logic [1:0] idx_t;
    typedef struct packed {
        logic [7:0] payload;
        logic [3:0] meta;
    } payload_t;

    logic clk;
    logic rst_n;
    logic flush;
    idx_t rr_i;
    logic [2:0] req_i;
    logic [2:0] gnt_o;
    payload_t data_i [2:0];
    logic req_o;
    logic gnt_i;
    payload_t data_o;
    idx_t idx_o;
    integer errors;

    rr_arb_tree #(
        .NumIn(3),
        .DataType(payload_t),
        .ExtPrio(1'b0),
        .AxiVldRdy(1'b1),
        .LockIn(1'b1),
        .FairArb(1'b1),
        .idx_t(idx_t)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .rr_i(rr_i),
        .req_i(req_i),
        .gnt_o(gnt_o),
        .data_i(data_i),
        .req_o(req_o),
        .gnt_i(gnt_i),
        .data_o(data_o),
        .idx_o(idx_o)
    );

    always #5 clk = ~clk;

    task automatic expect_state;
        input logic exp_req;
        input idx_t exp_idx;
        input payload_t exp_data;
        input logic [2:0] exp_gnt;
        input [255:0] label;
        begin
            if (req_o !== exp_req) begin
                errors = errors + 1;
                $display("FAIL %0s req_o expected=%0b actual=%0b", label, exp_req, req_o);
            end
            if (idx_o !== exp_idx) begin
                errors = errors + 1;
                $display("FAIL %0s idx expected=%0d actual=%0d", label, exp_idx, idx_o);
            end
            if (data_o !== exp_data) begin
                errors = errors + 1;
                $display("FAIL %0s data expected=%h actual=%h", label, exp_data, data_o);
            end
            if (gnt_o !== exp_gnt) begin
                errors = errors + 1;
                $display("FAIL %0s gnt expected=%b actual=%b", label, exp_gnt, gnt_o);
            end
        end
    endtask

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        flush = 1'b0;
        rr_i = '0;
        req_i = '0;
        gnt_i = 1'b0;
        data_i[0] = 12'h1A0;
        data_i[1] = 12'h2B1;
        data_i[2] = 12'h3C2;
        errors = 0;

        #22;
        rst_n = 1'b1;
        @(posedge clk);
        #1;
        expect_state(1'b0, 2'd0, 12'h000, 3'b000, "idle after reset");

        req_i = 3'b101;
        gnt_i = 1'b1;
        #1;
        expect_state(1'b1, 2'd0, 12'h1A0, 3'b001, "first round robin grant");
        @(posedge clk);
        #1;
        expect_state(1'b1, 2'd2, 12'h3C2, 3'b100, "second round robin grant");
        @(posedge clk);
        #1;
        expect_state(1'b1, 2'd0, 12'h1A0, 3'b001, "wrapped round robin grant");

        req_i = 3'b110;
        gnt_i = 1'b0;
        #1;
        expect_state(1'b1, 2'd1, 12'h2B1, 3'b000, "lock selection while stalled");
        @(posedge clk);
        #1;
        expect_state(1'b1, 2'd1, 12'h2B1, 3'b000, "locked selection remains stable");

        gnt_i = 1'b1;
        #1;
        expect_state(1'b1, 2'd1, 12'h2B1, 3'b010, "locked request granted");
        @(posedge clk);
        #1;
        expect_state(1'b1, 2'd1, 12'h2B1, 3'b010, "priority state updates after locked grant");
        @(posedge clk);
        #1;
        expect_state(1'b1, 2'd2, 12'h3C2, 3'b100, "round robin advances on the next accepted cycle");

        flush = 1'b1;
        @(posedge clk);
        flush = 1'b0;
        req_i = 3'b011;
        #1;
        expect_state(1'b1, 2'd0, 12'h1A0, 3'b001, "flush resets priority state");

        if (errors == 0)
            $display("PASS typed rr_arb_tree deterministic checks");
        else
            $display("FAIL typed rr_arb_tree deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule

module spt0_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        valid_i,
    output logic        ready_o,
    input  logic [11:0] data_i,
    output logic        valid_o,
    input  logic        ready_i,
    output logic [11:0] data_o
);

    typedef struct packed {
        logic [7:0] payload;
        logic [3:0] meta;
    } payload_t;

    payload_t in_data;
    payload_t out_data;

    assign in_data = data_i;
    assign data_o = out_data;

    spill_register #(
        .T(payload_t),
        .Bypass(1'b0)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_i(in_data),
        .valid_o(valid_o),
        .ready_i(ready_i),
        .data_o(out_data)
    );

endmodule

module spt1_tb (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        valid_i,
    output logic        ready_o,
    input  logic [11:0] data_i,
    output logic        valid_o,
    input  logic        ready_i,
    output logic [11:0] data_o
);

    typedef struct packed {
        logic [7:0] payload;
        logic [3:0] meta;
    } payload_t;

    payload_t in_data;
    payload_t out_data;

    assign in_data = data_i;
    assign data_o = out_data;

    spill_register #(
        .T(payload_t),
        .Bypass(1'b1)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .valid_i(valid_i),
        .ready_o(ready_o),
        .data_i(in_data),
        .valid_o(valid_o),
        .ready_i(ready_i),
        .data_o(out_data)
    );

endmodule

module dmt0_tb (
    input  logic        inp_valid_i,
    output logic        inp_ready_o,
    input  logic [1:0]  oup_sel_i,
    output logic [2:0]  oup_valid_o,
    input  logic [2:0]  oup_ready_i
);

    stream_demux #(
        .N_OUP(3),
        .LOG_N_OUP(2)
    ) dut (
        .inp_valid_i(inp_valid_i),
        .inp_ready_o(inp_ready_o),
        .oup_sel_i(oup_sel_i),
        .oup_valid_o(oup_valid_o),
        .oup_ready_i(oup_ready_i)
    );

endmodule
