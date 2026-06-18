module shift_reg_tb_local;

    logic       clk;
    logic       rst_n;

    logic       pass_valid_i;
    logic [7:0] pass_data_i;
    logic       pass_valid_o;
    logic [7:0] pass_data_o;

    logic       gated_valid_i;
    logic [7:0] gated_data_i;
    logic       gated_valid_o;
    logic [7:0] gated_data_o;

    logic [7:0] shift_data_i;
    logic [7:0] shift_data_o;

    integer errors;

    shift_reg_gated #(
        .Width(8),
        .Depth(0)
    ) dut_pass (
        .clk_i(clk),
        .rst_ni(rst_n),
        .valid_i(pass_valid_i),
        .data_i(pass_data_i),
        .valid_o(pass_valid_o),
        .data_o(pass_data_o)
    );

    shift_reg_gated #(
        .Width(8),
        .Depth(3)
    ) dut_gated (
        .clk_i(clk),
        .rst_ni(rst_n),
        .valid_i(gated_valid_i),
        .data_i(gated_data_i),
        .valid_o(gated_valid_o),
        .data_o(gated_data_o)
    );

    shift_reg #(
        .Width(8),
        .Depth(3)
    ) dut_shift (
        .clk_i(clk),
        .rst_ni(rst_n),
        .d_i(shift_data_i),
        .d_o(shift_data_o)
    );

    always #5 clk = ~clk;

    initial begin
        clk = 1'b0;
        rst_n = 1'b0;
        pass_valid_i = 1'b0;
        pass_data_i = 8'h00;
        gated_valid_i = 1'b0;
        gated_data_i = 8'h00;
        shift_data_i = 8'h00;
        errors = 0;

        #1;
        if (gated_valid_o != 1'b0 || gated_data_o != 8'h00 || shift_data_o != 8'h00) begin
            errors = errors + 1;
            $display(
                "FAIL reset state: gated_valid=%0d gated_data=%0h shift_data=%0h",
                gated_valid_o,
                gated_data_o,
                shift_data_o
            );
        end

        #9;
        rst_n = 1'b1;

        pass_valid_i = 1'b1;
        pass_data_i = 8'hA5;
        #1;
        if (pass_valid_o != 1'b1 || pass_data_o != 8'hA5) begin
            errors = errors + 1;
            $display("FAIL depth0 pass-through high: valid=%0d data=%0h", pass_valid_o, pass_data_o);
        end

        pass_valid_i = 1'b0;
        pass_data_i = 8'h3C;
        #1;
        if (pass_valid_o != 1'b0 || pass_data_o != 8'h3C) begin
            errors = errors + 1;
            $display("FAIL depth0 pass-through low: valid=%0d data=%0h", pass_valid_o, pass_data_o);
        end

        gated_valid_i = 1'b1;
        gated_data_i = 8'h11;
        shift_data_i = 8'h10;
        #10;
        if (gated_valid_o != 1'b0 || shift_data_o != 8'h00) begin
            errors = errors + 1;
            $display("FAIL first cycle latency: gated_valid=%0d shift_data=%0h", gated_valid_o, shift_data_o);
        end

        gated_valid_i = 1'b1;
        gated_data_i = 8'h22;
        shift_data_i = 8'h20;
        #10;
        if (gated_valid_o != 1'b0 || shift_data_o != 8'h00) begin
            errors = errors + 1;
            $display("FAIL second cycle latency: gated_valid=%0d shift_data=%0h", gated_valid_o, shift_data_o);
        end

        gated_valid_i = 1'b0;
        gated_data_i = 8'h33;
        shift_data_i = 8'h30;
        #10;
        if (gated_valid_o != 1'b1 || gated_data_o != 8'h11 || shift_data_o != 8'h10) begin
            errors = errors + 1;
            $display(
                "FAIL third cycle first output: gated_valid=%0d gated_data=%0h shift_data=%0h",
                gated_valid_o,
                gated_data_o,
                shift_data_o
            );
        end

        gated_valid_i = 1'b0;
        gated_data_i = 8'h44;
        shift_data_i = 8'h40;
        #10;
        if (gated_valid_o != 1'b1 || gated_data_o != 8'h22 || shift_data_o != 8'h20) begin
            errors = errors + 1;
            $display(
                "FAIL fourth cycle second output: gated_valid=%0d gated_data=%0h shift_data=%0h",
                gated_valid_o,
                gated_data_o,
                shift_data_o
            );
        end

        shift_data_i = 8'h50;
        #10;
        if (gated_valid_o != 1'b0 || shift_data_o != 8'h30) begin
            errors = errors + 1;
            $display("FAIL gated drain or shift advance: gated_valid=%0d shift_data=%0h", gated_valid_o, shift_data_o);
        end

        if (errors == 0)
            $display("PASS shift_reg deterministic checks");
        else
            $display("FAIL shift_reg deterministic checks: %0d errors", errors);

        $finish;
    end

endmodule
