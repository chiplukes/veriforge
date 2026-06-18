module stream_fork_dynamic #(
    parameter int unsigned N_OUP = 3
) (
    input  logic             clk_i,
    input  logic             rst_ni,
    input  logic             valid_i,
    output logic             ready_o,
    input  logic [N_OUP-1:0] sel_i,
    input  logic             sel_valid_i,
    output logic             sel_ready_o,
    output logic [N_OUP-1:0] valid_o,
    input  logic [N_OUP-1:0] ready_i
);

    logic             int_inp_valid;
    logic             int_inp_ready;
    logic [N_OUP-1:0] int_oup_valid;
    logic [N_OUP-1:0] int_oup_ready;

    for (genvar i = 0; i < N_OUP; i++) begin : gen_oups
        always_comb begin
            valid_o[i] = 1'b0;
            int_oup_ready[i] = 1'b0;
            if (sel_valid_i) begin
                if (sel_i[i]) begin
                    valid_o[i] = int_oup_valid[i];
                    int_oup_ready[i] = ready_i[i];
                end else begin
                    int_oup_ready[i] = 1'b1;
                end
            end
        end
    end

    always_comb begin
        int_inp_valid = 1'b0;
        ready_o = 1'b0;
        sel_ready_o = 1'b0;
        if (sel_valid_i) begin
            int_inp_valid = valid_i;
            ready_o = int_inp_ready;
            sel_ready_o = int_inp_ready;
        end
    end

    stream_fork #(
        .N_OUP(N_OUP)
    ) i_fork (
        .clk_i(clk_i),
        .rst_ni(rst_ni),
        .valid_i(int_inp_valid),
        .ready_o(int_inp_ready),
        .valid_o(int_oup_valid),
        .ready_i(int_oup_ready)
    );

endmodule
