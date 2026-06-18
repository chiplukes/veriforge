module heaviside_tb_local;

    logic [2:0] x_i;
    logic [7:0] mask_o;
    integer errors;

    heaviside dut (
        .x_i(x_i),
        .mask_o(mask_o)
    );

    initial begin
        errors = 0;
        x_i = 3'd0;

        #1;
        if (mask_o !== 8'h01) begin
            errors = errors + 1;
            $display("FAIL x0: x=%0d mask=%h expected=01", x_i, mask_o);
        end

        x_i = 3'd1;
        #1;
        if (mask_o !== 8'h03) begin
            errors = errors + 1;
            $display("FAIL x1: x=%0d mask=%h expected=03", x_i, mask_o);
        end

        x_i = 3'd2;
        #1;
        if (mask_o !== 8'h07) begin
            errors = errors + 1;
            $display("FAIL x2: x=%0d mask=%h expected=07", x_i, mask_o);
        end

        x_i = 3'd4;
        #1;
        if (mask_o !== 8'h1F) begin
            errors = errors + 1;
            $display("FAIL x4: x=%0d mask=%h expected=1F", x_i, mask_o);
        end

        x_i = 3'd7;
        #1;
        if (mask_o !== 8'hFF) begin
            errors = errors + 1;
            $display("FAIL x7: x=%0d mask=%h expected=FF", x_i, mask_o);
        end

        if (errors == 0)
            $display("PASS heaviside deterministic checks");
        else
            $display("FAIL heaviside deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
