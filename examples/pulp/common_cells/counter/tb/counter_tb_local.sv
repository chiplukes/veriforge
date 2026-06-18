module counter_tb_local;

    logic       clk;
    logic       rst_n;
    logic       clear_i;
    logic       en_i;
    logic       load_i;
    logic       down_i;
    logic [3:0] d_i;
    logic [3:0] q_transient;
    logic [3:0] q_sticky;
    logic       overflow_transient;
    logic       overflow_sticky;

    integer errors;

    counter #(
        .WIDTH(4),
        .STICKY_OVERFLOW(1'b0)
    ) dut_transient (
        .clk_i(clk),
        .rst_ni(rst_n),
        .clear_i(clear_i),
        .en_i(en_i),
        .load_i(load_i),
        .down_i(down_i),
        .d_i(d_i),
        .q_o(q_transient),
        .overflow_o(overflow_transient)
    );

    counter #(
        .WIDTH(4),
        .STICKY_OVERFLOW(1'b1)
    ) dut_sticky (
        .clk_i(clk),
        .rst_ni(rst_n),
        .clear_i(clear_i),
        .en_i(en_i),
        .load_i(load_i),
        .down_i(down_i),
        .d_i(d_i),
        .q_o(q_sticky),
        .overflow_o(overflow_sticky)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        clear_i = 1'b0;
        en_i = 1'b0;
        load_i = 1'b0;
        down_i = 1'b0;
        d_i = 4'd0;
        errors = 0;

        #1;
        if (q_transient != 4'd0 || q_sticky != 4'd0 || overflow_transient != 1'b0 || overflow_sticky != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL reset state: transient_q=%0d sticky_q=%0d transient_ovf=%0d sticky_ovf=%0d",
                q_transient,
                q_sticky,
                overflow_transient,
                overflow_sticky
            );
        end

        #9;
        rst_n = 1'b1;

        load_i = 1'b1;
        d_i = 4'd3;
        #10;
        load_i = 1'b0;
        #1;
        if (q_transient != 4'd3 || q_sticky != 4'd3 || overflow_transient != 1'b0 || overflow_sticky != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL load 3: transient_q=%0d sticky_q=%0d transient_ovf=%0d sticky_ovf=%0d",
                q_transient,
                q_sticky,
                overflow_transient,
                overflow_sticky
            );
        end

        en_i = 1'b1;
        down_i = 1'b0;
        #20;
        en_i = 1'b0;
        #1;
        if (q_transient != 4'd5 || q_sticky != 4'd5 || overflow_transient != 1'b0 || overflow_sticky != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL increment twice: transient_q=%0d sticky_q=%0d transient_ovf=%0d sticky_ovf=%0d",
                q_transient,
                q_sticky,
                overflow_transient,
                overflow_sticky
            );
        end

        load_i = 1'b1;
        d_i = 4'd15;
        #10;
        load_i = 1'b0;
        #1;
        if (q_transient != 4'd15 || q_sticky != 4'd15 || overflow_transient != 1'b0 || overflow_sticky != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL load 15: transient_q=%0d sticky_q=%0d transient_ovf=%0d sticky_ovf=%0d",
                q_transient,
                q_sticky,
                overflow_transient,
                overflow_sticky
            );
        end

        en_i = 1'b1;
        down_i = 1'b0;
        #10;
        en_i = 1'b0;
        #1;
        if (q_transient != 4'd0 || q_sticky != 4'd0 || overflow_transient != 1'b1 || overflow_sticky != 1'b1) begin
            errors = errors + 1;
            $display(
                "FAIL overflow increment: transient_q=%0d sticky_q=%0d transient_ovf=%0d sticky_ovf=%0d",
                q_transient,
                q_sticky,
                overflow_transient,
                overflow_sticky
            );
        end

        en_i = 1'b1;
        down_i = 1'b1;
        #10;
        en_i = 1'b0;
        #1;
        if (q_transient != 4'd15 || q_sticky != 4'd15 || overflow_transient != 1'b0 || overflow_sticky != 1'b1) begin
            errors = errors + 1;
            $display(
                "FAIL decrement after overflow: transient_q=%0d sticky_q=%0d transient_ovf=%0d sticky_ovf=%0d",
                q_transient,
                q_sticky,
                overflow_transient,
                overflow_sticky
            );
        end

        clear_i = 1'b1;
        #10;
        clear_i = 1'b0;
        #1;
        if (q_transient != 4'd0 || q_sticky != 4'd0 || overflow_transient != 1'b0 || overflow_sticky != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL synchronous clear: transient_q=%0d sticky_q=%0d transient_ovf=%0d sticky_ovf=%0d",
                q_transient,
                q_sticky,
                overflow_transient,
                overflow_sticky
            );
        end

        load_i = 1'b1;
        d_i = 4'd0;
        #10;
        load_i = 1'b0;
        en_i = 1'b1;
        down_i = 1'b1;
        #10;
        en_i = 1'b0;
        #1;
        if (q_transient != 4'd15 || q_sticky != 4'd15 || overflow_transient != 1'b1 || overflow_sticky != 1'b1) begin
            errors = errors + 1;
            $display(
                "FAIL underflow decrement: transient_q=%0d sticky_q=%0d transient_ovf=%0d sticky_ovf=%0d",
                q_transient,
                q_sticky,
                overflow_transient,
                overflow_sticky
            );
        end

        if (errors == 0)
            $display("PASS counter deterministic checks");
        else
            $display("FAIL counter deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
