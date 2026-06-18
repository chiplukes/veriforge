module omega_sw2_rr #(
    parameter integer WIDTH = 10,
    parameter integer SpillReg = 1'b0
) (
    input  logic                 clk_i,
    input  logic                 rst_ni,
    input  logic                 flush_i,
    input  logic [2*WIDTH-1:0]   data_i,
    input  logic [1:0]           sel_i,
    input  logic [1:0]           valid_i,
    output logic [1:0]           ready_o,
    output logic [2*WIDTH-1:0]   data_o,
    output logic [1:0]           valid_o,
    input  logic [1:0]           ready_i
);

    logic rr0_q;
    logic rr1_q;
    logic req00;
    logic req10;
    logic req01;
    logic req11;
    logic contended0;
    logic contended1;
    logic arb_valid0;
    logic arb_valid1;
    logic arb_idx0;
    logic arb_idx1;
    logic [WIDTH-1:0] arb_data0;
    logic [WIDTH-1:0] arb_data1;
    logic arb_ready0;
    logic arb_ready1;
    logic [WIDTH-1:0] spill_out0;
    logic [WIDTH-1:0] spill_out1;

    assign req00 = valid_i[0] && (sel_i[0] == 1'b0);
    assign req10 = valid_i[1] && (sel_i[1] == 1'b0);
    assign req01 = valid_i[0] && (sel_i[0] == 1'b1);
    assign req11 = valid_i[1] && (sel_i[1] == 1'b1);
    assign contended0 = req00 && req10;
    assign contended1 = req01 && req11;

    always_comb begin
        arb_valid0 = 1'b0;
        arb_idx0 = 1'b0;
        arb_data0 = '0;
        if (rr0_q == 1'b0) begin
            if (req00) begin
                arb_valid0 = 1'b1;
                arb_idx0 = 1'b0;
                arb_data0 = data_i[WIDTH-1:0];
            end else if (req10) begin
                arb_valid0 = 1'b1;
                arb_idx0 = 1'b1;
                arb_data0 = data_i[2*WIDTH-1:WIDTH];
            end
        end else begin
            if (req10) begin
                arb_valid0 = 1'b1;
                arb_idx0 = 1'b1;
                arb_data0 = data_i[2*WIDTH-1:WIDTH];
            end else if (req00) begin
                arb_valid0 = 1'b1;
                arb_idx0 = 1'b0;
                arb_data0 = data_i[WIDTH-1:0];
            end
        end

        arb_valid1 = 1'b0;
        arb_idx1 = 1'b0;
        arb_data1 = '0;
        if (rr1_q == 1'b0) begin
            if (req01) begin
                arb_valid1 = 1'b1;
                arb_idx1 = 1'b0;
                arb_data1 = data_i[WIDTH-1:0];
            end else if (req11) begin
                arb_valid1 = 1'b1;
                arb_idx1 = 1'b1;
                arb_data1 = data_i[2*WIDTH-1:WIDTH];
            end
        end else begin
            if (req11) begin
                arb_valid1 = 1'b1;
                arb_idx1 = 1'b1;
                arb_data1 = data_i[2*WIDTH-1:WIDTH];
            end else if (req01) begin
                arb_valid1 = 1'b1;
                arb_idx1 = 1'b0;
                arb_data1 = data_i[WIDTH-1:0];
            end
        end
    end

    if (WIDTH == 11) begin : gen_spill_11
        logic [10:0] spill_in0_w;
        logic [10:0] spill_in1_w;
        logic [10:0] spill_out0_w;
        logic [10:0] spill_out1_w;

        assign spill_in0_w = arb_data0;
        assign spill_in1_w = arb_data1;

        spill_register #(
            .DATA_WIDTH(11),
            .Bypass(!SpillReg)
        ) i_spill_0 (
            .clk_i(clk_i),
            .rst_ni(rst_ni),
            .valid_i(arb_valid0),
            .ready_o(arb_ready0),
            .data_i(spill_in0_w),
            .valid_o(valid_o[0]),
            .ready_i(ready_i[0]),
            .data_o(spill_out0_w)
        );

        spill_register #(
            .DATA_WIDTH(11),
            .Bypass(!SpillReg)
        ) i_spill_1 (
            .clk_i(clk_i),
            .rst_ni(rst_ni),
            .valid_i(arb_valid1),
            .ready_o(arb_ready1),
            .data_i(spill_in1_w),
            .valid_o(valid_o[1]),
            .ready_i(ready_i[1]),
            .data_o(spill_out1_w)
        );

        assign spill_out0 = spill_out0_w;
        assign spill_out1 = spill_out1_w;
    end else if (WIDTH == 10) begin : gen_spill_10
        logic [9:0] spill_in0_w;
        logic [9:0] spill_in1_w;
        logic [9:0] spill_out0_w;
        logic [9:0] spill_out1_w;

        assign spill_in0_w = arb_data0;
        assign spill_in1_w = arb_data1;

        spill_register #(
            .DATA_WIDTH(10),
            .Bypass(!SpillReg)
        ) i_spill_0 (
            .clk_i(clk_i),
            .rst_ni(rst_ni),
            .valid_i(arb_valid0),
            .ready_o(arb_ready0),
            .data_i(spill_in0_w),
            .valid_o(valid_o[0]),
            .ready_i(ready_i[0]),
            .data_o(spill_out0_w)
        );

        spill_register #(
            .DATA_WIDTH(10),
            .Bypass(!SpillReg)
        ) i_spill_1 (
            .clk_i(clk_i),
            .rst_ni(rst_ni),
            .valid_i(arb_valid1),
            .ready_o(arb_ready1),
            .data_i(spill_in1_w),
            .valid_o(valid_o[1]),
            .ready_i(ready_i[1]),
            .data_o(spill_out1_w)
        );

        assign spill_out0 = spill_out0_w;
        assign spill_out1 = spill_out1_w;
    end else begin : gen_spill_generic
        spill_register #(
            .DATA_WIDTH(WIDTH),
            .Bypass(!SpillReg)
        ) i_spill_0 (
            .clk_i(clk_i),
            .rst_ni(rst_ni),
            .valid_i(arb_valid0),
            .ready_o(arb_ready0),
            .data_i(arb_data0),
            .valid_o(valid_o[0]),
            .ready_i(ready_i[0]),
            .data_o(spill_out0)
        );

        spill_register #(
            .DATA_WIDTH(WIDTH),
            .Bypass(!SpillReg)
        ) i_spill_1 (
            .clk_i(clk_i),
            .rst_ni(rst_ni),
            .valid_i(arb_valid1),
            .ready_o(arb_ready1),
            .data_i(arb_data1),
            .valid_o(valid_o[1]),
            .ready_i(ready_i[1]),
            .data_o(spill_out1)
        );
    end

    assign data_o[WIDTH-1:0] = spill_out0;
    assign data_o[2*WIDTH-1:WIDTH] = spill_out1;

    always_comb begin
        ready_o = 2'b00;
        if (arb_valid0) begin
            if (arb_idx0 == 1'b0) begin
                ready_o[0] = arb_ready0;
            end else begin
                ready_o[1] = arb_ready0;
            end
        end
        if (arb_valid1) begin
            if (arb_idx1 == 1'b0) begin
                ready_o[0] = arb_ready1;
            end else begin
                ready_o[1] = arb_ready1;
            end
        end
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            rr0_q <= 1'b0;
            rr1_q <= 1'b0;
        end else if (flush_i) begin
            rr0_q <= 1'b0;
            rr1_q <= 1'b0;
        end else begin
            if (arb_valid0 && arb_ready0 && contended0) begin
                rr0_q <= ~arb_idx0;
            end
            if (arb_valid1 && arb_ready1 && contended1) begin
                rr1_q <= ~arb_idx1;
            end
        end
    end

endmodule

module stream_omega_net #(
    parameter integer SpillReg = 1'b0
) (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        flush_i,
    input  logic [31:0] data_i,
    input  logic [7:0]  sel_i,
    input  logic [3:0]  valid_i,
    output logic [3:0]  ready_o,
    output logic [31:0] data_o,
    output logic [7:0]  idx_o,
    output logic [3:0]  valid_o,
    input  logic [3:0]  ready_i
);

    logic [10:0] s0_word0;
    logic [10:0] s0_word1;
    logic [10:0] s0_word2;
    logic [10:0] s0_word3;
    logic [21:0] s0_ab_out;
    logic [21:0] s0_cd_out;
    logic [1:0] s0_ab_valid;
    logic [1:0] s0_cd_valid;
    logic [1:0] s0_ab_ready_to_stage1;
    logic [1:0] s0_cd_ready_to_stage1;
    logic [9:0] s1_ac_word0;
    logic [9:0] s1_ac_word1;
    logic [9:0] s1_bd_word0;
    logic [9:0] s1_bd_word1;
    logic [19:0] s1_ac_out;
    logic [19:0] s1_bd_out;
    logic [1:0] s1_ac_ready_back;
    logic [1:0] s1_bd_ready_back;

    assign s0_word0 = {2'd0, sel_i[0], data_i[7:0]};
    assign s0_word1 = {2'd1, sel_i[2], data_i[15:8]};
    assign s0_word2 = {2'd2, sel_i[4], data_i[23:16]};
    assign s0_word3 = {2'd3, sel_i[6], data_i[31:24]};

    omega_sw2_rr #(
        .WIDTH(11),
        .SpillReg(SpillReg)
    ) i_stage0_ab (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .flush_i(flush_i),
        .data_i({s0_word1, s0_word0}),
        .sel_i({sel_i[3], sel_i[1]}),
        .valid_i({valid_i[1], valid_i[0]}),
        .ready_o(ready_o[1:0]),
        .data_o(s0_ab_out),
        .valid_o(s0_ab_valid),
        .ready_i(s0_ab_ready_to_stage1)
    );

    omega_sw2_rr #(
        .WIDTH(11),
        .SpillReg(SpillReg)
    ) i_stage0_cd (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .flush_i(flush_i),
        .data_i({s0_word3, s0_word2}),
        .sel_i({sel_i[7], sel_i[5]}),
        .valid_i({valid_i[3], valid_i[2]}),
        .ready_o(ready_o[3:2]),
        .data_o(s0_cd_out),
        .valid_o(s0_cd_valid),
        .ready_i(s0_cd_ready_to_stage1)
    );

    assign s1_ac_word0 = {s0_ab_out[10:9], s0_ab_out[7:0]};
    assign s1_ac_word1 = {s0_cd_out[10:9], s0_cd_out[7:0]};
    assign s1_bd_word0 = {s0_ab_out[21:20], s0_ab_out[18:11]};
    assign s1_bd_word1 = {s0_cd_out[21:20], s0_cd_out[18:11]};

    omega_sw2_rr #(
        .WIDTH(10),
        .SpillReg(SpillReg)
    ) i_stage1_ac (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .flush_i(flush_i),
        .data_i({s1_ac_word1, s1_ac_word0}),
        .sel_i({s0_cd_out[8], s0_ab_out[8]}),
        .valid_i({s0_cd_valid[0], s0_ab_valid[0]}),
        .ready_o(s1_ac_ready_back),
        .data_o(s1_ac_out),
        .valid_o(valid_o[1:0]),
        .ready_i(ready_i[1:0])
    );

    omega_sw2_rr #(
        .WIDTH(10),
        .SpillReg(SpillReg)
    ) i_stage1_bd (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .flush_i(flush_i),
        .data_i({s1_bd_word1, s1_bd_word0}),
        .sel_i({s0_cd_out[19], s0_ab_out[19]}),
        .valid_i({s0_cd_valid[1], s0_ab_valid[1]}),
        .ready_o(s1_bd_ready_back),
        .data_o(s1_bd_out),
        .valid_o(valid_o[3:2]),
        .ready_i(ready_i[3:2])
    );

    assign s0_ab_ready_to_stage1[0] = s1_ac_ready_back[0];
    assign s0_cd_ready_to_stage1[0] = s1_ac_ready_back[1];
    assign s0_ab_ready_to_stage1[1] = s1_bd_ready_back[0];
    assign s0_cd_ready_to_stage1[1] = s1_bd_ready_back[1];

    assign data_o[7:0] = s1_ac_out[7:0];
    assign idx_o[1:0] = s1_ac_out[9:8];
    assign data_o[15:8] = s1_ac_out[17:10];
    assign idx_o[3:2] = s1_ac_out[19:18];
    assign data_o[23:16] = s1_bd_out[7:0];
    assign idx_o[5:4] = s1_bd_out[9:8];
    assign data_o[31:24] = s1_bd_out[17:10];
    assign idx_o[7:6] = s1_bd_out[19:18];

endmodule
