module exp_backoff_tb_local;

    logic clk;
    logic rst_n;
    logic set_i;
    logic clr_i;
    logic is_zero_o;

    integer errors;

    exp_backoff #(
        .Seed(16'hBEEF),
        .MaxExp(4)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .set_i(set_i),
        .clr_i(clr_i),
        .is_zero_o(is_zero_o)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        set_i = 1'b0;
        clr_i = 1'b0;
        errors = 0;

        #1;
        if (is_zero_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL reset zero state: is_zero=%0d", is_zero_o);
        end

        #9;
        rst_n = 1'b1;

        set_i = 1'b1;
        #10;
        if (is_zero_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL first set warmup: is_zero=%0d", is_zero_o);
        end

        #10;
        if (is_zero_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL second set loads one-cycle backoff: is_zero=%0d", is_zero_o);
        end

        set_i = 1'b0;
        #10;
        if (is_zero_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL one-cycle backoff drains to zero: is_zero=%0d", is_zero_o);
        end

        set_i = 1'b1;
        #10;
        if (is_zero_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL third set loads three-cycle backoff: is_zero=%0d", is_zero_o);
        end

        set_i = 1'b0;
        #10;
        if (is_zero_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL three-cycle backoff step one: is_zero=%0d", is_zero_o);
        end

        #10;
        if (is_zero_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL three-cycle backoff step two: is_zero=%0d", is_zero_o);
        end

        #10;
        if (is_zero_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL three-cycle backoff drains to zero: is_zero=%0d", is_zero_o);
        end

        set_i = 1'b1;
        #10;
        if (is_zero_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL fourth set enters active backoff: is_zero=%0d", is_zero_o);
        end

        set_i = 1'b0;
        clr_i = 1'b1;
        #10;
        if (is_zero_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL clear resets active backoff: is_zero=%0d", is_zero_o);
        end

        clr_i = 1'b0;
        set_i = 1'b1;
        #10;
        if (is_zero_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL first set after clear warmup: is_zero=%0d", is_zero_o);
        end

        if (errors == 0)
            $display("PASS exp_backoff deterministic checks");
        else
            $display("FAIL exp_backoff deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
