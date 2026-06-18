module stream_xbar #(
    parameter int unsigned NumInp = 32'd0,
    parameter int unsigned NumOut = 32'd0,
    parameter int unsigned DataWidth = 32'd1,
    parameter type payload_t = logic [DataWidth-1:0],
    parameter bit OutSpillReg = 1'b0,
    parameter int unsigned ExtPrio = 1'b0,
    parameter int unsigned AxiVldRdy = 1'b1,
    parameter int unsigned LockIn = 1'b1,
    parameter int unsigned SelWidth = (NumOut > 32'd1) ? $clog2(NumOut) : 32'd1,
    parameter type sel_oup_t = logic [SelWidth-1:0],
    parameter int unsigned IdxWidth = (NumInp > 32'd1) ? $clog2(NumInp) : 32'd1,
    parameter type idx_inp_t = logic [IdxWidth-1:0]
) (
    input  logic                  clk_i,
    input  logic                  rst_ni,
    input  logic                  flush_i,
    input  idx_inp_t [NumOut-1:0] rr_i,
    input  payload_t [NumInp-1:0] data_i,
    input  sel_oup_t [NumInp-1:0] sel_i,
    input  logic     [NumInp-1:0] valid_i,
    output logic     [NumInp-1:0] ready_o,
    output payload_t [NumOut-1:0] data_o,
    output idx_inp_t [NumOut-1:0] idx_o,
    output logic     [NumOut-1:0] valid_o,
    input  logic     [NumOut-1:0] ready_i
);

    typedef struct packed {
        payload_t data;
        idx_inp_t idx;
    } spill_data_t;

    logic     [NumInp-1:0][NumOut-1:0] inp_valid;
    logic     [NumInp-1:0][NumOut-1:0] inp_ready;

    for (genvar i = 0; i < NumInp; i++) begin : gen_inps
        stream_demux #(
            .N_OUP(NumOut)
        ) i_stream_demux (
            .inp_valid_i(valid_i[i]),
            .inp_ready_o(ready_o[i]),
            .oup_sel_i(sel_i[i]),
            .oup_valid_o(inp_valid[i]),
            .oup_ready_i(inp_ready[i])
        );
    end

    for (genvar j = 0; j < NumOut; j++) begin : gen_outs
        spill_data_t arb;
        spill_data_t spill;
        logic arb_valid;
        logic arb_ready;
        payload_t [NumInp-1:0] arb_data_i;
        logic     [NumInp-1:0] arb_req_i;
        logic     [NumInp-1:0] arb_gnt_o;

        for (genvar i = 0; i < NumInp; i++) begin : gen_cross
            assign arb_data_i[i] = data_i[i];
            assign arb_req_i[i] = inp_valid[i][j];
            assign inp_ready[i][j] = arb_gnt_o[i];
        end

        rr_arb_tree #(
            .NumIn(NumInp),
            .DataType(payload_t),
            .idx_t(idx_inp_t),
            .ExtPrio(ExtPrio),
            .AxiVldRdy(AxiVldRdy),
            .LockIn(LockIn)
        ) i_rr_arb_tree (
            .clk_i(clk_i),
            .rst_ni(rst_ni),
            .flush_i(flush_i),
            .rr_i(rr_i[j]),
            .req_i(arb_req_i),
            .gnt_o(arb_gnt_o),
            .data_i(arb_data_i),
            .req_o(arb_valid),
            .gnt_i(arb_ready),
            .data_o(arb.data),
            .idx_o(arb.idx)
        );

        spill_register #(
            .T(spill_data_t),
            .Bypass(!OutSpillReg)
        ) i_spill_register (
            .clk_i(clk_i),
            .rst_ni(rst_ni),
            .valid_i(arb_valid),
            .ready_o(arb_ready),
            .data_i(arb),
            .valid_o(valid_o[j]),
            .ready_i(ready_i[j]),
            .data_o(spill)
        );

        always_comb begin
            data_o[j] = spill.data;
            idx_o[j] = spill.idx;
        end
    end

endmodule
