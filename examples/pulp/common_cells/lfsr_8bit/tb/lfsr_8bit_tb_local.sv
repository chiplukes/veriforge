module lfsr_8bit_tb_local;

    logic       clk;
    logic       rst_n;
    logic       en_i;
    logic [7:0] refill_way_oh;
    logic [2:0] refill_way_bin;

    integer errors;

    lfsr_8bit #(
        .SEED(8'hA5),
        .WIDTH(8)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .en_i(en_i),
        .refill_way_oh(refill_way_oh),
        .refill_way_bin(refill_way_bin)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        en_i = 1'b0;
        errors = 0;

        #10;
        if (refill_way_bin != 3'b101 || refill_way_oh != 8'b00100000) begin
            errors = errors + 1;
            $display("FAIL reset seed outputs: bin=%0d oh=%b", refill_way_bin, refill_way_oh);
        end

        rst_n = 1'b1;
        #10;
        if (refill_way_bin != 3'b101 || refill_way_oh != 8'b00100000) begin
            errors = errors + 1;
            $display("FAIL disabled hold after reset: bin=%0d oh=%b", refill_way_bin, refill_way_oh);
        end

        en_i = 1'b1;
        #10;
        if (refill_way_bin != 3'b011 || refill_way_oh != 8'b00001000) begin
            errors = errors + 1;
            $display("FAIL first enabled step: bin=%0d oh=%b", refill_way_bin, refill_way_oh);
        end

        #10;
        if (refill_way_bin != 3'b111 || refill_way_oh != 8'b10000000) begin
            errors = errors + 1;
            $display("FAIL second enabled step: bin=%0d oh=%b", refill_way_bin, refill_way_oh);
        end

        en_i = 1'b0;
        #10;
        if (refill_way_bin != 3'b111 || refill_way_oh != 8'b10000000) begin
            errors = errors + 1;
            $display("FAIL mid-sequence hold: bin=%0d oh=%b", refill_way_bin, refill_way_oh);
        end

        en_i = 1'b1;
        #10;
        if (refill_way_bin != 3'b110 || refill_way_oh != 8'b01000000) begin
            errors = errors + 1;
            $display("FAIL third enabled step: bin=%0d oh=%b", refill_way_bin, refill_way_oh);
        end

        #10;
        if (refill_way_bin != 3'b100 || refill_way_oh != 8'b00010000) begin
            errors = errors + 1;
            $display("FAIL fourth enabled step: bin=%0d oh=%b", refill_way_bin, refill_way_oh);
        end

        if (errors == 0)
            $display("PASS lfsr_8bit deterministic checks");
        else
            $display("FAIL lfsr_8bit deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
