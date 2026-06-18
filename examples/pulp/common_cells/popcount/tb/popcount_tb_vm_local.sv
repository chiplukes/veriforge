module popcount_tb_vm_local;

    logic data_w1;
    logic popcount_w1;

    logic [4:0] data_w5;
    logic [3:0] popcount_w5;

    logic [15:0] data_w16;
    logic [4:0] popcount_w16;

    logic [31:0] data_w32;
    logic [5:0] popcount_w32;

    integer errors;

    popcount #(.INPUT_WIDTH(1)) dut_w1 (
        .data_i(data_w1),
        .popcount_o(popcount_w1)
    );

    popcount #(.INPUT_WIDTH(5)) dut_w5 (
        .data_i(data_w5),
        .popcount_o(popcount_w5)
    );

    popcount #(.INPUT_WIDTH(16)) dut_w16 (
        .data_i(data_w16),
        .popcount_o(popcount_w16)
    );

    popcount #(.INPUT_WIDTH(32)) dut_w32 (
        .data_i(data_w32),
        .popcount_o(popcount_w32)
    );

    initial begin
        errors = 0;

        data_w1 = 0;
        #1;
        if (popcount_w1 != 1'd0) begin
            errors = errors + 1;
            $display("FAIL vm w1 zero: got=%0d exp=0", popcount_w1);
        end

        data_w1 = 1'b1;
        #1;
        if (popcount_w1 != 1'd1) begin
            errors = errors + 1;
            $display("FAIL vm w1 one: got=%0d exp=1", popcount_w1);
        end

        data_w5 = 5'b10110;
        #1;
        if (popcount_w5 != 4'd3) begin
            errors = errors + 1;
            $display("FAIL vm w5 mixed: got=%0d exp=3", popcount_w5);
        end

        data_w5 = 5'b11111;
        #1;
        if (popcount_w5 != 4'd5) begin
            errors = errors + 1;
            $display("FAIL vm w5 ones: got=%0d exp=5", popcount_w5);
        end

        data_w16 = 16'hA55A;
        #1;
        if (popcount_w16 != 5'd8) begin
            errors = errors + 1;
            $display("FAIL vm w16 mixed: got=%0d exp=8", popcount_w16);
        end

        data_w16 = 16'hFFFF;
        #1;
        if (popcount_w16 != 5'd16) begin
            errors = errors + 1;
            $display("FAIL vm w16 ones: got=%0d exp=16", popcount_w16);
        end

        data_w32 = 32'hF0F0_00FF;
        #1;
        if (popcount_w32 != 6'd16) begin
            errors = errors + 1;
            $display("FAIL vm w32 mixed: got=%0d exp=16", popcount_w32);
        end

        data_w32 = 32'hFFFF_FFFF;
        #1;
        if (popcount_w32 != 6'd32) begin
            errors = errors + 1;
            $display("FAIL vm w32 ones: got=%0d exp=32", popcount_w32);
        end

        if (errors == 0)
            $display("PASS popcount vm deterministic checks");
        else
            $display("FAIL popcount vm deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
