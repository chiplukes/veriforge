module stream_xbar #(
    parameter OutSpillReg = 1'b0
) (
    input  logic        clk_i,
    input  logic        rst_ni,
    input  logic        flush_i,
    input  logic [23:0] data_i,
    input  logic [2:0]  sel_i,
    input  logic [2:0]  valid_i,
    output logic [2:0]  ready_o,
    output logic [15:0] data_o,
    output logic [3:0]  idx_o,
    output logic [1:0]  valid_o,
    input  logic [1:0]  ready_i
);

    logic [1:0] rr0_q;
    logic [1:0] rr1_q;
    logic [1:0] arb_idx0;
    logic [1:0] arb_idx1;
    logic [7:0] arb_data0;
    logic [7:0] arb_data1;
    logic arb_valid0;
    logic arb_valid1;
    logic arb_ready0;
    logic arb_ready1;
    logic req00;
    logic req10;
    logic req20;
    logic req01;
    logic req11;
    logic req21;
    logic contended0;
    logic contended1;
    logic [9:0] spill_in0;
    logic [9:0] spill_in1;
    logic [9:0] spill_out0;
    logic [9:0] spill_out1;

    assign req00 = valid_i[0] && (sel_i[0] == 1'b0);
    assign req10 = valid_i[1] && (sel_i[1] == 1'b0);
    assign req20 = valid_i[2] && (sel_i[2] == 1'b0);
    assign req01 = valid_i[0] && (sel_i[0] == 1'b1);
    assign req11 = valid_i[1] && (sel_i[1] == 1'b1);
    assign req21 = valid_i[2] && (sel_i[2] == 1'b1);
    assign contended0 = (req00 && req10) || (req00 && req20) || (req10 && req20);
    assign contended1 = (req01 && req11) || (req01 && req21) || (req11 && req21);

    always_comb begin
        arb_valid0 = 1'b0;
        arb_idx0 = 2'd0;
        arb_data0 = 8'h00;
        case (rr0_q)
            2'd0: begin
                if (req00) begin
                    arb_valid0 = 1'b1;
                    arb_idx0 = 2'd0;
                    arb_data0 = data_i[7:0];
                end else if (req10) begin
                    arb_valid0 = 1'b1;
                    arb_idx0 = 2'd1;
                    arb_data0 = data_i[15:8];
                end else if (req20) begin
                    arb_valid0 = 1'b1;
                    arb_idx0 = 2'd2;
                    arb_data0 = data_i[23:16];
                end
            end
            2'd1: begin
                if (req10) begin
                    arb_valid0 = 1'b1;
                    arb_idx0 = 2'd1;
                    arb_data0 = data_i[15:8];
                end else if (req20) begin
                    arb_valid0 = 1'b1;
                    arb_idx0 = 2'd2;
                    arb_data0 = data_i[23:16];
                end else if (req00) begin
                    arb_valid0 = 1'b1;
                    arb_idx0 = 2'd0;
                    arb_data0 = data_i[7:0];
                end
            end
            default: begin
                if (req20) begin
                    arb_valid0 = 1'b1;
                    arb_idx0 = 2'd2;
                    arb_data0 = data_i[23:16];
                end else if (req00) begin
                    arb_valid0 = 1'b1;
                    arb_idx0 = 2'd0;
                    arb_data0 = data_i[7:0];
                end else if (req10) begin
                    arb_valid0 = 1'b1;
                    arb_idx0 = 2'd1;
                    arb_data0 = data_i[15:8];
                end
            end
        endcase

        arb_valid1 = 1'b0;
        arb_idx1 = 2'd0;
        arb_data1 = 8'h00;
        case (rr1_q)
            2'd0: begin
                if (req01) begin
                    arb_valid1 = 1'b1;
                    arb_idx1 = 2'd0;
                    arb_data1 = data_i[7:0];
                end else if (req11) begin
                    arb_valid1 = 1'b1;
                    arb_idx1 = 2'd1;
                    arb_data1 = data_i[15:8];
                end else if (req21) begin
                    arb_valid1 = 1'b1;
                    arb_idx1 = 2'd2;
                    arb_data1 = data_i[23:16];
                end
            end
            2'd1: begin
                if (req11) begin
                    arb_valid1 = 1'b1;
                    arb_idx1 = 2'd1;
                    arb_data1 = data_i[15:8];
                end else if (req21) begin
                    arb_valid1 = 1'b1;
                    arb_idx1 = 2'd2;
                    arb_data1 = data_i[23:16];
                end else if (req01) begin
                    arb_valid1 = 1'b1;
                    arb_idx1 = 2'd0;
                    arb_data1 = data_i[7:0];
                end
            end
            default: begin
                if (req21) begin
                    arb_valid1 = 1'b1;
                    arb_idx1 = 2'd2;
                    arb_data1 = data_i[23:16];
                end else if (req01) begin
                    arb_valid1 = 1'b1;
                    arb_idx1 = 2'd0;
                    arb_data1 = data_i[7:0];
                end else if (req11) begin
                    arb_valid1 = 1'b1;
                    arb_idx1 = 2'd1;
                    arb_data1 = data_i[15:8];
                end
            end
        endcase
    end

    assign spill_in0 = {arb_idx0, arb_data0};
    assign spill_in1 = {arb_idx1, arb_data1};

    spill_register #(
        .DATA_WIDTH(10),
        .Bypass(!OutSpillReg)
    ) i_spill_0 (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .valid_i(arb_valid0),
        .ready_o(arb_ready0),
        .data_i(spill_in0),
        .valid_o(valid_o[0]),
        .ready_i(ready_i[0]),
        .data_o(spill_out0)
    );

    spill_register #(
        .DATA_WIDTH(10),
        .Bypass(!OutSpillReg)
    ) i_spill_1 (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .valid_i(arb_valid1),
        .ready_o(arb_ready1),
        .data_i(spill_in1),
        .valid_o(valid_o[1]),
        .ready_i(ready_i[1]),
        .data_o(spill_out1)
    );

    assign data_o[7:0] = spill_out0[7:0];
    assign idx_o[1:0] = spill_out0[9:8];
    assign data_o[15:8] = spill_out1[7:0];
    assign idx_o[3:2] = spill_out1[9:8];

    always_comb begin
        ready_o = 3'b000;
        if (arb_valid0) begin
            case (arb_idx0)
                2'd0: ready_o[0] = arb_ready0;
                2'd1: ready_o[1] = arb_ready0;
                default: ready_o[2] = arb_ready0;
            endcase
        end
        if (arb_valid1) begin
            case (arb_idx1)
                2'd0: ready_o[0] = arb_ready1;
                2'd1: ready_o[1] = arb_ready1;
                default: ready_o[2] = arb_ready1;
            endcase
        end
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            rr0_q <= 2'd0;
            rr1_q <= 2'd0;
        end else if (flush_i) begin
            rr0_q <= 2'd0;
            rr1_q <= 2'd0;
        end else begin
            if (arb_valid0 && arb_ready0 && contended0) begin
                if (arb_idx0 == 2'd2) begin
                    rr0_q <= 2'd0;
                end else begin
                    rr0_q <= arb_idx0 + 1'b1;
                end
            end
            if (arb_valid1 && arb_ready1 && contended1) begin
                if (arb_idx1 == 2'd2) begin
                    rr1_q <= 2'd0;
                end else begin
                    rr1_q <= arb_idx1 + 1'b1;
                end
            end
        end
    end

endmodule
