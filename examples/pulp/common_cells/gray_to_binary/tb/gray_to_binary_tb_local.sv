module gray_to_binary_tb_local;

    logic [3:0] a;
    logic [3:0] z;
    integer errors;

    gray_to_binary dut (
        .A(a),
        .Z(z)
    );

    initial begin
        errors = 0;
        a = 4'b0000;

        #1;
        if (z !== 4'b0000) begin
            errors = errors + 1;
            $display("FAIL zero: a=%b z=%b expected=0000", a, z);
        end

        a = 4'b0001;
        #1;
        if (z !== 4'b0001) begin
            errors = errors + 1;
            $display("FAIL one: a=%b z=%b expected=0001", a, z);
        end

        a = 4'b0011;
        #1;
        if (z !== 4'b0010) begin
            errors = errors + 1;
            $display("FAIL two: a=%b z=%b expected=0010", a, z);
        end

        a = 4'b0010;
        #1;
        if (z !== 4'b0011) begin
            errors = errors + 1;
            $display("FAIL three: a=%b z=%b expected=0011", a, z);
        end

        a = 4'b0110;
        #1;
        if (z !== 4'b0100) begin
            errors = errors + 1;
            $display("FAIL four: a=%b z=%b expected=0100", a, z);
        end

        a = 4'b0100;
        #1;
        if (z !== 4'b0111) begin
            errors = errors + 1;
            $display("FAIL seven: a=%b z=%b expected=0111", a, z);
        end

        a = 4'b1100;
        #1;
        if (z !== 4'b1000) begin
            errors = errors + 1;
            $display("FAIL eight: a=%b z=%b expected=1000", a, z);
        end

        a = 4'b1000;
        #1;
        if (z !== 4'b1111) begin
            errors = errors + 1;
            $display("FAIL fifteen: a=%b z=%b expected=1111", a, z);
        end

        if (errors == 0)
            $display("PASS gray_to_binary deterministic checks");
        else
            $display("FAIL gray_to_binary deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
