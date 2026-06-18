module credit_counter_tb_local;

    logic       clk;
    logic       rst_n;

    logic       full_give_i;
    logic       full_take_i;
    logic       full_init_i;
    logic [2:0] full_credit_o;
    logic       full_left_o;
    logic       full_crit_o;
    logic       full_full_o;

    logic       empty_give_i;
    logic       empty_take_i;
    logic       empty_init_i;
    logic [2:0] empty_credit_o;
    logic       empty_left_o;
    logic       empty_crit_o;
    logic       empty_full_o;

    integer errors;

    credit_counter #(
        .INIT_CREDIT_EMPTY(1'b0)
    ) dut_full (
        .clk_i(clk),
        .rst_ni(rst_n),
        .credit_o(full_credit_o),
        .credit_give_i(full_give_i),
        .credit_take_i(full_take_i),
        .credit_init_i(full_init_i),
        .credit_left_o(full_left_o),
        .credit_crit_o(full_crit_o),
        .credit_full_o(full_full_o)
    );

    credit_counter #(
        .INIT_CREDIT_EMPTY(1'b1)
    ) dut_empty (
        .clk_i(clk),
        .rst_ni(rst_n),
        .credit_o(empty_credit_o),
        .credit_give_i(empty_give_i),
        .credit_take_i(empty_take_i),
        .credit_init_i(empty_init_i),
        .credit_left_o(empty_left_o),
        .credit_crit_o(empty_crit_o),
        .credit_full_o(empty_full_o)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        full_give_i = 1'b0;
        full_take_i = 1'b0;
        full_init_i = 1'b0;
        empty_give_i = 1'b0;
        empty_take_i = 1'b0;
        empty_init_i = 1'b0;
        errors = 0;

        #1;
        if (full_credit_o != 3'd3 || full_left_o != 1'b1 || full_crit_o != 1'b0 || full_full_o != 1'b1) begin
            errors = errors + 1;
            $display(
                "FAIL full reset: credit=%0d left=%0d crit=%0d full=%0d",
                full_credit_o,
                full_left_o,
                full_crit_o,
                full_full_o
            );
        end
        if (empty_credit_o != 3'd0 || empty_left_o != 1'b0 || empty_crit_o != 1'b0 || empty_full_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL empty reset: credit=%0d left=%0d crit=%0d full=%0d",
                empty_credit_o,
                empty_left_o,
                empty_crit_o,
                empty_full_o
            );
        end

        #9;
        rst_n = 1'b1;

        full_take_i = 1'b1;
        #10;
        full_take_i = 1'b0;
        #1;
        if (full_credit_o != 3'd2 || full_left_o != 1'b1 || full_crit_o != 1'b1 || full_full_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL full take to crit: credit=%0d left=%0d crit=%0d full=%0d",
                full_credit_o,
                full_left_o,
                full_crit_o,
                full_full_o
            );
        end

        full_give_i = 1'b1;
        full_take_i = 1'b1;
        #10;
        full_give_i = 1'b0;
        full_take_i = 1'b0;
        #1;
        if (full_credit_o != 3'd2 || full_left_o != 1'b1 || full_crit_o != 1'b1 || full_full_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL full same-cycle hold: credit=%0d left=%0d crit=%0d full=%0d",
                full_credit_o,
                full_left_o,
                full_crit_o,
                full_full_o
            );
        end

        full_take_i = 1'b1;
        #10;
        full_take_i = 1'b0;
        #1;
        if (full_credit_o != 3'd1 || full_left_o != 1'b1 || full_crit_o != 1'b0 || full_full_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL full take to one: credit=%0d left=%0d crit=%0d full=%0d",
                full_credit_o,
                full_left_o,
                full_crit_o,
                full_full_o
            );
        end

        full_take_i = 1'b1;
        full_init_i = 1'b1;
        #10;
        full_take_i = 1'b0;
        full_init_i = 1'b0;
        #1;
        if (full_credit_o != 3'd3 || full_left_o != 1'b1 || full_crit_o != 1'b0 || full_full_o != 1'b1) begin
            errors = errors + 1;
            $display(
                "FAIL full init priority: credit=%0d left=%0d crit=%0d full=%0d",
                full_credit_o,
                full_left_o,
                full_crit_o,
                full_full_o
            );
        end

        empty_give_i = 1'b1;
        #10;
        empty_give_i = 1'b0;
        #1;
        if (empty_credit_o != 3'd1 || empty_left_o != 1'b1 || empty_crit_o != 1'b0 || empty_full_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL empty give to one: credit=%0d left=%0d crit=%0d full=%0d",
                empty_credit_o,
                empty_left_o,
                empty_crit_o,
                empty_full_o
            );
        end

        empty_give_i = 1'b1;
        #10;
        empty_give_i = 1'b0;
        #1;
        if (empty_credit_o != 3'd2 || empty_left_o != 1'b1 || empty_crit_o != 1'b1 || empty_full_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL empty give to crit: credit=%0d left=%0d crit=%0d full=%0d",
                empty_credit_o,
                empty_left_o,
                empty_crit_o,
                empty_full_o
            );
        end

        empty_give_i = 1'b1;
        empty_take_i = 1'b1;
        #10;
        empty_give_i = 1'b0;
        empty_take_i = 1'b0;
        #1;
        if (empty_credit_o != 3'd2 || empty_left_o != 1'b1 || empty_crit_o != 1'b1 || empty_full_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL empty same-cycle hold: credit=%0d left=%0d crit=%0d full=%0d",
                empty_credit_o,
                empty_left_o,
                empty_crit_o,
                empty_full_o
            );
        end

        empty_give_i = 1'b1;
        #10;
        empty_give_i = 1'b0;
        #1;
        if (empty_credit_o != 3'd3 || empty_left_o != 1'b1 || empty_crit_o != 1'b0 || empty_full_o != 1'b1) begin
            errors = errors + 1;
            $display(
                "FAIL empty give to full: credit=%0d left=%0d crit=%0d full=%0d",
                empty_credit_o,
                empty_left_o,
                empty_crit_o,
                empty_full_o
            );
        end

        empty_take_i = 1'b1;
        empty_init_i = 1'b1;
        #10;
        empty_take_i = 1'b0;
        empty_init_i = 1'b0;
        #1;
        if (empty_credit_o != 3'd0 || empty_left_o != 1'b0 || empty_crit_o != 1'b0 || empty_full_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL empty init priority: credit=%0d left=%0d crit=%0d full=%0d",
                empty_credit_o,
                empty_left_o,
                empty_crit_o,
                empty_full_o
            );
        end

        empty_give_i = 1'b1;
        #10;
        empty_give_i = 1'b0;
        #1;
        if (empty_credit_o != 3'd1 || empty_left_o != 1'b1 || empty_crit_o != 1'b0 || empty_full_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL empty give after init: credit=%0d left=%0d crit=%0d full=%0d",
                empty_credit_o,
                empty_left_o,
                empty_crit_o,
                empty_full_o
            );
        end

        empty_take_i = 1'b1;
        #10;
        empty_take_i = 1'b0;
        #1;
        if (empty_credit_o != 3'd0 || empty_left_o != 1'b0 || empty_crit_o != 1'b0 || empty_full_o != 1'b0) begin
            errors = errors + 1;
            $display(
                "FAIL empty take to zero: credit=%0d left=%0d crit=%0d full=%0d",
                empty_credit_o,
                empty_left_o,
                empty_crit_o,
                empty_full_o
            );
        end

        if (errors == 0)
            $display("PASS credit_counter deterministic checks");
        else
            $display("FAIL credit_counter deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
