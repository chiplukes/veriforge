module max_counter_tb_local;

    logic       clk;
    logic       rst_n;
    logic       clear_i;
    logic       clear_max_i;
    logic       en_i;
    logic       load_i;
    logic       down_i;
    logic [3:0] delta_i;
    logic [3:0] d_i;
    logic [3:0] q_o;
    logic [3:0] max_o;
    logic       overflow_o;
    logic       overflow_max_o;

    integer errors;

    max_counter #(
        .WIDTH(4)
    ) dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .clear_i(clear_i),
        .clear_max_i(clear_max_i),
        .en_i(en_i),
        .load_i(load_i),
        .down_i(down_i),
        .delta_i(delta_i),
        .d_i(d_i),
        .q_o(q_o),
        .max_o(max_o),
        .overflow_o(overflow_o),
        .overflow_max_o(overflow_max_o)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        clear_i = 1'b0;
        clear_max_i = 1'b0;
        en_i = 1'b0;
        load_i = 1'b0;
        down_i = 1'b0;
        delta_i = 4'd0;
        d_i = 4'd0;
        errors = 0;

        #1;
        if (q_o != 4'd0 || max_o != 4'd0 || overflow_o != 1'b0 || overflow_max_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL reset state: q=%0d max=%0d ovf=%0d ovf_max=%0d",
                q_o,
                max_o,
                overflow_o,
                overflow_max_o
            );
        end

        #9;
        rst_n = 1'b1;

        load_i = 1'b1;
        d_i = 4'd3;
        #10;
        load_i = 1'b0;
        #1;
        if (q_o != 4'd3 || max_o != 4'd3 || overflow_o != 1'b0 || overflow_max_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL load 3: q=%0d max=%0d ovf=%0d ovf_max=%0d",
                q_o,
                max_o,
                overflow_o,
                overflow_max_o
            );
        end

        #10;
        #1;
        if (q_o != 4'd3 || max_o != 4'd3 || overflow_o != 1'b0 || overflow_max_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL track max 3: q=%0d max=%0d ovf=%0d ovf_max=%0d",
                q_o,
                max_o,
                overflow_o,
                overflow_max_o
            );
        end

        en_i = 1'b1;
        down_i = 1'b0;
        delta_i = 4'd2;
        #10;
        en_i = 1'b0;
        #1;
        if (q_o != 4'd5 || max_o != 4'd5 || overflow_o != 1'b0 || overflow_max_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL increment by 2: q=%0d max=%0d ovf=%0d ovf_max=%0d",
                q_o,
                max_o,
                overflow_o,
                overflow_max_o
            );
        end

        #10;
        #1;
        if (q_o != 4'd5 || max_o != 4'd5 || overflow_o != 1'b0 || overflow_max_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL track max 5: q=%0d max=%0d ovf=%0d ovf_max=%0d",
                q_o,
                max_o,
                overflow_o,
                overflow_max_o
            );
        end

        en_i = 1'b1;
        delta_i = 4'd11;
        #10;
        en_i = 1'b0;
        #1;
        if (q_o != 4'd0 || max_o != 4'd5 || overflow_o != 1'b1 || overflow_max_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL overflow increment: q=%0d max=%0d ovf=%0d ovf_max=%0d",
                q_o,
                max_o,
                overflow_o,
                overflow_max_o
            );
        end

        en_i = 1'b1;
        delta_i = 4'd6;
        #10;
        en_i = 1'b0;
        #1;
        if (q_o != 4'd6 || max_o != 4'd6 || overflow_o != 1'b1) begin
            errors = errors + 1;
            $display(
                "FAIL post-overflow increment: q=%0d max=%0d ovf=%0d ovf_max=%0d",
                q_o,
                max_o,
                overflow_o,
                overflow_max_o
            );
        end

        #10;
        #1;
        if (q_o != 4'd6 || max_o != 4'd6 || overflow_o != 1'b1 || overflow_max_o != 1'b1) begin
            errors = errors + 1;
            $display(
                "FAIL overflow max track: q=%0d max=%0d ovf=%0d ovf_max=%0d",
                q_o,
                max_o,
                overflow_o,
                overflow_max_o
            );
        end

        clear_max_i = 1'b1;
        #10;
        clear_max_i = 1'b0;
        #1;
        if (q_o != 4'd6 || max_o != 4'd6 || overflow_o != 1'b1 || overflow_max_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL clear max only: q=%0d max=%0d ovf=%0d ovf_max=%0d",
                q_o,
                max_o,
                overflow_o,
                overflow_max_o
            );
        end

        clear_i = 1'b1;
        clear_max_i = 1'b1;
        #10;
        clear_i = 1'b0;
        clear_max_i = 1'b0;
        #1;
        if (q_o != 4'd0 || max_o != 4'd0 || overflow_o != 1'b0 || overflow_max_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL clear both: q=%0d max=%0d ovf=%0d ovf_max=%0d",
                q_o,
                max_o,
                overflow_o,
                overflow_max_o
            );
        end

        load_i = 1'b1;
        d_i = 4'd2;
        #10;
        load_i = 1'b0;
        #10;
        en_i = 1'b1;
        down_i = 1'b1;
        delta_i = 4'd1;
        #10;
        en_i = 1'b0;
        #1;
        if (q_o != 4'd1 || max_o != 4'd2 || overflow_o != 1'b0 || overflow_max_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL down-count preserve max: q=%0d max=%0d ovf=%0d ovf_max=%0d",
                q_o,
                max_o,
                overflow_o,
                overflow_max_o
            );
        end

        if (errors == 0)
            $display("PASS max_counter deterministic checks");
        else
            $display("FAIL max_counter deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
