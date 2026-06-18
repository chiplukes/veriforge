module serial_deglitch_tb_local;

    logic clk;
    logic rst_n;
    logic en_i;
    logic d_i;
    logic q_o;

    integer errors;

    serial_deglitch #(
        .SIZE(3)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .en_i(en_i),
        .d_i(d_i),
        .q_o(q_o)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        en_i = 1'b0;
        d_i = 1'b0;
        errors = 0;

        #1;
        if (q_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL reset low output: q=%0d", q_o);
        end

        #9;
        rst_n = 1'b1;

        d_i = 1'b1;
        #10;
        if (q_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL disabled high hold: q=%0d", q_o);
        end

        en_i = 1'b1;
        #10;
        if (q_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL first high sample latency: q=%0d", q_o);
        end

        #10;
        if (q_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL second high sample latency: q=%0d", q_o);
        end

        #10;
        if (q_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL third high sample asserts output: q=%0d", q_o);
        end

        d_i = 1'b0;
        en_i = 1'b0;
        #10;
        if (q_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL disabled high hold after rise: q=%0d", q_o);
        end

        en_i = 1'b1;
        #10;
        if (q_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL first low sample latency: q=%0d", q_o);
        end

        #10;
        if (q_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL second low sample latency: q=%0d", q_o);
        end

        #10;
        if (q_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL third low sample clears output: q=%0d", q_o);
        end

        d_i = 1'b1;
        #10;
        if (q_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL short glitch first sample: q=%0d", q_o);
        end

        #10;
        if (q_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL short glitch second sample: q=%0d", q_o);
        end

        d_i = 1'b0;
        #10;
        if (q_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL short glitch rejection: q=%0d", q_o);
        end

        if (errors == 0)
            $display("PASS serial_deglitch deterministic checks");
        else
            $display("FAIL serial_deglitch deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
