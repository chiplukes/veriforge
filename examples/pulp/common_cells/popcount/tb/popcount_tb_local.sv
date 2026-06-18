module popcount_tb_local;

    logic data_w1;
    logic popcount_w1;

    logic [4:0] data_w5;
    logic [3:0] popcount_w5;

    logic [15:0] data_w16;
    logic [4:0] popcount_w16;

    logic [31:0] data_w32;
    logic [5:0] popcount_w32;

    logic [63:0] data_w64;
    logic [6:0] popcount_w64;

    logic [980:0] data_w981;
    logic [10:0] popcount_w981;

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

    popcount #(.INPUT_WIDTH(64)) dut_w64 (
        .data_i(data_w64),
        .popcount_o(popcount_w64)
    );

    popcount #(.INPUT_WIDTH(981)) dut_w981 (
        .data_i(data_w981),
        .popcount_o(popcount_w981)
    );

    initial begin
        $dumpfile("dump.vcd");
        $dumpvars(0, popcount_tb_local);

        errors = 0;

        data_w1 = 0;
        #1;
        if (popcount_w1 != 1'd0) begin
            errors = errors + 1;
            $display("FAIL w1 zero: got=%0d exp=0", popcount_w1);
        end

        data_w1 = 1'b1;
        #1;
        if (popcount_w1 != 1'd1) begin
            errors = errors + 1;
            $display("FAIL w1 one: got=%0d exp=1", popcount_w1);
        end

        data_w5 = 5'b00000;
        #1;
        if (popcount_w5 != 4'd0) begin
            errors = errors + 1;
            $display("FAIL w5 zero: got=%0d exp=0", popcount_w5);
        end

        data_w5 = 5'b10110;
        #1;
        if (popcount_w5 != 4'd3) begin
            errors = errors + 1;
            $display("FAIL w5 mixed: got=%0d exp=3", popcount_w5);
        end

        data_w5 = 5'b11111;
        #1;
        if (popcount_w5 != 4'd5) begin
            errors = errors + 1;
            $display("FAIL w5 ones: got=%0d exp=5", popcount_w5);
        end

        data_w16 = 16'h0000;
        #1;
        if (popcount_w16 != 5'd0) begin
            errors = errors + 1;
            $display("FAIL w16 zero: got=%0d exp=0", popcount_w16);
        end

        data_w16 = 16'hA55A;
        #1;
        if (popcount_w16 != 5'd8) begin
            errors = errors + 1;
            $display("FAIL w16 mixed: got=%0d exp=8", popcount_w16);
        end

        data_w16 = 16'hFFFF;
        #1;
        if (popcount_w16 != 5'd16) begin
            errors = errors + 1;
            $display("FAIL w16 ones: got=%0d exp=16", popcount_w16);
        end

        data_w32 = 32'h0000_0000;
        #1;
        if (popcount_w32 != 6'd0) begin
            errors = errors + 1;
            $display("FAIL w32 zero: got=%0d exp=0", popcount_w32);
        end

        data_w32 = 32'hF0F0_00FF;
        #1;
        if (popcount_w32 != 6'd16) begin
            errors = errors + 1;
            $display("FAIL w32 mixed: got=%0d exp=16", popcount_w32);
        end

        data_w32 = 32'hFFFF_FFFF;
        #1;
        if (popcount_w32 != 6'd32) begin
            errors = errors + 1;
            $display("FAIL w32 ones: got=%0d exp=32", popcount_w32);
        end

        data_w64 = 64'h0000_0000_0000_0000;
        #1;
        if (popcount_w64 != 7'd0) begin
            errors = errors + 1;
            $display("FAIL w64 zero: got=%0d exp=0", popcount_w64);
        end

        data_w64 = 64'hFFFF_0000_F0F0_0F0F;
        #1;
        if (popcount_w64 != 7'd32) begin
            errors = errors + 1;
            $display("FAIL w64 mixed: got=%0d exp=32", popcount_w64);
        end

        data_w64 = 64'hFFFF_FFFF_FFFF_FFFF;
        #1;
        if (popcount_w64 != 7'd64) begin
            errors = errors + 1;
            $display("FAIL w64 ones: got=%0d exp=64", popcount_w64);
        end

        data_w981 = 0;
        #1;
        if (popcount_w981 != 11'd0) begin
            errors = errors + 1;
            $display("FAIL w981 zero: got=%0d exp=0", popcount_w981);
        end

        data_w981 = 0;
        data_w981[0] = 1'b1;
        #1;
        if (popcount_w981 != 11'd1) begin
            errors = errors + 1;
            $display("FAIL w981 one: got=%0d exp=1", popcount_w981);
        end

        data_w981 = 0;
        data_w981[980] = 1'b1;
        data_w981[0] = 1'b1;
        #1;
        if (popcount_w981 != 11'd2) begin
            errors = errors + 1;
            $display("FAIL w981 edge bits: got=%0d exp=2", popcount_w981);
        end

        data_w981 = {981{1'b1}};
        #1;
        if (popcount_w981 != 11'd981) begin
            errors = errors + 1;
            $display("FAIL w981 ones: got=%0d exp=981", popcount_w981);
        end

        if (errors == 0)
            $display("PASS popcount deterministic checks");
        else
            $display("FAIL popcount deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
