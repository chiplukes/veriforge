module rr_arb_tree_tb_local;

    localparam int unsigned NumIn = 32'd4;
    localparam int unsigned DataWidth = 32'd8;
    localparam int unsigned IdxWidth = 32'd2;

    logic clk;
    logic rst_n;
    logic flush;
    logic [IdxWidth-1:0] rr;
    logic [NumIn-1:0] req;
    logic [NumIn-1:0] gnt;
    logic [NumIn*DataWidth-1:0] data_bus;
    logic req_oup;
    logic gnt_oup;
    logic [DataWidth-1:0] data_oup;
    logic [IdxWidth-1:0] idx_oup;
    integer errors;

    rr_arb_tree #(
        .NumIn(NumIn),
        .DataWidth(DataWidth),
        .ExtPrio(1'b0),
        .AxiVldRdy(1'b1),
        .LockIn(1'b1),
        .FairArb(1'b1)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .flush_i(flush),
        .rr_i(rr),
        .req_i(req),
        .gnt_o(gnt),
        .data_i(data_bus),
        .req_o(req_oup),
        .gnt_i(gnt_oup),
        .data_o(data_oup),
        .idx_o(idx_oup)
    );

    always #5 clk = ~clk;

    task automatic expect_state;
        input logic exp_req;
        input logic [IdxWidth-1:0] exp_idx;
        input logic [DataWidth-1:0] exp_data;
        input logic [NumIn-1:0] exp_gnt;
        input [255:0] label;
        begin
            if (req_oup !== exp_req) begin
                errors = errors + 1;
                $display("FAIL %0s req_o expected=%0b actual=%0b", label, exp_req, req_oup);
            end
            if (idx_oup !== exp_idx) begin
                errors = errors + 1;
                $display("FAIL %0s idx expected=%0d actual=%0d", label, exp_idx, idx_oup);
            end
            if (data_oup !== exp_data) begin
                errors = errors + 1;
                $display("FAIL %0s data expected=%h actual=%h", label, exp_data, data_oup);
            end
            if (gnt !== exp_gnt) begin
                errors = errors + 1;
                $display("FAIL %0s gnt expected=%b actual=%b", label, exp_gnt, gnt);
            end
        end
    endtask

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        flush = 1'b0;
        rr = '0;
        req = '0;
        gnt_oup = 1'b0;
        data_bus = {8'hD3, 8'hC2, 8'hB1, 8'hA0};
        errors = 0;

        #22;
        rst_n = 1'b1;
        @(posedge clk);
        #1;

        expect_state(1'b0, 2'd0, 8'h00, 4'b0000, "idle after reset");

        req = 4'b0101;
        gnt_oup = 1'b1;
        #1;
        expect_state(1'b1, 2'd0, 8'hA0, 4'b0001, "first round robin grant");
        @(posedge clk);
        #1;
        expect_state(1'b1, 2'd2, 8'hC2, 4'b0100, "second round robin grant");
        @(posedge clk);
        #1;
        expect_state(1'b1, 2'd0, 8'hA0, 4'b0001, "wrapped round robin grant");

        req = 4'b0110;
        gnt_oup = 1'b0;
        #1;
        expect_state(1'b1, 2'd1, 8'hB1, 4'b0000, "lock selection while stalled");
        @(posedge clk);
        #1;
        expect_state(1'b1, 2'd1, 8'hB1, 4'b0000, "locked selection remains stable");

        gnt_oup = 1'b1;
        #1;
        expect_state(1'b1, 2'd1, 8'hB1, 4'b0010, "locked request granted");
        @(posedge clk);
        #1;
        expect_state(1'b1, 2'd1, 8'hB1, 4'b0010, "priority state updates after locked grant");
        @(posedge clk);
        #1;
        expect_state(1'b1, 2'd2, 8'hC2, 4'b0100, "round robin advances on the next accepted cycle");

        flush = 1'b1;
        @(posedge clk);
        flush = 1'b0;
        req = 4'b0011;
        #1;
        expect_state(1'b1, 2'd0, 8'hA0, 4'b0001, "flush resets priority state");

        req = 4'b1000;
        #1;
        expect_state(1'b1, 2'd3, 8'hD3, 4'b1000, "single active requester routes data");
        @(posedge clk);

        req = '0;
        gnt_oup = 1'b0;
        #1;
        expect_state(1'b0, 2'd0, 8'h00, 4'b0000, "returns idle");

        if (errors == 0)
            $display("PASS rr_arb_tree deterministic checks");
        else
            $display("FAIL rr_arb_tree deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
