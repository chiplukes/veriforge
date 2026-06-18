module onehot_to_bin_tb_local;

    logic [7:0] onehot;
    logic [2:0] bin;
    integer errors;

    onehot_to_bin dut (
        .onehot(onehot),
        .bin(bin)
    );

    initial begin
        errors = 0;
        onehot = 8'b00000000;

        #1;
        if (bin !== 3'b000) begin
            errors = errors + 1;
            $display("FAIL zero: onehot=%b bin=%b expected=000", onehot, bin);
        end

        onehot = 8'b00000001;
        #1;
        if (bin !== 3'b000) begin
            errors = errors + 1;
            $display("FAIL bit0: onehot=%b bin=%b expected=000", onehot, bin);
        end

        onehot = 8'b00000010;
        #1;
        if (bin !== 3'b001) begin
            errors = errors + 1;
            $display("FAIL bit1: onehot=%b bin=%b expected=001", onehot, bin);
        end

        onehot = 8'b00001000;
        #1;
        if (bin !== 3'b011) begin
            errors = errors + 1;
            $display("FAIL bit3: onehot=%b bin=%b expected=011", onehot, bin);
        end

        onehot = 8'b00100000;
        #1;
        if (bin !== 3'b101) begin
            errors = errors + 1;
            $display("FAIL bit5: onehot=%b bin=%b expected=101", onehot, bin);
        end

        onehot = 8'b10000000;
        #1;
        if (bin !== 3'b111) begin
            errors = errors + 1;
            $display("FAIL bit7: onehot=%b bin=%b expected=111", onehot, bin);
        end

        if (errors == 0)
            $display("PASS onehot_to_bin deterministic checks");
        else
            $display("FAIL onehot_to_bin deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
