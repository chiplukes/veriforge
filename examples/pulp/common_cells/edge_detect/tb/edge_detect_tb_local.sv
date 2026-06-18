module edge_detect_tb_local;

    logic clk;
    logic rst_n;
    logic d_i;
    logic re_o;
    logic fe_o;

    integer errors;

    edge_detect dut (
        .clk_i(clk),
        .rst_ni(rst_n),
        .d_i(d_i),
        .re_o(re_o),
        .fe_o(fe_o)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        d_i = 1'b0;
        errors = 0;

        #1;
        if (re_o != 1'b0 || fe_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL reset outputs: re=%0d fe=%0d", re_o, fe_o);
        end

        #9;
        rst_n = 1'b1;

        d_i = 1'b1;
        #10;
        #1;
        if (re_o != 1'b0 || fe_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL first rising latency: re=%0d fe=%0d", re_o, fe_o);
        end

        #10;
        #1;
        if (re_o != 1'b1 || fe_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL rising pulse: re=%0d fe=%0d", re_o, fe_o);
        end

        #10;
        #1;
        if (re_o != 1'b0 || fe_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL rising pulse clears: re=%0d fe=%0d", re_o, fe_o);
        end

        d_i = 1'b0;
        #10;
        #1;
        if (re_o != 1'b0 || fe_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL first falling latency: re=%0d fe=%0d", re_o, fe_o);
        end

        #10;
        #1;
        if (re_o != 1'b0 || fe_o != 1'b1) begin
            errors = errors + 1;
            $display("FAIL falling pulse: re=%0d fe=%0d", re_o, fe_o);
        end

        #10;
        #1;
        if (re_o != 1'b0 || fe_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL falling pulse clears: re=%0d fe=%0d", re_o, fe_o);
        end

        d_i = 1'b1;
        #10;
        #1;
        if (re_o != 1'b0 || fe_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL second rising latency: re=%0d fe=%0d", re_o, fe_o);
        end

        #10;
        #1;
        if (re_o != 1'b1 || fe_o != 1'b0) begin
            errors = errors + 1;
            $display("FAIL second rising pulse: re=%0d fe=%0d", re_o, fe_o);
        end

        if (errors == 0)
            $display("PASS edge_detect deterministic checks");
        else
            $display("FAIL edge_detect deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
