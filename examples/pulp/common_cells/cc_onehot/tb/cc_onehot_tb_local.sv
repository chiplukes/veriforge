module cc_onehot_tb_local;

    logic [3:0] d_i;
    logic is_onehot_o;
    integer errors;

    cc_onehot dut (
        .d_i(d_i),
        .is_onehot_o(is_onehot_o)
    );

    initial begin
        errors = 0;
        d_i = 4'b0000;

        #1;
        if (is_onehot_o !== 1'b0) begin
            errors = errors + 1;
            $display("FAIL zero: d_i=%b is_onehot=%0d expected=0", d_i, is_onehot_o);
        end

        d_i = 4'b0001;
        #1;
        if (is_onehot_o !== 1'b1) begin
            errors = errors + 1;
            $display("FAIL bit0: d_i=%b is_onehot=%0d expected=1", d_i, is_onehot_o);
        end

        d_i = 4'b0010;
        #1;
        if (is_onehot_o !== 1'b1) begin
            errors = errors + 1;
            $display("FAIL bit1: d_i=%b is_onehot=%0d expected=1", d_i, is_onehot_o);
        end

        d_i = 4'b0100;
        #1;
        if (is_onehot_o !== 1'b1) begin
            errors = errors + 1;
            $display("FAIL bit2: d_i=%b is_onehot=%0d expected=1", d_i, is_onehot_o);
        end

        d_i = 4'b1000;
        #1;
        if (is_onehot_o !== 1'b1) begin
            errors = errors + 1;
            $display("FAIL bit3: d_i=%b is_onehot=%0d expected=1", d_i, is_onehot_o);
        end

        d_i = 4'b0011;
        #1;
        if (is_onehot_o !== 1'b0) begin
            errors = errors + 1;
            $display("FAIL two bits: d_i=%b is_onehot=%0d expected=0", d_i, is_onehot_o);
        end

        d_i = 4'b1111;
        #1;
        if (is_onehot_o !== 1'b0) begin
            errors = errors + 1;
            $display("FAIL all bits: d_i=%b is_onehot=%0d expected=0", d_i, is_onehot_o);
        end

        if (errors == 0)
            $display("PASS cc_onehot deterministic checks");
        else
            $display("FAIL cc_onehot deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
