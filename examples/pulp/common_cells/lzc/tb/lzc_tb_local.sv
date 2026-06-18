module lzc_tb_local;

    logic [7:0] trailing_in;
    logic [2:0] trailing_cnt;
    logic trailing_empty;

    logic [7:0] leading_in;
    logic [2:0] leading_cnt;
    logic leading_empty;

    integer errors;

    lzc #(
        .MODE(1'b0)
    ) trailing_dut (
        .in_i(trailing_in),
        .cnt_o(trailing_cnt),
        .empty_o(trailing_empty)
    );

    lzc #(
        .MODE(1'b1)
    ) leading_dut (
        .in_i(leading_in),
        .cnt_o(leading_cnt),
        .empty_o(leading_empty)
    );

    initial begin
        errors = 0;
        trailing_in = 8'b00000000;
        leading_in = 8'b00000000;

        #1;
        if (trailing_empty != 1'b1 || trailing_cnt != 3'd7) begin
            errors = errors + 1;
            $display("FAIL trailing zero input: empty=%0d cnt=%0d", trailing_empty, trailing_cnt);
        end
        if (leading_empty != 1'b1 || leading_cnt != 3'd7) begin
            errors = errors + 1;
            $display("FAIL leading zero input: empty=%0d cnt=%0d", leading_empty, leading_cnt);
        end

        trailing_in = 8'b00000001;
        #1;
        if (trailing_empty != 1'b0 || trailing_cnt != 3'd0) begin
            errors = errors + 1;
            $display("FAIL trailing bit0: empty=%0d cnt=%0d", trailing_empty, trailing_cnt);
        end

        trailing_in = 8'b00001000;
        #1;
        if (trailing_empty != 1'b0 || trailing_cnt != 3'd3) begin
            errors = errors + 1;
            $display("FAIL trailing bit3: empty=%0d cnt=%0d", trailing_empty, trailing_cnt);
        end

        trailing_in = 8'b10100000;
        #1;
        if (trailing_empty != 1'b0 || trailing_cnt != 3'd5) begin
            errors = errors + 1;
            $display("FAIL trailing bit5: empty=%0d cnt=%0d", trailing_empty, trailing_cnt);
        end

        leading_in = 8'b10000000;
        #1;
        if (leading_empty != 1'b0 || leading_cnt != 3'd0) begin
            errors = errors + 1;
            $display("FAIL leading bit7: empty=%0d cnt=%0d", leading_empty, leading_cnt);
        end

        leading_in = 8'b00010000;
        #1;
        if (leading_empty != 1'b0 || leading_cnt != 3'd3) begin
            errors = errors + 1;
            $display("FAIL leading bit4: empty=%0d cnt=%0d", leading_empty, leading_cnt);
        end

        leading_in = 8'b00000101;
        #1;
        if (leading_empty != 1'b0 || leading_cnt != 3'd5) begin
            errors = errors + 1;
            $display("FAIL leading bit2: empty=%0d cnt=%0d", leading_empty, leading_cnt);
        end

        if (errors == 0)
            $display("PASS lzc deterministic checks");
        else
            $display("FAIL lzc deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
