module read_tb_local;

    logic [7:0] d_i;
    logic [7:0] d_o;
    integer errors;

    read dut (
        .d_i(d_i),
        .d_o(d_o)
    );

    initial begin
        errors = 0;
        d_i = 8'h00;

        #1;
        if (d_o !== 8'h00) begin
            errors = errors + 1;
            $display("FAIL zero: d_i=%h d_o=%h expected=00", d_i, d_o);
        end

        d_i = 8'h55;
        #1;
        if (d_o !== 8'h55) begin
            errors = errors + 1;
            $display("FAIL alt0: d_i=%h d_o=%h expected=55", d_i, d_o);
        end

        d_i = 8'hA0;
        #1;
        if (d_o !== 8'hA0) begin
            errors = errors + 1;
            $display("FAIL upper: d_i=%h d_o=%h expected=A0", d_i, d_o);
        end

        d_i = 8'hFF;
        #1;
        if (d_o !== 8'hFF) begin
            errors = errors + 1;
            $display("FAIL full: d_i=%h d_o=%h expected=FF", d_i, d_o);
        end

        if (errors == 0)
            $display("PASS read deterministic checks");
        else
            $display("FAIL read deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
