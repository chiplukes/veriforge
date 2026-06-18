module rr_arb_tree #(
    parameter int unsigned NumIn = 32'd4,
    parameter int unsigned DataWidth = 32'd8,
    parameter bit ExtPrio = 1'b0,
    parameter bit AxiVldRdy = 1'b1,
    parameter bit LockIn = 1'b0,
    parameter bit FairArb = 1'b1,
    parameter int unsigned IdxWidth = (NumIn > 32'd1) ? $clog2(NumIn) : 32'd1
) (
    input  logic                       clk_i,
    input  logic                       rst_ni,
    input  logic                       flush_i,
    input  logic [IdxWidth-1:0]        rr_i,
    input  logic [NumIn-1:0]           req_i,
    output logic [NumIn-1:0]           gnt_o,
    input  logic [NumIn*DataWidth-1:0] data_i,
    output logic                       req_o,
    input  logic                       gnt_i,
    output logic [DataWidth-1:0]       data_o,
    output logic [IdxWidth-1:0]        idx_o
);

    logic [IdxWidth-1:0] rr_q;
    logic [IdxWidth-1:0] rr_d;
    logic [IdxWidth-1:0] rr_state;
    logic [NumIn-1:0] req_d;
    logic [NumIn-1:0] req_q;
    logic lock_q;
    logic lock_d;
    logic found_sel;
    logic found_next;
    logic [IdxWidth-1:0] sel_idx;
    logic [IdxWidth-1:0] next_idx;
    integer sel_scan_idx;
    integer sel_scan_offset;
    integer next_scan_idx;
    integer next_scan_offset;

    assign rr_state = ExtPrio ? rr_i : rr_q;
    assign req_d = (LockIn && lock_q) ? req_q : req_i;

    always_comb begin
        req_o = 1'b0;
        idx_o = '0;
        data_o = '0;
        gnt_o = '0;
        found_sel = 1'b0;
        sel_idx = rr_state;

        for (sel_scan_offset = 0; sel_scan_offset < NumIn; sel_scan_offset = sel_scan_offset + 1) begin
            sel_scan_idx = rr_state + sel_scan_offset;
            if (sel_scan_idx >= NumIn) begin
                sel_scan_idx = sel_scan_idx - NumIn;
            end
            if (!found_sel && req_d[sel_scan_idx]) begin
                found_sel = 1'b1;
                sel_idx = sel_scan_idx[IdxWidth-1:0];
            end
        end

        if (found_sel) begin
            req_o = 1'b1;
            idx_o = sel_idx;
            if (NumIn == 32'd4 && DataWidth == 32'd8) begin
                case (sel_idx)
                    2'd0: data_o = data_i[7:0];
                    2'd1: data_o = data_i[15:8];
                    2'd2: data_o = data_i[23:16];
                    default: data_o = data_i[31:24];
                endcase
            end
            if (gnt_i) begin
                gnt_o[sel_idx] = AxiVldRdy | req_d[sel_idx];
            end
        end
    end

    always_comb begin
        next_idx = rr_state;
        found_next = 1'b0;

        for (next_scan_offset = 1; next_scan_offset <= NumIn; next_scan_offset = next_scan_offset + 1) begin
            next_scan_idx = rr_state + next_scan_offset;
            if (next_scan_idx >= NumIn) begin
                next_scan_idx = next_scan_idx - NumIn;
            end
            if (!found_next && req_d[next_scan_idx]) begin
                found_next = 1'b1;
                next_idx = next_scan_idx[IdxWidth-1:0];
            end
        end

        rr_d = rr_state;
        if (!ExtPrio && gnt_i && req_o) begin
            if (FairArb) begin
                rr_d = next_idx;
            end else if (rr_state == NumIn - 1) begin
                rr_d = '0;
            end else begin
                rr_d = rr_state + 1'b1;
            end
        end
    end

    always_comb begin
        lock_d = req_o & ~gnt_i;
    end

    always @(posedge clk_i or negedge rst_ni) begin
        if (!rst_ni) begin
            rr_q <= '0;
            lock_q <= 1'b0;
            req_q <= '0;
        end else if (flush_i) begin
            rr_q <= '0;
            lock_q <= 1'b0;
            req_q <= '0;
        end else begin
            if (!ExtPrio) begin
                rr_q <= rr_d;
            end
            if (LockIn) begin
                lock_q <= lock_d;
                req_q <= req_d;
            end
        end
    end

endmodule
